package com.agentbrowser.probe;

import android.text.InputType;
import android.view.inputmethod.EditorInfo;
import org.json.JSONObject;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

/** Host10 adapter semantics that must hold before any device IME run. */
public final class RemoteInputTest {
    private static RemoteInput.EditorContext context(boolean editable, boolean readOnly) {
        return new RemoteInput.EditorContext("node-1", editable, readOnly, "text", "email",
            "next", true, false, 4, 9);
    }

    @Test public void editorBindingCarriesTargetAndRevisions() throws Exception {
        JSONObject binding = new JSONObject(RemoteInput.editorBinding(context(true, false), "edit-1"));
        assertEquals("editor", binding.getString("type"));
        assertEquals("edit-1", binding.getString("edit_session_id"));
        assertEquals("node-1", binding.getString("target_id"));
        assertEquals(4, binding.getLong("focus_revision"));
        assertEquals(9, binding.getLong("editor_revision"));
    }

    @Test public void gestureBindingCarriesOnlyItsSession() throws Exception {
        JSONObject binding = new JSONObject(RemoteInput.gestureBinding("gesture-7"));
        assertEquals("gesture", binding.getString("type"));
        assertEquals("gesture-7", binding.getString("gesture_session_id"));
        assertEquals(2, binding.length());
    }

    @Test public void editorInfoMapsRemoteInputAndEnterHint() {
        RemoteInput.EditorContext email = context(true, false);
        assertEquals(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS
            | InputType.TYPE_TEXT_FLAG_MULTI_LINE, RemoteInput.editorInputType(email));
        assertEquals(EditorInfo.IME_FLAG_NO_EXTRACT_UI | EditorInfo.IME_ACTION_NEXT, RemoteInput.imeOptions(email));
        assertEquals("next", RemoteInput.editorAction(EditorInfo.IME_ACTION_NEXT));
        assertNull(RemoteInput.editorAction(EditorInfo.IME_ACTION_UNSPECIFIED));

        RemoteInput.EditorContext password = new RemoteInput.EditorContext("node-2", true, false,
            "password", null, "done", false, true, 1, 2);
        assertEquals(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD,
            RemoteInput.editorInputType(password));
        assertEquals(EditorInfo.IME_FLAG_NO_EXTRACT_UI | EditorInfo.IME_ACTION_DONE,
            RemoteInput.imeOptions(password));
    }

    @Test public void codePointConversionKeepsEmojiWhole() throws Exception {
        JSONObject surrounding = new JSONObject().put("text", "A😀中").put("offset", 0);
        JSONObject after = new JSONObject().put("start", 4).put("end", 4);
        JSONObject start = new JSONObject().put("start", 1).put("end", 1);

        assertEquals(1, RemoteInput.codePointsToUtf16(surrounding, after, 1, true));
        assertEquals(3, RemoteInput.codePointsToUtf16(surrounding, after, 2, true));
        assertEquals(2, RemoteInput.codePointsToUtf16(surrounding, start, 1, false));
        assertThrows(IllegalStateException.class,
            () -> RemoteInput.codePointsToUtf16(surrounding, after, 2, false));
    }

    @Test public void editableRequiresWritableContextAndCompatibleLease() throws Exception {
        JSONObject host = new JSONObject().put("editor_context", new JSONObject()
            .put("target_id", "node-1").put("editable", true).put("read_only", false)
            .put("focus_revision", 4).put("editor_revision", 9));
        assertTrue(RemoteInput.editable(host));
        host.put("operation_running", true);
        assertFalse(RemoteInput.editable(host));

        host.put("input_lease", new JSONObject().put("kind", "composition").put("target_id", "node-1"));
        assertTrue(RemoteInput.editable(host));
        host.getJSONObject("input_lease").put("target_id", "node-2");
        assertFalse(RemoteInput.editable(host));

        host.put("editor_context", new JSONObject().put("target_id", "node-1")
            .put("editable", true).put("read_only", true)
            .put("focus_revision", 4).put("editor_revision", 9));
        assertFalse(RemoteInput.editable(host));
    }

    @Test public void gestureLeaseMatchesOperationAndSession() throws Exception {
        JSONObject host = new JSONObject();
        assertTrue(RemoteInput.gestureAllowed(host, "gesture-1", null, null));
        assertFalse(RemoteInput.gestureAllowed(host, null, null, null));
        host.put("operation_running", true);
        assertFalse(RemoteInput.gestureAllowed(host, "gesture-1", null, null));

        host.put("input_lease", new JSONObject().put("kind", "gesture")
            .put("operation_id", "gesture-1").put("gesture_session_id", "session-1"));
        assertTrue(RemoteInput.gestureAllowed(host, "gesture-1", "gesture-1", "session-1"));
        assertFalse(RemoteInput.gestureAllowed(host, "gesture-2", "gesture-1", "session-1"));
        assertFalse(RemoteInput.gestureAllowed(host, "gesture-1", "gesture-1", "session-2"));
    }

    @Test public void receiptsMustMatchOperationSequenceAndState() {
        String editing = "{\"editing\":{\"operation_id\":\"compose-1\",\"event_sequence\":2,\"state\":\"applied\"}}";
        assertTrue(RemoteInput.receiptMatches(editing, "compose-1", 2, "applied"));
        assertFalse(RemoteInput.receiptMatches(editing, "compose-2", 2, "applied"));
        assertFalse(RemoteInput.receiptMatches(editing, "compose-1", 1, "applied"));
        assertFalse(RemoteInput.receiptMatches(editing, "compose-1", 2, "pending"));

        String gesture = "{\"input\":{\"state\":\"succeeded\"}}";
        assertTrue(RemoteInput.gestureReceiptMatches(gesture, "succeeded"));
        assertFalse(RemoteInput.gestureReceiptMatches(gesture, "pending"));
        assertFalse(RemoteInput.gestureReceiptMatches("{}", "succeeded"));
    }

