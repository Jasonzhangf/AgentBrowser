package com.agentbrowser.probe;

import android.content.Context;
import android.graphics.Color;
import android.view.KeyEvent;
import android.view.View;
import android.view.inputmethod.BaseInputConnection;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputMethodManager;
import org.json.JSONObject;

/** Invisible OS InputConnection for the remote editable target. */
final class RemoteImeView extends View {
    interface Host {
        RemoteInput.EditorContext context();
        JSONObject selection();
        JSONObject surrounding();
        boolean eligible();
        String submit(String operationId, long eventSequence, String binding, String event);
        String cancel(String operationId, long eventSequence, String binding, String event);
    }

    private final Host host;
    private final InputMethodManager inputMethodManager;
    private boolean active;
    private boolean inputConnectionReady;
    private String connectionIdentity;
    private Connection connection;

    RemoteImeView(Context context, Host host) {
        super(context);
        this.host = host;
        inputMethodManager = (InputMethodManager) context.getSystemService(Context.INPUT_METHOD_SERVICE);
        setFocusable(true);
        setFocusableInTouchMode(true);
        setBackgroundColor(Color.TRANSPARENT);
        setAlpha(0f);
        setContentDescription("远端页面输入法桥接");
    }

    boolean active() {
        return active;
    }

    void sync() {
        boolean eligible = host.eligible();
        String identity = eligible ? RemoteInput.editorIdentity(host.context()) : null;
        if (eligible && !active) {
            connectionIdentity = identity;
            active = true;
            requestFocus();
            post(this::showInputWhenReady);
        } else if (!eligible && active) {
            deactivate();
            connectionIdentity = null;
        } else if (active && identity != null && !identity.equals(connectionIdentity)) {
            connectionIdentity = identity;
            if (connection != null) connection.abandonComposition();
            if (inputConnectionReady) inputMethodManager.restartInput(this);
        }
    }

    private void showInputWhenReady() {
        if (active && host.eligible() && inputConnectionReady) {
            inputMethodManager.showSoftInput(this, InputMethodManager.SHOW_IMPLICIT);
        }
    }

    void deactivate() {
        active = false;
        inputConnectionReady = false;
        if (connection != null) connection.abandonComposition();
        inputMethodManager.hideSoftInputFromWindow(getWindowToken(), 0);
        clearFocus();
    }

    @Override public boolean onCheckIsTextEditor() {
        return active && host.eligible();
    }

    @Override public InputConnection onCreateInputConnection(EditorInfo outAttrs) {
        RemoteInput.EditorContext context = host.context();
        if (connection != null && connection.hasDifferentCompositionTarget(context))
            connection.abandonComposition();
        if (!onCheckIsTextEditor() || context == null || !context.writable()) {
            inputConnectionReady = false;
            connection = null;
            return null;
        }
        outAttrs.inputType = RemoteInput.editorInputType(context);
        outAttrs.imeOptions = RemoteInput.imeOptions(context);
        connection = new Connection();
        inputConnectionReady = true;
        post(this::showInputWhenReady);
        return connection;
    }

    private final class Connection extends BaseInputConnection {
        private String composingOperation;
        private String composingText = "";
        private RemoteInput.EditorContext composingContext;
        private long eventSequence;

        Connection() {
            super(RemoteImeView.this, false);
        }

        @Override public boolean beginBatchEdit() {
            return onCheckIsTextEditor();
        }

        @Override public boolean endBatchEdit() {
            return onCheckIsTextEditor();
        }

        @Override public boolean setComposingText(CharSequence text, int newCursorPosition) {
            if (!onCheckIsTextEditor()) return false;
            String value = text == null ? "" : text.toString();
            RemoteInput.EditorContext context = host.context();
            if (context == null || !context.writable()) return false;
            try {
                if (composingOperation == null) {
                    composingOperation = RemoteInput.newOperationId("compose");
                    composingContext = context;
                    String binding = RemoteInput.editorBinding(context, composingOperation);
                    String result = host.submit(composingOperation, 0, binding,
                        RemoteInput.event("composition_begin", "data", value));
                    if (!RemoteInput.receiptMatches(result, composingOperation, 0, "pending")) {
                        discardComposition();
                        return false;
                    }
                    eventSequence = 1;
                } else {
                    if (!context.sameTarget(composingContext)) {
                        abandonComposition();
                        return false;
                    }
                    String binding = RemoteInput.editorBinding(context, composingOperation);
                    String result = host.submit(composingOperation, eventSequence, binding,
                        RemoteInput.event("composition_update", "data", value));
                    if (!RemoteInput.receiptMatches(result, composingOperation, eventSequence, "pending")) {
                        discardComposition();
                        return false;
                    }
                    eventSequence++;
                }
                composingText = value;
                return true;
            } catch (RuntimeException rejected) {
                discardComposition();
                return false;
            }
        }

