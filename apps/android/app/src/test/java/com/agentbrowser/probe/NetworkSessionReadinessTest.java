package com.agentbrowser.probe;

import org.json.JSONObject;
import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public final class NetworkSessionReadinessTest {
    @Test public void hostReadyRequiresNoPendingOperation() throws Exception {
        assertTrue(NetworkSession.hostReady(new JSONObject()));
        assertFalse(NetworkSession.hostReady(new JSONObject().put("viewport_pending", true)));
        assertFalse(NetworkSession.hostReady(new JSONObject().put("operation_running", true)));
    }

    @Test public void missingHostIsNotReady() {
        assertFalse(NetworkSession.hostReady(null));
    }
}
