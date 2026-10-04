package com.agentbrowser.probe;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.AtomicFile;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Single admission owner for the Android first-connection configuration. It
 * owns the SAF picker, the secure transfer-code dialog, immutable generation
 * writes, the AtomicFile active reference, exact readback classification and
 * the immutable binding handed to a NEW connection.
 */
final class ConnectionSetupSession {
    enum IntentType { ORDINARY, REPAIR }

    static final int REQUEST_OPEN_CONNECTION = 0x4D31;
    static final String ACTIVE_FILE = "active-generation.json";
    static final String ACTIVE_SCHEMA = "agentbrowser.connection.active";
    static final int ACTIVE_VERSION = 1;

    interface CodePrompt {
        String requestCode() throws Exception;
    }

    private final Activity activity;
    private final File pairingRoot;
    private final File stagingRoot;
    private final File generationsRoot;
    private final File activeFile;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Object lock = new Object();
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Map<String, Integer> leases = new HashMap<>();
    private final ArrayList<File> retainedAttempts = new ArrayList<>();

    private String state = "required";
    private String error;
    private String label;
    private long activeRevision = -1;
    private ActiveConfiguration active;
    private ActiveSnapshot knownPrior;
    private ActiveSnapshot attempted;
    private boolean busy;
    private boolean closed;
    private IntentType pendingIntent;
    private String pendingPreState;
    private boolean abortRequested;
    private AlertDialog codeDialog;
    private volatile CodePrompt codePrompt;
    private boolean codePromptHookActive;
    private static volatile CodePrompt codePromptHook;

    ConnectionSetupSession(Activity activity) {
        this(activity, activity.getApplicationContext().getFilesDir());
    }

    ConnectionSetupSession(Activity activity, File filesRoot) {
        this.activity = activity;
        this.pairingRoot = new File(filesRoot, "pairing");
        this.stagingRoot = new File(pairingRoot, ".staging");
        this.generationsRoot = new File(pairingRoot, ".generations");
        this.activeFile = new File(pairingRoot, ACTIVE_FILE);
        synchronized (lock) {
            loadActiveLocked();
        }
    }

    /** Test hook; production always opens the native secure dialog. */
    static void setCodePromptHook(CodePrompt prompt) {
        codePromptHook = prompt;
    }

    JSONObject projection() {
        synchronized (lock) {
            try {
                return new JSONObject()
                    .put("state", state)
                    .put("error", error == null ? JSONObject.NULL : error)
                    .put("label", label == null ? JSONObject.NULL : label)
                    .put("repair_import_allowed", "unavailable".equals(state))
                    .put("active_revision", activeRevision < 0 ? JSONObject.NULL : activeRevision);
            } catch (org.json.JSONException failure) {
                throw new IllegalStateException("CONNECTION_PROJECTION_INVALID", failure);
            }
        }
    }

    boolean importing() {
        synchronized (lock) {
            return busy;
        }
    }

    void onOpenRequested() {
        requireMainThread();
        synchronized (lock) {
            if (closed) throw new IllegalStateException("CONNECTION_SETUP_CLOSED");
            if (busy) throw new IllegalStateException("CONNECTION_SETUP_BUSY");
            if ("unresolved".equals(state)) throw new IllegalStateException("CONFIG_ACTIVE_COMMIT_UNRESOLVED");
            if ("unavailable".equals(state)) throw new IllegalStateException("CONFIG_ACTIVE_REFERENCE_INVALID");
            state = "opening";
            error = null;
            busy = true;
            pendingIntent = IntentType.ORDINARY;
        }
        if (activity == null) return;
        try {
            activity.startActivityForResult(documentPicker(), REQUEST_OPEN_CONNECTION);
        } catch (Exception failure) {
            synchronized (lock) {
                busy = false;
                state = "required";
                error = "CONFIG_PICKER_UNAVAILABLE";
            }
        }
    }

    void onImportRequested() {
        beginImport(IntentType.ORDINARY);
    }

    void onRepairImportRequested() {
        beginImport(IntentType.REPAIR);
    }

    void onRetryReconcileRequested() {
        requireMainThread();
        synchronized (lock) {
            if (closed) throw new IllegalStateException("CONNECTION_SETUP_CLOSED");
            if (busy) throw new IllegalStateException("CONNECTION_SETUP_BUSY");
            if (!"unresolved".equals(state) && !"unavailable".equals(state))
                throw new IllegalStateException("CONFIG_RECONCILE_NOT_ALLOWED");
            reconcileLocked();
        }
    }

