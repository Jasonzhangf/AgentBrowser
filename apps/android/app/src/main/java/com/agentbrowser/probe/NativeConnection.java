package com.agentbrowser.probe;

/** JNI ABI for the native connection owner. Credentials stay in native storage. */
public final class NativeConnection {
    private static boolean loaded;
    private NativeConnection() { }

    static synchronized void load() {
        if (!loaded) {
            System.loadLibrary("agentbrowser_android");
            loaded = true;
        }
    }

    public static native long open(String endpoint, byte[] ca, byte[] cert, byte[] key);
    public static native NetworkFrame frame(long handle);
    public static native void acknowledge(long handle, long ticket);
    public static native String command(long handle, int op, long epoch, long ticket, double x, double y,
                                        double dx, double dy, String text);
    public static native void close(long handle);
}
