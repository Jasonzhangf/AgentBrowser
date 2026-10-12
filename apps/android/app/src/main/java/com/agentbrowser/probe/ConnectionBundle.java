package com.agentbrowser.probe;

import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/**
 * Reader of the connection bundle. The bundle carries only the necessary
 * connection configuration: the endpoint address and the bearer token.
 *
 * This owner reads bytes and returns the two values. It never writes the
 * pairing directory, and it does not classify a bad file: one code covers every
 * way the bytes can fail to yield the two values. A wrong value is allowed to
 * import and then fail at connect.
 */
final class ConnectionBundle {
    private final String endpoint;
    private final String token;

    private ConnectionBundle(String endpoint, String token) {
        this.endpoint = endpoint;
        this.token = token;
    }

    String endpoint() { return endpoint; }

    String token() { return token; }

    static ConnectionBundle parse(byte[] raw) {
        try {
            JSONObject value = new JSONObject(new String(raw, StandardCharsets.UTF_8));
            Object endpoint = value.opt("endpoint");
            Object token = value.opt("token");
            if (endpoint instanceof String address && token instanceof String secret)
                return new ConnectionBundle(address, secret);
        } catch (Exception undecodable) {
            // Falls through to the one failure below.
        }
        throw new IllegalArgumentException("BUNDLE_INVALID");
    }
}
