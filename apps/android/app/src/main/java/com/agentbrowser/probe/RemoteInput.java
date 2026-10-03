package com.agentbrowser.probe;

import android.text.InputType;
import android.view.inputmethod.EditorInfo;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.concurrent.atomic.AtomicLong;

/** Host10 editing ABI adapter. No control state or page content is cached here. */
final class RemoteInput {
    record EditorContext(String targetId, boolean editable, boolean readOnly, String inputType,
                         String inputMode, String enterKeyHint, boolean multiline, boolean password,
                         long focusRevision, long editorRevision) {
        static EditorContext parse(JSONObject value) {
            if (value == null) return null;
            return new EditorContext(value.optString("target_id", ""),
                value.optBoolean("editable", false), value.optBoolean("read_only", true),
                value.optString("input_type", null), value.optString("input_mode", null),
                value.optString("enter_key_hint", null), value.optBoolean("multiline", false),
                value.optBoolean("password", false), value.optLong("focus_revision", -1),
                value.optLong("editor_revision", -1));
        }

        boolean writable() {
            return editable && !readOnly && !targetId.isEmpty() && focusRevision >= 0 && editorRevision >= 0;
        }

        boolean sameTarget(EditorContext other) {
            return other != null && targetId.equals(other.targetId) && focusRevision == other.focusRevision
                && editorRevision == other.editorRevision;
        }
    }

    private static final AtomicLong NEXT_OPERATION = new AtomicLong(1);

    private RemoteInput() { }

    static String newOperationId(String kind) {
        long value = NEXT_OPERATION.getAndUpdate(current -> current == Long.MAX_VALUE ? 1 : current + 1);
        return kind + "-" + value;
    }

    static String editorBinding(EditorContext context, String editSessionId) {
        if (context == null || !context.writable()) throw new IllegalStateException("EDITOR_CONTEXT_REQUIRED");
        return "{\"type\":\"editor\",\"edit_session_id\":" + JSONObject.quote(editSessionId)
            + ",\"target_id\":" + JSONObject.quote(context.targetId())
            + ",\"focus_revision\":" + context.focusRevision()
            + ",\"editor_revision\":" + context.editorRevision() + "}";
    }

    static String gestureBinding(String gestureSessionId) {
        return "{\"type\":\"gesture\",\"gesture_session_id\":" + JSONObject.quote(gestureSessionId) + "}";
    }

    static String editorIdentity(EditorContext context) {
        if (context == null) return null;
        return context.targetId() + "\n" + context.focusRevision() + "\n" + context.inputType() + "\n"
            + context.inputMode() + "\n" + context.enterKeyHint() + "\n" + context.multiline()
            + "\n" + context.password();
    }

    static String event(String type, Object... fields) {
        try {
            JSONObject value = new JSONObject().put("type", type);
            if ((fields.length & 1) != 0) throw new IllegalArgumentException("INVALID_REMOTE_INPUT_EVENT");
            for (int index = 0; index < fields.length; index += 2) value.put((String) fields[index], fields[index + 1]);
            return value.toString();
        } catch (JSONException impossible) {
            throw new IllegalStateException("INVALID_REMOTE_INPUT_EVENT", impossible);
        }
    }

    static boolean eventTerminal(String eventJson) {
        return switch (eventType(eventJson)) {
            case "pointer_up", "pointer_cancel", "composition_commit", "composition_cancel",
                 "replace_text", "delete_range", "delete_surrounding", "set_selection",
                 "editor_action", "key_down", "key_up" -> true;
            default -> false;
        };
    }

    static String eventType(String eventJson) {
        try {
            return new JSONObject(eventJson).optString("type", "");
        } catch (JSONException invalid) {
            return "";
        }
    }

    static int editorInputType(EditorContext context) {
        int flags = context.multiline() ? InputType.TYPE_TEXT_FLAG_MULTI_LINE : 0;
        if (context.password()) return InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD | flags;
        String type = context.inputType() == null ? "" : context.inputType();
        String mode = context.inputMode() == null ? "" : context.inputMode();
        if ("email".equals(mode) || "email".equals(type))
            return InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS | flags;
        if ("tel".equals(mode) || "tel".equals(type))
            return InputType.TYPE_CLASS_PHONE | flags;
        if ("url".equals(mode) || "url".equals(type))
            return InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI | flags;
        if ("numeric".equals(mode) || "number".equals(type) || "decimal".equals(mode))
            return InputType.TYPE_CLASS_NUMBER | flags;
        if ("search".equals(mode) || "search".equals(type))
            return InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_WEB_EDIT_TEXT | flags;
        return InputType.TYPE_CLASS_TEXT | flags;
    }

    static int imeOptions(EditorContext context) {
        int options = EditorInfo.IME_FLAG_NO_EXTRACT_UI;
        String hint = context.enterKeyHint() == null ? "" : context.enterKeyHint();
        return options | switch (hint) {
            case "go" -> EditorInfo.IME_ACTION_GO;
            case "next" -> EditorInfo.IME_ACTION_NEXT;
            case "previous" -> EditorInfo.IME_ACTION_PREVIOUS;
            case "search" -> EditorInfo.IME_ACTION_SEARCH;
            case "send" -> EditorInfo.IME_ACTION_SEND;
            case "done" -> EditorInfo.IME_ACTION_DONE;
            case "enter" -> context.multiline() ? EditorInfo.IME_ACTION_NONE : EditorInfo.IME_ACTION_DONE;
            default -> EditorInfo.IME_ACTION_NONE;
        };
    }

