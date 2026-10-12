package com.agentbrowser.probe;

import org.json.JSONObject;
import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

/**
 * The displayed native frame may only validate against the authoritative Host
 * identity (session, active tab, presentation/document/viewport revisions and
 * the local connection generation). Stale cross-session/tab completions must be
 * refused rather than published as the new identity.
 */
public final class NativeIdentityProjectionTest {
    private static boolean matches(long generation, String session, String tab, long presentation,
                                   long document, long viewport, String hostSession, String hostTab,
                                   long hostPresentation, long hostDocument, long hostViewport) {
        return MainActivity.identityMatches(generation, 5L, session, tab, presentation, document, viewport,
            hostSession, hostTab, hostPresentation, hostDocument, hostViewport);
    }

    @Test public void matchingHostIdentityValidates() {
        assertTrue(matches(5L, "session-1", "tab-7", 9L, 2L, 3L, "session-1", "tab-7", 9L, 2L, 3L));
    }

    @Test public void staleConnectionGenerationIsRefused() {
        assertFalse(matches(4L, "session-1", "tab-7", 9L, 2L, 3L, "session-1", "tab-7", 9L, 2L, 3L));
    }

    @Test public void crossSessionFrameIsRefused() {
        assertFalse(matches(5L, "session-0", "tab-7", 9L, 2L, 3L, "session-1", "tab-7", 9L, 2L, 3L));
    }

    @Test public void crossTabFrameIsRefused() {
        assertFalse(matches(5L, "session-1", "tab-6", 9L, 2L, 3L, "session-1", "tab-7", 9L, 2L, 3L));
    }

    @Test public void stalePresentationRevisionAfterTabReturnIsRefused() {
        // A -> B -> A bumps presentation_revision; the stale A frame keeps the
        // old revision and must not validate against the current A tab.
        assertFalse(matches(5L, "session-1", "tab-7", 8L, 2L, 3L, "session-1", "tab-7", 9L, 2L, 3L));
    }

    @Test public void staleDocumentOrViewportRevisionIsRefused() {
        assertFalse(matches(5L, "session-1", "tab-7", 9L, 1L, 3L, "session-1", "tab-7", 9L, 2L, 3L));
        assertFalse(matches(5L, "session-1", "tab-7", 9L, 2L, 2L, "session-1", "tab-7", 9L, 2L, 3L));
    }

    @Test public void missingHostOrFrameTabIdentityIsRefused() {
        assertFalse(matches(5L, "session-1", "tab-7", 9L, 2L, 3L, "session-1", null, 9L, 2L, 3L));
        assertFalse(matches(5L, "session-1", "tab-7", 9L, 2L, 3L, null, "tab-7", 9L, 2L, 3L));
        assertFalse(matches(5L, null, "tab-7", 9L, 2L, 3L, "session-1", "tab-7", 9L, 2L, 3L));
        assertFalse(matches(5L, "session-1", "", 9L, 2L, 3L, "session-1", "tab-7", 9L, 2L, 3L));
    }

    @Test public void statusIdentityChangeOnlyFiresForRealIdentityDrift() throws Exception {
        JSONObject base = new JSONObject().put("session_id", "session-1").put("active_tab_id", "tab-7")
            .put("presentation_revision", 9L).put("document_revision", 2L).put("viewport_revision", 3L)
            .put("viewport_pending", false);
        assertTrue("First observed status is a change", NetworkSession.identityStatusChanged(null, base));
        assertFalse(NetworkSession.identityStatusChanged(base, new JSONObject(base.toString())));
        assertTrue(NetworkSession.identityStatusChanged(base, new JSONObject(base.toString()).put("active_tab_id", "tab-8")));
        assertTrue(NetworkSession.identityStatusChanged(base, new JSONObject(base.toString()).put("presentation_revision", 10L)));
        assertTrue(NetworkSession.identityStatusChanged(base, new JSONObject(base.toString()).put("document_revision", 4L)));
        assertTrue(NetworkSession.identityStatusChanged(base, new JSONObject(base.toString()).put("viewport_revision", 5L)));
        assertTrue(NetworkSession.identityStatusChanged(base, new JSONObject(base.toString()).put("viewport_pending", true)));
        assertTrue(NetworkSession.identityStatusChanged(base, new JSONObject(base.toString()).put("session_id", "session-2")));
    }
}
