package com.agentbrowser.probe;

/** JNI account boundary. Relay credentials and the device key never enter WebView. */
final class NativeAccount {
    private NativeAccount() { }

    static native long login(String origin, byte[] ca, String username, String password);
    static native String status(long handle);
    static native String registerDevice(long handle, String name);
    static native String refresh(long handle);
    static native void revoke(long handle);
    static native void close(long handle);
}
