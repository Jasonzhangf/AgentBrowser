package com.agentbrowser.probe;

/** Native-owned network media frame. Encoded bytes never cross the WebView bridge. */
public final class NetworkFrame {
    public static final int MAX_BYTES = AccessUnit.MAX_BYTES;
    public final byte[] bytes;
    public final int codedWidth, codedHeight, visibleWidth, visibleHeight;
    public final long ptsUs, ticket;

    public NetworkFrame(byte[] value, int cw, int ch, int vw, int vh, long pts, long token) {
        if (value == null || value.length == 0 || value.length > MAX_BYTES)
            throw new IllegalArgumentException("NETWORK_FRAME_BYTES_LIMIT");
        if (cw < 2 || ch < 2 || cw > 4096 || ch > 4096 || (cw & 1) != 0 || (ch & 1) != 0
                || vw < 1 || vh < 1 || vw > cw || vh > ch || cw - vw > 1 || ch - vh > 1)
            throw new IllegalArgumentException("NETWORK_FRAME_DIMENSIONS");
        if (pts < 0 || token < 1) throw new IllegalArgumentException("NETWORK_FRAME_IDENTITY");
        bytes = value.clone();
        codedWidth = cw; codedHeight = ch; visibleWidth = vw; visibleHeight = vh;
        ptsUs = pts; ticket = token;
    }

    AccessUnit accessUnit(long generation) {
        return new AccessUnit(bytes, codedWidth, codedHeight, visibleWidth, visibleHeight, ptsUs, generation);
    }
}
