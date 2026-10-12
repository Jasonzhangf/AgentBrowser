package com.agentbrowser.probe;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Single owner for the Android connection configuration. It owns the SAF
 * picker, the import of the necessary connection configuration, and the
 * projection handed to the chrome UI.
 *
 * The import writes exactly two files into the pairing directory:
 * {@code endpoint.txt} and {@code token}. Nothing else is written, and no
 * authorization state exists between the picker and the connection.
 */
final class ConnectionSetupSession {
    static final int REQUEST_OPEN_CONNECTION = 0x4D31;

    private final Activity activity;
    private final File pairingRoot;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Object lock = new Object();
    private final ExecutorService worker = Executors.newSingleThreadExecutor();

    private String state = "unconfigured";
    private String error;
    private String label;
    private ActiveConfiguration active;
    private boolean busy;
    private boolean closed;

    ConnectionSetupSession(Activity activity) {
        this(activity, activity.getApplicationContext().getFilesDir());
    }

    ConnectionSetupSession(Activity activity, File filesRoot) {
        this.activity = activity;
        this.pairingRoot = new File(filesRoot, "pairing");
        synchronized (lock) {
            loadActiveLocked();
        }
    }

    JSONObject projection() {
        synchronized (lock) {
            try {
                return new JSONObject()
                    .put("state", state)
                    .put("error", error == null ? JSONObject.NULL : error)
                    .put("label", label == null ? JSONObject.NULL : label);
            } catch (JSONException failure) {
                throw new IllegalStateException("CONNECTION_PROJECTION_INVALID", failure);
            }
        }
    }

    boolean importing() {
        synchronized (lock) {
            return busy;
        }
    }

    /** The setup entry point opens the connection picker. */
    void onOpenRequested() {
        beginImport();
    }

    void onImportRequested() {
        beginImport();
    }

    void onActivityResult(Uri uri) {
        requireMainThread();
        synchronized (lock) {
            if (!busy) return;
            if (uri == null) {
                busy = false;
                error = null;
                loadActiveLocked();
                return;
            }
            error = null;
        }
        worker.execute(() -> runImport(uri));
    }

    void onActivityBack() {
        requireMainThread();
    }

    /** Instrumentation entrypoint: launch the same picker intent without JS. */
    void requestPickerForTest() {
        beginImport();
    }

    /** Instrumentation entrypoint: same path as the SAF callback. */
    void startImportForTest(Uri uri) {
        synchronized (lock) {
            if (closed) throw new IllegalStateException("CONNECTION_SETUP_CLOSED");
            if (busy) throw new IllegalStateException("CONNECTION_SETUP_BUSY");
            busy = true;
            error = null;
        }
        worker.execute(() -> runImport(uri));
    }

    /**
     * The binding handed to a NEW connection. There is no admission step.
     *
     * The one guard kept on this path: {@code endpoint} and {@code token} cross
     * JNI into Rust, where a null reaches {@code get_string} on a null pointer
     * and crashes instead of reporting. {@link ActiveConfiguration} already
     * refuses a null member, so only the binding itself can be absent here. This
     * guard exists to avoid that crash, not to validate the connection.
     */
    ActiveConfiguration admitNewBinding() {
        synchronized (lock) {
            if (closed) throw new IllegalStateException("CONNECTION_SETUP_CLOSED");
            if (active == null) throw new IllegalStateException("CONNECTION_NOT_CONFIGURED");
            return active;
        }
    }

    void close() {
        synchronized (lock) {
            if (closed) return;
            closed = true;
        }
        worker.shutdownNow();
    }

    // --- Import entry ---

    private void beginImport() {
        requireMainThread();
        synchronized (lock) {
            if (closed) throw new IllegalStateException("CONNECTION_SETUP_CLOSED");
            if (busy) throw new IllegalStateException("CONNECTION_SETUP_BUSY");
            busy = true;
            error = null;
        }
        if (activity == null) return;
        try {
            activity.startActivityForResult(documentPicker(), REQUEST_OPEN_CONNECTION);
        } catch (Exception failure) {
            synchronized (lock) {
                busy = false;
                error = "CONFIG_PICKER_UNAVAILABLE";
            }
        }
    }

    private Intent documentPicker() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType("*/*");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        return intent;
    }

    // --- Import pipeline ---

    private void runImport(Uri uri) {
        try {
            byte[] raw = read(uri);
            ConnectionBundle bundle = ConnectionBundle.parse(raw);
            writePairing(bundle);
            synchronized (lock) {
                busy = false;
                error = null;
                loadActiveLocked();
            }
        } catch (Exception failure) {
            synchronized (lock) {
                busy = false;
                error = typedMessage(failure);
            }
        }
    }

    private byte[] read(Uri uri) throws IOException {
        try (InputStream stream = activity.getContentResolver().openInputStream(uri)) {
            if (stream == null) throw new IOException("CONFIG_PICKER_UNAVAILABLE");
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            for (int count; (count = stream.read(buffer)) != -1; ) out.write(buffer, 0, count);
            return out.toByteArray();
        }
    }

    /** The whole import: two files, written before the projection reports configured. */
    private void writePairing(ConnectionBundle bundle) throws IOException {
        if (!pairingRoot.isDirectory() && !pairingRoot.mkdirs())
            throw new IOException("PAIRING_DIRECTORY_UNAVAILABLE");
        writeFile(new File(pairingRoot, ActiveConfiguration.ENDPOINT_FILE),
            bundle.endpoint().getBytes(StandardCharsets.UTF_8));
        writeFile(new File(pairingRoot, ActiveConfiguration.TOKEN_FILE),
            bundle.token().getBytes(StandardCharsets.UTF_8));
    }

    private static void writeFile(File file, byte[] value) throws IOException {
        try (FileOutputStream stream = new FileOutputStream(file)) {
            stream.write(value);
            stream.getFD().sync();
        }
    }

    // --- Active reference ---

    /**
     * The projection reads the two files back. Their presence decides
     * {@code configured} against {@code unconfigured}; the values are taken as
     * written and are not re-validated here.
     */
    private void loadActiveLocked() {
        File endpointFile = new File(pairingRoot, ActiveConfiguration.ENDPOINT_FILE);
        File tokenFile = new File(pairingRoot, ActiveConfiguration.TOKEN_FILE);
        if (!endpointFile.isFile() || !tokenFile.isFile()) {
            state = "unconfigured";
            error = null;
            label = null;
            active = null;
            return;
        }
        String endpoint = readPairingFile(endpointFile);
        active = new ActiveConfiguration(endpoint, readPairingFile(tokenFile));
        state = "configured";
        error = null;
        label = endpoint;
    }

    private static String readPairingFile(File file) {
        try {
            return new String(java.nio.file.Files.readAllBytes(file.toPath()),
                StandardCharsets.UTF_8).trim();
        } catch (IOException failure) {
            throw new java.io.UncheckedIOException(failure);
        }
    }

    private static String typedMessage(Exception failure) {
        String message = failure.getMessage();
        return message == null || message.isBlank()
            ? failure.getClass().getSimpleName()
            : message;
    }

    private static void requireMainThread() {
        if (Looper.myLooper() != Looper.getMainLooper())
            throw new IllegalStateException("CONNECTION_SETUP_NOT_MAIN_THREAD");
    }
}
