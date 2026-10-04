package com.agentbrowser.probe;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.SurfaceView;
import android.view.SurfaceHolder;
import android.view.View;
import android.view.ViewGroup;
import android.view.MotionEvent;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    static final String ORIGIN = "https://probe.agentbrowser.invalid/";
    /**
     * Thin chrome height owned by the native layout projection: the top strip of
     * safeContentRect reserved for the Cordis toolbar when native video is
     * actually presented. It is independent of IME, drawer, presentation and
     * connection state.
     */
    static final int CHROME_HEIGHT_DP = 56;
    private MediaProbe probe;
    AnnexBDecoder annex;
    NetworkSession network;
    AccountSession account;
    private boolean annexSelected;
    private boolean networkSelected;
    private FrameLayout stage;
    private FrameLayout root;
    private boolean landscape;
    FrameLayout videoClip;
    int codedWidth=360, codedHeight=640, visibleWidth=360, visibleHeight=640;
    WebView webView;
    SurfaceView video;
    RemoteImeView remoteIme;
    private WindowInsets currentInsets;
    private android.view.View.OnApplyWindowInsetsListener insetsListener;
    private android.view.View.OnLayoutChangeListener stageLayoutListener;
    private android.view.View.OnLayoutChangeListener rootLayoutListener;
    private android.window.OnBackInvokedCallback backCallback;
    private Rect lastReportedViewport;
    private boolean forceViewportReport;
    private LayoutSnapshot lastLayoutState;
    private String presentation = "browsing";
    private long layoutRevision;
    private Rect lastPresentedContentRect;
    private FrameIdentity displayedFrame;
    private String gestureOperationId;
    private long gestureEventSequence;
    private long gestureEpoch, gestureDocument, gestureViewport;
    private static final AtomicLong NEXT_GESTURE = new AtomicLong(1);
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        probe = new MediaProbe(this);
        annex = new AnnexBDecoder();
        network = new NetworkSession(this, this::submitNetworkFrame, () -> annex.stop());
        network.onImeSync(this::syncRemoteIme);
        network.onStatusSync(this::onHostStatusChanged);
        account = new AccountSession(this);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(13,20,16));
        insetsListener = (view, insets) -> { currentInsets = insets; recomputeLayout(); return insets; };
        root.setOnApplyWindowInsetsListener(insetsListener);
        stage = new FrameLayout(this);
        stage.setBackgroundColor(Color.rgb(13,20,16));
        video = new SurfaceView(this);
        video.setContentDescription("H.264 原生视频显示区");
        videoClip = new FrameLayout(this);
        videoClip.setClipChildren(true);
        videoClip.addView(video);
        stage.addView(videoClip);
        remoteIme = new RemoteImeView(this, new RemoteImeView.Host() {
            public RemoteInput.EditorContext context() {
                return RemoteInput.EditorContext.parse(network.editorContext());
            }
            public org.json.JSONObject selection() {
                return network.selection();
            }
            public org.json.JSONObject surrounding() {
                return network.surrounding();
            }
            public boolean eligible() {
                return network.remoteImeEligible();
            }
            public String submit(String operationId, long eventSequence, String binding, String event) {
                return network.editing(network.epoch(), operationId, eventSequence, binding, event);
            }
            public String cancel(String operationId, long eventSequence, String binding, String event) {
                return network.cancelEditing(network.epoch(), operationId, eventSequence, binding, event);
            }
        });
        stage.addView(remoteIme, new FrameLayout.LayoutParams(1, 1, Gravity.TOP | Gravity.LEFT));
        video.setOnTouchListener((view, event) -> networkTouch(event));
        stageLayoutListener = (v,l,t,r,b,ol,ot,or,ob) -> {
            layoutVideo();
            if (networkSelected && (r-l!=or-ol || b-t!=ob-ot)) reportViewport();
        };
        stage.addOnLayoutChangeListener(stageLayoutListener);
        video.getHolder().addCallback(new SurfaceHolder.Callback() {
            public void surfaceCreated(SurfaceHolder holder) { probe.setSurface(holder.getSurface()); annex.setSurface(holder.getSurface()); }
            public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) { }
            public void surfaceDestroyed(SurfaceHolder holder) { network.disconnect(); probe.setSurface(null); annex.setSurface(null); }
        });
        webView = new WebView(this);
        webView.setBackgroundColor(Color.rgb(245,247,244));
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setAllowFileAccess(false);
        webView.getSettings().setAllowContentAccess(false);
        webView.getSettings().setBlockNetworkLoads(true);
        webView.addJavascriptInterface(new Bridge(), "ProbeNative");
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return true; }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String file = switch (uri.toString()) {
                    case ORIGIN + "index.html" -> "index.html";
                    case ORIGIN + "app.js" -> "app.js";
                    case ORIGIN + "app.css" -> "app.css";
                    default -> null;
                };
                if (file == null || !"GET".equals(request.getMethod())) return blocked();
                try {
                    String mime = file.endsWith("js") ? "text/javascript" : file.endsWith("css") ? "text/css" : "text/html";
                    return new WebResourceResponse(mime, "UTF-8", 200, "OK", Map.of("Cache-Control","no-store"), getAssets().open("ui/" + file));
                } catch (IOException error) { return new WebResourceResponse("text/plain", "UTF-8", 500, "Asset missing", Map.of(), new ByteArrayInputStream(error.toString().getBytes(StandardCharsets.UTF_8))); }
            }
        });
        root.addView(stage);
        root.addView(webView);
        rootLayoutListener = (v,l,t,r,b,ol,ot,or,ob) -> recomputeLayout();
        root.addOnLayoutChangeListener(rootLayoutListener);
        setContentView(root);
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            backCallback = () -> handleBack();
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, backCallback);
        }
        getWindow().getInsetsController().setSystemBarsAppearance(
            android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
            android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
        root.requestApplyInsets();
        webView.loadUrl(ORIGIN + "index.html");
    }
    private static WebResourceResponse blocked() {
        return new WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", Map.of(), new ByteArrayInputStream(new byte[0]));
    }

    // --- Native layout owner: root/stage/videoClip/SurfaceView parent-space geometry. ---

    static final class Rect {
        final int x, y, width, height;
        Rect(int x, int y, int width, int height) { this.x=x; this.y=y; this.width=width; this.height=height; }
        int right() { return x + width; }
        int bottom() { return y + height; }
        boolean equalsRect(Rect other) {
            return other != null && x==other.x && y==other.y && width==other.width && height==other.height;
        }
    }

    static final class LayoutRects {
        final Rect drawableWindowRect, safeContentRect, chromeRect, advertisedViewportRect, presentedContentRect;
        LayoutRects(Rect drawableWindowRect, Rect safeContentRect, Rect chromeRect, Rect advertisedViewportRect, Rect presentedContentRect) {
            this.drawableWindowRect=drawableWindowRect; this.safeContentRect=safeContentRect; this.chromeRect=chromeRect;
            this.advertisedViewportRect=advertisedViewportRect; this.presentedContentRect=presentedContentRect;
        }
    }

    static final class LayoutSnapshot {
        final LayoutRects rects;
        final Rect webRect;
        final boolean nativeVideoVisible;
        final boolean geometryMatches;
        final FrameIdentity displayedIdentity;
        final String currentTabId;
        LayoutSnapshot(LayoutRects rects, Rect webRect, boolean nativeVideoVisible, boolean geometryMatches,
                       FrameIdentity displayedIdentity, String currentTabId) {
            this.rects=rects; this.webRect=webRect; this.nativeVideoVisible=nativeVideoVisible; this.geometryMatches=geometryMatches;
            this.displayedIdentity=displayedIdentity; this.currentTabId=currentTabId;
        }
    }

    /** Root-local dp rects derived once from physical root bounds and insets. */
    static LayoutRects deriveRects(int rootWidthPx, int rootHeightPx, float density, int systemTopPx, int systemRightPx, int systemBottomPx, int systemLeftPx, int imeBottomPx) {
        int drawableWidth = Math.max(0, Math.round(rootWidthPx / density));
        int drawableHeight = Math.max(0, Math.round(rootHeightPx / density));
        int systemTop = Math.max(0, Math.round(systemTopPx / density));
        int systemRight = Math.max(0, Math.round(systemRightPx / density));
        int systemBottom = Math.max(0, Math.round(systemBottomPx / density));
        int systemLeft = Math.max(0, Math.round(systemLeftPx / density));
        Rect drawable = new Rect(0, 0, drawableWidth, drawableHeight);
        int safeX = systemLeft;
        int safeY = systemTop;
        int safeWidth = Math.max(0, drawableWidth - systemLeft - systemRight);
        int safeHeight = Math.max(0, drawableHeight - systemTop - systemBottom);
        Rect safe = new Rect(safeX, safeY, safeWidth, safeHeight);
        int chromeHeight = Math.min(CHROME_HEIGHT_DP, safeHeight);
        Rect chrome = new Rect(safeX, safeY, safeWidth, chromeHeight);
        Rect advertised = new Rect(safeX, safeY + chromeHeight, safeWidth, Math.max(0, safeHeight - chromeHeight));
        int imeBottom = Math.max(0, Math.round(imeBottomPx / density));
        int imeTop = drawableHeight - imeBottom;
        int overlap = Math.max(0, safe.bottom() - Math.max(safe.y, imeTop));
        int presentedHeight = Math.max(0, advertised.height - overlap);
        Rect presented = new Rect(advertised.x, advertised.y, advertised.width, presentedHeight);
        return new LayoutRects(drawable, safe, chrome, advertised, presented);
    }

    private void recomputeLayout() {
        if (root == null) return;
        int width = root.getWidth(), height = root.getHeight();
        if (width <= 0 || height <= 0) return;
        Insets bars = currentInsets == null ? Insets.NONE : currentInsets.getInsets(WindowInsets.Type.systemBars());
        Insets ime = currentInsets == null ? Insets.NONE : currentInsets.getInsets(WindowInsets.Type.ime());
        float density = getResources().getDisplayMetrics().density;
        LayoutRects rects = deriveRects(width, height, density, bars.top, bars.right, bars.bottom, bars.left, ime.bottom);
        boolean presentedChanged = !rects.presentedContentRect.equalsRect(lastPresentedContentRect);
        lastPresentedContentRect = rects.presentedContentRect;
        applyGeometry(rects);
        LayoutSnapshot next = buildLayoutSnapshot(rects);
        if (!sameLayout(lastLayoutState, next)) {
            layoutRevision++;
            lastLayoutState = next;
        }
        if (forceViewportReport || presentedChanged || !rects.advertisedViewportRect.equalsRect(lastReportedViewport)) {
            reportViewport();
        }
    }

    private void applyGeometry(LayoutRects rects) {
        float density = getResources().getDisplayMetrics().density;
        Rect presented = rects.presentedContentRect;
        applyFrame(stage, px(presented.x, density), px(presented.y, density), px(presented.width, density), px(presented.height, density));
        layoutVideo();
    }

    static boolean frameNeedsUpdate(int currentWidth, int currentHeight, int currentLeft, int currentTop,
            int currentGravity, int width, int height, int left, int top) {
        return currentWidth != width || currentHeight != height
            || currentLeft != left || currentTop != top
            || currentGravity != (Gravity.TOP | Gravity.LEFT);
    }

    private static void applyFrame(View view, int x, int y, int width, int height) {
        ViewGroup.LayoutParams current = view.getLayoutParams();
        if (current instanceof FrameLayout.LayoutParams params
                && !frameNeedsUpdate(params.width, params.height, params.leftMargin, params.topMargin,
                    params.gravity, width, height, x, y)) {
            return;
        }
        view.setLayoutParams(frameParams(x, y, width, height));
    }

    private static FrameLayout.LayoutParams frameParams(int x, int y, int width, int height) {
        FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(width, height, Gravity.TOP | Gravity.LEFT);
        params.leftMargin = x;
        params.topMargin = y;
        return params;
    }

    private static int px(int dp, float density) { return Math.round(dp * density); }

    private LayoutSnapshot buildLayoutSnapshot(LayoutRects rects) {
        String currentTabId = networkSelected ? network.activeTabId() : null;
        FrameIdentity identity = displayedFrame;
        boolean identityValid = networkSelected && identity != null
            && identityMatches(identity.generation, network.generation(), identity.sessionId, identity.tabId,
                identity.presentationRevision, identity.documentRevision, identity.viewportRevision,
                network.hostSessionId(), currentTabId, network.hostPresentationRevision(),
                network.hostDocumentRevision(), network.hostViewportRevision());
        boolean geometryMatches = identityValid && !network.hostViewportPending() && stageBoundsMatch(rects.presentedContentRect);
        boolean nativeVideoVisible = identityValid && geometryMatches;
        Rect web = "overlay".equals(presentation) || !nativeVideoVisible ? rects.drawableWindowRect : rects.chromeRect;
        return new LayoutSnapshot(rects, web, nativeVideoVisible, geometryMatches, identityValid ? identity : null, currentTabId);
    }

    /**
     * The displayed frame is valid only for the current Host identity: the
     * connection generation, session, active tab, presentation/document/viewport
     * revisions must all come from the same Host owner. A stale cross-tab frame
     * never validates, and no identity is guessed from session id, URL or DOM.
     */
    static boolean identityMatches(long generation, long currentGeneration, String frameSessionId, String frameTabId,
            long framePresentationRevision, long frameDocumentRevision, long frameViewportRevision,
            String hostSessionId, String hostTabId, long hostPresentationRevision,
            long hostDocumentRevision, long hostViewportRevision) {
        return generation == currentGeneration
            && frameSessionId != null && !frameSessionId.isEmpty()
            && hostSessionId != null && frameSessionId.equals(hostSessionId)
            && frameTabId != null && !frameTabId.isEmpty()
            && hostTabId != null && frameTabId.equals(hostTabId)
            && framePresentationRevision == hostPresentationRevision
            && frameDocumentRevision == hostDocumentRevision
            && frameViewportRevision == hostViewportRevision;
    }

    private boolean stageBoundsMatch(Rect presentedDp) {
        if (stage == null || stage.getWidth() <= 0 || stage.getHeight() <= 0) return false;
        float density = getResources().getDisplayMetrics().density;
        Rect actual = new Rect(Math.round(stage.getLeft()/density), Math.round(stage.getTop()/density),
            Math.round(stage.getWidth()/density), Math.round(stage.getHeight()/density));
        return actual.equalsRect(presentedDp);
    }

    private static boolean sameLayout(LayoutSnapshot a, LayoutSnapshot b) {
        if (a == null || b == null) return false;
        return a.webRect.equalsRect(b.webRect)
            && a.rects.drawableWindowRect.equalsRect(b.rects.drawableWindowRect)
            && a.rects.safeContentRect.equalsRect(b.rects.safeContentRect)
            && a.rects.chromeRect.equalsRect(b.rects.chromeRect)
            && a.rects.advertisedViewportRect.equalsRect(b.rects.advertisedViewportRect)
            && a.rects.presentedContentRect.equalsRect(b.rects.presentedContentRect)
            && a.nativeVideoVisible == b.nativeVideoVisible
            && a.geometryMatches == b.geometryMatches
            && java.util.Objects.equals(a.currentTabId, b.currentTabId)
            && sameIdentity(a.displayedIdentity, b.displayedIdentity);
    }

    private static boolean sameIdentity(FrameIdentity a, FrameIdentity b) {
        if (a == null || b == null) return a == b;
        return a.token == b.token && a.generation == b.generation
            && a.presentationRevision == b.presentationRevision
            && a.documentRevision == b.documentRevision && a.viewportRevision == b.viewportRevision
            && java.util.Objects.equals(a.sessionId, b.sessionId) && java.util.Objects.equals(a.tabId, b.tabId);
    }

    private void layoutVideo() {
        if (stage == null || videoClip == null || video == null) return;
        int stageWidth = stage.getWidth(), stageHeight = stage.getHeight();
        float scale = Math.min(stageWidth / (float) Math.max(1, visibleWidth), stageHeight / (float) Math.max(1, visibleHeight));
        if (scale <= 0 || !Float.isFinite(scale)) return;
        int clipWidth = Math.max(0, Math.round(visibleWidth * scale));
        int clipHeight = Math.max(0, Math.round(visibleHeight * scale));
        applyFrame(videoClip, Math.max(0, (stageWidth - clipWidth) / 2), Math.max(0, (stageHeight - clipHeight) / 2),
            clipWidth, clipHeight);
        applyFrame(video, 0, 0, Math.max(0, Math.round(codedWidth * scale)), Math.max(0, Math.round(codedHeight * scale)));
    }

    private void geometry(int cw,int ch,int vw,int vh) {
        codedWidth=cw; codedHeight=ch; visibleWidth=vw; visibleHeight=vh;
        video.getHolder().setFixedSize(cw,ch);
        layoutVideo();
    }

    private void syncRemoteIme() {
        if (remoteIme != null) remoteIme.sync();
    }

    @Override public void onConfigurationChanged(android.content.res.Configuration configuration) {
        super.onConfigurationChanged(configuration);
        landscape = configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE;
        cancelGesture();
        forceViewportReport = true;
        lastReportedViewport = null;
        root.requestApplyInsets();
        root.post(this::recomputeLayout);
    }

    /** Local native ingress; call on main thread. No encoded bytes enter WebView. */
    public java.util.concurrent.CompletableFuture<AnnexBDecoder.Receipt> submitAccessUnit(AccessUnit unit) {
        if(android.os.Looper.myLooper()!=android.os.Looper.getMainLooper()) throw new IllegalStateException("MAIN_THREAD_REQUIRED");
        if(!probe.snapshot().optBoolean("released")) throw new IllegalStateException("MP4_PROBE_BUSY");
        var result=annex.submit(unit);
        annexSelected=true;
        geometry(unit.codedWidth,unit.codedHeight,unit.visibleWidth,unit.visibleHeight);
        return result;
    }
    private java.util.concurrent.CompletableFuture<AnnexBDecoder.Receipt> submitNetworkFrame(NetworkFrame frame, long token, long generation) {
        if (!network.current(token)) throw new IllegalStateException("STALE_CONNECTION_GENERATION");
        var rendered = submitAccessUnit(frame.accessUnit(generation));
        rendered.whenComplete((receipt, failure) -> {
            if (failure != null) return;
            runOnUiThread(() -> publishDisplayedFrame(frame, token, generation));
        });
        return rendered;
    }

    /**
     * Publish the displayed identity only after a real decoder receipt and only
     * when it still matches the current Host identity. Runs on the main looper;
     * an old render completion from another session/tab is dropped, never
     * published as the new identity.
     */
    private void publishDisplayedFrame(NetworkFrame frame, long token, long generation) {
        if (!networkSelected || !network.current(token)) return;
        if (!identityMatches(generation, network.generation(), frame.sessionId, frame.tabId,
                frame.presentationRevision, frame.documentRevision, frame.viewportRevision,
                network.hostSessionId(), network.activeTabId(), network.hostPresentationRevision(),
                network.hostDocumentRevision(), network.hostViewportRevision())) return;
        displayedFrame = new FrameIdentity(token, generation, frame.sessionId, frame.tabId,
            frame.presentationRevision, frame.documentRevision, frame.viewportRevision);
        recomputeLayout();
    }

    private void onHostStatusChanged() {
        if (networkSelected) recomputeLayout();
    }
    private boolean networkTouch(MotionEvent event) {
        synchronized(network) {
            int action = event.getActionMasked();
            if (!networkSelected) return false;
            if (event.getPointerCount() != 1 || action == MotionEvent.ACTION_POINTER_DOWN
                    || action == MotionEvent.ACTION_POINTER_UP) {
                cancelGesture();
                return true;
            }
            float sx = videoClip.getWidth() / (float) Math.max(1, visibleWidth);
            float sy = videoClip.getHeight() / (float) Math.max(1, visibleHeight);
            double x = event.getX() / sx;
            double y = event.getY() / sy;
            if (sx <= 0 || sy <= 0 || !Double.isFinite(x) || !Double.isFinite(y)
                    || x < 0 || y < 0 || x >= visibleWidth || y >= visibleHeight) {
                cancelGesture();
                return true;
            }
            if (action == MotionEvent.ACTION_CANCEL) {
                cancelGesture();
                return true;
            }
            if (action == MotionEvent.ACTION_DOWN) {
                NetworkSession.InputContext context = network.inputContext();
                if (context == null) return true;
                gestureOperationId = "pointer-" + NEXT_GESTURE.getAndUpdate(current -> current == Long.MAX_VALUE ? 1 : current + 1);
                gestureEventSequence = 0;
                gestureEpoch = context.epoch();
                gestureDocument = context.document();
                gestureViewport = context.viewport();
                try {
                    network.gesture(gestureEpoch, gestureOperationId, gestureOperationId, gestureEventSequence,
                        RemoteInput.event("pointer_down", "pointer_id", 1, "x", x, "y", y,
                            "button", "left", "buttons", 1, "modifiers", 0));
                } catch (RuntimeException rejected) {
                    gestureOperationId = null;
                    gestureEventSequence = 0;
                    return true;
                }
                gestureEventSequence++;
                return true;
            }
            if (gestureOperationId == null) return true;
            NetworkSession.InputContext context = network.inputContext();
            if (context == null || context.epoch() != gestureEpoch || context.document() != gestureDocument
                    || context.viewport() != gestureViewport) {
                cancelGesture();
                return true;
            }
            if (action == MotionEvent.ACTION_MOVE) {
                try {
                    network.gesture(gestureEpoch, gestureOperationId, gestureOperationId, gestureEventSequence,
                        RemoteInput.event("pointer_move", "pointer_id", 1, "x", x, "y", y, "buttons", 1, "modifiers", 0));
                } catch (RuntimeException rejected) {
                    cancelGesture();
                    return true;
                }
            } else if (action == MotionEvent.ACTION_UP) {
                try {
                    network.gesture(gestureEpoch, gestureOperationId, gestureOperationId, gestureEventSequence,
                        RemoteInput.event("pointer_up", "pointer_id", 1, "x", x, "y", y, "button", "left", "buttons", 0, "modifiers", 0));
                } catch (RuntimeException rejected) {
                    cancelGesture();
                    return true;
                }
            } else {
                return true;
            }
            gestureEventSequence++;
            if (action == MotionEvent.ACTION_UP) {
                gestureOperationId = null;
                gestureEventSequence = 0;
            }
            return true;
        }
    }
    private void cancelGesture() {
        if (gestureOperationId == null) return;
        try {
            network.cancelGesture(gestureEpoch, gestureOperationId, gestureEventSequence);
        } catch (RuntimeException alreadyGone) {
            // The Host may have ended the lease (disconnect, follow-up event);
            // local state is dropped below and no event is replayed.
        } finally {
            gestureOperationId = null;
            gestureEventSequence = 0;
        }
    }
    private String dispatch(ProbeCommand command) throws org.json.JSONException {
        if(command.op==ProbeCommand.Op.PLAY) {
            if(network.active()) throw new IllegalStateException("NETWORK_BUSY");
            if(!annex.released()) throw new IllegalStateException("ANNEX_B_BUSY");
            annexSelected=false; networkSelected=false; displayedFrame=null; geometry(360,640,360,640);
        }
        if(command.op==ProbeCommand.Op.STOP) { network.disconnect(); annex.stop(); }
        if(command.op==ProbeCommand.Op.STATUS && networkSelected) return networkSnapshot();
        if(annexSelected) return annex.snapshot().toString();
        probe.request(command);
        return withLayout(probe.snapshot().put("source","mp4")).toString();
    }
    String dispatchNetwork(org.json.JSONObject value) throws org.json.JSONException {
        String op = value.optString("op", "");
        switch (op) {
            case "connect" -> {
                requireFields(value, "op");
                if (!annex.released() || !probe.snapshot().optBoolean("released")) throw new IllegalStateException("MEDIA_BUSY");
                networkSelected = true;
                forceViewportReport = true;
                network.connect();
                recomputeLayout();
            }
            case "disconnect" -> { requireFields(value, "op"); network.disconnect(); displayedFrame = null; recomputeLayout(); }
            case "observe" -> { requireFields(value, "op"); network.command(0, 0, 0, 0, 0, 0, ""); }
            case "takeover" -> { requireFields(value, "op", "epoch"); network.command(1, value.getLong("epoch"), 0, 0, 0, 0, ""); }
            case "release" -> { requireFields(value, "op", "epoch"); network.command(2, value.getLong("epoch"), 0, 0, 0, 0, ""); }
            case "navigate" -> { requireFields(value, "op", "epoch", "url"); network.command(7, value.getLong("epoch"), 0, 0, 0, 0, value.getString("url")); }
            case "click" -> { requireFields(value, "op", "epoch", "x", "y"); network.command(3, value.getLong("epoch"), finite(value, "x"), finite(value, "y"), 0, 0, ""); }
            case "input_text" -> { requireFields(value, "op", "epoch", "text"); String text = value.getString("text"); if (text.length() > 4096) throw new IllegalArgumentException("INPUT_TEXT_LIMIT"); network.command(4, value.getLong("epoch"), 0, 0, 0, 0, text); }
            case "scroll" -> { requireFields(value, "op", "epoch", "x", "y", "dx", "dy"); network.command(5, value.getLong("epoch"), finite(value, "x"), finite(value, "y"), finite(value, "dx"), finite(value, "dy"), ""); }
            case "status" -> requireFields(value, "op");
            default -> throw new IllegalArgumentException("UNKNOWN_NETWORK_COMMAND");
        }
        return networkSnapshot();
    }
    String dispatchAccount(org.json.JSONObject value) throws Exception {
        return account.request(value).toString();
    }

    String dispatchPresentation(JSONObject value) throws Exception {
        if (android.os.Looper.myLooper() != android.os.Looper.getMainLooper()) throw new IllegalStateException("MAIN_THREAD_REQUIRED");
        String id = value.optString("id", "");
        java.util.Iterator<String> keys = value.keys();
        while (keys.hasNext()) {
            String key = keys.next();
            if (!key.equals("id") && !key.equals("op") && !key.equals("state")) {
                return presentationResult(id, "rejected", "GEOMETRY_UNAVAILABLE");
            }
        }
        String state = value.optString("state", "");
        if (!"browsing".equals(state) && !"overlay".equals(state)) {
            return presentationResult(id, "rejected", "GEOMETRY_UNAVAILABLE");
        }
        if (!isResumed()) return presentationResult(id, "rejected", "ACTIVITY_NOT_RESUMED");
        LayoutSnapshot snapshot = lastLayoutState;
        if (snapshot == null) return presentationResult(id, "rejected", "GEOMETRY_UNAVAILABLE");
        applyPresentation(state, snapshot.rects);
        LayoutSnapshot applied = lastLayoutState;
        return new JSONObject().put("id", id).put("op", "set_presentation").put("status", "applied")
            .put("geometry", new JSONObject().put("webRect", rectJson(applied.webRect)).put("revision", layoutRevision)).toString();
    }

    private static String presentationResult(String id, String status, String reason) throws org.json.JSONException {
        return new JSONObject().put("id", id).put("op", "set_presentation").put("status", status).put("reason", reason).toString();
    }

    private void applyPresentation(String next, LayoutRects rects) {
        boolean changed = !next.equals(presentation);
        presentation = next;
        // Overlay is a local presentation layer over the same WebView: raise it
        // above the native stage. Browsing restores the native stage on top.
        if ("overlay".equals(next)) webView.bringToFront();
        else stage.bringToFront();
        applyGeometry(rects);
        LayoutSnapshot snapshot = buildLayoutSnapshot(rects);
        if (changed || !sameLayout(lastLayoutState, snapshot)) layoutRevision++;
        lastLayoutState = snapshot;
    }

    private boolean isResumed() {
        return getWindow() != null && getWindow().getDecorView().isAttachedToWindow();
    }

    private void reportViewport() {
        if (!networkSelected || lastLayoutState == null) return;
        Rect advertised = lastLayoutState.rects.advertisedViewportRect;
        if (advertised.width <= 0 || advertised.height <= 0) return;
        lastReportedViewport = advertised;
        forceViewportReport = false;
        network.declareViewport(advertised.width, advertised.height, landscape);
    }
    private static void requireFields(org.json.JSONObject value, String... allowed) throws org.json.JSONException {
        java.util.Set<String> names = new java.util.HashSet<>(java.util.Arrays.asList(allowed));
        java.util.Iterator<String> keys = value.keys();
        while (keys.hasNext()) if (!names.contains(keys.next())) throw new IllegalArgumentException("UNKNOWN_NETWORK_COMMAND_FIELD");
        for (String name : allowed) if (!"op".equals(name) && !value.has(name)) throw new IllegalArgumentException("MISSING_NETWORK_COMMAND_FIELD");
    }
    private static double finite(org.json.JSONObject value, String key) throws org.json.JSONException {
        double number = value.getDouble(key);
        if (!Double.isFinite(number) || Math.abs(number) > 1_000_000) throw new IllegalArgumentException("INVALID_NETWORK_COORDINATE");
        return number;
    }

    private String networkSnapshot() throws org.json.JSONException {
        return withLayout(network.snapshot(annex.snapshot())).toString();
    }

    private JSONObject withLayout(JSONObject value) throws org.json.JSONException {
        if (lastLayoutState != null) value.put("layout", layoutJson(lastLayoutState));
        return value;
    }

    private JSONObject layoutJson(LayoutSnapshot snapshot) throws org.json.JSONException {
        JSONObject value = new JSONObject()
            .put("drawableWindowRect", rectJson(snapshot.rects.drawableWindowRect))
            .put("safeContentRect", rectJson(snapshot.rects.safeContentRect))
            .put("advertisedViewportRect", rectJson(snapshot.rects.advertisedViewportRect))
            .put("presentedContentRect", rectJson(snapshot.rects.presentedContentRect))
            .put("chromeRect", rectJson(snapshot.rects.chromeRect))
            .put("webRect", rectJson(snapshot.webRect))
            .put("visibleContent", snapshot.nativeVideoVisible ? "native-video" : "webview")
            .put("currentTabId", snapshot.currentTabId == null ? JSONObject.NULL : snapshot.currentTabId)
            .put("presentationViewport", rectJson(snapshot.rects.presentedContentRect))
            .put("geometryMatches", snapshot.geometryMatches)
            .put("presentation", presentation)
            .put("revision", layoutRevision);
        if (snapshot.displayedIdentity != null) {
            value.put("displayedFrame", new JSONObject()
                .put("generation", snapshot.displayedIdentity.generation)
                .put("sessionId", snapshot.displayedIdentity.sessionId)
                .put("tabId", snapshot.displayedIdentity.tabId)
                .put("documentRevision", snapshot.displayedIdentity.documentRevision)
                .put("viewportRevision", snapshot.displayedIdentity.viewportRevision));
        }
        return value;
    }

    private static JSONObject rectJson(Rect rect) throws org.json.JSONException {
        return new JSONObject().put("x", rect.x).put("y", rect.y).put("width", rect.width).put("height", rect.height);
    }

    private static final class FrameIdentity {
        final long token, generation, presentationRevision, documentRevision, viewportRevision;
        final String sessionId, tabId;
        FrameIdentity(long token, long generation, String sessionId, String tabId, long presentationRevision, long documentRevision, long viewportRevision) {
            this.token=token; this.generation=generation; this.sessionId=sessionId; this.tabId=tabId;
            this.presentationRevision=presentationRevision; this.documentRevision=documentRevision; this.viewportRevision=viewportRevision;
        }
    }

    private final class Bridge {
        @JavascriptInterface public String request(String raw) {
            try {
                org.json.JSONObject json = new org.json.JSONObject(raw);
                String op = json.optString("op", "");
                if ("set_presentation".equals(op)) {
                    var task = new java.util.concurrent.FutureTask<String>(() -> dispatchPresentation(json));
                    runOnUiThread(task);
                    return task.get(2, java.util.concurrent.TimeUnit.SECONDS);
                }
                if (java.util.Set.of("account_status", "account_login", "account_register_device", "account_refresh", "account_logout").contains(op)) {
                    var task = new java.util.concurrent.FutureTask<String>(() -> dispatchAccount(json));
                    runOnUiThread(task);
                    return task.get(2, java.util.concurrent.TimeUnit.SECONDS);
                }
                if (java.util.Set.of("connect", "disconnect", "observe", "takeover", "release", "navigate", "click", "input_text", "scroll").contains(op)
                        || ("status".equals(op) && networkSelected)) {
                    var task = new java.util.concurrent.FutureTask<String>(() -> dispatchNetwork(json));
                    runOnUiThread(task);
                    return task.get(2, java.util.concurrent.TimeUnit.SECONDS);
                }
                ProbeCommand command=ProbeCommand.parse(raw);
                var task=new java.util.concurrent.FutureTask<String>(() -> dispatch(command));
                runOnUiThread(task);
                return task.get(2,java.util.concurrent.TimeUnit.SECONDS);
            }
            catch (Exception error) {
                // Command rejection remains an error; never mutate active decode state.
                return "{\"rejection\":" + org.json.JSONObject.quote(error.getClass().getSimpleName()+": "+error.getMessage()) + "}";
            }
        }
    }
    @Override protected void onStart() { super.onStart(); probe.foreground(true); annex.foreground(true); }
    @Override protected void onStop() { network.disconnect(); displayedFrame = null; if (remoteIme != null) remoteIme.deactivate(); probe.foreground(false); annex.foreground(false); super.onStop(); }
    private void handleBack() {
        if ("overlay".equals(presentation) && lastLayoutState != null) {
            applyPresentation("browsing", lastLayoutState.rects);
            return;
        }
        super.onBackPressed();
    }
    @Override public void onBackPressed() {
        handleBack();
    }
    @Override protected void onDestroy() {
        if (backCallback != null && android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(backCallback);
            backCallback = null;
        }
        if (root != null && insetsListener != null) root.setOnApplyWindowInsetsListener(null);
        if (stage != null && stageLayoutListener != null) stage.removeOnLayoutChangeListener(stageLayoutListener);
        if (root != null && rootLayoutListener != null) root.removeOnLayoutChangeListener(rootLayoutListener);
        if (remoteIme != null) remoteIme.deactivate();
        probe.close();
        annex.close();
        network.close();
        account.close();
        webView.removeJavascriptInterface("ProbeNative");
        webView.destroy();
        super.onDestroy();
    }
}
