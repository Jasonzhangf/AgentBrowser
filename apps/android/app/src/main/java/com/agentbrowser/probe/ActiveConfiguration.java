package com.agentbrowser.probe;

import java.util.Objects;

/**
 * The necessary connection configuration for one committed pairing: the
 * endpoint address and the bearer token. A reader keeps this snapshot for its
 * whole connection and never re-reads the pairing directory.
 */
final class ActiveConfiguration {
    static final String ENDPOINT_FILE = "endpoint.txt";
    static final String TOKEN_FILE = "token";

    private final String endpoint;
    private final String token;

    ActiveConfiguration(String endpoint, String token) {
        this.endpoint = Objects.requireNonNull(endpoint, "endpoint");
        this.token = Objects.requireNonNull(token, "token");
    }

    String endpoint() { return endpoint; }

    String token() { return token; }
}
