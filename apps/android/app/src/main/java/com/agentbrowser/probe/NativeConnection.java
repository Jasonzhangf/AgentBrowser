package com.agentbrowser.probe;

/** JNI ABI for the native connection owner. Credentials stay in native storage. */
public final class NativeConnection {
    public enum Transport { WSS, WEBRTC }

    /** Explicit native transport selection; no implicit fallback is allowed. */
    public record TransportConfig(Transport transport, String bindIp) {
        public TransportConfig {
            if (transport == null) throw new IllegalArgumentException("TRANSPORT_REQUIRED");
            if (transport == Transport.WEBRTC && (bindIp == null || bindIp.isBlank()))
                throw new IllegalArgumentException("WEBRTC_BIND_IP_REQUIRED");
            if (transport == Transport.WSS && bindIp != null)
                throw new IllegalArgumentException("WSS_BIND_IP_FORBIDDEN");
        }

        public static TransportConfig wss() { return new TransportConfig(Transport.WSS, null); }
        public static TransportConfig webRtc(String bindIp) { return new TransportConfig(Transport.WEBRTC, bindIp); }
    }

    private static boolean loaded;
    private NativeConnection() { }

    static synchronized void load() {
        if (!loaded) {
            System.loadLibrary("agentbrowser_android");
            loaded = true;
        }
    }

    public static native long open(String endpoint, byte[] ca, byte[] cert, byte[] key);
    public static native long openWebRtc(String endpoint, byte[] ca, byte[] cert, byte[] key, String bindIp);

    static long open(String endpoint, byte[] ca, byte[] cert, byte[] key, TransportConfig config) {
        if (config == null) throw new IllegalArgumentException("TRANSPORT_CONFIG_REQUIRED");
        return config.transport() == Transport.WEBRTC
            ? openWebRtc(endpoint, ca, cert, key, config.bindIp())
            : open(endpoint, ca, cert, key);
    }

    public static native NetworkFrame frame(long handle);
    public static native void acknowledge(long handle, long ticket);
    public static native String command(long handle, int op, long epoch, long ticket, double x, double y,
                                        double dx, double dy, String text);
    public static native void close(long handle);
}
