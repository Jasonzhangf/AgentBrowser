package com.agentbrowser.probe;

import org.json.JSONObject;
import org.junit.Test;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

/** Module-only validation of the frozen agentbrowser.connection@1 outer schema. */
public final class ConnectionBundleTest {
    private static final String CODE = "ABCDEFGHJKMN";

    private static byte[] valid() throws Exception {
        try (InputStream stream = ConnectionBundleTest.class.getResourceAsStream(
                "/first-connection/valid.abconnection")) {
            if (stream == null) throw new IllegalStateException("VALID_BUNDLE_FIXTURE_MISSING");
            return stream.readAllBytes();
        }
    }

    private static JSONObject validJson() throws Exception {
        return new JSONObject(new String(valid(), StandardCharsets.UTF_8));
    }

    private static byte[] encoded(JSONObject value) {
        return value.toString().getBytes(StandardCharsets.UTF_8);
    }

    @Test public void parsesValidBundleAndValidatesRealPkcs12() throws Exception {
        ConnectionBundle bundle = ConnectionBundle.parse(valid(), CODE);
        assertEquals("wss://localhost:8443/", bundle.endpoint());
        assertEquals("11111111-2222-4333-8444-555555555555", bundle.deviceId());
        assertEquals("Android test phone", bundle.deviceLabel());
        assertEquals(1, bundle.version());
        assertTrue(bundle.caDer().length > 0);
        assertTrue(bundle.clientCertDer().length > 0);
        assertTrue(bundle.clientKeyDer().length > 0);
        assertEquals(64, bundle.p12Sha256().length());
    }

    @Test public void rejectsUnknownOuterField() throws Exception {
        JSONObject value = validJson().put("metadata", true);
        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(value), CODE));
        assertTrue(error.getMessage(), error.getMessage().contains("UNKNOWN_BUNDLE_FIELD"));
    }

    @Test public void rejectsMissingOuterField() throws Exception {
        JSONObject value = validJson();
        value.remove("device_label");
        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(value), CODE));
        assertTrue(error.getMessage(), error.getMessage().contains("MISSING_BUNDLE_FIELD"));
    }

    @Test public void rejectsWrongFormatAndTransport() throws Exception {
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(validJson().put("format", "other")), CODE))
            .getMessage().contains("BUNDLE_FORMAT_INVALID"));
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(validJson().put("transport", "webrtc")), CODE))
            .getMessage().contains("BUNDLE_TRANSPORT_INVALID"));
    }

    @Test public void rejectsVersionTypeAndValue() throws Exception {
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(validJson().put("version", 2)), CODE))
            .getMessage().contains("BUNDLE_VERSION_UNSUPPORTED"));
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(validJson().put("version", "1")), CODE))
            .getMessage().contains("BUNDLE_VERSION_INVALID"));
    }

    @Test public void rejectsEndpointWithUserInfoQueryFragmentOrPath() throws Exception {
        for (String endpoint : new String[]{
                "wss://user@localhost:8443/",
                "wss://localhost:8443/?a=1",
                "wss://localhost:8443/#frag",
                "wss://localhost:8443/path",
                "wss://localhost:8443"}) {
            IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> ConnectionBundle.parse(encoded(validJson().put("endpoint", endpoint)), CODE));
            assertTrue(endpoint, error.getMessage().contains("BUNDLE_ENDPOINT_INVALID"));
        }
    }

    @Test public void rejectsWrongDeviceIdentity() throws Exception {
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(validJson().put("device_id", "not-a-uuid")), CODE))
            .getMessage().contains("BUNDLE_DEVICE_ID_INVALID"));
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(validJson().put("device_label", "")), CODE))
            .getMessage().contains("BUNDLE_DEVICE_LABEL_INVALID"));
    }

    @Test public void rejectsWrongDigestAndOversizedPkcs12() throws Exception {
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(validJson().put("p12_sha256", "00".repeat(32))), CODE))
            .getMessage().contains("BUNDLE_P12_DIGEST_MISMATCH"));
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(validJson().put("p12_base64",
                java.util.Base64.getEncoder().encodeToString(new byte[32769]))), CODE))
            .getMessage().contains("BUNDLE_P12_OVERSIZED"));
    }

    @Test public void rejectsOuterPayloadOverSixtyFourKilobytes() {
        byte[] oversized = new byte[65537];
        java.util.Arrays.fill(oversized, (byte) 'x');
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(oversized, CODE))
            .getMessage().contains("BUNDLE_TOO_LARGE"));
    }

    @Test public void rejectsWrongTransferCode() throws Exception {
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(valid(), "WRONGCODE"))
            .getMessage().contains("BUNDLE_TRANSFER_CODE_INVALID"));
    }

    @Test public void rejectsUnknownAliasInRealPkcs12() throws Exception {
        byte[] raw = valid();
        JSONObject value = new JSONObject(new String(raw, StandardCharsets.UTF_8));
        java.security.KeyStore store = java.security.KeyStore.getInstance("PKCS12");
        store.load(new java.io.ByteArrayInputStream(
            java.util.Base64.getDecoder().decode(value.getString("p12_base64"))), CODE.toCharArray());
        java.security.cert.Certificate cert = store.getCertificate("agentbrowser-client");
        store.setCertificateEntry("agentbrowser-extra", cert);
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        store.store(out, CODE.toCharArray());
        byte[] p12 = out.toByteArray();
        String sha = java.util.HexFormat.of().formatHex(
            java.security.MessageDigest.getInstance("SHA-256").digest(p12));
        value.put("p12_base64", java.util.Base64.getEncoder().encodeToString(p12));
        value.put("p12_sha256", sha);
        assertTrue(assertThrows(IllegalArgumentException.class,
            () -> ConnectionBundle.parse(encoded(value), CODE))
            .getMessage().contains("BUNDLE_P12_ALIAS_INVALID"));
    }
}
