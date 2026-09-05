package com.agentbrowser.probe;

import android.app.Activity;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.SurfaceView;
import android.view.SurfaceHolder;
import android.view.View;
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
    private boolean annexSelected;
    private FrameLayout stage;
    FrameLayout videoClip;
    private int codedWidth=360, codedHeight=640, visibleWidth=360, visibleHeight=640;
    WebView webView;
    SurfaceView video;
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        probe = new MediaProbe(this);
        annex = new AnnexBDecoder();
        LinearLayout layout = new LinearLayout(this);
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
        stage.addOnLayoutChangeListener((v,l,t,r,b,ol,ot,or,ob) -> {
            layoutVideo();
        });
        video.getHolder().addCallback(new SurfaceHolder.Callback() {
            public void surfaceCreated(SurfaceHolder holder) { probe.setSurface(holder.getSurface()); annex.setSurface(holder.getSurface()); }
            public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) { }
            public void surfaceDestroyed(SurfaceHolder holder) { probe.setSurface(null); annex.setSurface(null); }
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
        boolean landscape = getResources().getConfiguration().orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE;
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
    private String dispatch(ProbeCommand command) throws org.json.JSONException {
        if(command.op==ProbeCommand.Op.PLAY) {
            if(!annex.released()) throw new IllegalStateException("ANNEX_B_BUSY");
            annexSelected=false; geometry(360,640,360,640);
        }
        if(command.op==ProbeCommand.Op.STOP) annex.stop();
        if(annexSelected) return annex.snapshot().toString();
        probe.request(command);
        return probe.snapshot().put("source","mp4").toString();
    }
    private final class Bridge {
        @JavascriptInterface public String request(String raw) {
            try {
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
    @Override protected void onStop() { probe.foreground(false); annex.foreground(false); super.onStop(); }
    @Override protected void onDestroy() {
        probe.close();
        annex.close();
        webView.removeJavascriptInterface("ProbeNative");
        webView.destroy();
        super.onDestroy();
    }
}
