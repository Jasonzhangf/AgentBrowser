package com.agentbrowser.probe;

import java.io.ByteArrayInputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.Key;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.Signature;
import java.security.cert.CertPathValidator;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.security.cert.PKIXParameters;
import java.security.cert.TrustAnchor;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collection;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Strict parser and cryptographic validator for the frozen
 * {@code agentbrowser.connection@1} bundle. This owner only reads bytes and
 * returns immutable material; it never writes the active reference.
 */
final class ConnectionBundle {
    static final int MAX_BUNDLE_BYTES = 65536;
    static final int MAX_P12_BYTES = 32768;
    static final int MAX_DER_BYTES = 16384;
    static final String FORMAT = "agentbrowser.connection";
    static final String CA_ALIAS = "agentbrowser-host-ca";
    static final String ENDPOINT_ALIAS = "agentbrowser-endpoint";
    static final String CLIENT_ALIAS = "agentbrowser-client";
    private static final Set<String> FIELDS = Set.of(
        "format", "version", "transport", "endpoint", "device_id", "device_label",
        "created_at_unix", "p12_base64", "p12_sha256");
    private static final Pattern SHA256_HEX = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern UUID_TEXT = Pattern.compile(
        "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}");

    private final String endpoint;
    private final String deviceId;
    private final String deviceLabel;
    private final int version;
    private final byte[] caDer;
    private final byte[] clientCertDer;
    private final byte[] clientKeyDer;
    private final String p12Sha256;

    private ConnectionBundle(String endpoint, String deviceId, String deviceLabel, int version,
            byte[] caDer, byte[] clientCertDer, byte[] clientKeyDer, String p12Sha256) {
        this.endpoint = endpoint;
        this.deviceId = deviceId;
        this.deviceLabel = deviceLabel;
        this.version = version;
        this.caDer = caDer;
        this.clientCertDer = clientCertDer;
        this.clientKeyDer = clientKeyDer;
        this.p12Sha256 = p12Sha256;
    }

    String endpoint() { return endpoint; }
    String deviceId() { return deviceId; }
    String deviceLabel() { return deviceLabel; }
    int version() { return version; }
    byte[] caDer() { return caDer.clone(); }
    byte[] clientCertDer() { return clientCertDer.clone(); }
    byte[] clientKeyDer() { return clientKeyDer.clone(); }
    String p12Sha256() { return p12Sha256; }

