package com.agentbrowser.probe;

/** Platform-local bytes and geometry, not a BrowserSession or transport message. */
public final class AccessUnit {
    public static final int MAX_BYTES = 1024 * 1024;
    public final int codedWidth, codedHeight, visibleWidth, visibleHeight;
    public final long ptsUs, generation;
    private final byte[] encoded;
    public AccessUnit(byte[] bytes, int cw, int ch, int vw, int vh, long pts, long gen) {
        if (bytes == null || bytes.length == 0 || bytes.length > MAX_BYTES) throw new IllegalArgumentException("ACCESS_UNIT_BYTES_LIMIT");
        if (cw < 2 || ch < 2 || cw > 4096 || ch > 4096 || (cw & 1) != 0 || (ch & 1) != 0
                || vw < 1 || vh < 1 || vw > cw || vh > ch || cw-vw > 1 || ch-vh > 1)
            throw new IllegalArgumentException("INVALID_DECODE_DIMENSIONS");
        if (pts < 0 || gen < 1) throw new IllegalArgumentException("INVALID_DECODE_IDENTITY");
        encoded = bytes.clone();
        boolean sps=false, pps=false, idr=false;
        int offset=0;
        while (offset < encoded.length) {
            int prefix=prefix(encoded,offset);
            if (prefix==0) throw new IllegalArgumentException("INVALID_ANNEX_B");
            int header=offset+prefix, next=header+1;
            while (next<encoded.length && prefix(encoded,next)==0) next++;
            if (next-header<2 || (encoded[header]&0x80)!=0) throw new IllegalArgumentException("INVALID_NAL");
            int type=encoded[header]&31;
            if (type==7) sps=true;
            else if (type==8) pps=true;
            else if (type==5) idr=true;
            else if (type!=6 && type!=9) throw new IllegalArgumentException("UNSUPPORTED_NAL");
            offset=next;
        }
        if (!sps || !pps || !idr) throw new IllegalArgumentException("SELF_CONTAINED_IDR_REQUIRED");
        codedWidth=cw; codedHeight=ch; visibleWidth=vw; visibleHeight=vh; ptsUs=pts; generation=gen;
    }
    private static int prefix(byte[] bytes,int i) {
        if (i+2>=bytes.length || bytes[i]!=0 || bytes[i+1]!=0) return 0;
        if (bytes[i+2]==1) return 3;
        return i+3<bytes.length && bytes[i+2]==0 && bytes[i+3]==1 ? 4 : 0;
    }
    public byte[] bytes() { return encoded.clone(); }
    boolean sameGeometry(AccessUnit other) {
        return codedWidth==other.codedWidth && codedHeight==other.codedHeight
            && visibleWidth==other.visibleWidth && visibleHeight==other.visibleHeight;
    }
}
