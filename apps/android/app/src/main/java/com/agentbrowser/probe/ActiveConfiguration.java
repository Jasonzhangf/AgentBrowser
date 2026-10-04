package com.agentbrowser.probe;

import java.io.File;
import java.util.Objects;

/**
 * Immutable material binding for one committed generation. A reader keeps this
 * snapshot for its whole lease and never re-reads the active reference.
 */
final class ActiveConfiguration {
    static final String ENDPOINT_FILE = "endpoint.txt";
    static final String CA_FILE = "ca.der";
    static final String CLIENT_CERT_FILE = "client.der";
    static final String CLIENT_KEY_FILE = "key.der";

    private final String generationId;
    private final long activeRevision;
    private final String endpoint;
    private final byte[] ca;
    private final byte[] clientCert;
    private final byte[] clientKey;
    private final String deviceLabel;
    private final String endpointSha256;
    private final String caSha256;
    private final String clientCertSha256;
    private final String clientKeySha256;
    private final File generationRoot;

    ActiveConfiguration(String generationId, long activeRevision, String endpoint,
            byte[] ca, byte[] clientCert, byte[] clientKey, String deviceLabel,
            String endpointSha256, String caSha256, String clientCertSha256,
            String clientKeySha256, File generationRoot) {
        this.generationId = Objects.requireNonNull(generationId, "generationId");
        if (activeRevision < 0) throw new IllegalArgumentException("ACTIVE_REVISION_INVALID");
        this.activeRevision = activeRevision;
        this.endpoint = Objects.requireNonNull(endpoint, "endpoint");
        this.ca = Objects.requireNonNull(ca, "ca").clone();
        this.clientCert = Objects.requireNonNull(clientCert, "clientCert").clone();
        this.clientKey = Objects.requireNonNull(clientKey, "clientKey").clone();
        this.deviceLabel = Objects.requireNonNull(deviceLabel, "deviceLabel");
        this.endpointSha256 = Objects.requireNonNull(endpointSha256, "endpointSha256");
        this.caSha256 = Objects.requireNonNull(caSha256, "caSha256");
        this.clientCertSha256 = Objects.requireNonNull(clientCertSha256, "clientCertSha256");
        this.clientKeySha256 = Objects.requireNonNull(clientKeySha256, "clientKeySha256");
        this.generationRoot = Objects.requireNonNull(generationRoot, "generationRoot");
    }

    String generationId() { return generationId; }
    long activeRevision() { return activeRevision; }
    String endpoint() { return endpoint; }
    byte[] ca() { return ca.clone(); }
    byte[] clientCert() { return clientCert.clone(); }
    byte[] clientKey() { return clientKey.clone(); }
    String deviceLabel() { return deviceLabel; }
    String endpointSha256() { return endpointSha256; }
    String caSha256() { return caSha256; }
    String clientCertSha256() { return clientCertSha256; }
    String clientKeySha256() { return clientKeySha256; }
    File generationRoot() { return generationRoot; }
}