    static ConnectionBundle parse(byte[] raw, String transferCode) {
        if (raw == null || raw.length == 0) throw invalid("BUNDLE_EMPTY");
        if (raw.length > MAX_BUNDLE_BYTES) throw invalid("BUNDLE_TOO_LARGE");
        String text = new String(raw, StandardCharsets.UTF_8);
        if (!java.util.Arrays.equals(text.getBytes(StandardCharsets.UTF_8), raw))
            throw invalid("BUNDLE_NOT_UTF8");
        rejectDuplicateKeys(text);
        JSONObject value;
        try {
            value = new JSONObject(text);
        } catch (JSONException malformed) {
            throw invalid("BUNDLE_JSON_INVALID", malformed);
        }
        for (String name : FIELDS) if (!value.has(name)) throw invalid("MISSING_BUNDLE_FIELD:" + name);
        for (java.util.Iterator<String> keys = value.keys(); keys.hasNext(); ) {
            String name = keys.next();
            if (!FIELDS.contains(name)) throw invalid("UNKNOWN_BUNDLE_FIELD:" + name);
        }
        if (!FORMAT.equals(string(value, "format"))) throw invalid("BUNDLE_FORMAT_INVALID");
        if (!"wss".equals(string(value, "transport"))) throw invalid("BUNDLE_TRANSPORT_INVALID");
        if (!integer(value, "version")) throw invalid("BUNDLE_VERSION_INVALID");
        int version = value.optInt("version");
        if (version != 1) throw invalid("BUNDLE_VERSION_UNSUPPORTED");
        if (!integer(value, "created_at_unix")) throw invalid("BUNDLE_CREATED_AT_INVALID");
        if (value.optLong("created_at_unix") < 0) throw invalid("BUNDLE_CREATED_AT_INVALID");
        String endpoint = string(value, "endpoint");
        validateEndpoint(endpoint);
        String deviceId = string(value, "device_id");
        if (!UUID_TEXT.matcher(deviceId).matches()) throw invalid("BUNDLE_DEVICE_ID_INVALID");
        try {
            UUID.fromString(deviceId);
        } catch (IllegalArgumentException badUuid) {
            throw invalid("BUNDLE_DEVICE_ID_INVALID", badUuid);
        }
        String deviceLabel = string(value, "device_label");
        validateLabel(deviceLabel);
        String p12Base64 = string(value, "p12_base64");
        String declaredSha = string(value, "p12_sha256");
        if (!SHA256_HEX.matcher(declaredSha).matches()) throw invalid("BUNDLE_P12_DIGEST_INVALID");
        byte[] p12;
        try {
            p12 = Base64.getDecoder().decode(p12Base64);
        } catch (IllegalArgumentException badBase64) {
            throw invalid("BUNDLE_P12_BASE64_INVALID", badBase64);
        }
        if (p12.length == 0) throw invalid("BUNDLE_P12_EMPTY");
        if (p12.length > MAX_P12_BYTES) throw invalid("BUNDLE_P12_OVERSIZED");
        String actualSha = hex(digest("SHA-256", p12));
        if (!actualSha.equals(declaredSha)) throw invalid("BUNDLE_P12_DIGEST_MISMATCH");
        // A null or empty transfer code is the default no-code import: the
        // bundle is still opened, its private key extracted and its
        // certificates validated. A protected bundle yields the typed
        // BUNDLE_TRANSFER_CODE_REQUIRED here and then takes the explicit
        // transfer-code path.
        Material material = validatePkcs12(p12, transferCode, endpoint);
        return new ConnectionBundle(endpoint, deviceId, deviceLabel, version,
            material.caDer(), material.clientCertDer(), material.clientKeyDer(), declaredSha);
    }

    private record Material(byte[] caDer, byte[] clientCertDer, byte[] clientKeyDer) { }

