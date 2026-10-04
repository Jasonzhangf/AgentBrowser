package com.agentbrowser.probe;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;

/** Native transport selection stays explicit and has no implicit fallback. */
public final class NativeTransportConfigTest {
    @Test public void wssFactoryHasNoBindAddress() {
        NativeConnection.TransportConfig config = NativeConnection.TransportConfig.wss();
        assertEquals(NativeConnection.Transport.WSS, config.transport());
        assertNull(config.bindIp());
    }

    @Test public void nativeConfigRejectsContradictoryValues() {
        assertThrows(IllegalArgumentException.class,
            () -> new NativeConnection.TransportConfig(NativeConnection.Transport.WSS, "127.0.0.1"));
        assertThrows(IllegalArgumentException.class,
            () -> new NativeConnection.TransportConfig(NativeConnection.Transport.WEBRTC, ""));
    }
}
