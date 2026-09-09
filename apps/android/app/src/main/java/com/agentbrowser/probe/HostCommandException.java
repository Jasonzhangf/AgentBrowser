package com.agentbrowser.probe;

/** A correlated Host error; the operation failed, but the connection remains usable. */
public final class HostCommandException extends IllegalStateException {
    public final String code;
    public HostCommandException(String code, String message) {
        super("Host " + code + ": " + message);
        this.code = code;
    }
}
