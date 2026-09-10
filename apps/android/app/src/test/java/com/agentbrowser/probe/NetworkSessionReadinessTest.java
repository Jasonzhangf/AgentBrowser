package com.agentbrowser.probe;

import org.json.JSONObject;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
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

    @Test public void navigationCanWaitForAnInternalViewportCommand() {
        assertTrue(NetworkSession.queuesBehindViewport(7, 6));
        assertFalse(NetworkSession.queuesBehindViewport(7, 7));
        assertFalse(NetworkSession.queuesBehindViewport(3, 6));
    }

    @Test public void onlyOneNavigationCanWaitAndItStartsAfterViewportSettles() {
        NetworkSession.CommandRequest queued = new NetworkSession.CommandRequest(7, 4, 0, 0, 0, 0, "https://example.invalid");
        NetworkSession.Viewport before = new NetworkSession.Viewport(391, 845, false);
        NetworkSession.Viewport after = new NetworkSession.Viewport(844, 391, true);

        assertEquals(NetworkSession.CommandAdvance.SUBMIT_VIEWPORT,
            NetworkSession.nextCommandAdvance(queued, after, before, null));
        assertEquals(NetworkSession.CommandAdvance.START_QUEUED,
            NetworkSession.nextCommandAdvance(queued, after, after, null));
    }

    @Test public void rejectedViewportBlocksQueuedNavigationWithoutResubmission() {
        NetworkSession.CommandRequest queued = new NetworkSession.CommandRequest(7, 4, 0, 0, 0, 0, "https://example.invalid");
        NetworkSession.Viewport rejected = new NetworkSession.Viewport(391, 845, false);

        assertEquals(NetworkSession.CommandAdvance.BLOCK_QUEUED,
            NetworkSession.nextCommandAdvance(queued, rejected, null, rejected));
        assertFalse(NetworkSession.viewportNeedsSubmission(rejected, null, rejected));
    }
}