    private static Material validatePkcs12(byte[] p12, String transferCode, String endpoint) {
        // No transfer code is the default no-code bundle: its PKCS#12 is
        // written without key or certificate encryption and opens with the
        // empty password. A protected bundle does not open that way, and then
        // the explicit protected mode asks for its one-time transfer code.
        // This owner still opens the real container, extracts the real private
        // key and validates the real certificates on both paths.
        boolean noCode = transferCode == null || transferCode.isEmpty();
        char[] password = noCode ? new char[0] : transferCode.toCharArray();
        KeyStore store;
        try {
            store = KeyStore.getInstance("PKCS12");
            store.load(new ByteArrayInputStream(p12), password);
        } catch (Exception failure) {
            if (noCode) throw invalid("BUNDLE_TRANSFER_CODE_REQUIRED", failure);
            throw invalid("BUNDLE_TRANSFER_CODE_INVALID", failure);
        }
        Set<String> aliases = new HashSet<>();
        try {
            for (Enumeration<String> names = store.aliases(); names.hasMoreElements(); )
                aliases.add(names.nextElement());
        } catch (Exception failure) {
            throw invalid("BUNDLE_P12_ALIAS_INVALID", failure);
        }
        if (!aliases.contains(CA_ALIAS)
                || !aliases.contains(ENDPOINT_ALIAS) || !aliases.contains(CLIENT_ALIAS)) {
            throw invalid("BUNDLE_P12_ALIAS_INVALID");
        }
        try {
            if (!store.isCertificateEntry(CA_ALIAS)
                    || !store.isCertificateEntry(ENDPOINT_ALIAS)
                    || !store.isKeyEntry(CLIENT_ALIAS)) {
                throw invalid("BUNDLE_P12_ALIAS_INVALID");
            }
        } catch (Exception failure) {
            if (failure instanceof IllegalArgumentException) {
                throw (IllegalArgumentException) failure;
            }
            throw invalid("BUNDLE_P12_ALIAS_INVALID", failure);
        }
        X509Certificate ca = certificate(store, CA_ALIAS);
        X509Certificate endpointCert = certificate(store, ENDPOINT_ALIAS);
        X509Certificate clientCert = certificate(store, CLIENT_ALIAS);
        Set<String> extraAliases = new HashSet<>(aliases);
        extraAliases.removeAll(Set.of(CA_ALIAS, ENDPOINT_ALIAS, CLIENT_ALIAS));
        if (extraAliases.size() > 1) throw invalid("BUNDLE_P12_ALIAS_INVALID");
        for (String extra : extraAliases) {
            // Android's PKCS12 provider exposes the client chain CA as an
            // additional alias. Only an exact duplicate of the required CA
            // certificate is allowed; any other extra entry is rejected.
            try {
                if (!store.isCertificateEntry(extra) || !ca.equals(store.getCertificate(extra))) {
                    throw invalid("BUNDLE_P12_ALIAS_INVALID");
                }
            } catch (Exception failure) {
                if (failure instanceof IllegalArgumentException) {
                    throw (IllegalArgumentException) failure;
                }
                throw invalid("BUNDLE_P12_ALIAS_INVALID", failure);
            }
        }
        try {
            Certificate[] chain = store.getCertificateChain(CLIENT_ALIAS);
            if (chain == null || chain.length < 1 || chain.length > 2
                    || !clientCert.equals(chain[0])
                    || (chain.length == 2 && !ca.equals(chain[1]))) {
                throw invalid("BUNDLE_CLIENT_CHAIN_INVALID");
            }
        } catch (Exception failure) {
            if (failure instanceof IllegalArgumentException) {
                throw (IllegalArgumentException) failure;
            }
            throw invalid("BUNDLE_CLIENT_CHAIN_INVALID", failure);
        }
        Key key;
        try {
            key = store.getKey(CLIENT_ALIAS, password);
        } catch (Exception failure) {
            if (noCode) throw invalid("BUNDLE_TRANSFER_CODE_REQUIRED", failure);
            throw invalid("BUNDLE_TRANSFER_CODE_INVALID", failure);
        }
        if (!(key instanceof PrivateKey privateKey)) throw invalid("BUNDLE_P12_ALIAS_INVALID");
        if (!"PKCS#8".equalsIgnoreCase(privateKey.getFormat())) throw invalid("BUNDLE_CLIENT_KEY_FORMAT_INVALID");
        validateChain(ca, endpointCert, "BUNDLE_ENDPOINT_CHAIN_INVALID");
        validateChain(ca, clientCert, "BUNDLE_CLIENT_CHAIN_INVALID");
        requireEku(endpointCert, "1.3.6.1.5.5.7.3.1", "BUNDLE_ENDPOINT_EKU_INVALID");
        requireEku(clientCert, "1.3.6.1.5.5.7.3.2", "BUNDLE_CLIENT_EKU_INVALID");
        if (endpointCert.getBasicConstraints() >= 0) throw invalid("BUNDLE_ENDPOINT_BASIC_CONSTRAINTS_INVALID");
        if (clientCert.getBasicConstraints() >= 0) throw invalid("BUNDLE_CLIENT_BASIC_CONSTRAINTS_INVALID");
        byte[] clientSki = clientCert.getExtensionValue("2.5.29.14");
        if (clientSki == null || clientSki.length == 0) throw invalid("BUNDLE_CLIENT_SKI_INVALID");
        if (clientCert.getSubjectX500Principal() == null
                || clientCert.getSubjectX500Principal().getName().isBlank())
            throw invalid("BUNDLE_CLIENT_SUBJECT_INVALID");
        validateEndpointHostname(endpoint, endpointCert);
        verifyKeyMatchesCertificate(privateKey, clientCert.getPublicKey());
        try {
            return new Material(ca.getEncoded(), clientCert.getEncoded(), privateKey.getEncoded());
        } catch (Exception failure) {
            throw invalid("BUNDLE_MATERIAL_ENCODING_INVALID", failure);
        }
    }

