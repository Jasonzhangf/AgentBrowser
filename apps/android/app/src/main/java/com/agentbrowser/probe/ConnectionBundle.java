package com.agentbrowser.probe;

import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.Set;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Reader of the {@code agentbrowser.connection@1} bundle. The bundle carries
 * only the necessary connection configuration: the endpoint address and the
 * bearer token. This owner only reads bytes and returns the two values; it
 * never writes the pairing directory.
 */
final class ConnectionBundle {
    static final int MAX_BUNDLE_BYTES = 65536;
    static final String FORMAT = "agentbrowser.connection";
    private static final Set<String> FIELDS = Set.of("format", "version", "endpoint", "token");

    private final String endpoint;
    private final String token;

    private ConnectionBundle(String endpoint, String token) {
        this.endpoint = endpoint;
        this.token = token;
    }

    String endpoint() { return endpoint; }

    String token() { return token; }

    static ConnectionBundle parse(byte[] raw) {
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
        for (Iterator<String> keys = value.keys(); keys.hasNext(); ) {
            String name = keys.next();
            if (!FIELDS.contains(name)) throw invalid("UNKNOWN_BUNDLE_FIELD:" + name);
        }
        if (!FORMAT.equals(string(value, "format"))) throw invalid("BUNDLE_FORMAT_INVALID");
        if (!integer(value, "version")) throw invalid("BUNDLE_VERSION_INVALID");
        if (value.optInt("version") != 1) throw invalid("BUNDLE_VERSION_UNSUPPORTED");
        String endpoint = string(value, "endpoint");
        validateEndpoint(endpoint);
        String token = string(value, "token");
        if (token.isBlank() || token.chars().anyMatch(Character::isWhitespace))
            throw invalid("BUNDLE_TOKEN_INVALID");
        return new ConnectionBundle(endpoint, token);
    }

    /** The endpoint is exactly {@code <host>:<port>}. */
    static void validateEndpoint(String endpoint) {
        if (endpoint == null || endpoint.isBlank() || endpoint.chars().anyMatch(Character::isWhitespace))
            throw invalid("BUNDLE_ENDPOINT_INVALID");
        int separator = endpoint.lastIndexOf(':');
        if (separator <= 0 || separator == endpoint.length() - 1)
            throw invalid("BUNDLE_ENDPOINT_INVALID");
        String host = endpoint.substring(0, separator);
        if (host.startsWith("[") && host.endsWith("]")) host = host.substring(1, host.length() - 1);
        if (host.isBlank()) throw invalid("BUNDLE_ENDPOINT_INVALID");
        int port;
        try {
            port = Integer.parseInt(endpoint.substring(separator + 1));
        } catch (NumberFormatException failure) {
            throw invalid("BUNDLE_ENDPOINT_INVALID", failure);
        }
        if (port < 1 || port > 65535) throw invalid("BUNDLE_ENDPOINT_INVALID");
    }

    private static boolean integer(JSONObject value, String name) {
        Object raw = value.opt(name);
        return raw instanceof Integer || raw instanceof Long;
    }

    private static String string(JSONObject value, String name) {
        Object raw = value.opt(name);
        if (!(raw instanceof String text)) throw invalid("BUNDLE_FIELD_TYPE_INVALID:" + name);
        return text;
    }

    /**
     * The outer document is a flat object of unique keys. A duplicate key is
     * rejected before the JSON object is built, because the last write would
     * otherwise silently win.
     */
    private static void rejectDuplicateKeys(String text) {
        LinkedHashSet<String> seen = new LinkedHashSet<>();
        java.util.ArrayDeque<Boolean> objects = new java.util.ArrayDeque<>();
        for (int index = 0; index < text.length(); index++) {
            char value = text.charAt(index);
            if (value == '{') {
                objects.push(Boolean.TRUE);
                continue;
            }
            if (value == '}') {
                if (objects.isEmpty()) throw invalid("BUNDLE_JSON_INVALID");
                objects.pop();
                continue;
            }
            if (value != '"') continue;
            int end = stringEnd(text, index);
            String token = text.substring(index + 1, end);
            index = end;
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
