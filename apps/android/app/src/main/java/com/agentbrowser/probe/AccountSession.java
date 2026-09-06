package com.agentbrowser.probe;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Owns the Android account-directory projection and native account lifetime.
 * WebView receives snapshots only; this class never stores a password or token.
 */
final class AccountSession {
    private static final int MAX_ORIGIN_BYTES = 256;
    private static final int MAX_CA_BYTES = 128 * 1024;
    private final Context context;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private long generation;
    private long handle;
    private boolean closed;
    private String state = "signed_out";
    private String error;
    private String cleanupError;
    private JSONObject nativeState = emptyNativeState();

    AccountSession(Context context) { this.context = context.getApplicationContext(); }

    synchronized JSONObject request(JSONObject command) throws Exception {
        String op = command.optString("op", "");
        if (!"account_status".equals(op) && closed) throw new IllegalStateException("ACCOUNT_SESSION_CLOSED");
        switch (op) {
            case "account_status" -> require(command, "op");
            case "account_login" -> login(command);
            case "account_register_device" -> registerDevice(command);
            case "account_refresh" -> refresh(command);
            case "account_logout" -> logout(command);
            default -> throw new IllegalArgumentException("UNKNOWN_ACCOUNT_COMMAND");
        }
        return snapshot();
    }

    synchronized JSONObject status() { return snapshot(); }

    synchronized void close() {
        if (closed) return;
        closed = true;
        generation = next(generation);
        long old = handle;
        handle = 0;
        RuntimeException cleanupFailure = old == 0 ? null : closeNative(old);
        nativeState = emptyNativeState();
        if (cleanupFailure == null) {
            state = "signed_out";
            error = null;
        } else {
            state = "error";
            error = null;
            recordCleanupFailure("ACCOUNT_NATIVE_CLOSE_FAILED", cleanupFailure);
        }
        worker.shutdownNow();
    }

    private void login(JSONObject command) throws Exception {
        require(command, "op", "username", "password");
        if (pending()) throw new IllegalStateException("ACCOUNT_OPERATION_PENDING");
        String username = command.getString("username");
        String password = command.getString("password");
        if (username.isEmpty() || username.length() > 64) throw new IllegalArgumentException("INVALID_USERNAME");
        if (password.isEmpty() || password.length() > 1024) throw new IllegalArgumentException("INVALID_PASSWORD");
        byte[] ca = readRelayFile("ca.der", MAX_CA_BYTES);
        String origin = new String(readRelayFile("origin.txt", MAX_ORIGIN_BYTES), StandardCharsets.UTF_8).trim();
        if (origin.isEmpty()) throw new IllegalStateException("RELAY_ORIGIN_MISSING");
        long expected = next(generation);
        generation = expected;
        state = "signing_in";
        error = null;
        nativeState = emptyNativeState();
        long old = handle;
        handle = 0;
        RuntimeException cleanupFailure = old == 0 ? null : closeNative(old);
        if (cleanupFailure != null) {
            state = "error";
            recordCleanupFailure("ACCOUNT_NATIVE_CLOSE_FAILED", cleanupFailure);
            return;
        }
        worker.execute(() -> login(expected, origin, ca, username, password));
    }

    private void login(long expected, String origin, byte[] ca, String username, String password) {
        long opened = 0;
        try {
            NativeConnection.load();
            opened = NativeAccount.login(origin, ca, username, password);
            JSONObject value = new JSONObject(NativeAccount.status(opened));
            synchronized (this) {
                if (closed || generation != expected) {
                    RuntimeException cleanupFailure = closeNative(opened);
                    if (cleanupFailure != null) recordCleanupFailure("ACCOUNT_NATIVE_CLOSE_FAILED", cleanupFailure);
                    return;
                }
                handle = opened;
                nativeState = value;
                state = value.optString("accountState", "authenticated");
                error = null;
            }
        } catch (Exception failure) {
            RuntimeException cleanupFailure = opened == 0 ? null : closeNative(opened);
            synchronized (this) {
                if (cleanupFailure != null) recordCleanupFailure("ACCOUNT_NATIVE_CLOSE_FAILED", cleanupFailure);
                if (closed || generation != expected) return;
                state = "error";
                error = failure.toString();
            }
        }
    }

    private void registerDevice(JSONObject command) throws Exception {
        require(command, "op", "name");
        if (pending()) throw new IllegalStateException("ACCOUNT_OPERATION_PENDING");
        if (handle == 0 || !authenticated()) throw new IllegalStateException("ACCOUNT_NOT_AUTHENTICATED");
        String name = command.getString("name");
        if (name.isEmpty() || name.length() > 64) throw new IllegalArgumentException("INVALID_DEVICE_NAME");
        long expected = next(generation);
        generation = expected;
        state = "registering_device";
        error = null;
        long current = handle;
        worker.execute(() -> {
            try {
                JSONObject value = new JSONObject(NativeAccount.registerDevice(current, name));
                synchronized (this) {
                    if (closed || generation != expected || handle != current) return;
                    nativeState = value;
                    state = value.optString("accountState", "authenticated");
                    error = null;
                }
            } catch (Exception failure) {
                synchronized (this) {
                    if (closed || generation != expected || handle != current) return;
                    state = "error";
                    error = failure.toString();
                }
            }
        });
    }

