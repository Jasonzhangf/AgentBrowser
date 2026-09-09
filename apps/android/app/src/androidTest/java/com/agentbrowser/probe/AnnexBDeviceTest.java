package com.agentbrowser.probe;

import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Rect;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.test.InstrumentationTestCase;
import android.view.PixelCopy;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public class AnnexBDeviceTest extends InstrumentationTestCase {
    private MainActivity activity;
    private byte[] fixture(String name) throws Exception {
        try(var input=getInstrumentation().getContext().getAssets().open("annexb/"+name+".h264")) { return input.readAllBytes(); }
    }
    private CompletableFuture<AnnexBDecoder.Receipt> submit(AccessUnit unit) {
        AtomicReference<CompletableFuture<AnnexBDecoder.Receipt>> result=new AtomicReference<>();
        getInstrumentation().runOnMainSync(() -> {
            try { result.set(activity.submitAccessUnit(unit)); }
            catch(Exception error) { result.set(CompletableFuture.failedFuture(error)); }
        });
        return result.get();
    }
    private void rejected(CompletableFuture<?> future,String code) throws Exception {
        try { future.get(5,TimeUnit.SECONDS); fail("expected "+code); }
        catch(java.util.concurrent.ExecutionException error) { assertTrue(error.toString(),error.getCause().toString().contains(code)); }
    }
    private void released() {
        long end=SystemClock.elapsedRealtime()+5000;
        while(!activity.annex.released() && SystemClock.elapsedRealtime()<end) SystemClock.sleep(20);
        assertTrue("codec released",activity.annex.released());
    }
    private void invalid(Runnable action) {
        try { action.run();fail("invalid local input accepted"); }
        catch(IllegalArgumentException expected) { assertNotNull(expected.getMessage()); }
    }
    private Bitmap capture(int width,int height,String name,boolean crop) throws Exception {
        SystemClock.sleep(100);
        Bitmap image;
        if(crop) {
            Rect rect=new Rect();
            getInstrumentation().runOnMainSync(() -> {
                int[] position=new int[2];activity.videoClip.getLocationOnScreen(position);
                rect.set(position[0],position[1],position[0]+activity.videoClip.getWidth(),position[1]+activity.videoClip.getHeight());
            });
            Bitmap screen=getInstrumentation().getUiAutomation().takeScreenshot();
            assertNotNull("composed screenshot",screen);
            assertTrue("right padding absent from composed screen",android.graphics.Color.red(screen.getPixel(rect.right,rect.centerY()))<80);
            assertTrue("bottom padding absent from composed screen",android.graphics.Color.red(screen.getPixel(rect.centerX(),rect.bottom))<250);
            Bitmap clipped=Bitmap.createBitmap(screen,rect.left,rect.top,rect.width(),rect.height());
            image=Bitmap.createScaledBitmap(clipped,width,height,false);
        } else {
        image=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);
        CountDownLatch done=new CountDownLatch(1);
        int[] code={-1};
        getInstrumentation().runOnMainSync(() -> {
            PixelCopy.OnPixelCopyFinishedListener listener=value->{code[0]=value;done.countDown();};
            PixelCopy.request(activity.video,image,listener,new Handler(Looper.getMainLooper()));
        });
        assertTrue(done.await(3,TimeUnit.SECONDS));assertEquals(PixelCopy.SUCCESS,code[0]);
        }
        File dir=new File(activity.getFilesDir(),"annexb-evidence");assertTrue(dir.isDirectory()||dir.mkdirs());
        try(var output=new FileOutputStream(new File(dir,name+".png"))) { assertTrue(image.compress(Bitmap.CompressFormat.PNG,100,output)); }
        return image;
    }
    public void testHostAccessUnits() throws Exception {
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try {
            long ready=SystemClock.elapsedRealtime()+5000;
            while(!activity.video.getHolder().getSurface().isValid() && SystemClock.elapsedRealtime()<ready) SystemClock.sleep(20);
            byte[] before=fixture("before"),after=fixture("after"),resized=fixture("resized");
            invalid(() -> new AccessUnit(new byte[AccessUnit.MAX_BYTES+1],160,120,160,120,0,1));
            invalid(() -> new AccessUnit(before,159,120,159,120,0,1));
            invalid(() -> new AccessUnit(before,160,120,161,120,0,1));
            invalid(() -> new AccessUnit(new byte[]{1,2,3},160,120,160,120,0,1));
            submit(new AccessUnit(before,160,120,160,120,0,1)).get(5,TimeUnit.SECONDS);
            Bitmap first=capture(160,120,"before",false);
            submit(new AccessUnit(after,160,120,160,120,33333,1)).get(5,TimeUnit.SECONDS);
            Bitmap second=capture(160,120,"after",false);
            int changed=0;
            for(int y=0;y<120;y++) for(int x=0;x<160;x++) if(first.getPixel(x,y)!=second.getPixel(x,y)) changed++;
            assertTrue("Host pixels change: "+changed,changed>1000);
            rejected(submit(new AccessUnit(resized,392,846,391,845,66666,1)),"GENERATION");
            submit(new AccessUnit(resized,392,846,391,845,66666,2)).get(5,TimeUnit.SECONDS);
            capture(392,846,"resized-coded",false);
            Bitmap visible=capture(391,845,"resized-visible",true);
            assertEquals(391,visible.getWidth());assertEquals(845,visible.getHeight());
            double ratio=activity.videoClip.getWidth()/(double)activity.videoClip.getHeight();
            assertTrue("visible aspect preserved",Math.abs(ratio-391.0/845)<.002);
            assertTrue("composed content is white",android.graphics.Color.red(visible.getPixel(200,400))>220);
            assertTrue("composed content retains green square",android.graphics.Color.green(visible.getPixel(20,20))>100
                && android.graphics.Color.red(visible.getPixel(20,20))<80);
            assertTrue("visible right edge retains page",android.graphics.Color.red(visible.getPixel(390,400))>220);
            assertTrue("visible bottom edge retains page",android.graphics.Color.red(visible.getPixel(200,844))>220);
            getInstrumentation().runOnMainSync(() -> {
                Rect bounds=new Rect();
                assertTrue(activity.video.getGlobalVisibleRect(bounds));
                assertEquals("visible child width clipped",activity.videoClip.getWidth(),bounds.width());
                assertEquals("visible child height clipped",activity.videoClip.getHeight(),bounds.height());
                assertTrue("coded right padding outside clip",activity.video.getWidth()>bounds.width());
                assertTrue("coded bottom padding outside clip",activity.video.getHeight()>bounds.height());
            });
            rejected(submit(new AccessUnit(after,160,120,160,120,99999,1)),"STALE_DECODER_GENERATION");
            assertEquals(2,activity.annex.snapshot().getLong("generation"));
            assertEquals(1,activity.annex.snapshot().getInt("renderedFrames"));
            // Structurally valid NAL headers, invalid codec contents: must fail decode, not hang or report a frame.
            byte[] broken={0,0,1,0x67,66,0,0,1,0x68,1,0,0,1,0x65,1};
            try { submit(new AccessUnit(broken,160,120,160,120,100000,3)).get(5,TimeUnit.SECONDS);fail("corrupt codec input accepted"); }
            catch(java.util.concurrent.ExecutionException expected) { }
            released();assertEquals("error",activity.annex.snapshot().getString("state"));
            try { submit(new AccessUnit(before,162,120,162,120,100001,4)).get(5,TimeUnit.SECONDS);fail("coded size mismatch accepted"); }
            catch(java.util.concurrent.ExecutionException expected) { assertTrue(expected.toString(),expected.getCause().toString().contains("DIMENSIONS_MISMATCH")); }
            released();
            submit(new AccessUnit(before,160,120,160,120,100002,5)).get(5,TimeUnit.SECONDS);
            getInstrumentation().runOnMainSync(() -> activity.video.setVisibility(android.view.View.INVISIBLE));
            released();
            rejected(submit(new AccessUnit(before,160,120,160,120,100003,6)),"SURFACE_UNAVAILABLE");
            getInstrumentation().runOnMainSync(() -> activity.video.setVisibility(android.view.View.VISIBLE));
            SystemClock.sleep(200);
            submit(new AccessUnit(after,160,120,160,120,100004,6)).get(5,TimeUnit.SECONDS);
            SystemClock.sleep(200);
            assertEquals("no stale callback in new generation",1,activity.annex.snapshot().getInt("renderedFrames"));
            getInstrumentation().runOnMainSync(() -> assertTrue(activity.moveTaskToBack(true)));
            released();
            rejected(submit(new AccessUnit(before,160,120,160,120,100005,7)),"HOST_INACTIVE");
            JSONObject result=new JSONObject().put("changedPixels",changed).put("visibleWidth",391).put("visibleHeight",845)
                .put("codedWidth",392).put("codedHeight",846).put("cropVerified",true).put("generationRejected",true)
                .put("corruptDataRejected",true).put("codedMismatchRejected",true).put("surfaceRelease",true)
                .put("activityRelease",true).put("inputLimitsRejected",true).put("codec",activity.annex.snapshot().getString("codec"));
            try(var output=new FileOutputStream(new File(activity.getFilesDir(),"annexb-evidence/result.json"))) { output.write(result.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8)); }
        } finally { getInstrumentation().runOnMainSync(() -> activity.finish()); }
    }
}