    void onActivityResult(Uri uri) {
        requireMainThread();
        IntentType intent;
        synchronized (lock) {
            if (!busy || pendingIntent == null) return;
            if (uri == null) {
                finishCancelledLocked();
                return;
            }
            intent = pendingIntent;
            abortRequested = false;
            if (pendingPreState == null) pendingPreState = state;
            state = "importing";
            error = null;
        }
        worker.execute(() -> runImport(uri, intent));
    }

    void onActivityBack() {
        requireMainThread();
        synchronized (lock) {
            if (!busy) return;
            abortRequested = true;
            if (codeDialog != null) codeDialog.dismiss();
        }
    }

    /** Instrumentation entrypoint: launch the same picker intent without JS. */
    void requestPickerForTest(IntentType intent) {
        beginImport(intent);
    }

    /** Instrumentation entrypoint: same path as the SAF callback. */
    void startImportForTest(Uri uri, IntentType intent) {
        synchronized (lock) {
            if (closed) throw new IllegalStateException("CONNECTION_SETUP_CLOSED");
            if (busy) throw new IllegalStateException("CONNECTION_SETUP_BUSY");
            admitIntentLocked(intent);
            busy = true;
            pendingIntent = intent;
            pendingPreState = state;
            state = "importing";
            error = null;
            abortRequested = false;
        }
        worker.execute(() -> runImport(uri, intent));
    }

    ActiveConfiguration admitNewBinding() {
        synchronized (lock) {
            if (closed) throw new IllegalStateException("CONNECTION_SETUP_CLOSED");
            if ("unresolved".equals(state)) {
                reconcileLocked();
            }
            if ("unresolved".equals(state))
                throw new IllegalStateException("CONFIG_ACTIVE_COMMIT_UNRESOLVED");
            if ("unavailable".equals(state))
                throw new IllegalStateException("CONFIG_ACTIVE_REFERENCE_INVALID");
            if (!"configured".equals(state) || active == null)
                throw new IllegalStateException("CONNECTION_NOT_CONFIGURED");
            ActiveSnapshot actual = readSnapshotLocked();
            if (actual.outcome != Outcome.VALID || active == null
                    || !actual.identity.equals(identityOf(active))) {
                reconcileLocked();
                if (!"configured".equals(state) || active == null)
                    throw new IllegalStateException("CONFIG_ACTIVE_COMMIT_UNRESOLVED");
            }
            leases.merge(active.generationId(), 1, Integer::sum);
            return active;
        }
    }

    void release(ActiveConfiguration configuration) {
        if (configuration == null) return;
        synchronized (lock) {
            Integer count = leases.get(configuration.generationId());
            if (count == null) return;
            if (count <= 1) leases.remove(configuration.generationId());
            else leases.put(configuration.generationId(), count - 1);
        }
    }

    void close() {
        synchronized (lock) {
            if (closed) return;
            closed = true;
            abortRequested = true;
            if (codeDialog != null) {
                codeDialog.dismiss();
                codeDialog = null;
            }
        }
        worker.shutdownNow();
    }

    // --- Intent admission ---

    private void beginImport(IntentType intent) {
        requireMainThread();
        synchronized (lock) {
            if (closed) throw new IllegalStateException("CONNECTION_SETUP_CLOSED");
            if (busy) throw new IllegalStateException("CONNECTION_SETUP_BUSY");
            admitIntentLocked(intent);
            busy = true;
            pendingIntent = intent;
            pendingPreState = state;
            state = "opening";
            error = null;
            abortRequested = false;
        }
        if (activity == null) return;
        try {
            activity.startActivityForResult(documentPicker(), REQUEST_OPEN_CONNECTION);
        } catch (Exception failure) {
            synchronized (lock) {
                busy = false;
                pendingIntent = null;
                state = "required";
                error = "CONFIG_PICKER_UNAVAILABLE";
            }
        }
    }

    private void admitIntentLocked(IntentType intent) {
        if (intent == IntentType.REPAIR) {
            if (!"unavailable".equals(state))
                throw new IllegalStateException("CONFIG_REPAIR_IMPORT_NOT_ALLOWED");
            return;
        }
        if (!"required".equals(state) && !"configured".equals(state))
            throw new IllegalStateException("CONFIG_ORDINARY_IMPORT_BLOCKED");
    }