    private static X509Certificate certificate(KeyStore store, String alias) {
        try {
            Certificate certificate = store.getCertificate(alias);
            if (certificate == null) throw invalid("BUNDLE_P12_ALIAS_INVALID");
            return (X509Certificate) certificate;
        } catch (ClassCastException failure) {
            throw invalid("BUNDLE_P12_ALIAS_INVALID", failure);
        } catch (Exception failure) {
            throw invalid("BUNDLE_P12_ALIAS_INVALID", failure);
        }
    }

    private static void validateChain(X509Certificate ca, X509Certificate leaf, String errorCode) {
        if (ca.getBasicConstraints() < 0) throw invalid("BUNDLE_CA_BASIC_CONSTRAINTS_INVALID");
        try {
            java.security.cert.CertPath path = CertificateFactory.getInstance("X.509")
                .generateCertPath(List.of(leaf, ca));
            PKIXParameters parameters = new PKIXParameters(
                Set.of(new TrustAnchor(ca, null)));
            parameters.setRevocationEnabled(false);
            CertPathValidator.getInstance("PKIX").validate(path, parameters);
        } catch (Exception failure) {
            throw invalid(errorCode, failure);
        }
    }

    private static void requireEku(X509Certificate certificate, String oid, String errorCode) {
        try {
            List<String> usages = certificate.getExtendedKeyUsage();
            if (usages == null || !usages.contains(oid)) throw invalid(errorCode);
        } catch (Exception failure) {
            throw invalid(errorCode, failure);
        }
    }

    private static void validateEndpointHostname(String endpoint, X509Certificate certificate) {
        String host;
        try {
            host = new URI(endpoint).getHost();
        } catch (Exception failure) {
            throw invalid("BUNDLE_ENDPOINT_INVALID", failure);
        }
        Collection<List<?>> names;
        try {
            names = certificate.getSubjectAlternativeNames();
        } catch (Exception failure) {
            throw invalid("BUNDLE_ENDPOINT_SAN_INVALID", failure);
        }
        if (names == null || names.isEmpty()) throw invalid("BUNDLE_ENDPOINT_SAN_INVALID");
        for (List<?> entry : names) {
            if (entry.size() < 2) continue;
            Integer type = (Integer) entry.get(0);
            Object value = entry.get(1);
            if (!(value instanceof String text)) continue;
            if (type == 2 && text.equalsIgnoreCase(host)) return;
            if (type == 7 && text.equalsIgnoreCase(host)) return;
        }
        throw invalid("BUNDLE_ENDPOINT_SAN_MISMATCH");
    }

    private static void verifyKeyMatchesCertificate(PrivateKey key, PublicKey publicKey) {
        String algorithm = switch (key.getAlgorithm().toUpperCase(Locale.ROOT)) {
            case "RSA" -> "SHA256withRSA";
            case "EC", "ECDSA" -> "SHA256withECDSA";
            default -> throw invalid("BUNDLE_CLIENT_KEY_ALGORITHM_INVALID");
        };
        try {
            byte[] challenge = new byte[32];
            new java.security.SecureRandom().nextBytes(challenge);
            Signature signer = Signature.getInstance(algorithm);
            signer.initSign(key);
            signer.update(challenge);
            byte[] signature = signer.sign();
            Signature verifier = Signature.getInstance(algorithm);
            verifier.initVerify(publicKey);
            verifier.update(challenge);
            if (!verifier.verify(signature)) throw invalid("BUNDLE_CLIENT_KEY_MISMATCH");
        } catch (IllegalArgumentException propagated) {
            throw propagated;
        } catch (Exception failure) {
            throw invalid("BUNDLE_CLIENT_KEY_MISMATCH", failure);
        }
    }

