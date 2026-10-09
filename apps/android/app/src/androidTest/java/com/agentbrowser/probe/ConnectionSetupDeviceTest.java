package com.agentbrowser.probe;

import android.content.Context;
import android.net.Uri;
import android.os.SystemClock;
import android.test.InstrumentationTestCase;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Base64;

/**
 * MODULE_ONLY: real Android filesystem, AtomicFile and JCA PKCS#12 validation
 * through the public ConnectionSetupSession owner. This is not BB01 and does
 * not claim a paired producer endpoint.
 */
public final class ConnectionSetupDeviceTest extends InstrumentationTestCase {
    private static final String CODE = "ABCDEFGHJKMN";
    private static final String OTHER_CODE = "ZZZZZZZZZZZZ";
    private Context target;
    private File root;
    private File pairing;
    private ConnectionSetupSession session;

    @Override protected void setUp() throws Exception {
        super.setUp();
        target = getInstrumentation().getTargetContext();
        root = new File(target.getFilesDir(), "connection-setup-test-" + System.nanoTime());
        pairing = new File(root, "pairing");
        assertTrue(pairing.mkdirs() || pairing.isDirectory());
        session = new ConnectionSetupSession(null, root);
    }

    @Override protected void tearDown() throws Exception {
        if (session != null) session.close();
        delete(root);
        ConnectionSetupSession.setCodePromptHook(null);
        super.tearDown();
    }

    private static void delete(File file) {
        if (file == null || !file.exists()) return;
        File[] children = file.listFiles();
        if (children != null) for (File child : children) delete(child);
        file.delete();
    }

    private byte[] asset(String name) throws Exception {
        try (InputStream stream = getInstrumentation().getContext().getAssets()
                .open("first-connection/" + name)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int read;
            while ((read = stream.read(buffer)) != -1) out.write(buffer, 0, read);
            return out.toByteArray();
        }
    }

    private File writeTemp(String name, byte[] bytes) throws Exception {
        File file = new File(target.getCacheDir(), name + "-" + System.nanoTime());
        try (FileOutputStream out = new FileOutputStream(file)) {
            out.write(bytes);
        }
        return file;
    }

    private JSONObject state() {
        return session.projection();
    }