    private Intent documentPicker() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType("*/*");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        return intent;
    }

    private void finishCancelledLocked() {
        String preState = pendingPreState;
        busy = false;
        pendingIntent = null;
        abortRequested = false;
        error = null;
        pendingPreState = null;
        if ("unavailable".equals(preState)) {
            state = "unavailable";
            error = "CONFIG_ACTIVE_REFERENCE_INVALID";
        } else if ("configured".equals(preState)) {
            state = "configured";
        } else {
            state = "required";
        }
    }

    // --- Import pipeline ---

    private void runImport(Uri uri, IntentType intent) {
        String preState;
        synchronized (lock) {
            preState = pendingPreState != null ? pendingPreState : state;
            state = "importing";
            error = null;
        }
        try {
            byte[] raw = readUri(uri);
            ConnectionBundle bundle = null;
            String code;
            for (int attempt = 0; attempt < 3; attempt++) {
                code = requestCode();
                if (code == null) {
                    finishCancel(intent, preState);
                    return;
                }
                try {
                    bundle = ConnectionBundle.parse(raw, code);
                    break;
                } catch (IllegalArgumentException failure) {
                    String message = failure.getMessage() == null ? "BUNDLE_INVALID" : failure.getMessage();
                    if (!message.startsWith("BUNDLE_TRANSFER_CODE_INVALID")) {
                        throw failure;
                    }
                    synchronized (lock) {
                        error = "BUNDLE_TRANSFER_CODE_INVALID";
                    }
                    if (!codePromptHookActive) throw failure;
                }
            }
            if (bundle == null) throw new IllegalArgumentException("BUNDLE_TRANSFER_CODE_INVALID");
            commit(bundle, intent, preState);
        } catch (ImportAbort abort) {
            finishCancel(intent, preState);
        } catch (IllegalStateException alreadyTyped) {
            finishFailure(intent, preState, alreadyTyped.getMessage());
        } catch (Exception failure) {
            finishFailure(intent, preState, typedMessage(failure));
        }
    }

    private void commit(ConnectionBundle bundle, IntentType intent, String preState) throws Exception {
        synchronized (lock) {
            if (intent == IntentType.REPAIR) {
                if (!"unavailable".equals(preState))
                    throw new IllegalStateException("CONFIG_REPAIR_IMPORT_NOT_ALLOWED");
            } else if (!"required".equals(preState) && !"configured".equals(preState)) {
                throw new IllegalStateException("CONFIG_ORDINARY_IMPORT_BLOCKED");
            }
        }
        String generationId = newGenerationId();
        File staging = new File(stagingRoot, generationId);
        byte[] endpointBytes = (bundle.endpoint() + "\n").getBytes(StandardCharsets.UTF_8);
        byte[] ca = bundle.caDer();
        byte[] clientCert = bundle.clientCertDer();
        byte[] clientKey = bundle.clientKeyDer();
        String endpointSha = ConnectionBundle.hex(ConnectionBundle.digest("SHA-256", endpointBytes));
        String caSha = ConnectionBundle.hex(ConnectionBundle.digest("SHA-256", ca));
        String clientCertSha = ConnectionBundle.hex(ConnectionBundle.digest("SHA-256", clientCert));
        String clientKeySha = ConnectionBundle.hex(ConnectionBundle.digest("SHA-256", clientKey));
        try {
            ensureDirectory(staging, true);
            writeSynced(new File(staging, ActiveConfiguration.ENDPOINT_FILE), endpointBytes, false);
            writeSynced(new File(staging, ActiveConfiguration.CA_FILE), ca, false);
            writeSynced(new File(staging, ActiveConfiguration.CLIENT_CERT_FILE), clientCert, false);
            writeSynced(new File(staging, ActiveConfiguration.CLIENT_KEY_FILE), clientKey, true);
            ensureDirectory(generationsRoot, false);
            File generationRoot = new File(generationsRoot, generationId);
            if (generationRoot.exists() || !staging.renameTo(generationRoot)) {
                throw new IllegalStateException("CONFIG_GENERATION_COMMIT_FAILED");
            }
            ActiveSnapshot prior;
            long revision;
            synchronized (lock) {
                ActiveSnapshot actual = readSnapshotLocked();
                if (intent == IntentType.REPAIR) {
                    if (!"unavailable".equals(preState) || actual.outcome != Outcome.INVALID)
                        throw new IllegalStateException("CONFIG_REPAIR_IMPORT_NOT_ALLOWED");
                } else if (!"required".equals(preState) && !"configured".equals(preState)) {
                    throw new IllegalStateException("CONFIG_ORDINARY_IMPORT_BLOCKED");
                }
                if (!actual.sameAs(knownPrior)) {
                    state = "unresolved";
                    error = "CONFIG_ACTIVE_COMMIT_UNRESOLVED";
                    busy = false;
                    pendingIntent = null;
                    pendingPreState = null;
                    throw new IllegalStateException("CONFIG_ACTIVE_COMMIT_UNRESOLVED");
                }
                prior = actual;
                revision = activeRevision < 0 ? 1 : activeRevision + 1;
            }
            ActiveConfiguration candidate = new ActiveConfiguration(generationId, revision,
                bundle.endpoint(), ca, clientCert, clientKey, bundle.deviceLabel(),
                endpointSha, caSha, clientCertSha, clientKeySha, generationRoot);
            byte[] activeBytes = activeJson(candidate);
            writeActiveFile(activeBytes);
            ActiveSnapshot readback = readSnapshotLocked();
            Outcome outcome = classify(prior, candidate, readback);
            synchronized (lock) {
                if (outcome == Outcome.COMMITTED) {
                    active = candidate;
                    activeRevision = revision;
                    label = candidate.deviceLabel();
                    state = "configured";
                    error = null;
                    knownPrior = readback;
                    attempted = null;
                    busy = false;
                    pendingIntent = null;
                    pendingPreState = null;
                } else if (outcome == Outcome.NOT_COMMITTED) {
                    busy = false;
                    pendingIntent = null;
                    pendingPreState = null;
                    attempted = readSnapshotOf(candidate);
                    if (prior.outcome == Outcome.NONE) {
                        state = "required";
                        active = null;
                        label = null;
                        activeRevision = -1;
                        error = null;
                    } else if (prior.outcome == Outcome.INVALID) {
                        state = "unavailable";
                        active = null;
                        error = "CONFIG_ACTIVE_REFERENCE_INVALID";
                    } else {
                        state = "configured";
                        error = null;
                    }
                    knownPrior = prior;
                } else {
                    state = "unresolved";
                    error = "CONFIG_ACTIVE_COMMIT_UNRESOLVED";
                    busy = false;
                    pendingIntent = null;
                    pendingPreState = null;
                    attempted = readSnapshotOf(candidate);
                    retainedAttempts.add(generationRoot);
                }
            }
            if (outcome == Outcome.COMMITTED || outcome == Outcome.NOT_COMMITTED) {
                cleanupAfterResolution(candidate, outcome);
            }
        } catch (Exception failure) {
            throw failure;
        }
    }

    private enum Outcome { NONE, VALID, INVALID, UNRESOLVED, COMMITTED, NOT_COMMITTED }

    private static final class ActiveSnapshot {
        final Outcome outcome;
        final ActiveIdentity identity;
        final ActiveConfiguration configuration;
        final String errorCode;
        final byte[] raw;
        ActiveSnapshot(Outcome outcome, ActiveIdentity identity, ActiveConfiguration configuration,
                String errorCode, byte[] raw) {
            this.outcome = outcome;
            this.identity = identity;
            this.configuration = configuration;
            this.errorCode = errorCode;
            this.raw = raw;
        }
        boolean sameAs(ActiveSnapshot other) {
            if (other == null || outcome != other.outcome) return false;
            if (outcome != Outcome.VALID) {
                return Objects.equals(errorCode, other.errorCode)
                    && java.util.Arrays.equals(raw == null ? new byte[0] : raw,
                        other.raw == null ? new byte[0] : other.raw);
            }
            return identity.equals(other.identity)
                && java.util.Arrays.equals(raw == null ? new byte[0] : raw,
                    other.raw == null ? new byte[0] : other.raw);
        }
    }

    private static final class ActiveIdentity {
        final String generationId;
        final long activeRevision;
        final String label;
        final List<MaterialIdentity> materials;
        ActiveIdentity(String generationId, long activeRevision, String label,
                List<MaterialIdentity> materials) {
            this.generationId = generationId;
            this.activeRevision = activeRevision;
            this.label = label;
            this.materials = materials;
        }
        @Override public boolean equals(Object other) {
            if (!(other instanceof ActiveIdentity identity)) return false;
            return activeRevision == identity.activeRevision
                && Objects.equals(generationId, identity.generationId)
                && Objects.equals(label, identity.label)
                && materials.equals(identity.materials);
        }
        @Override public int hashCode() {
            return Objects.hash(generationId, activeRevision, label, materials);
        }
    }

    private record MaterialIdentity(String name, String path, String sha256, long length) { }

    private void loadActiveLocked() {
        ActiveSnapshot snapshot = readSnapshotLocked();
        knownPrior = snapshot;
        if (snapshot.outcome == Outcome.NONE) {
            state = "required";
            error = null;
            active = null;
            activeRevision = -1;
            label = null;
        } else if (snapshot.outcome == Outcome.VALID) {
            state = "configured";
            error = null;
            active = snapshot.configuration;
            activeRevision = snapshot.configuration.activeRevision();
            label = snapshot.configuration.deviceLabel();
        } else if (snapshot.outcome == Outcome.INVALID) {
            state = "unavailable";
            error = "CONFIG_ACTIVE_REFERENCE_INVALID";
            active = null;
        } else {
            state = "unresolved";
            error = "CONFIG_ACTIVE_COMMIT_UNRESOLVED";
            active = null;
        }
    }

    private void reconcileLocked() {
        ActiveSnapshot snapshot = readSnapshotLocked();
        if (snapshot.outcome == Outcome.VALID) {
            active = snapshot.configuration;
            activeRevision = snapshot.configuration.activeRevision();
            label = snapshot.configuration.deviceLabel();
            state = "configured";
            error = null;
            knownPrior = snapshot;
            attempted = null;
        } else if (snapshot.outcome == Outcome.INVALID) {
            active = null;
            state = "unavailable";
            error = "CONFIG_ACTIVE_REFERENCE_INVALID";
            knownPrior = snapshot;
        } else if (snapshot.outcome == Outcome.NONE) {
            if (knownPrior != null && knownPrior.outcome == Outcome.INVALID && snapshot.sameAs(knownPrior)) {
                state = "unavailable";
                error = "CONFIG_ACTIVE_REFERENCE_INVALID";
            } else if (knownPrior != null && knownPrior.outcome == Outcome.NONE) {
                state = "required";
                error = null;
                active = null;
                activeRevision = -1;
                label = null;
            } else {
                state = "unresolved";
                error = "CONFIG_ACTIVE_COMMIT_UNRESOLVED";
            }
            knownPrior = snapshot;
        } else {
            state = "unresolved";
            error = "CONFIG_ACTIVE_COMMIT_UNRESOLVED";
        }
    }

    private ActiveSnapshot readSnapshotLocked() {
        if (!activeFile.exists()) return new ActiveSnapshot(Outcome.NONE, null, null, null, null);
        if (!activeFile.isFile() || activeFile.length() > ConnectionBundle.MAX_BUNDLE_BYTES)
            return new ActiveSnapshot(Outcome.UNRESOLVED, null, null,
                "CONFIG_ACTIVE_COMMIT_UNRESOLVED", null);
        byte[] raw;
        try {
            raw = Files.readAllBytes(activeFile.toPath());
        } catch (IOException unreadable) {
            return new ActiveSnapshot(Outcome.UNRESOLVED, null, null,
                "CONFIG_ACTIVE_COMMIT_UNRESOLVED", null);
        }
        try {
            ActiveConfiguration configuration = parseActive(raw);
            ActiveIdentity identity = identityOf(configuration);
            return new ActiveSnapshot(Outcome.VALID, identity, configuration, null, raw);
        } catch (DeterministicInvalid invalid) {
            return new ActiveSnapshot(Outcome.INVALID, null, null, invalid.getMessage(), raw);
        } catch (MaterialUnavailable unavailable) {
            return new ActiveSnapshot(Outcome.UNRESOLVED, null, null,
                "CONFIG_ACTIVE_COMMIT_UNRESOLVED", raw);
        } catch (RuntimeException uncertain) {
            return new ActiveSnapshot(Outcome.UNRESOLVED, null, null,
                "CONFIG_ACTIVE_COMMIT_UNRESOLVED", raw);
        }
    }

    private static final class DeterministicInvalid extends RuntimeException {
        DeterministicInvalid(String message) { super(message); }
    }

    private ActiveConfiguration parseActive(byte[] raw) {
        JSONObject value;
        try {
            value = new JSONObject(new String(raw, StandardCharsets.UTF_8));
        } catch (Exception failure) {
            throw new DeterministicInvalid("CONFIG_ACTIVE_REFERENCE_INVALID");
        }
        try {
            if (!ACTIVE_SCHEMA.equals(value.getString("schema"))
                    || value.getInt("version") != ACTIVE_VERSION)
                throw new DeterministicInvalid("CONFIG_ACTIVE_REFERENCE_INVALID");
            String generationId = value.getString("generation_id");
            long revision = value.getLong("active_revision");
            String label = value.getString("label");
            ConnectionBundle.validateLabel(label);
            JSONArray materials = value.getJSONArray("materials");
            if (materials.length() != 4) throw new DeterministicInvalid("CONFIG_ACTIVE_REFERENCE_INVALID");
            Map<String, MaterialIdentity> byName = new HashMap<>();
            for (int index = 0; index < materials.length(); index++) {
                JSONObject item = materials.getJSONObject(index);
                String name = item.getString("name");
                String path = item.getString("path");
                String sha = item.getString("sha256");
                long length = item.getLong("length");
                if (!ConnectionBundle.isSha256Hex(sha) || length < 0 || length > ConnectionBundle.MAX_DER_BYTES)
                    throw new DeterministicInvalid("CONFIG_ACTIVE_REFERENCE_INVALID");
                if (byName.put(name, new MaterialIdentity(name, path, sha, length)) != null)
                    throw new DeterministicInvalid("CONFIG_ACTIVE_REFERENCE_INVALID");
            }
            if (!byName.keySet().equals(java.util.Set.of(
                    ActiveConfiguration.ENDPOINT_FILE, ActiveConfiguration.CA_FILE,
                    ActiveConfiguration.CLIENT_CERT_FILE, ActiveConfiguration.CLIENT_KEY_FILE)))
                throw new DeterministicInvalid("CONFIG_ACTIVE_REFERENCE_INVALID");
            File generationRoot = new File(generationsRoot, generationId);
            byte[] endpointBytes = readMaterial(byName.get(ActiveConfiguration.ENDPOINT_FILE), generationRoot, false);
            byte[] ca = readMaterial(byName.get(ActiveConfiguration.CA_FILE), generationRoot, false);
            byte[] clientCert = readMaterial(byName.get(ActiveConfiguration.CLIENT_CERT_FILE), generationRoot, false);
            byte[] clientKey = readMaterial(byName.get(ActiveConfiguration.CLIENT_KEY_FILE), generationRoot, true);
            String endpoint = new String(endpointBytes, StandardCharsets.UTF_8).trim();
            ConnectionBundle.validateEndpoint(endpoint);
            return new ActiveConfiguration(generationId, revision, endpoint, ca, clientCert, clientKey,
                label, byName.get(ActiveConfiguration.ENDPOINT_FILE).sha256(),
                byName.get(ActiveConfiguration.CA_FILE).sha256(),
                byName.get(ActiveConfiguration.CLIENT_CERT_FILE).sha256(),
                byName.get(ActiveConfiguration.CLIENT_KEY_FILE).sha256(), generationRoot);
        } catch (DeterministicInvalid propagated) {
            throw propagated;
        } catch (MaterialUnavailable unavailable) {
            throw unavailable;
        } catch (Exception failure) {
            throw new DeterministicInvalid("CONFIG_ACTIVE_REFERENCE_INVALID");
        }
    }

    private static final class MaterialUnavailable extends RuntimeException {
        MaterialUnavailable(String message, Throwable cause) { super(message, cause); }
    }

    private byte[] readMaterial(MaterialIdentity identity, File generationRoot, boolean key)
            throws MaterialUnavailable {
        if (!identity.path().equals(identity.name())
                || identity.path().contains("/") || identity.path().contains(".."))
            throw new DeterministicInvalid("CONFIG_ACTIVE_REFERENCE_INVALID");
        File file = new File(generationRoot, identity.path());
        if (!file.isFile()) throw new MaterialUnavailable("CONFIG_ACTIVE_COMMIT_UNRESOLVED", null);
        byte[] bytes;
        try {
            bytes = Files.readAllBytes(file.toPath());
        } catch (IOException failure) {
            throw new MaterialUnavailable("CONFIG_ACTIVE_COMMIT_UNRESOLVED", failure);
        }
        if (bytes.length != identity.length()) throw new DeterministicInvalid("CONFIG_ACTIVE_REFERENCE_INVALID");
        String actual = ConnectionBundle.hex(ConnectionBundle.digest("SHA-256", bytes));
        if (!actual.equals(identity.sha256())) throw new DeterministicInvalid("CONFIG_ACTIVE_REFERENCE_INVALID");
        return bytes;
    }

    private ActiveIdentity identityOf(ActiveConfiguration configuration) {
        return new ActiveIdentity(configuration.generationId(), configuration.activeRevision(),
            configuration.deviceLabel(), List.of(
                new MaterialIdentity(ActiveConfiguration.ENDPOINT_FILE, ActiveConfiguration.ENDPOINT_FILE,
                    configuration.endpointSha256(),
                    (configuration.endpoint() + "\n").getBytes(StandardCharsets.UTF_8).length),
                new MaterialIdentity(ActiveConfiguration.CA_FILE, ActiveConfiguration.CA_FILE,
                    configuration.caSha256(), configuration.ca().length),
                new MaterialIdentity(ActiveConfiguration.CLIENT_CERT_FILE, ActiveConfiguration.CLIENT_CERT_FILE,
                    configuration.clientCertSha256(), configuration.clientCert().length),
                new MaterialIdentity(ActiveConfiguration.CLIENT_KEY_FILE, ActiveConfiguration.CLIENT_KEY_FILE,
                    configuration.clientKeySha256(), configuration.clientKey().length)));
    }

    private ActiveSnapshot readSnapshotOf(ActiveConfiguration configuration) {
        return new ActiveSnapshot(Outcome.VALID, identityOf(configuration), configuration, null,
            activeJson(configuration));
    }

    private byte[] activeJson(ActiveConfiguration configuration) {
        try {
            JSONObject value = new JSONObject()
                .put("schema", ACTIVE_SCHEMA)
                .put("version", ACTIVE_VERSION)
                .put("generation_id", configuration.generationId())
                .put("active_revision", configuration.activeRevision())
                .put("label", configuration.deviceLabel());
            JSONArray materials = new JSONArray();
            for (MaterialIdentity identity : identityOf(configuration).materials) {
                materials.put(new JSONObject()
                    .put("name", identity.name())
                    .put("path", identity.path())
                    .put("sha256", identity.sha256())
                    .put("length", identity.length()));
            }
            value.put("materials", materials);
            return (value.toString() + "\n").getBytes(StandardCharsets.UTF_8);
        } catch (org.json.JSONException failure) {
            throw new IllegalStateException("CONFIG_ACTIVE_SERIALIZATION_FAILED", failure);
        }
    }

    private Outcome classify(ActiveSnapshot prior, ActiveConfiguration candidate, ActiveSnapshot readback) {
        if (readback.outcome == Outcome.VALID
                && readback.identity.equals(identityOf(candidate))) return Outcome.COMMITTED;
        if (prior != null && readback.sameAs(prior)) return Outcome.NOT_COMMITTED;
        if (readback.outcome == Outcome.NONE && (prior == null || prior.outcome == Outcome.NONE))
            return Outcome.NOT_COMMITTED;
        return Outcome.UNRESOLVED;
    }

    private void writeActiveFile(byte[] bytes) throws IOException {
        ensureDirectory(pairingRoot, true);
        AtomicFile file = new AtomicFile(activeFile);
        FileOutputStream stream = file.startWrite();
        try {
            stream.write(bytes);
            stream.flush();
            stream.getFD().sync();
            file.finishWrite(stream);
        } catch (IOException failure) {
            file.failWrite(stream);
            throw failure;
        }
    }

    private void cleanupAfterResolution(ActiveConfiguration candidate, Outcome outcome) {
        File staging = new File(stagingRoot, candidate.generationId());
        deleteRecursively(staging);
        File[] generations = generationsRoot.listFiles();
        if (generations == null) return;
        synchronized (lock) {
            for (File generation : generations) {
                if (generation.equals(candidate.generationRoot())) continue;
                if (leases.containsKey(generation.getName())) continue;
                if (active != null && active.generationRoot().equals(generation)) continue;
                deleteRecursively(generation);
            }
        }
    }

    private static void ensureDirectory(File directory, boolean privateToOwner) throws IOException {
        if (!directory.exists() && !directory.mkdirs() && !directory.isDirectory())
            throw new IOException("CONFIG_STAGING_WRITE_FAILED:" + directory);
        if (privateToOwner) {
            try {
                java.nio.file.Files.setPosixFilePermissions(directory.toPath(),
                    java.nio.file.attribute.PosixFilePermissions.fromString("rwx------"));
            } catch (UnsupportedOperationException ignored) {
                // Android app-private storage already isolates this directory.
            }
        }
    }

    private static void writeSynced(File file, byte[] bytes, boolean ownerOnly) throws IOException {
        try (FileOutputStream stream = new FileOutputStream(file, false)) {
            stream.write(bytes);
            stream.flush();
            stream.getFD().sync();
        }
        if (ownerOnly) {
            try {
                java.nio.file.Files.setPosixFilePermissions(file.toPath(),
                    java.nio.file.attribute.PosixFilePermissions.fromString("rw-------"));
            } catch (UnsupportedOperationException ignored) {
                // Android app-private storage already isolates this file.
            }
        }
    }

    private static void deleteRecursively(File file) {
        if (file == null || !file.exists()) return;
        File[] children = file.listFiles();
        if (children != null) for (File child : children) deleteRecursively(child);
        file.delete();
    }

    private byte[] readUri(Uri uri) throws Exception {
        try (InputStream stream = uri.getScheme() != null && uri.getScheme().equals("file")
                ? new FileInputStream(new File(Objects.requireNonNull(uri.getPath())))
                : activity.getContentResolver().openInputStream(uri)) {
            if (stream == null) throw new IllegalStateException("BUNDLE_NOT_FOUND");
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int total = 0;
            int read;
            while ((read = stream.read(buffer)) != -1) {
                total += read;
                if (total > ConnectionBundle.MAX_BUNDLE_BYTES)
                    throw new IllegalArgumentException("BUNDLE_TOO_LARGE");
                out.write(buffer, 0, read);
            }
            if (total == 0) throw new IllegalArgumentException("BUNDLE_EMPTY");
            return out.toByteArray();
        }
    }

    private String requestCode() throws Exception {
        if (abortRequested) throw new ImportAbort();
        CodePrompt prompt = codePromptHook != null ? codePromptHook : codePrompt;
        if (prompt != null) {
            codePromptHookActive = true;
            return prompt.requestCode();
        }
        codePromptHookActive = false;
        CountDownLatch answered = new CountDownLatch(1);
        String[] result = new String[1];
        main.post(() -> {
            try {
                showCodeDialog(answered, result);
            } catch (RuntimeException failure) {
                result[0] = null;
                answered.countDown();
            }
        });
        if (!answered.await(120, TimeUnit.SECONDS)) {
            main.post(this::dismissCodeDialog);
            throw new ImportAbort();
        }
        return result[0];
    }

    private void showCodeDialog(CountDownLatch answered, String[] result) {
        requireMainThread();
        EditText input = new EditText(activity);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        input.setSingleLine(true);
        LinearLayout layout = new LinearLayout(activity);
        layout.setOrientation(LinearLayout.VERTICAL);
        int padding = Math.round(24 * activity.getResources().getDisplayMetrics().density);
        layout.setPadding(padding, padding / 2, padding, 0);
        TextView label = new TextView(activity);
        label.setText("Transfer code");
        layout.addView(label);
        layout.addView(input, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        AlertDialog dialog = new AlertDialog.Builder(activity)
            .setTitle("Import connection")
            .setView(layout)
            .setCancelable(true)
            .setPositiveButton("Import", (d, which) -> {
                result[0] = input.getText().toString();
                answered.countDown();
            })
            .setNegativeButton("Cancel", (d, which) -> {
                result[0] = null;
                answered.countDown();
            })
            .setOnCancelListener(d -> {
                result[0] = null;
                answered.countDown();
            })
            .create();
        dialog.setOnDismissListener(d -> {
            synchronized (lock) {
                if (codeDialog == dialog) codeDialog = null;
            }
        });
        dialog.getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE,
            WindowManager.LayoutParams.FLAG_SECURE);
        synchronized (lock) {
            codeDialog = dialog;
        }
        dialog.show();
    }

    private void dismissCodeDialog() {
        synchronized (lock) {
            if (codeDialog != null) codeDialog.dismiss();
            codeDialog = null;
        }
    }

    private void finishCancel(IntentType intent, String preState) {
        synchronized (lock) {
            busy = false;
            pendingIntent = null;
            pendingPreState = null;
            abortRequested = false;
            error = null;
            if ("unavailable".equals(preState)) {
                state = "unavailable";
                error = "CONFIG_ACTIVE_REFERENCE_INVALID";
            } else if ("configured".equals(preState)) {
                state = "configured";
            } else {
                state = "required";
            }
        }
    }

    private void finishFailure(IntentType intent, String preState, String message) {
        synchronized (lock) {
            busy = false;
            pendingIntent = null;
            pendingPreState = null;
            abortRequested = false;
            if ("configured".equals(preState) && active != null) {
                state = "configured";
            } else if ("unavailable".equals(preState)) {
                state = "unavailable";
            } else {
                state = "required";
            }
            error = message == null ? "CONFIG_IMPORT_FAILED" : message;
        }
    }

    private static String typedMessage(Exception failure) {
        if (failure instanceof IllegalArgumentException
                || failure instanceof IllegalStateException) {
            return failure.getMessage();
        }
        return failure.getClass().getSimpleName() + ": " + failure.getMessage();
    }

    private static String newGenerationId() {
        UUID uuid = UUID.randomUUID();
        return String.format(Locale.ROOT, "%016x%016x", uuid.getMostSignificantBits(),
            uuid.getLeastSignificantBits());
    }

    private static void requireMainThread() {
        if (Looper.myLooper() != Looper.getMainLooper())
            throw new IllegalStateException("MAIN_THREAD_REQUIRED");
    }

    private static final class ImportAbort extends RuntimeException { }
}
