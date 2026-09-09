package com.agentbrowser.probe;

import org.json.JSONException;
import org.json.JSONObject;

// Small closed ABI: no file paths, URLs, media bytes, or remote Host commands.
final class ProbeCommand {
    enum Op { PLAY, STOP, STATUS }
    final Op op;
    final String sample;
    private ProbeCommand(Op op, String sample) { this.op = op; this.sample = sample; }
    static ProbeCommand parse(String raw) throws JSONException {
        if (raw == null || raw.length() > 256) throw new IllegalArgumentException("COMMAND_SIZE");
        JSONObject value = new JSONObject(raw);
        Object op = value.get("op");
        if ("play".equals(op)) {
            Object sample = value.get("sample");
            if (value.length() != 2 || !("portrait".equals(sample) || "broken".equals(sample)))
                throw new IllegalArgumentException("INVALID_SAMPLE");
            return new ProbeCommand(Op.PLAY, (String) sample);
        }
        if (value.length() != 1) throw new IllegalArgumentException("UNKNOWN_COMMAND_FIELD");
        if ("stop".equals(op)) return new ProbeCommand(Op.STOP, null);
        if ("status".equals(op)) return new ProbeCommand(Op.STATUS, null);
        throw new IllegalArgumentException("UNKNOWN_COMMAND");
    }
}