    static String editorAction(int action) {
        return switch (action) {
            case EditorInfo.IME_ACTION_GO -> "go";
            case EditorInfo.IME_ACTION_SEARCH -> "search";
            case EditorInfo.IME_ACTION_SEND -> "send";
            case EditorInfo.IME_ACTION_NEXT -> "next";
            case EditorInfo.IME_ACTION_PREVIOUS -> "previous";
            case EditorInfo.IME_ACTION_DONE -> "done";
            default -> null;
        };
    }

    static boolean receiptMatches(String json, String operationId, long eventSequence, String... states) {
        try {
            JSONObject editing = new JSONObject(json).optJSONObject("editing");
            if (editing == null || !operationId.equals(editing.optString("operation_id", ""))
                    || editing.optLong("event_sequence", -1) != eventSequence) return false;
            String state = editing.optString("state", "");
            for (String expected : states) if (expected.equals(state)) return true;
            return false;
        } catch (JSONException invalid) {
            return false;
        }
    }

    static boolean gestureReceiptMatches(String json, String... states) {
        try {
            JSONObject input = new JSONObject(json).optJSONObject("input");
            if (input == null) return false;
            String state = input.optString("state", "");
            for (String expected : states) if (expected.equals(state)) return true;
            return false;
        } catch (JSONException invalid) {
            return false;
        }
    }

    /** Reject a gesture transport acknowledgement that is not the event's real outcome. */
    static void requireGestureReceipt(String json, String eventJson) {
        String type = eventType(eventJson);
        String[] expected = switch (type) {
            case "pointer_down", "pointer_move" -> new String[]{"pending"};
            case "pointer_up" -> new String[]{"succeeded"};
            case "pointer_cancel" -> new String[]{"cancelled"};
            default -> new String[0];
        };
        if (expected.length > 0 && !gestureReceiptMatches(json, expected))
            throw new IllegalStateException("UNEXPECTED_GESTURE_RECEIPT");
    }

    static long codePointsToUtf16(JSONObject surrounding, JSONObject selection, int codePoints, boolean before) {
        if (surrounding == null) throw new IllegalStateException("SURROUNDING_UNAVAILABLE");
        if (selection == null) throw new IllegalStateException("SELECTION_UNAVAILABLE");
        String text = surrounding.optString("text", "");
        long offset = surrounding.optLong("offset", -1);
        long caret = before ? Math.min(selection.optLong("start", -1), selection.optLong("end", -1))
            : Math.max(selection.optLong("start", -1), selection.optLong("end", -1));
        if (offset < 0 || caret < offset || caret > offset + text.length())
            throw new IllegalStateException("INVALID_SURROUNDING_RANGE");
        int relative = (int) (caret - offset);
        if (codePoints < 0) throw new IllegalArgumentException("INVALID_CODEPOINT_COUNT");
        if (codePoints == 0) return 0;
        if (before) {
            int boundary = relative;
            for (int remaining = codePoints; remaining > 0; remaining--) {
                boundary--;
                if (boundary < 0) throw new IllegalStateException("SURROUNDING_OVERFLOW");
                if (boundary > 0 && Character.isLowSurrogate(text.charAt(boundary))
                        && Character.isHighSurrogate(text.charAt(boundary - 1))) boundary--;
                if (boundary < 0) throw new IllegalStateException("SURROUNDING_OVERFLOW");
            }
            return relative - boundary;
        }
        int boundary = relative;
        for (int remaining = codePoints; remaining > 0; remaining--) {
            if (boundary >= text.length()) throw new IllegalStateException("SURROUNDING_OVERFLOW");
            if (Character.isHighSurrogate(text.charAt(boundary))) {
                if (boundary + 1 >= text.length() || !Character.isLowSurrogate(text.charAt(boundary + 1)))
                    throw new IllegalStateException("INVALID_UTF16_RANGE");
                boundary += 2;
            } else {
                if (Character.isLowSurrogate(text.charAt(boundary)))
                    throw new IllegalStateException("INVALID_UTF16_RANGE");
                boundary++;
            }
        }
        return boundary - relative;
    }

    static boolean editable(JSONObject host) {
        if (host == null) return false;
        EditorContext context = EditorContext.parse(host.optJSONObject("editor_context"));
        if (context == null || !context.writable()) return false;
        JSONObject lease = host.optJSONObject("input_lease");
        if (lease == null) return !host.optBoolean("operation_running", false);
        return "composition".equals(lease.optString("kind", ""))
            && context.targetId().equals(lease.optString("target_id", ""));
    }

    static boolean gestureAllowed(JSONObject host, String localOperationId, String operationId, String localSession) {
        JSONObject lease = host == null ? null : host.optJSONObject("input_lease");
        if (lease == null) return !host.optBoolean("operation_running", false) && localOperationId != null;
        if (!"gesture".equals(lease.optString("kind", ""))) return false;
        return localOperationId != null && operationId != null
            && operationId.equals(localOperationId)
            && localOperationId.equals(lease.optString("operation_id", ""))
            && (localSession == null || localSession.equals(lease.optString("gesture_session_id", "")));
    }

    /**
     * A local gesture may still be canceled by its owning attachment after the
     * frame/identity fences reject new events. Never cancel a lease owned by
     * someone else.
     */
    static boolean gestureCancelOwned(JSONObject host, String operationId) {
        if (host == null || operationId == null || operationId.isEmpty()) return false;
        JSONObject lease = host.optJSONObject("input_lease");
        return lease == null || ("gesture".equals(lease.optString("kind", ""))
            && operationId.equals(lease.optString("operation_id", "")));
    }

    /** Only the attachment that opened our composition lease may cancel it. */
    static boolean compositionCancelOwned(JSONObject host, String operationId) {
        if (host == null || operationId == null || operationId.isEmpty()) return false;
        JSONObject lease = host.optJSONObject("input_lease");
        return lease != null && "composition".equals(lease.optString("kind", ""))
            && operationId.equals(lease.optString("operation_id", ""));
    }
}