    private JSONObject awaitSettled(long timeoutMs) {
        long deadline = SystemClock.elapsedRealtime() + timeoutMs;
        do {
            JSONObject value = state();
            String state = value.optString("state");
            if (!"opening".equals(state) && !"importing".equals(state)) return value;
            SystemClock.sleep(50);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Import did not settle: " + state());
        return null;
    }

    private JSONObject importBundle(byte[] bundle, String code, ConnectionSetupSession.IntentType intent)
            throws Exception {
        ConnectionSetupSession.setCodePromptHook(() -> code);
        File file = writeTemp("bundle", bundle);
        session.startImportForTest(Uri.fromFile(file), intent);
        JSONObject result = awaitSettled(15000);
        file.delete();
        return result;
    }

    private String activeText() throws Exception {
        File active = new File(pairing, ConnectionSetupSession.ACTIVE_FILE);
        return new String(Files.readAllBytes(active.toPath()), StandardCharsets.UTF_8);
    }

    public void testValidImportCommitsConfiguredBinding() throws Exception {
        JSONObject configured = importBundle(asset("valid.abconnection"), CODE,
            ConnectionSetupSession.IntentType.ORDINARY);
        assertEquals("configured", configured.getString("state"));
        assertEquals("Android test phone", configured.getString("label"));
        assertEquals(1L, configured.getLong("active_revision"));
        assertFalse(configured.getBoolean("repair_import_allowed"));
        File active = new File(pairing, ConnectionSetupSession.ACTIVE_FILE);
        assertTrue("active reference must exist", active.isFile());
        assertTrue(activeText().contains("\"generation_id\""));
        ActiveConfiguration binding = session.admitNewBinding();
        assertEquals("wss://localhost:8443/", binding.endpoint());
        assertEquals("Android test phone", binding.deviceLabel());
        assertTrue(binding.ca().length > 0);
        assertTrue(binding.clientCert().length > 0);
        assertTrue(binding.clientKey().length > 0);
        assertTrue(new File(binding.generationRoot(), "key.der").isFile());
        session.release(binding);
    }

    public void testUnprotectedImportNeedsNoTransferCode() throws Exception {
        final boolean[] prompted = {false};
        ConnectionSetupSession.setCodePromptHook(() -> {
            prompted[0] = true;
            return null;
        });
        File file = writeTemp("nocode", asset("nocode.abconnection"));
        session.startImportForTest(Uri.fromFile(file), ConnectionSetupSession.IntentType.ORDINARY);
        JSONObject configured = awaitSettled(15000);
        file.delete();
        assertFalse("the no-code bundle must not open the transfer-code dialog", prompted[0]);
        assertEquals("configured", configured.getString("state"));
        assertEquals("Android test phone", configured.getString("label"));
        assertEquals(1L, configured.getLong("active_revision"));
        ActiveConfiguration binding = session.admitNewBinding();
        assertEquals("wss://localhost:8443/", binding.endpoint());
        assertTrue(binding.ca().length > 0);
        assertTrue(binding.clientCert().length > 0);
        assertTrue(binding.clientKey().length > 0);
        assertTrue(new File(binding.generationRoot(), "key.der").isFile());
        session.release(binding);
    }

    public void testSetupEntryImportCommitsWithoutBlockedPreState() throws Exception {
        final boolean[] prompted = {false};
        ConnectionSetupSession.setCodePromptHook(() -> {
            prompted[0] = true;
            return null;
        });
        File file = writeTemp("setup-nocode", asset("nocode.abconnection"));
        getInstrumentation().runOnMainSync(() -> session.onOpenRequested());
        assertEquals("opening", state().getString("state"));
        getInstrumentation().runOnMainSync(() -> session.onActivityResult(Uri.fromFile(file)));
        JSONObject configured = awaitSettled(15000);
        file.delete();
        assertFalse("the setup entry path must not open the transfer-code dialog", prompted[0]);
        assertTrue("setup entry must not surface a blocked-commit error: "
            + configured.opt("error"), configured.isNull("error"));
        assertEquals("configured", configured.getString("state"));
        assertEquals("Android test phone", configured.getString("label"));
        assertEquals(1L, configured.getLong("active_revision"));
        ActiveConfiguration binding = session.admitNewBinding();
        assertEquals("wss://localhost:8443/", binding.endpoint());
        assertTrue(new File(binding.generationRoot(), "key.der").isFile());
        session.release(binding);
    }

    public void testWrongTransferCodeDeniedAndPriorPreserved() throws Exception {
        importBundle(asset("valid.abconnection"), CODE, ConnectionSetupSession.IntentType.ORDINARY);
        String before = activeText();
        JSONObject result = importBundle(asset("valid.abconnection"), OTHER_CODE,
            ConnectionSetupSession.IntentType.ORDINARY);
        assertEquals("configured", result.getString("state"));
        assertTrue(result.getString("error").contains("BUNDLE_TRANSFER_CODE_INVALID"));
        assertEquals("prior active bytes must be retained", before, activeText());
        ActiveConfiguration binding = session.admitNewBinding();
        assertEquals("Android test phone", binding.deviceLabel());
        session.release(binding);
    }

    public void testUnknownFieldDeniedAndPriorPreserved() throws Exception {
        importBundle(asset("valid.abconnection"), CODE, ConnectionSetupSession.IntentType.ORDINARY);
        String before = activeText();
        JSONObject value = new JSONObject(new String(asset("valid.abconnection"), StandardCharsets.UTF_8));
        value.put("metadata", true);
        JSONObject result = importBundle(value.toString().getBytes(StandardCharsets.UTF_8), CODE,
            ConnectionSetupSession.IntentType.ORDINARY);
        assertEquals("configured", result.getString("state"));
        assertTrue(result.getString("error").contains("UNKNOWN_BUNDLE_FIELD"));
        assertEquals(before, activeText());
    }

    public void testWrongDigestDeniedAndPriorPreserved() throws Exception {
        importBundle(asset("valid.abconnection"), CODE, ConnectionSetupSession.IntentType.ORDINARY);
        String before = activeText();
        JSONObject value = new JSONObject(new String(asset("valid.abconnection"), StandardCharsets.UTF_8));
        value.put("p12_sha256", "00".repeat(32));
        JSONObject result = importBundle(value.toString().getBytes(StandardCharsets.UTF_8), CODE,
            ConnectionSetupSession.IntentType.ORDINARY);
        assertEquals("configured", result.getString("state"));
        assertTrue(result.getString("error").contains("BUNDLE_P12_DIGEST_MISMATCH"));
        assertEquals(before, activeText());
    }

    public void testEndpointSanMismatchDenied() throws Exception {
        importBundle(asset("valid.abconnection"), CODE, ConnectionSetupSession.IntentType.ORDINARY);
        JSONObject value = new JSONObject(new String(asset("valid.abconnection"), StandardCharsets.UTF_8));
        value.put("endpoint", "wss://other.invalid:8443/");
        JSONObject result = importBundle(value.toString().getBytes(StandardCharsets.UTF_8), CODE,
            ConnectionSetupSession.IntentType.ORDINARY);
        assertTrue(result.getString("error").contains("BUNDLE_ENDPOINT_SAN_MISMATCH"));
    }

    public void testReplacementCommitAdvancesRevision() throws Exception {
        JSONObject first = importBundle(asset("valid.abconnection"), CODE,
            ConnectionSetupSession.IntentType.ORDINARY);
        assertEquals(1L, first.getLong("active_revision"));
        String firstText = activeText();
        JSONObject second = importBundle(asset("valid.abconnection"), CODE,
            ConnectionSetupSession.IntentType.ORDINARY);
        assertEquals("configured", second.getString("state"));
        assertEquals(2L, second.getLong("active_revision"));
        assertFalse("replacement must publish a new generation", firstText.equals(activeText()));
        ActiveConfiguration binding = session.admitNewBinding();
        assertEquals(2L, binding.activeRevision());
        session.release(binding);
    }

    public void testPickerCancelPreservesPriorStateAndBytes() throws Exception {
        importBundle(asset("valid.abconnection"), CODE, ConnectionSetupSession.IntentType.ORDINARY);
        String before = activeText();
        getInstrumentation().runOnMainSync(() ->
            session.requestPickerForTest(ConnectionSetupSession.IntentType.ORDINARY));
        getInstrumentation().runOnMainSync(() -> session.onActivityResult(null));
        assertEquals("configured", state().getString("state"));
        assertEquals(before, activeText());
    }

    public void testProcessRestartRecoversActualActive() throws Exception {
        importBundle(asset("valid.abconnection"), CODE, ConnectionSetupSession.IntentType.ORDINARY);
        String before = activeText();
        session.close();
        session = new ConnectionSetupSession(null, root);
        JSONObject recovered = state();
        assertEquals("configured", recovered.getString("state"));
        assertEquals("Android test phone", recovered.getString("label"));
        ActiveConfiguration binding = session.admitNewBinding();
        assertEquals("wss://localhost:8443/", binding.endpoint());
        session.release(binding);
        assertEquals(before, activeText());
    }

    public void testDeterministicInvalidAllowsOnlyRepairImport() throws Exception {
        File active = new File(pairing, ConnectionSetupSession.ACTIVE_FILE);
        Files.write(active.toPath(), "{not-json".getBytes(StandardCharsets.UTF_8));
        session.close();
        session = new ConnectionSetupSession(null, root);
        JSONObject unavailable = state();
        assertEquals("unavailable", unavailable.getString("state"));
        assertTrue(unavailable.getBoolean("repair_import_allowed"));
        assertTrue(unavailable.getString("error").contains("CONFIG_ACTIVE_REFERENCE_INVALID"));
        try {
            session.admitNewBinding();
            fail("NEW binding must be denied for a deterministic invalid active");
        } catch (IllegalStateException expected) {
            assertTrue(expected.getMessage().contains("CONFIG_ACTIVE_REFERENCE_INVALID"));
        }
        File before = new File(pairing, ConnectionSetupSession.ACTIVE_FILE);
        byte[] beforeBytes = Files.readAllBytes(before.toPath());
        File ordinary = writeTemp("ordinary-denied", asset("valid.abconnection"));
        try {
            session.startImportForTest(Uri.fromFile(ordinary),
                ConnectionSetupSession.IntentType.ORDINARY);
            fail("ordinary import must be blocked for a deterministic invalid active");
        } catch (IllegalStateException expected) {
            assertTrue(expected.getMessage().contains("CONFIG_ORDINARY_IMPORT_BLOCKED"));
        } finally {
            ordinary.delete();
        }
        assertArrayEquals(beforeBytes, Files.readAllBytes(before.toPath()));
        JSONObject repaired = importBundle(asset("valid.abconnection"), CODE,
            ConnectionSetupSession.IntentType.REPAIR);
        assertEquals("configured", repaired.getString("state"));
        assertFalse(repaired.getBoolean("repair_import_allowed"));
        ActiveConfiguration binding = session.admitNewBinding();
        assertEquals("Android test phone", binding.deviceLabel());
        session.release(binding);
    }

    public void testRepairImportRejectedWhenConfigured() throws Exception {
        importBundle(asset("valid.abconnection"), CODE, ConnectionSetupSession.IntentType.ORDINARY);
        String before = activeText();
        File repair = writeTemp("repair", asset("valid.abconnection"));
        try {
            session.startImportForTest(Uri.fromFile(repair),
                ConnectionSetupSession.IntentType.REPAIR);
            fail("repair import must be rejected from configured state");
        } catch (IllegalStateException expected) {
            assertTrue(expected.getMessage().contains("CONFIG_REPAIR_IMPORT_NOT_ALLOWED"));
        } finally {
            repair.delete();
        }
        assertEquals(before, activeText());
    }

    public void testAtomicFileWriteFailureLeavesUnresolvedOrPrior() throws Exception {
        File active = new File(pairing, ConnectionSetupSession.ACTIVE_FILE);
        Files.write(active.toPath(), "{not-json".getBytes(StandardCharsets.UTF_8));
        session.close();
        session = new ConnectionSetupSession(null, root);
        assertTrue(active.delete());
        assertTrue("active path must become a directory to force rename failure", active.mkdirs());
        JSONObject result = importBundle(asset("valid.abconnection"), CODE,
            ConnectionSetupSession.IntentType.REPAIR);
        String state = result.getString("state");
        assertTrue("state=" + state + " error=" + result.optString("error"),
            "unavailable".equals(state) || "unresolved".equals(state));
        try {
            session.admitNewBinding();
            fail("uncertain admission must be denied");
        } catch (IllegalStateException expected) {
            assertTrue(expected.getMessage(),
                expected.getMessage().contains("CONFIG_ACTIVE_REFERENCE_INVALID")
                    || expected.getMessage().contains("CONFIG_ACTIVE_COMMIT_UNRESOLVED"));
        }
    }

    private static void assertArrayEquals(byte[] expected, byte[] actual) {
        assertEquals(java.util.Arrays.toString(expected), java.util.Arrays.toString(actual));
    }
}