    @Test public void gestureReceiptsRequireTheHostStateForThatEvent() {
        RemoteInput.requireGestureReceipt("{\"input\":{\"state\":\"pending\"}}",
            RemoteInput.event("pointer_down", "pointer_id", 1));
        RemoteInput.requireGestureReceipt("{\"input\":{\"state\":\"succeeded\"}}",
            RemoteInput.event("pointer_up", "pointer_id", 1));
        RemoteInput.requireGestureReceipt("{\"input\":{\"state\":\"cancelled\"}}",
            RemoteInput.event("pointer_cancel", "pointer_id", 1));
        assertThrows(IllegalStateException.class, () -> RemoteInput.requireGestureReceipt(
            "{\"input\":{\"state\":\"succeeded\"}}", RemoteInput.event("pointer_down", "pointer_id", 1)));
        assertThrows(IllegalStateException.class, () -> RemoteInput.requireGestureReceipt(
            "{\"input\":{\"state\":\"pending\"}}", RemoteInput.event("pointer_up", "pointer_id", 1)));
    }

    @Test public void editorIdentityChangesWhenRemoteTargetChanges() {
        String first = RemoteInput.editorIdentity(context(true, false));
        RemoteInput.EditorContext changed = new RemoteInput.EditorContext("node-1", true, false,
            "text", "email", "next", true, false, 5, 9);
        assertFalse(first.equals(RemoteInput.editorIdentity(changed)));
    }

    @Test public void compositionTargetRejectsRevisionOrFieldSwitch() {
        RemoteInput.EditorContext opened = context(true, false);
        RemoteInput.EditorContext same = new RemoteInput.EditorContext("node-1", true, false,
            "text", "email", "next", true, false, 4, 9);
        RemoteInput.EditorContext revised = new RemoteInput.EditorContext("node-1", true, false,
            "text", "email", "next", true, false, 4, 10);
        RemoteInput.EditorContext switched = new RemoteInput.EditorContext("node-1", true, false,
            "text", "email", "next", true, false, 5, 10);
        RemoteInput.EditorContext otherNode = new RemoteInput.EditorContext("node-2", true, false,
            "text", "email", "next", true, false, 4, 10);
        assertTrue(opened.sameTarget(same));
        assertFalse(opened.sameTarget(revised));
        assertFalse(opened.sameTarget(switched));
        assertFalse(opened.sameTarget(otherNode));
        assertFalse(opened.sameTarget(null));
    }

    @Test public void eventTerminalMatchesHostLeaseLifecycle() {
        assertFalse(RemoteInput.eventTerminal(RemoteInput.event("pointer_down", "pointer_id", 1)));
        assertFalse(RemoteInput.eventTerminal(RemoteInput.event("pointer_move", "pointer_id", 1)));
        assertTrue(RemoteInput.eventTerminal(RemoteInput.event("pointer_up", "pointer_id", 1)));
        assertTrue(RemoteInput.eventTerminal(RemoteInput.event("pointer_cancel", "pointer_id", 1)));
        assertFalse(RemoteInput.eventTerminal(RemoteInput.event("composition_begin", "data", "中")));
        assertFalse(RemoteInput.eventTerminal(RemoteInput.event("composition_update", "data", "中文")));
        assertTrue(RemoteInput.eventTerminal(RemoteInput.event("composition_commit", "data", "中文")));
        assertTrue(RemoteInput.eventTerminal(RemoteInput.event("composition_cancel")));
        assertTrue(RemoteInput.eventTerminal(RemoteInput.event("replace_text", "text", "x", "input_type", "insertText")));
    }

    @Test public void cancelOwnershipRejectsForeignLeases() throws Exception {
        JSONObject composition = new JSONObject().put("input_lease", new JSONObject()
            .put("kind", "composition").put("operation_id", "compose-1"));
        assertTrue(RemoteInput.compositionCancelOwned(composition, "compose-1"));
        assertFalse(RemoteInput.compositionCancelOwned(composition, "compose-2"));
        assertFalse(RemoteInput.compositionCancelOwned(new JSONObject(), "compose-1"));

        JSONObject gesture = new JSONObject().put("input_lease", new JSONObject()
            .put("kind", "gesture").put("operation_id", "pointer-1"));
        assertTrue(RemoteInput.gestureCancelOwned(gesture, "pointer-1"));
        assertFalse(RemoteInput.gestureCancelOwned(gesture, "pointer-2"));
        assertFalse(RemoteInput.gestureCancelOwned(new JSONObject().put("input_lease",
            new JSONObject().put("kind", "composition").put("operation_id", "compose-1")), "pointer-1"));
    }

    @Test public void emojiSelectionUsesUtf16Offsets() throws Exception {
        JSONObject surrounding = new JSONObject().put("text", "A😀中").put("offset", 0);
        JSONObject selection = new JSONObject().put("start", 1).put("end", 3);
        assertEquals(0, RemoteInput.codePointsToUtf16(surrounding, selection, 0, false));
        assertEquals(1, RemoteInput.codePointsToUtf16(surrounding, selection, 1, false));
        assertEquals(1, RemoteInput.codePointsToUtf16(surrounding, selection, 1, true));
    }

    @Test public void invalidSurroundingRangeFailsExplicitly() throws Exception {
        JSONObject surrounding = new JSONObject().put("text", "abc").put("offset", 10);
        JSONObject selection = new JSONObject().put("start", 0).put("end", 0);
        assertThrows(IllegalStateException.class,
            () -> RemoteInput.codePointsToUtf16(surrounding, selection, 1, true));
    }
}
