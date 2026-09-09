package com.agentbrowser.probe;

import java.nio.file.Files;
import java.nio.file.Path;

/** Real JNI/TLS smoke before APK deployment; does not claim native display. */
public final class NativeSmoke {
    private static void rejected(Runnable action) {
        try { action.run(); throw new AssertionError("Expected native rejection"); }
        catch (IllegalStateException expected) { }
    }
    public static void main(String[] args) throws Exception {
        Path pairing = Path.of(args[0]);
        NativeConnection.load();
        long handle = NativeConnection.open(Files.readString(pairing.resolve("endpoint.txt")).trim(),
            Files.readAllBytes(pairing.resolve("ca.der")), Files.readAllBytes(pairing.resolve("client.der")),
            Files.readAllBytes(pairing.resolve("key.der")));
        try {
            try {
                NativeConnection.command(handle, 1, Long.MAX_VALUE, 0, 0, 0, 0, 0, "");
                throw new AssertionError("Expected stale takeover rejection");
            } catch (IllegalStateException expected) {
                if (!(expected instanceof HostCommandException rejection))
                    throw new AssertionError("Host rejection lost its native type", expected);
                if (!rejection.code.equals("STALE_CONTROL"))
                    throw new AssertionError("Host rejection lost its code", expected);
            }
            String afterRejection = NativeConnection.command(handle, 0, 0, 0, 0, 0, 0, 0, "");
            if (!afterRejection.contains("session_id"))
                throw new AssertionError("Rejected command must leave status readable");
            rejected(() -> NativeConnection.acknowledge(handle, 1));
            rejected(() -> NativeConnection.command(handle, 3, 0, 0, 30, 30, 0, 0, ""));
            NetworkFrame frame = null;
            long deadline = System.nanoTime() + 10_000_000_000L;
            while (frame == null && System.nanoTime() < deadline) frame = NativeConnection.frame(handle);
            if (frame == null || frame.visibleWidth != 391 || frame.visibleHeight != 845)
                throw new AssertionError("Expected actual mobile Host frame");
            NetworkFrame received = frame;
            rejected(() -> NativeConnection.frame(handle));
            rejected(() -> NativeConnection.acknowledge(handle, received.ticket + 1));
            NativeConnection.acknowledge(handle, frame.ticket);
            rejected(() -> NativeConnection.acknowledge(handle, received.ticket));
            String status = NativeConnection.command(handle, 0, 0, 0, 0, 0, 0, 0, "");
            if (!status.contains("session_id")) throw new AssertionError("Expected Host status");
            System.out.println("JNI real endpoint PASS: typed Host rejection, continued status, framing and stale tickets");
        } finally { NativeConnection.close(handle); }
        rejected(() -> NativeConnection.frame(handle));
        rejected(() -> NativeConnection.acknowledge(handle, 1));
        rejected(() -> NativeConnection.close(handle));
    }
}
