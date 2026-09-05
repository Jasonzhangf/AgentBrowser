package com.agentbrowser.probe;

import android.content.Intent;
import android.graphics.Bitmap;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.test.InstrumentationTestCase;
import android.view.PixelCopy;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

// Executes shipped Cordis UI buttons in the installed WebView, then samples native surface pixels.
public class ProbeDeviceTest extends InstrumentationTestCase {
    private MainActivity activity;
    private String js(String script) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>();
        getInstrumentation().runOnMainSync(() -> activity.webView.evaluateJavascript(script, value -> { result.set(value); done.countDown(); }));
        assertTrue("JS response deadline", done.await(3, TimeUnit.SECONDS));
        return result.get();
    }
    private void until(String script, long timeout) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeout;
        do { if ("true".equals(js(script))) return; SystemClock.sleep(100); } while (SystemClock.elapsedRealtime() < deadline);
        fail("UI condition timed out: " + script + "\n" + js("document.body.innerText"));
    }
    private void click(String id) throws Exception { js("document.getElementById('" + id + "').click()"); }
    private Bitmap capture(String name) throws Exception {
        Bitmap bitmap = Bitmap.createBitmap(activity.video.getWidth(), activity.video.getHeight(), Bitmap.Config.ARGB_8888);
        CountDownLatch done = new CountDownLatch(1);
        int[] status = {-1};
        getInstrumentation().runOnMainSync(() -> PixelCopy.request(activity.video, bitmap, value -> { status[0] = value; done.countDown(); }, new Handler(Looper.getMainLooper())));
        assertTrue(done.await(3, TimeUnit.SECONDS));
        assertEquals("Surface PixelCopy", PixelCopy.SUCCESS, status[0]);
        File directory = new File(activity.getFilesDir(), "probe-evidence");
        assertTrue(directory.isDirectory() || directory.mkdirs());
        try (FileOutputStream output = new FileOutputStream(new File(directory, name + ".png"))) { assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)); }
        return bitmap;
    }
    public void testRealUiMediaLifecycle() throws Exception {
        Intent intent = new Intent(getInstrumentation().getTargetContext(), MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        activity = (MainActivity) getInstrumentation().startActivitySync(intent);
        try {
            until("!!document.getElementById('play')", 10000);
            assertEquals("true", js("JSON.parse(ProbeNative.request('{\"op\":\"navigate\"}')).rejection.includes('UNKNOWN_COMMAND')"));
            assertEquals("true", js("JSON.parse(ProbeNative.request('{\"op\":\"play\",\"sample\":\"https://example.com\"}')).rejection.includes('INVALID_SAMPLE')"));
            click("play");
            until("Number(document.querySelector('section').dataset.frames)>12", 6000);
            Bitmap first = capture("frame-a");
            SystemClock.sleep(1200);
            Bitmap second = capture("frame-b");
            int changed = 0, colorful = 0, samples = 0;
            for (int y=0; y<first.getHeight(); y+=8) for (int x=0; x<first.getWidth(); x+=8) {
                int a=first.getPixel(x,y), b=second.getPixel(x,y); samples++;
                if (a!=b) changed++;
                if (android.graphics.Color.red(a)>80 || android.graphics.Color.green(a)>80 || android.graphics.Color.blue(a)>80) colorful++;
            }
            assertTrue("dynamic native pixels: " + changed + "/" + samples, changed > samples/50);
            assertTrue("nonblack native pixels", colorful > samples/4);
            int live = Integer.parseInt(js("Number(document.querySelector('section').dataset.frames)"));
            click("stop");
            until("document.querySelector('section').dataset.released==='true'", 5000);
            assertEquals("\"stopped\"", js("document.querySelector('section').dataset.state"));
            String stoppedGeneration = js("JSON.parse(ProbeNative.request('{\"op\":\"status\"}')).generation");
            assertEquals("true", js("JSON.parse(ProbeNative.request('{\"op\":\"stop\",\"payload\":\"media\"}')).rejection.includes('UNKNOWN_COMMAND_FIELD')"));
            assertEquals(stoppedGeneration, js("JSON.parse(ProbeNative.request('{\"op\":\"status\"}')).generation"));
            click("broken");
            until("document.querySelector('section').dataset.state==='error' && document.querySelector('section').dataset.released==='true'", 5000);
            click("play");
            until("Number(document.querySelector('section').dataset.frames)>5", 5000);
            click("plugin");
            until("document.getElementById('plugin').dataset.mounted==='false' && !document.querySelector('section')", 5000);
            click("plugin");
            until("!!document.getElementById('play')", 5000);
            click("play");
            until("Number(document.querySelector('section').dataset.frames)>5", 5000);
            getInstrumentation().runOnMainSync(() -> assertTrue("real task background", activity.moveTaskToBack(true)));
            until("document.querySelector('section').dataset.released==='true'", 5000);
            // Resume from an external actor, like tapping the launcher; the app
            // must not grant itself permission to launch from the background.
            try (android.os.ParcelFileDescriptor descriptor = getInstrumentation().getUiAutomation().executeShellCommand(
                    "am start -W -f 0x00020000 -n com.agentbrowser.probe/.MainActivity");
                 java.io.FileInputStream output = new java.io.FileInputStream(descriptor.getFileDescriptor())) {
                String launch = new String(output.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
                assertTrue(launch, launch.contains("Status: ok"));
            }
            long resumeDeadline = SystemClock.elapsedRealtime() + 5000;
            java.util.concurrent.atomic.AtomicBoolean focused = new java.util.concurrent.atomic.AtomicBoolean();
            do {
                getInstrumentation().runOnMainSync(() -> focused.set(activity.hasWindowFocus()));
                if (focused.get()) break;
                SystemClock.sleep(100);
            } while (SystemClock.elapsedRealtime() < resumeDeadline);
            assertTrue("resumed Activity window", focused.get());
            click("play");
            until("document.querySelector('section').dataset.state==='completed' && document.querySelector('section').dataset.released==='true'", 16000);
            until("Number(document.querySelector('section').dataset.frames)>=350", 2000);
            int completed = Integer.parseInt(js("Number(document.querySelector('section').dataset.frames)"));
            String codec = new org.json.JSONArray("[" + js("JSON.parse(ProbeNative.request('{\"op\":\"status\"}')).codec") + "]").getString(0);
            assertFalse("actual configured codec name", codec.isEmpty());
            JSONObject result = new JSONObject().put("changedPixels",changed).put("sampledPixels",samples)
                .put("nonblackPixels",colorful).put("renderedFramesBeforeStop", live)
                .put("surfacePixelCopy",true).put("stopRelease",true).put("badMediaError",true)
                .put("cordisUnloadReload",true).put("activityStopRelease",true)
                .put("naturalEosRelease",true).put("completedFrames",completed).put("codec",codec).put("untrustedCommandsRejected",true);
            try (FileOutputStream output = new FileOutputStream(new File(activity.getFilesDir(),"probe-evidence/result.json"))) {
                output.write(result.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));
            }
        } finally { getInstrumentation().runOnMainSync(() -> activity.finish()); }
    }
}
