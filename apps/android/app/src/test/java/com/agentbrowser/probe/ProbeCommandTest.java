package com.agentbrowser.probe;
import org.junit.Test;
import static org.junit.Assert.*;

public class ProbeCommandTest {
    @Test public void closedCommands() throws Exception {
        assertEquals(ProbeCommand.Op.PLAY, ProbeCommand.parse("{\"op\":\"play\",\"sample\":\"portrait\"}").op);
        assertEquals(ProbeCommand.Op.STOP, ProbeCommand.parse("{\"op\":\"stop\"}").op);
    }
    @Test public void rejectUntrustedFieldsAndSources() {
        assertThrows(Exception.class, () -> ProbeCommand.parse("{\"op\":\"play\",\"sample\":\"https://evil.invalid\"}"));
        assertThrows(Exception.class, () -> ProbeCommand.parse("{\"op\":\"stop\",\"payload\":\"video\"}"));
        assertThrows(Exception.class, () -> ProbeCommand.parse("{\"op\":\"takeover\"}"));
        assertThrows(Exception.class, () -> ProbeCommand.parse("x".repeat(257)));
        assertThrows(Exception.class, () -> ProbeCommand.parse("null"));
    }
}