    private void refresh(JSONObject command) throws Exception {
        require(command, "op");
        if (pending()) throw new IllegalStateException("ACCOUNT_OPERATION_PENDING");
        if (handle == 0 || !authenticated()) throw new IllegalStateException("ACCOUNT_NOT_AUTHENTICATED");
        long expected = next(generation);
        generation = expected;
        state = "refreshing_directory";
        error = null;
        long current = handle;
        worker.execute(() -> {
            try {
                JSONObject value = new JSONObject(NativeAccount.refresh(current));
                synchronized (this) {
                    if (closed || generation != expected || handle != current) return;
                    nativeState = value;
                    state = value.optString("accountState", "authenticated");
                    error = null;
                }
            } catch (Exception failure) {
                synchronized (this) {
                    if (closed || generation != expected || handle != current) return;
                    if (expired()) state = "expired";
                    else state = "error";
                    error = failure.toString();
                }
            }
        });
    }

    private void logout(JSONObject command) throws Exception {
        require(command, "op");
        if (pending() && handle == 0) {
            generation = next(generation);
            state = "signed_out";
            nativeState = emptyNativeState();
            error = null;
            return;
        }
        long current = handle;
        long expected = next(generation);
        generation = expected;
        handle = 0;
        state = "signing_out";
        error = null;
        if (current == 0) {
            state = "signed_out";
            nativeState = emptyNativeState();
            return;
        }
        worker.execute(() -> {
            try {
                // RelayClient::revoke is the only remote logout truth. If it
                // fails, local cleanup still happens but the warning is kept.
                NativeAccount.revoke(current);
                synchronized (this) {
                    if (closed || generation != expected || handle != 0) return;
                    state = "signed_out";
                    nativeState = emptyNativeState();
                    error = null;
                }
            } catch (Exception failure) {
                RuntimeException cleanupFailure = closeNative(current);
                synchronized (this) {
                    if (cleanupFailure != null) recordCleanupFailure("ACCOUNT_NATIVE_CLOSE_FAILED", cleanupFailure);
                    if (closed || generation != expected || handle != 0) return;
                    state = "signed_out";
                    nativeState = emptyNativeState();
                    error = "RELAY_REVOKE_UNCONFIRMED: " + failure;
                }
            }
        });
    }

    private synchronized boolean pending() {
        return state.equals("signing_in") || state.equals("registering_device")
            || state.equals("refreshing_directory") || state.equals("signing_out");
    }

    private synchronized boolean authenticated() {
        return state.equals("authenticated") && !expired();
    }

    private synchronized boolean expired() {
        long expires = nativeState.optLong("expiresAtMs", 0);
        return expires > 0 && System.currentTimeMillis() >= expires;
    }

    private synchronized JSONObject snapshot() {
        try {
            refreshNativeState();
            JSONObject value = new JSONObject(nativeState.toString());
            String shownState = state;
            if ((state.equals("authenticated") || state.equals("error")) && expired()) shownState = "expired";
            value.put("accountState", shownState);
            value.put("generation", generation);
            value.put("pending", pending());
            String visibleError = error;
            if (cleanupError != null) visibleError = visibleError == null ? cleanupError : visibleError + "; " + cleanupError;
            value.put("error", visibleError == null ? JSONObject.NULL : visibleError);
            if (!value.has("hosts")) value.put("hosts", new JSONArray());
            return value;
        } catch (org.json.JSONException invalid) {
            throw new IllegalStateException("INVALID_ACCOUNT_SNAPSHOT", invalid);
        }
    }

    private byte[] readRelayFile(String name, int max) throws Exception {
        File file = new File(new File(context.getFilesDir(), "relay"), name);
        if (!file.isFile() || file.length() == 0 || file.length() > max)
            throw new IllegalStateException("RELAY_CONFIG_MISSING_OR_OVERSIZED:" + name);
        return Files.readAllBytes(file.toPath());
    }

    private static JSONObject emptyNativeState() {
        try {
            return new JSONObject().put("accountState", "signed_out").put("expiresAtMs", 0)
                .put("deviceId", JSONObject.NULL).put("directoryState", "empty").put("hosts", new JSONArray());
        } catch (org.json.JSONException invalid) { throw new IllegalStateException(invalid); }
    }

    private static long next(long value) {
        if (value == Long.MAX_VALUE) throw new IllegalStateException("ACCOUNT_GENERATION_EXHAUSTED");
        return value + 1;
    }

    private void refreshNativeState() {
        if (closed || handle == 0 || pending()) return;
        try {
            nativeState = new JSONObject(NativeAccount.status(handle));
        } catch (Exception failure) {
            nativeState = emptyNativeState();
            state = "error";
            error = "ACCOUNT_NATIVE_STATUS_FAILED: " + failure;
        }
    }

    private void recordCleanupFailure(String code, RuntimeException failure) {
        String value = code + ": " + failure;
        cleanupError = cleanupError == null ? value : cleanupError + "; " + value;
    }

    private static RuntimeException closeNative(long value) {
        try {
            NativeAccount.close(value);
            return null;
        } catch (RuntimeException failure) {
            return failure;
        }
    }

    private static void require(JSONObject value, String... allowed) throws Exception {
        java.util.Set<String> names = new java.util.HashSet<>(java.util.Arrays.asList(allowed));
        java.util.Iterator<String> keys = value.keys();
        while (keys.hasNext()) if (!names.contains(keys.next())) throw new IllegalArgumentException("UNKNOWN_ACCOUNT_COMMAND_FIELD");
        for (String name : allowed) if (!"op".equals(name) && !value.has(name)) throw new IllegalArgumentException("MISSING_ACCOUNT_COMMAND_FIELD");
    }
}
