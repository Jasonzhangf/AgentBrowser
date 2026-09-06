package com.agentbrowser.probe;

import android.app.Activity;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.SurfaceView;
import android.view.SurfaceHolder;
import android.view.View;
import android.view.MotionEvent;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.FrameLayout;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Map;

public final class MainActivity extends Activity {
    static final String ORIGIN = "https://probe.agentbrowser.invalid/";
    private MediaProbe probe;
    AnnexBDecoder annex;
    NetworkSession network;
    private boolean annexSelected;
    private boolean networkSelected;
    private FrameLayout stage;
    private LinearLayout layout;
    private boolean landscape;
    FrameLayout videoClip;
    int codedWidth=360, codedHeight=640, visibleWidth=360, visibleHeight=640;
    WebView webView;
    SurfaceView video;
    private NetworkSession.InputContext touchContext;
    private float touchX,touchY;
    private boolean touchMoved;
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        probe = new MediaProbe(this);
        annex = new AnnexBDecoder();
        network = new NetworkSession(this, this::submitNetworkFrame, () -> annex.stop());
        layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setBackgroundColor(Color.rgb(245,247,244));
        layout.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(android.view.WindowInsets.Type.systemBars());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return insets;
        });
        stage = new FrameLayout(this);
        stage.setBackgroundColor(Color.rgb(13,20,16));
        video = new SurfaceView(this);
        video.setContentDescription("H.264 原生视频显示区");
        videoClip = new FrameLayout(this);
        videoClip.setClipChildren(true);
        videoClip.addView(video);
        stage.addView(videoClip);
        video.setOnTouchListener((view, event) -> networkTouch(event));
        stage.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob) -> {
            layoutVideo();
            if (networkSelected && (r-l!=or-ol || b-t!=ob-ot)) reportViewport();
        });
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
        landscape = getResources().getConfiguration().orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE;
        if (landscape) layout.setOrientation(LinearLayout.HORIZONTAL);
        layout.addView(stage, landscape ? new LinearLayout.LayoutParams(0, -1, 1) : new LinearLayout.LayoutParams(-1, 0, 1));
        int panel = (int) (370 * getResources().getDisplayMetrics().density);
        layout.addView(webView, landscape ? new LinearLayout.LayoutParams(panel, -1) : new LinearLayout.LayoutParams(-1, panel));
        setContentView(layout);
        getWindow().getInsetsController().setSystemBarsAppearance(
            android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
            android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
        layout.requestApplyInsets();
        webView.loadUrl(ORIGIN + "index.html");
    }
    private static WebResourceResponse blocked() {
        return new WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", Map.of(), new ByteArrayInputStream(new byte[0]));
    }
    private void layoutVideo() {
        float scale=Math.min(stage.getWidth()/(float)visibleWidth,stage.getHeight()/(float)visibleHeight);
        if(scale<=0) return;
        videoClip.setLayoutParams(new FrameLayout.LayoutParams(Math.round(visibleWidth*scale),Math.round(visibleHeight*scale),android.view.Gravity.CENTER));
        video.setLayoutParams(new FrameLayout.LayoutParams(Math.round(codedWidth*scale),Math.round(codedHeight*scale),android.view.Gravity.TOP|android.view.Gravity.LEFT));
    }
    private void geometry(int cw,int ch,int vw,int vh) {
        codedWidth=cw; codedHeight=ch; visibleWidth=vw; visibleHeight=vh;
        video.getHolder().setFixedSize(cw,ch);
        layoutVideo();
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
        return submitAccessUnit(frame.accessUnit(generation));
    }
    private boolean networkTouch(MotionEvent event) {
        synchronized(network) {
            int action=event.getActionMasked();
            NetworkSession.InputContext current=network.inputContext();
            if (!networkSelected || current==null || event.getPointerCount()!=1
                    || action==MotionEvent.ACTION_CANCEL || action==MotionEvent.ACTION_POINTER_UP) {
                touchContext=null;
                return networkSelected;
            }
            float sx=videoClip.getWidth()/(float)Math.max(1,visibleWidth);
            float sy=videoClip.getHeight()/(float)Math.max(1,visibleHeight);
            double x=event.getX()/sx,y=event.getY()/sy;
            if(sx<=0||sy<=0||x<0||y<0||x>=visibleWidth||y>=visibleHeight) {
                touchContext=null;
                return true;
            }
            if(action==MotionEvent.ACTION_DOWN) {
                touchContext=current;touchX=event.getX();touchY=event.getY();touchMoved=false;
                return true;
            }
            if(touchContext==null||!touchContext.equals(current)) {
                touchContext=null;
                return true;
            }
            int slop=android.view.ViewConfiguration.get(this).getScaledTouchSlop();
            touchMoved|=Math.hypot(event.getX()-touchX,event.getY()-touchY)>slop;
            if(action!=MotionEvent.ACTION_UP)return true;
            touchContext=null;
            if(touchMoved) {
                double dx=(touchX-event.getX())/sx,dy=(touchY-event.getY())/sy;
                // One completed swipe is one atomic scroll; cancellation never clicks.
                if(dx!=0||dy!=0)network.command(5,current.epoch(),touchX/sx,touchY/sy,dx,dy,"");
            } else network.command(3,current.epoch(),x,y,0,0,"");
            return true;
        }
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
                int panel=(int)(136*getResources().getDisplayMetrics().density);
                // Reserve compact chrome, then measure the remaining actual page
                // area. Host viewport negotiation will use this area, not screen size.
                webView.setLayoutParams(landscape ? new LinearLayout.LayoutParams(panel,-1) : new LinearLayout.LayoutParams(-1,panel));
                network.connect();
                stage.post(this::reportViewport);
            }
            case "disconnect" -> { requireFields(value, "op"); network.disconnect(); }
            case "observe" -> { requireFields(value, "op"); network.command(0, 0, 0, 0, 0, 0, ""); }
            case "takeover" -> { requireFields(value, "op", "epoch"); network.command(1, value.getLong("epoch"), 0, 0, 0, 0, ""); }
            case "release" -> { requireFields(value, "op", "epoch"); network.command(2, value.getLong("epoch"), 0, 0, 0, 0, ""); }
            case "click" -> { requireFields(value, "op", "epoch", "x", "y"); network.command(3, value.getLong("epoch"), finite(value, "x"), finite(value, "y"), 0, 0, ""); }
            case "input_text" -> { requireFields(value, "op", "epoch", "text"); String text = value.getString("text"); if (text.length() > 4096) throw new IllegalArgumentException("INPUT_TEXT_LIMIT"); network.command(4, value.getLong("epoch"), 0, 0, 0, 0, text); }
            case "scroll" -> { requireFields(value, "op", "epoch", "x", "y", "dx", "dy"); network.command(5, value.getLong("epoch"), finite(value, "x"), finite(value, "y"), finite(value, "dx"), finite(value, "dy"), ""); }
            case "status" -> requireFields(value, "op");
            default -> throw new IllegalArgumentException("UNKNOWN_NETWORK_COMMAND");
        }
        return network.snapshot(annex.snapshot()).toString();
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
                if (java.util.Set.of("connect", "disconnect", "observe", "takeover", "release", "click", "input_text", "scroll").contains(op)
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
    @Override protected void onStop() { network.disconnect(); probe.foreground(false); annex.foreground(false); super.onStop(); }
    @Override protected void onDestroy() {
        probe.close();
        annex.close();
        network.close();
        webView.removeJavascriptInterface("ProbeNative");
        webView.destroy();
        super.onDestroy();
    }
}
