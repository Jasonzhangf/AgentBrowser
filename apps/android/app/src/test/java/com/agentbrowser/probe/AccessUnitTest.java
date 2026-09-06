package com.agentbrowser.probe;
import org.junit.Test;
import static org.junit.Assert.*;

public class AccessUnitTest {
    private byte[] unit() { return new byte[]{0,0,0,1,0x67,66,0,0,1,0x68,1,0,0,1,0x65,1}; }
    @Test public void boundedDimensionsAndBytes() {
        new AccessUnit(unit(),392,846,391,845,0,1);
        assertThrows(IllegalArgumentException.class, () -> new AccessUnit(unit(),391,846,391,845,0,1));
        assertThrows(IllegalArgumentException.class, () -> new AccessUnit(unit(),392,846,393,845,0,1));
        assertThrows(IllegalArgumentException.class, () -> new AccessUnit(unit(),4098,846,4098,845,0,1));
        assertThrows(IllegalArgumentException.class, () -> new AccessUnit(new byte[1024*1024+1],392,846,391,845,0,1));
        assertThrows(IllegalArgumentException.class, () -> new AccessUnit(unit(),392,846,391,845,-1,1));
    }
    @Test public void requiresSelfContainedIdr() {
        assertThrows(IllegalArgumentException.class, () -> new AccessUnit(new byte[]{1,2,3},160,120,160,120,0,1));
        assertThrows(IllegalArgumentException.class, () -> new AccessUnit(new byte[]{0,0,1,0x65,1},160,120,160,120,0,1));
        byte[] bytes=unit();
        AccessUnit value=new AccessUnit(bytes,160,120,160,120,0,1);
        bytes[4]=0;
        assertEquals(0x67,value.bytes()[4]);
    }
}
