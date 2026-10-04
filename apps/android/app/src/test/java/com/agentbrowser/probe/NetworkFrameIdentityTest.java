package com.agentbrowser.probe;

import org.junit.Test;

import java.lang.reflect.Constructor;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

/**
 * Pins the JNI frame projection contract. The native owner builds
 * {@code NetworkFrame} with the descriptor below; these tests fail if the Java
 * constructor drifts from it or drops the Host session/tab/presentation
 * identity that the layout projection needs.
 */
public final class NetworkFrameIdentityTest {
    /** Must equal the descriptor in packages/android-bridge/src/lib.rs `frame`. */
    private static final String JNI_CONSTRUCTOR_DESCRIPTOR =
        "([BIIIIJJJJLjava/lang/String;Ljava/lang/String;J)V";

    /** Minimal self-contained Annex B access unit: SPS, PPS, IDR. */
    private static final byte[] ACCESS_UNIT = {
        0, 0, 1, 0x67, (byte) 0xAA, 0, 0, 1, 0x68, (byte) 0xAA, 0, 0, 1, 0x65, (byte) 0xAA,
    };

    private static NetworkFrame frame(String session, String tab, long presentation) {
        return new NetworkFrame(ACCESS_UNIT.clone(), 4, 4, 4, 4, 1L, 7L, 2L, 3L, session, tab, presentation);
    }

    private static String descriptor(Class<?>[] types) {
        StringBuilder value = new StringBuilder("(");
        for (Class<?> type : types) {
            if (type == byte[].class) value.append("[B");
            else if (type == int.class) value.append('I');
            else if (type == long.class) value.append('J');
            else if (type == String.class) value.append("Ljava/lang/String;");
            else throw new AssertionError("Unsupported JNI projection type: " + type);
        }
        return value.append(")V").toString();
    }

    @Test public void constructorMatchesNativeJniDescriptor() throws Exception {
        Constructor<NetworkFrame> constructor = NetworkFrame.class.getConstructor(
            byte[].class, int.class, int.class, int.class, int.class,
            long.class, long.class, long.class, long.class, String.class, String.class, long.class);
        assertEquals(JNI_CONSTRUCTOR_DESCRIPTOR, descriptor(constructor.getParameterTypes()));
    }

    @Test public void identityFieldsAreCarriedFromTheHostFrame() {
        NetworkFrame frame = frame("session-1", "tab-7", 9L);
        assertEquals("session-1", frame.sessionId);
        assertEquals("tab-7", frame.tabId);
        assertEquals(9L, frame.presentationRevision);
        assertEquals(7L, frame.ticket);
        assertEquals(2L, frame.documentRevision);
        assertEquals(3L, frame.viewportRevision);
        assertArrayEquals(ACCESS_UNIT, frame.bytes);
    }

    @Test public void missingSessionOrTabIdentityIsRejected() {
        rejected(() -> frame(null, "tab-7", 1L));
        rejected(() -> frame("", "tab-7", 1L));
        rejected(() -> frame("session-1", null, 1L));
        rejected(() -> frame("session-1", "", 1L));
    }

    @Test public void invalidPresentationRevisionIsRejected() {
        rejected(() -> frame("session-1", "tab-7", -1L));
    }

    @Test public void accessUnitPreservesGeometryAndGeneration() {
        AccessUnit unit = frame("session-1", "tab-7", 1L).accessUnit(42L);
        assertEquals(4, unit.codedWidth);
        assertEquals(4, unit.visibleHeight);
        assertEquals(42L, unit.generation);
    }

    private static void rejected(Runnable action) {
        try { action.run(); fail("Expected NETWORK_FRAME_IDENTITY rejection"); }
        catch (IllegalArgumentException expected) { assertEquals("NETWORK_FRAME_IDENTITY", expected.getMessage()); }
    }
}
