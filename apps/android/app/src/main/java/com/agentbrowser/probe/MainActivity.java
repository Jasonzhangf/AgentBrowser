package com.agentbrowser.probe;

import android.app.Activity;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.TextureView;
import android.view.Surface;
import android.graphics.SurfaceTexture;
import android.view.View;
import android.view.MotionEvent;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.FrameLayout;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

public final class MainActivity extends Activity {
    static final String ORIGIN = "https://probe.agentbrowser.invalid/";
    private MediaProbe probe;
    AnnexBDecoder annex;
    NetworkSession network;
    AccountSession account;
    private boolean annexSelected;
    private boolean networkSelected;
    private FrameLayout stage;
    private LinearLayout layout;
    private boolean landscape;
    FrameLayout videoClip;
    int codedWidth=360, codedHeight=640, visibleWidth=360, visibleHeight=640;
    WebView webView;
    TextureView video;
    LinearLayout nativeControls;
    FrameLayout browserBar;
    TextView nativeStatus, nativeError;
    EditText nativeAddress, nativeInput;
    TextView gestureStatus;
    Button navigate, back, forward, reload;
    private final Map<Integer, Float> pointerLastX = new HashMap<>();
    private final Map<Integer, Float> pointerLastY = new HashMap<>();
    private final Map<Integer, Float> pointerDownX = new HashMap<>();
    private final Map<Integer, Float> pointerDownY = new HashMap<>();
    private final Map<Integer, Boolean> pointerScrolled = new HashMap<>();
    private int scrollGeneration;
    private boolean touchActive;
    private boolean imeConnected;
    private final android.os.Handler nativeControlTicker=new android.os.Handler(android.os.Looper.getMainLooper(), runnable -> {
        if (!networkSelected && !network.active()) return false;
        try { refreshNativeControls(network.snapshot(annex.snapshot())); } catch (RuntimeException ignored) { }
        return true;
    });
    private Runnable refreshNativeStatus;
    private final android.os.Handler snapshotTicker = new android.os.Handler(android.os.Looper.getMainLooper());
    private void snapshotDebugBitmap() {
        if (video == null || !(video instanceof TextureView)) return;
        TextureView tv = (TextureView) video;
        if (!tv.isAvailable()) {
            android.util.Log.i("AgentBrowserSnapshot", "TextureView not available");
            return;
        }
        try {
            SurfaceTexture st = tv.getSurfaceTexture();
            if (st == null) {
                android.util.Log.i("AgentBrowserSnapshot", "SurfaceTexture is null");
                return;
            }
            android.graphics.Bitmap bmp = tv.getBitmap();
            if (bmp == null) {
                android.util.Log.i("AgentBrowserSnapshot", "getBitmap() returned null");
                return;
            }
            int w = bmp.getWidth();
            int h = bmp.getHeight();
            int nonWhite = 0;
            int sampleMin = 255, sampleMax = 0;
            for (int y = 0; y < h; y += Math.max(1, h/50)) {
                for (int x = 0; x < w; x += Math.max(1, w/50)) {
                    int p = bmp.getPixel(x, y);
                    int r = (p >> 16) & 0xff;
                    int g = (p >> 8) & 0xff;
                    int b = p & 0xff;
                    int avg = (r + g + b) / 3;
                    if (avg < 240) nonWhite++;
                    if (avg < sampleMin) sampleMin = avg;
                    if (avg > sampleMax) sampleMax = avg;
                }
            }
            android.util.Log.i("AgentBrowserSnapshot", "bitmap " + w + "x" + h + " nonWhite=" + nonWhite + " min=" + sampleMin + " max=" + sampleMax);
            java.io.File dir = getExternalFilesDir(null);
            if (dir == null) dir = getFilesDir();
            if (dir == null) return;
            java.io.File out = new java.io.File(dir, "frame-debug.png");
            java.io.FileOutputStream fos = new java.io.FileOutputStream(out);
            bmp.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, fos);
            fos.close();
            // Also save JPEG for smaller size
            java.io.File jpg = new java.io.File(dir, "frame-debug.jpg");
            java.io.FileOutputStream fjpg = new java.io.FileOutputStream(jpg);
            bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 80, fjpg);
            fjpg.close();
        } catch (Throwable error) {
            android.util.Log.i("AgentBrowserSnapshot", "snapshot failed: " + error);
        }
    }
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        probe = new MediaProbe(this);
        annex = new AnnexBDecoder();
        network = new NetworkSession(this, this::submitNetworkFrame, () -> annex.stop(), this::refreshNativeControls);
        account = new AccountSession(this);
        layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setBackgroundColor(Color.BLACK);
        layout.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets occupied = insets.getInsets(
                android.view.WindowInsets.Type.systemBars() | android.view.WindowInsets.Type.ime());
            view.setPadding(occupied.left, occupied.top, occupied.right, occupied.bottom);
            return insets;
        });
        stage = new FrameLayout(this);
        stage.setBackgroundColor(Color.rgb(13,20,16));
        video = new TextureView(this);
        video.setContentDescription("H.264 原生视频显示区");
        video.setOpaque(true);
        videoClip = new FrameLayout(this);
        videoClip.setClipChildren(true);
        videoClip.addView(video);
        stage.addView(videoClip);
        video.setClickable(true); video.setOnTouchListener((view, event) -> { touchActive=true; return networkTouch(event); });
        stage.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob) -> {
            layoutVideo();
            if (networkSelected && (r-l!=or-ol || b-t!=ob-ot)) reportViewport();
        });
        videoClip.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob) -> {
            if (network.active() && (r-l!=or-ol || b-t!=ob-ot)) layoutChrome();
        });
        video.setSurfaceTextureListener(new TextureView.SurfaceTextureListener() {
            public void onSurfaceTextureAvailable(SurfaceTexture surfaceTexture, int width, int height) {
                Surface s = new Surface(surfaceTexture);
                probe.setSurface(s); annex.setSurface(s);
            }
            public void onSurfaceTextureSizeChanged(SurfaceTexture surfaceTexture, int width, int height) { }
            public boolean onSurfaceTextureDestroyed(SurfaceTexture surfaceTexture) {
                network.disconnect(); probe.setSurface(null); annex.setSurface(null);
                return true;
            }
            public void onSurfaceTextureUpdated(SurfaceTexture surfaceTexture) { }
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
        nativeControls = createNativeControls();
        browserBar = new FrameLayout(this);
        browserBar.addView(nativeControls, new FrameLayout.LayoutParams(-1, -1));
        gestureStatus = new TextView(this);
        gestureStatus.setText("");
        gestureStatus.setTextColor(Color.WHITE);
        gestureStatus.setTextSize(10);
        gestureStatus.setPadding(dp(8), dp(4), dp(8), 0);
        gestureStatus.setBackgroundColor(Color.argb(130,20,20,20));
        gestureStatus.setVisibility(View.GONE);
        browserBar.addView(gestureStatus, new FrameLayout.LayoutParams(-2, -2, android.view.Gravity.TOP|android.view.Gravity.CENTER_HORIZONTAL));
        layout.addView(browserBar);
        layout.addView(stage);
        layoutChrome();
        setContentView(layout);
        getWindow().getInsetsController().setSystemBarsAppearance(
            android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
            android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
        layout.requestApplyInsets();
        networkSelected = true;
        layoutChrome();
        browserBar.post(this::refreshNativeControls);
        browserBar.post(this::reportViewport);
        webView.loadUrl(ORIGIN + "index.html");
        getWindow().getInsetsController().hide(android.view.WindowInsets.Type.ime());
        snapshotTicker.postDelayed(new Runnable() {
            public void run() {
                snapshotDebugBitmap();
                snapshotTicker.postDelayed(this, 2000);
            }
        }, 2000);
    }
    private static WebResourceResponse blocked() {
        return new WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", Map.of(), new ByteArrayInputStream(new byte[0]));
    }
    private void layoutChrome() {
        landscape=getResources().getConfiguration().orientation==android.content.res.Configuration.ORIENTATION_LANDSCAPE;
        layout.setOrientation(landscape?LinearLayout.HORIZONTAL:LinearLayout.VERTICAL);
        browserBar.setLayoutParams(landscape?new LinearLayout.LayoutParams(0,0,1):new LinearLayout.LayoutParams(-1,LinearLayout.LayoutParams.WRAP_CONTENT));
        nativeControls.setLayoutParams(landscape?new FrameLayout.LayoutParams(0,0,1):new FrameLayout.LayoutParams(-1,FrameLayout.LayoutParams.WRAP_CONTENT));
        stage.setLayoutParams(landscape?new LinearLayout.LayoutParams(0,-1,1):new LinearLayout.LayoutParams(-1,0,1));
    }
    private LinearLayout createNativeControls() {
        LinearLayout controls = new LinearLayout(this);
        controls.setOrientation(LinearLayout.VERTICAL);
        controls.setBackgroundColor(Color.rgb(245,247,244));
        controls.setPadding(dp(10), dp(6), dp(10), dp(6));
        nativeStatus = new TextView(this);
        nativeStatus.setText("远程浏览器正在连接…");
        nativeStatus.setTextSize(11);
        nativeError = new TextView(this);
        nativeError.setTextSize(11);
        nativeError.setTextColor(Color.rgb(158,37,37));
        nativeError.setPadding(0, dp(4), 0, 0);
        nativeStatus.setPadding(0,0,dp(92),0);
        back = button("←", Color.rgb(225,233,226), Color.rgb(32,60,44));
        back.setContentDescription("后退"); back.setOnClickListener(v -> runEpochCommand("back"));
        forward = button("→", Color.rgb(225,233,226), Color.rgb(32,60,44));
        forward.setContentDescription("前进"); forward.setOnClickListener(v -> runEpochCommand("forward"));
        reload = button("↻", Color.rgb(225,233,226), Color.rgb(32,60,44));
        reload.setContentDescription("刷新"); reload.setOnClickListener(v -> runEpochCommand("reload"));
        nativeAddress = new EditText(this);
        nativeAddress.setHint("输入网址或搜索");
        nativeAddress.setSingleLine(true);
        nativeAddress.setImeOptions(android.view.inputmethod.EditorInfo.IME_ACTION_GO);
        nativeAddress.setText("https://m.weibo.cn/hot");
        nativeAddress.setTextSize(14);
        nativeAddress.setPadding(dp(8), dp(4), dp(8), dp(4));
        nativeAddress.setOnEditorActionListener((view,id,kev) -> { navigatePage(nativeAddress.getText().toString().trim()); return true; });
        navigate = new Button(this); navigate.setText("↵"); navigate.setContentDescription("打开");
        navigate.setOnClickListener(v -> navigatePage(nativeAddress.getText().toString().trim()));
        LinearLayout addressRow = new LinearLayout(this); addressRow.setOrientation(LinearLayout.HORIZONTAL);
        addressRow.addView(nativeAddress, new LinearLayout.LayoutParams(0,-1,1));
        for (Button navigationButton : new Button[]{back, forward, reload, navigate}) addressRow.addView(navigationButton, new LinearLayout.LayoutParams(dp(44), -1));
        controls.addView(nativeStatus);
        controls.addView(addressRow, new LinearLayout.LayoutParams(-1, dp(42)));
        controls.addView(nativeError);
        nativeInput = new EditText(this);
        nativeInput.setVisibility(View.INVISIBLE);
        nativeInput.setHint("点击远程输入框后输入文字");
        nativeInput.setPadding(dp(8), dp(4), dp(8), dp(4));
        controls.addView(nativeInput, new LinearLayout.LayoutParams(-1, dp(42)));
        nativeInput.addTextChangedListener(new android.text.TextWatcher() {
            public void beforeTextChanged(CharSequence s,int start,int count,int after) {
                if (!imeConnected) return;
                if (count > 0 && after == 0) sendRemoteKeyEvent("Backspace", "Backspace", "");
            }
            public void onTextChanged(CharSequence s,int start,int before,int count) {
                if (!imeConnected || count <= 0) return;
                sendTextDelta(s.subSequence(start, start + count).toString());
            }
            public void afterTextChanged(android.text.Editable s) { }
        });
        nativeInput.setOnEditorActionListener((view, actionId, event) -> {
            if (android.view.inputmethod.EditorInfo.IME_ACTION_DONE == actionId
                    || android.view.inputmethod.EditorInfo.IME_ACTION_GO == actionId
                    || android.view.inputmethod.EditorInfo.IME_ACTION_SEARCH == actionId
                    || android.view.inputmethod.EditorInfo.IME_ACTION_SEND == actionId
                    || android.view.inputmethod.EditorInfo.IME_ACTION_NEXT == actionId) {
                sendRemoteKeyEvent("Enter", "Enter", "");
                return true;
            }
            return false;
        });
        refreshNativeStatus = () -> nativeControlTicker.postDelayed(refreshNativeStatus, 250);
        nativeControlTicker.postDelayed(refreshNativeStatus, 250);
        return controls;
    }
    private Button button(String text, int background, int foreground) {
        Button button = new Button(this);
        button.setText(text);
        button.setBackgroundColor(background);
        button.setTextColor(foreground);
        button.setTextSize(12);
        button.setMinHeight(dp(32));
        button.setPadding(dp(4), dp(2), dp(4), dp(2));
        return button;
    }
    private int dp(int value) { return Math.round(getResources().getDisplayMetrics().density * value); }
    private void runEpochCommand(String op) {
        try {
            org.json.JSONObject value = jsonCommand(op);
            value.put("epoch", network.epoch());
            dispatchNetwork(value);
            refreshNativeControls(network.snapshot(annex.snapshot()));
        } catch (Exception error) { nativeError.setText(error.getMessage()); }
    }
    private void navigatePage(String url) {
        try {
            if (url.isBlank()) return;
            org.json.JSONObject value = jsonCommand("navigate");
            value.put("epoch", network.epoch());
            value.put("url", url);
            dispatchNetwork(value);
            refreshNativeControls(network.snapshot(annex.snapshot()));
        } catch (Exception error) { nativeError.setText(error.getMessage()); }
    }
    private void sendTextDelta(String delta) {
        if (delta == null || delta.isEmpty() || delta.length() > 4096) return;
        try {
            org.json.JSONObject value = jsonCommand("input_text");
            value.put("epoch", network.epoch());
            value.put("text", delta);
            dispatchNetwork(value);
            refreshNativeControls(network.snapshot(annex.snapshot()));
        } catch (Exception error) { nativeError.setText(error.getMessage()); }
    }
    private void sendRemoteKeyEvent(String key, String code, String text) {
        try {
            org.json.JSONObject value = jsonCommand("key_event");
            value.put("epoch", network.epoch());
            value.put("key", key);
            value.put("code", code);
            value.put("text", text);
            dispatchNetwork(value);
            refreshNativeControls(network.snapshot(annex.snapshot()));
        } catch (Exception error) { nativeError.setText(error.getMessage()); }
    }
    private org.json.JSONObject jsonCommand(String op) throws org.json.JSONException { return new org.json.JSONObject().put("op", op); }
    private void refreshNativeControls() {
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) refreshNativeControls(network.snapshot(annex.snapshot()));
        else nativeControlTicker.post(this::refreshNativeControls);
    }
    private void refreshNativeControls(org.json.JSONObject status) {
        try {
            boolean wasNetworkSelected=networkSelected;
            boolean connected = status.optString("connectionState", "idle").equals("connected");
            boolean control = "control".equals(status.optString("controlMode"));
            boolean ready = status.optBoolean("inputReady");
            nativeError.setText(status.isNull("error") ? "" : String.valueOf(status.get("error")));
            String detail = ready ? "可导航、滚动、点击和输入" : "等待最新画面同步";
            nativeStatus.setText(connected ? (control ? "接管中 · " + detail : "观察中 · " + detail) : "远程浏览器未连接");
            gestureStatus.setText(control ? "手势透传" : "");
            boolean editable = false;
            try { editable = status.optJSONObject("remoteFocus") != null && status.getJSONObject("remoteFocus").optBoolean("editable"); } catch (RuntimeException ignored) { }
            nativeControls.setVisibility(View.VISIBLE);
            gestureStatus.setVisibility(control ? View.VISIBLE : View.GONE);
            if (editable && !imeConnected) imeConnect();
            else if (!editable && imeConnected) imeConnected=false;
            boolean canTakeover = connected && !control && annex.snapshot().optInt("renderedFrames", 0) > 0;
            if (canTakeover) { try { runEpochCommand("takeover"); } catch (RuntimeException ignored) { } }
            nativeAddress.setEnabled(ready && !editable);
            navigate.setEnabled(ready && !editable);
            back.setEnabled(ready);
            forward.setEnabled(ready);
            reload.setEnabled(ready);
            if (connected && !networkSelected) {
                networkSelected = true;
                stage.post(this::reportViewport);
            }
            if (!wasNetworkSelected && network.active()) stage.post(this::reportViewport);
        } catch (Exception error) { nativeStatus.setText(error.getMessage()); }
    }
    @Override public void onConfigurationChanged(android.content.res.Configuration configuration) {
        super.onConfigurationChanged(configuration);
        pointerLastX.clear();
        pointerLastY.clear();
        layoutChrome();
        layout.requestApplyInsets();
        // Stage layout reports the measured area; preserve this Activity and Host.
        stage.post(this::reportViewport);
    }
    private void layoutVideo() {
        float scale=Math.min(stage.getWidth()/(float)visibleWidth,stage.getHeight()/(float)visibleHeight);
        if(scale<=0) return;
        videoClip.setLayoutParams(new FrameLayout.LayoutParams(Math.round(visibleWidth*scale),Math.round(visibleHeight*scale),android.view.Gravity.CENTER));
        video.setLayoutParams(new FrameLayout.LayoutParams(Math.round(codedWidth*scale),Math.round(codedHeight*scale),android.view.Gravity.TOP|android.view.Gravity.LEFT));
    }
    private void geometry(int cw,int ch,int vw,int vh) {
        codedWidth=cw; codedHeight=ch; visibleWidth=vw; visibleHeight=vh;
        if (video.getSurfaceTexture() != null) video.getSurfaceTexture().setDefaultBufferSize(cw, ch);
        layoutVideo();
    }
    /** Local native ingress. Media decode no longer owns browser input readiness. */
    public java.util.concurrent.CompletableFuture<AnnexBDecoder.Receipt> submitAccessUnit(AccessUnit unit) {
        if(!probe.snapshot().optBoolean("released")) throw new IllegalStateException("MP4_PROBE_BUSY");
        try {
            var result=annex.submit(unit);
            annexSelected=true;
            runOnUiThread(() -> geometry(unit.codedWidth,unit.codedHeight,unit.visibleWidth,unit.visibleHeight));
            return result;
        } catch (RuntimeException failure) {
            runOnUiThread(() -> { });
            throw failure;
        }
    }
    private java.util.concurrent.CompletableFuture<AnnexBDecoder.Receipt> submitNetworkFrame(NetworkFrame frame, long token, long generation) {
        if (!network.current(token)) throw new IllegalStateException("STALE_CONNECTION_GENERATION");
        return submitAccessUnit(frame.accessUnit(generation));
    }
    private boolean networkTouch(MotionEvent event) {
        synchronized(network) {
            int action=event.getActionMasked();
            NetworkSession.InputContext current=network.inputContext();
            if (!networkSelected || current==null || action==MotionEvent.ACTION_CANCEL
                    || action==MotionEvent.ACTION_OUTSIDE) {
                pointerLastX.clear();
                pointerLastY.clear();
                pointerDownX.clear();
                pointerDownY.clear();
                pointerScrolled.clear();
                scrollGeneration++;
                touchActive=false;
                return true;
            }
            float sx=videoClip.getWidth()/(float)Math.max(1,visibleWidth);
            float sy=videoClip.getHeight()/(float)Math.max(1,visibleHeight);
            if(sx<=0||sy<=0)return true;
            int generation=scrollGeneration;
            for (int i=0; i<event.getPointerCount(); i++) {
                int pointerId=event.getPointerId(i);
                float x=event.getX(i)/sx;
                float y=event.getY(i)/sy;
                if (!pointerDownX.containsKey(pointerId)) {
                    pointerDownX.put(pointerId,x);
                    pointerDownY.put(pointerId,y);
                    pointerScrolled.put(pointerId,false);
                    pointerLastX.put(pointerId,x);
                    pointerLastY.put(pointerId,y);
                    network.pointer(pointerId,11,current.epoch(),x,y,1);
                }
                Float dx=pointerDownX.get(pointerId);
                Float dy=pointerDownY.get(pointerId);
                if (dx != null && dy != null && Math.hypot(x-dx,y-dy) > 10.0) {
                    boolean wasScrolled=Boolean.TRUE.equals(pointerScrolled.get(pointerId));
                    pointerDownX.put(pointerId,x);
                    pointerDownY.put(pointerId,y);
                    pointerScrolled.put(pointerId,true);
                    if (!wasScrolled) network.pointer(pointerId,11,current.epoch(),x,y,1);
                }
                network.pointer(pointerId,12,current.epoch(),x,y,1);
                pointerLastX.put(pointerId,x);
                pointerLastY.put(pointerId,y);
            }
            if(action==MotionEvent.ACTION_UP || action==(MotionEvent.ACTION_POINTER_UP | MotionEvent.ACTION_POINTER_INDEX_MASK)) {
                int pointerId=event.getPointerId(action & MotionEvent.ACTION_POINTER_INDEX_MASK);
                Float x=pointerLastX.get(pointerId);
                Float y=pointerLastY.get(pointerId);
                if (x != null && y != null) network.pointer(pointerId,13,current.epoch(),x,y,0);
                pointerLastX.remove(pointerId);
                pointerLastY.remove(pointerId);
                pointerDownX.remove(pointerId);
                pointerDownY.remove(pointerId);
                pointerScrolled.remove(pointerId);
                scrollGeneration++;
            }
            return true;
        }
    }
    private void imeConnect() {
        imeConnected = true;
        runOnUiThread(() -> {
            nativeInput.setVisibility(View.VISIBLE);
            nativeInput.setText("");
            nativeInput.requestFocus();
                        nativeInput.post(() -> { try { if (nativeInput.hasFocus()) nativeInput.requestFocusFromTouch(); } catch (RuntimeException ignored) { } });
        });
    }
    private String dispatch(ProbeCommand command) throws org.json.JSONException {
        if(command.op==ProbeCommand.Op.PLAY) {
            if(network.active()) throw new IllegalStateException("NETWORK_BUSY");
            if(!annex.released()) throw new IllegalStateException("ANNEX_B_BUSY");
            annexSelected=false; networkSelected=false; geometry(360,640,360,640);
        }
        if(command.op==ProbeCommand.Op.STOP) { network.disconnect(); annex.stop(); }
        if(command.op==ProbeCommand.Op.STATUS && networkSelected) return network.snapshot(annex.snapshot()).toString();
        if(annexSelected) return annex.snapshot().toString();
        probe.request(command);
        return probe.snapshot().put("source","mp4").toString();
    }
    String dispatchNetwork(org.json.JSONObject value) throws org.json.JSONException {
        String op = value.optString("op", "");
        switch (op) {
            case "connect" -> {
                requireFields(value, "op");
                if (!annex.released() || !probe.snapshot().optBoolean("released")) throw new IllegalStateException("MEDIA_BUSY");
                networkSelected = true;
                // Reserve compact chrome, then measure the remaining actual page
                // area. Host viewport negotiation will use this area, not screen size.
                layoutChrome();
                network.connect();
                stage.post(this::reportViewport);
            }
            case "disconnect" -> { requireFields(value, "op"); network.disconnect(); }
            case "observe" -> { requireFields(value, "op"); network.command(0, 0, 0, 0, 0, 0, ""); }
            case "takeover" -> { requireFields(value, "op", "epoch"); network.command(1, value.getLong("epoch"), 0, 0, 0, 0, ""); }
            case "release" -> { requireFields(value, "op", "epoch"); network.command(2, value.getLong("epoch"), 0, 0, 0, 0, ""); }
            case "navigate" -> { requireFields(value, "op", "epoch", "url"); network.command(7, value.getLong("epoch"), 0, 0, 0, 0, value.getString("url")); }
            case "back" -> { requireFields(value, "op", "epoch"); network.command(8, value.getLong("epoch"), 0, 0, 0, 0, ""); }
            case "forward" -> { requireFields(value, "op", "epoch"); network.command(9, value.getLong("epoch"), 0, 0, 0, 0, ""); }
            case "reload" -> { requireFields(value, "op", "epoch"); network.command(10, value.getLong("epoch"), 0, 0, 0, 0, ""); }
            case "scroll_up" -> { requireFields(value, "op", "epoch"); network.command(5, value.getLong("epoch"), 0.5, 0.5, 0, 180, ""); }
            case "scroll_down" -> { requireFields(value, "op", "epoch"); network.command(5, value.getLong("epoch"), 0.5, 0.5, 0, -180, ""); }
            case "click" -> { requireFields(value, "op", "epoch", "x", "y"); network.command(3, value.getLong("epoch"), finite(value, "x"), finite(value, "y"), 0, 0, ""); }
            case "input_text" -> { requireFields(value, "op", "epoch", "text"); String text = value.getString("text"); if (text.length() > 4096) throw new IllegalArgumentException("INPUT_TEXT_LIMIT"); network.command(4, value.getLong("epoch"), 0, 0, 0, 0, text); }
            case "key_event" -> { requireFields(value, "op", "epoch", "key", "code", "text"); network.keyEvent(value.getLong("epoch"), value.getString("key"), value.getString("code"), value.getString("text")); }
            case "scroll" -> { requireFields(value, "op", "epoch", "x", "y", "dx", "dy"); network.command(5, value.getLong("epoch"), finite(value, "x"), finite(value, "y"), finite(value, "dx"), finite(value, "dy"), ""); }
            case "status" -> requireFields(value, "op");
            default -> throw new IllegalArgumentException("UNKNOWN_NETWORK_COMMAND");
        }
        return network.snapshot(annex.snapshot()).toString();
    }
    String dispatchAccount(org.json.JSONObject value) throws Exception {
        return account.request(value).toString();
    }
    private void reportViewport() {
        if (!networkSelected || stage.getWidth()<=0 || stage.getHeight()<=0) return;
        float density = getResources().getDisplayMetrics().density;
        int width = Math.max(1, Math.round(stage.getWidth() / density));
        int height = Math.max(1, Math.round(stage.getHeight() / density));
        network.declareViewport(width, height, landscape);
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
    private final class Bridge {
        @JavascriptInterface public String request(String raw) {
            try {
                org.json.JSONObject json = new org.json.JSONObject(raw);
                String op = json.optString("op", "");
                if (java.util.Set.of("account_status", "account_login", "account_register_device", "account_refresh", "account_logout").contains(op)) {
                    var task = new java.util.concurrent.FutureTask<String>(() -> dispatchAccount(json));
                    runOnUiThread(task);
                    return task.get(2, java.util.concurrent.TimeUnit.SECONDS);
                }
                if (java.util.Set.of("connect", "disconnect", "observe", "takeover", "release", "navigate", "back", "forward", "reload", "scroll_up", "scroll_down", "click", "input_text", "scroll").contains(op)
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
    @Override protected void onStart() {
        super.onStart();
        probe.foreground(true);
        annex.foreground(true);
        try { network.connect(); } catch (RuntimeException ignored) { }
        stage.post(this::reportViewport);
    }
    @Override protected void onStop() { network.disconnect(); probe.foreground(false); annex.foreground(false); super.onStop(); }
    @Override protected void onDestroy() {
        probe.close();
        annex.close();
        network.close();
        account.close();
        webView.removeJavascriptInterface("ProbeNative");
        webView.destroy();
        super.onDestroy();
    }
}
