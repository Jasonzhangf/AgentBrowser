package com.agentbrowser.probe;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

/** Typed transport selection tests; no device or network is used. */
public final class WebRtcTransportConfigTest {
    @Test public void missingPairingConfigUsesExplicitWss() {
        NativeConnection.TransportConfig config = NetworkSession.parseTransportConfig("{\"transport\":\"wss\"}");
        assertEquals(NativeConnection.Transport.WSS, config.transport());
    }

    @Test public void parsesWebRtcLiteralBindAddress() {
        NativeConnection.TransportConfig config = NetworkSession.parseTransportConfig(
            "{\"transport\":\"webrtc\",\"bind_ip\":\"100.64.0.7\"}");
        assertEquals(NativeConnection.Transport.WEBRTC, config.transport());
        assertEquals("100.64.0.7", config.bindIp());
    }

    @Test public void rejectsMissingOrBlankWebRtcBindAddress() {
        assertThrows(IllegalArgumentException.class,
            () -> NetworkSession.parseTransportConfig("{\"transport\":\"webrtc\"}"));
        assertThrows(IllegalArgumentException.class,
            () -> NetworkSession.parseTransportConfig("{\"transport\":\"webrtc\",\"bind_ip\":\" \"}"));
    }

    @Test public void rejectsUnknownTransportAndFields() {
        assertThrows(IllegalArgumentException.class,
            () -> NetworkSession.parseTransportConfig("{\"transport\":\"udp\"}"));
        assertThrows(IllegalArgumentException.class,
            () -> NetworkSession.parseTransportConfig("{\"transport\":\"wss\",\"metadata\":true}"));
    }

    @Test public void rejectsAmbiguousWssBindAddress() {
        assertThrows(IllegalArgumentException.class,
            () -> NetworkSession.parseTransportConfig("{\"transport\":\"wss\",\"bind_ip\":\"127.0.0.1\"}"));
    }

    @Test public void rejectsMalformedOrEmptyConfig() {
        assertThrows(IllegalArgumentException.class, () -> NetworkSession.parseTransportConfig("{}"));
        assertThrows(IllegalArgumentException.class, () -> NetworkSession.parseTransportConfig("not-json"));
        assertThrows(IllegalArgumentException.class, () -> NetworkSession.parseTransportConfig(" "));
    }

    @Test public void nativeConfigRejectsContradictoryValues() {
        assertThrows(IllegalArgumentException.class,
            () -> new NativeConnection.TransportConfig(NativeConnection.Transport.WSS, "127.0.0.1"));
        assertThrows(IllegalArgumentException.class,
            () -> new NativeConnection.TransportConfig(NativeConnection.Transport.WEBRTC, ""));
    }
}
