package com.agentbrowser.probe;

import java.nio.file.Files;
import java.nio.file.Path;

/** Real WebRTC JNI consumer. Requires an owned Host/endpoint fixture and literal UDP bind IP. */
public final class WebRtcSmoke {
    private static void rejected(Runnable action) {
        try { action.run(); throw new AssertionError("Expected native rejection"); }
        catch (IllegalStateException expected) { }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("Usage: WebRtcSmoke <pairing> <bind-ip>");
        Path pairing = Path.of(args[0]);
        String bindIp = args[1];
        NativeConnection.load();
        rejected(() -> NativeConnection.openWebRtc(
            "wss://127.0.0.1:1", new byte[0], new byte[0], new byte[0], "not-an-ip"));

        long first = NativeConnection.open(
            Files.readString(pairing.resolve("endpoint.txt")).trim(),
            Files.readAllBytes(pairing.resolve("ca.der")), Files.readAllBytes(pairing.resolve("client.der")),
            Files.readAllBytes(pairing.resolve("key.der")), NativeConnection.TransportConfig.webRtc(bindIp));
        try {
            String status = NativeConnection.command(first, 0, 0, 0, 0, 0, 0, 0, "");
            if (!status.contains("session_id")) throw new AssertionError("WebRTC status missing session");
            NetworkFrame frame = null;
            long deadline = System.nanoTime() + 10_000_000_000L;
            while (frame == null && System.nanoTime() < deadline) frame = NativeConnection.frame(first);
            if (frame == null) throw new AssertionError("WebRTC media never reached native frame boundary");
            NativeConnection.acknowledge(first, frame.ticket);
        } finally {
            NativeConnection.close(first);
        }
        rejected(() -> NativeConnection.frame(first));

        long second = NativeConnection.open(
            Files.readString(pairing.resolve("endpoint.txt")).trim(),
            Files.readAllBytes(pairing.resolve("ca.der")), Files.readAllBytes(pairing.resolve("client.der")),
            Files.readAllBytes(pairing.resolve("key.der")), NativeConnection.TransportConfig.webRtc(bindIp));
        try {
            if (second == first) throw new AssertionError("Native handles were reused across generations");
            if (!NativeConnection.command(second, 0, 0, 0, 0, 0, 0, 0, "").contains("session_id"))
                throw new AssertionError("Reconnected WebRTC status missing session");
        } finally {
            NativeConnection.close(second);
        }
        System.out.println("WebRTC JNI PASS: typed config, UDP media boundary, release and stale-handle fencing");
    }
}
