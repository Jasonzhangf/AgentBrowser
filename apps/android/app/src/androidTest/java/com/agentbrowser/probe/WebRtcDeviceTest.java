package com.agentbrowser.probe;

import android.content.Intent;
import android.os.SystemClock;
import android.test.InstrumentationTestCase;

/** Existing browser entry: typed WebRTC selection must reach MediaCodec before ready. */
public final class WebRtcDeviceTest extends InstrumentationTestCase {
    private MainActivity activity;

    private String js(String script) throws Exception {
        java.util.concurrent.CountDownLatch done = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.atomic.AtomicReference<String> result = new java.util.concurrent.atomic.AtomicReference<>();
        getInstrumentation().runOnMainSync(() -> activity.webView.evaluateJavascript(script, value -> {
            result.set(value);
            done.countDown();
        }));
        assertTrue("JS deadline", done.await(3, java.util.concurrent.TimeUnit.SECONDS));
        return result.get();
    }

    private void until(String script, long timeout) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeout;
        do {
            if ("true".equals(js(script))) return;
            SystemClock.sleep(50);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Condition: " + script + "\nstatus=" + js("JSON.stringify(JSON.parse(ProbeNative.request('{\"op\":\"status\"}')) )"));
    }

    public void testTypedWebRtcReachesNativeMedia() throws Exception {
        activity = (MainActivity) getInstrumentation().startActivitySync(
            new Intent(getInstrumentation().getTargetContext(), MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try {
            until("!!document.getElementById('connect')", 10_000);
            js("document.getElementById('connect').click()");
            until("(()=>{const s=JSON.parse(ProbeNative.request('{\"op\":\"status\"}'));return s.transport==='webrtc'&&s.connectionState==='connected'&&s.renderedFrames>=1&&s.state!=='error'})()", 30_000);
            assertEquals("WebRTC config must remain selected", "\"webrtc\"", js("JSON.parse(ProbeNative.request('{\"op\":\"status\"}')).transport"));
            assertTrue("MediaCodec must render a WebRTC access unit", activity.annex.snapshot().getInt("renderedFrames") >= 1);
            org.json.JSONObject evidence = new org.json.JSONObject()
                .put("transport", activity.network.transport())
                .put("connectionState", "connected")
                .put("renderedFrames", activity.annex.snapshot().getInt("renderedFrames"))
                .put("mediaReady", true);
            java.io.File directory = new java.io.File(activity.getFilesDir(), "webrtc-evidence");
            assertTrue(directory.isDirectory() || directory.mkdirs());
            try (java.io.FileOutputStream output = new java.io.FileOutputStream(new java.io.File(directory, "result.json"))) {
                output.write(evidence.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));
            }
        } finally {
            getInstrumentation().runOnMainSync(() -> activity.finish());
        }
    }
}