        @Override public boolean commitText(CharSequence text, int newCursorPosition) {
            if (!onCheckIsTextEditor()) return false;
            String value = text == null ? "" : text.toString();
            RemoteInput.EditorContext context = host.context();
            if (context == null || !context.writable()) return false;
            try {
                if (composingOperation != null) {
                    if (!context.sameTarget(composingContext)) {
                        abandonComposition();
                        return false;
                    }
                    String binding = RemoteInput.editorBinding(context, composingOperation);
                    String result = host.submit(composingOperation, eventSequence, binding,
                        RemoteInput.event("composition_commit", "data", value));
                    if (!RemoteInput.receiptMatches(result, composingOperation, eventSequence, "applied")) {
                        discardComposition();
                        return false;
                    }
                    discardComposition();
                } else if (!value.isEmpty()) {
                    String operation = RemoteInput.newOperationId("compose");
                    String binding = RemoteInput.editorBinding(context, operation);
                    String result = host.submit(operation, 0, binding,
                        RemoteInput.event("replace_text", "text", value, "input_type", "insertText"));
                    if (!RemoteInput.receiptMatches(result, operation, 0, "applied")) return false;
                }
                return true;
            } catch (RuntimeException rejected) {
                discardComposition();
                return false;
            }
        }

        @Override public boolean finishComposingText() {
            if (composingOperation == null) return onCheckIsTextEditor();
            return commitText(composingText, 1);
        }

        @Override public boolean deleteSurroundingText(int beforeLength, int afterLength) {
            if (!onCheckIsTextEditor() || beforeLength < 0 || afterLength < 0) return false;
            return submitEdit("delete_surrounding", "before", beforeLength, "after", afterLength);
        }

        @Override public boolean deleteSurroundingTextInCodePoints(int beforeLength, int afterLength) {
            if (!onCheckIsTextEditor() || beforeLength < 0 || afterLength < 0) return false;
            RemoteInput.EditorContext context = host.context();
            JSONObject selection = context == null ? null : host.selection();
            try {
                // Android reports code points; Host10 owns UTF-16 offsets. The
                // focused snapshot's surrounding text is the only adapter input
                // permitted to convert these units, and it never leaves native.
                long before = RemoteInput.codePointsToUtf16(host.surrounding(), selection, beforeLength, true);
                long after = RemoteInput.codePointsToUtf16(host.surrounding(), selection, afterLength, false);
                return submitEdit("delete_surrounding", "before", before, "after", after);
            } catch (RuntimeException rejected) {
                return false;
            }
        }

        @Override public boolean setSelection(int start, int end) {
            if (!onCheckIsTextEditor() || start < 0 || end < 0) return false;
            return submitEdit("set_selection", "start", start, "end", end, "direction", "none");
        }

        @Override public boolean performEditorAction(int actionCode) {
            if (!onCheckIsTextEditor()) return false;
            String action = RemoteInput.editorAction(actionCode);
            if (action == null) return false;
            return submitEdit("editor_action", "action", action);
        }

        @Override public boolean sendKeyEvent(KeyEvent event) {
            if (!onCheckIsTextEditor() || event == null) return false;
            if (event.getKeyCode() == KeyEvent.KEYCODE_ESCAPE && event.getAction() == KeyEvent.ACTION_DOWN) {
                return cancelComposition();
            }
            return super.sendKeyEvent(event);
        }

        private boolean cancelComposition() {
            if (composingOperation == null) return onCheckIsTextEditor();
            RemoteInput.EditorContext context = host.context();
            if (context == null || !context.writable()) {
                discardComposition();
                return false;
            }
            try {
                if (!context.sameTarget(composingContext)) {
                    abandonComposition();
                    return false;
                }
                String binding = RemoteInput.editorBinding(context, composingOperation);
                String result = host.submit(composingOperation, eventSequence, binding, RemoteInput.event("composition_cancel"));
                if (!RemoteInput.receiptMatches(result, composingOperation, eventSequence, "cancelled")) {
                    discardComposition();
                    return false;
                }
                discardComposition();
                return true;
            } catch (RuntimeException rejected) {
                discardComposition();
                return false;
            }
        }

        private boolean submitEdit(String type, Object... fields) {
            RemoteInput.EditorContext context = host.context();
            if (context == null || !context.writable()) return false;
            if (composingOperation != null) {
                // A composition owns the Host editing lease until a terminal
                // event. Selection/delete/action must not open a second lease.
                if (!cancelComposition()) return false;
            }
            String operation = RemoteInput.newOperationId("compose");
            String binding = RemoteInput.editorBinding(context, operation);
            String result = host.submit(operation, 0, binding, RemoteInput.event(type, fields));
            return RemoteInput.receiptMatches(result, operation, 0, "applied");
        }

        private void discardComposition() {
            composingOperation = null;
            composingText = "";
            composingContext = null;
            eventSequence = 0;
        }

        private boolean hasDifferentCompositionTarget(RemoteInput.EditorContext context) {
            return composingOperation != null && context != null && !context.sameTarget(composingContext);
        }

        /**
         * Release local composition state on a remote identity change without
         * ever rebinding it to the new target. The Host may already have ended
         * the lease; a rejection is expected and is not replayable.
         */
        private void abandonComposition() {
            if (composingOperation == null) return;
            String operation = composingOperation;
            long sequence = eventSequence;
            RemoteInput.EditorContext context = composingContext;
            try {
                if (context != null) host.cancel(operation, sequence, RemoteInput.editorBinding(context, operation),
                    RemoteInput.event("composition_cancel"));
            } catch (RuntimeException rejected) {
                // The old target is no longer writable; local state is dropped below.
            } finally {
                discardComposition();
            }
        }
    }
}