    static void validateEndpoint(String endpoint) {
        if (endpoint == null || endpoint.isEmpty() || endpoint.length() > 512)
            throw invalid("BUNDLE_ENDPOINT_INVALID");
        URI uri;
        try {
            uri = new URI(endpoint);
        } catch (Exception failure) {
            throw invalid("BUNDLE_ENDPOINT_INVALID", failure);
        }
        if (!"wss".equalsIgnoreCase(uri.getScheme())) throw invalid("BUNDLE_ENDPOINT_INVALID");
        if (uri.getHost() == null || uri.getHost().isBlank()) throw invalid("BUNDLE_ENDPOINT_INVALID");
        if (uri.getRawUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null)
            throw invalid("BUNDLE_ENDPOINT_INVALID");
        if (!"/".equals(uri.getRawPath())) throw invalid("BUNDLE_ENDPOINT_INVALID");
        int port = uri.getPort();
        if (port == 0 || port > 65535) throw invalid("BUNDLE_ENDPOINT_INVALID");
    }

    static void validateLabel(String label) {
        if (label == null || label.isEmpty() || label.length() > 64)
            throw invalid("BUNDLE_DEVICE_LABEL_INVALID");
        for (int index = 0; index < label.length(); index++) {
            char value = label.charAt(index);
            if (Character.isISOControl(value)) throw invalid("BUNDLE_DEVICE_LABEL_INVALID");
            if (!Character.isDefined(value)) throw invalid("BUNDLE_DEVICE_LABEL_INVALID");
        }
    }

    static byte[] digest(String algorithm, byte[] bytes) {
        try {
            return MessageDigest.getInstance(algorithm).digest(bytes);
        } catch (Exception failure) {
            throw new IllegalStateException("DIGEST_UNAVAILABLE", failure);
        }
    }

    static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) out.append(String.format(Locale.ROOT, "%02x", value));
        return out.toString();
    }

    static boolean isSha256Hex(String value) {
        return value != null && SHA256_HEX.matcher(value).matches();
    }

    private static String string(JSONObject value, String name) {
        Object raw = value.opt(name);
        if (!(raw instanceof String text)) throw invalid("BUNDLE_FIELD_TYPE_INVALID:" + name);
        return text;
    }

    private static boolean integer(JSONObject value, String name) {
        Object raw = value.opt(name);
        return raw instanceof Integer || raw instanceof Long;
    }

    private static void rejectDuplicateKeys(String text) {
        Set<String> seen = new HashSet<>();
        java.util.Deque<Boolean> objects = new java.util.ArrayDeque<>();
        int index = 0;
        while (index < text.length()) {
            char value = text.charAt(index);
            if (value == '{') {
                objects.push(Boolean.TRUE);
                index++;
                continue;
            }
            if (value == '}') {
                if (!objects.isEmpty()) objects.pop();
                index++;
                continue;
            }
            if (value == '[' || value == ']' || value == ',' || value == ':'
                    || Character.isWhitespace(value)) {
                index++;
                continue;
            }
            if (value != '"') {
                index++;
                continue;
            }
            int end = stringEnd(text, index);
            String token = text.substring(index + 1, end);
            index = end + 1;
            int cursor = index;
            while (cursor < text.length() && Character.isWhitespace(text.charAt(cursor))) cursor++;
            if (cursor < text.length() && text.charAt(cursor) == ':' && !objects.isEmpty()) {
                if (!seen.add(token)) throw invalid("BUNDLE_DUPLICATE_FIELD:" + token);
            }
        }
    }

    private static int stringEnd(String text, int start) {
        for (int index = start + 1; index < text.length(); index++) {
            char value = text.charAt(index);
            if (value == '\\') {
                index++;
            } else if (value == '"') {
                return index;
            }
        }
        throw invalid("BUNDLE_JSON_INVALID");
    }

    private static IllegalArgumentException invalid(String message) {
        return new IllegalArgumentException(message);
    }

    private static IllegalArgumentException invalid(String message, Throwable cause) {
        return new IllegalArgumentException(message, cause);
    }
}
