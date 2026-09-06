package com.agentbrowser.probe;

import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.test.InstrumentationTestCase;
import android.view.MotionEvent;
import android.view.PixelCopy;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Shipped Cordis buttons -> JNI -> live Host -> native Surface pixels. */
public class NetworkDeviceTest extends InstrumentationTestCase {
    private MainActivity activity;
    private String js(String script)throws Exception{
        CountDownLatch done=new CountDownLatch(1);AtomicReference<String> result=new AtomicReference<>();
        getInstrumentation().runOnMainSync(()->activity.webView.evaluateJavascript(script,value->{result.set(value);done.countDown();}));
        assertTrue("JS deadline",done.await(3,TimeUnit.SECONDS));return result.get();
    }
    private void until(String script,long timeout)throws Exception{
        long end=SystemClock.elapsedRealtime()+timeout;
        do{assertDisplayedRevision();if("true".equals(js(script)))return;SystemClock.sleep(50);}while(SystemClock.elapsedRealtime()<end);
        fail("Condition: "+script+"\nstatus="+js("JSON.stringify("+STATUS+")")+"\n"+js("document.body.innerText"));
    }
    private void click(String id)throws Exception{js("document.getElementById('"+id+"').click()");}
    private static final String STATUS="JSON.parse(ProbeNative.request('{\"op\":\"status\"}'))";
    private void assertDisplayedRevision() throws Exception {
        assertEquals("Input requires the actually displayed Host revisions", "true", js("(s=>!s.inputReady||(s.documentRevision===s.displayedDocumentRevision&&s.viewportRevision===s.displayedViewportRevision))("+STATUS+")"));
    }
    private JSONObject viewport() throws Exception {
        int[] size=new int[4];
        getInstrumentation().runOnMainSync(()->{
            android.view.View stage=(android.view.View)activity.videoClip.getParent();
            float density=activity.getResources().getDisplayMetrics().density;
            size[0]=Math.round(stage.getWidth()/density);size[1]=Math.round(stage.getHeight()/density);
            size[2]=activity.visibleWidth;size[3]=activity.visibleHeight;
        });
        assertEquals("Host page width matches phone stage",size[0],size[2]);
        assertEquals("Host page height matches phone stage",size[1],size[3]);
        return new JSONObject().put("cssWidth",size[0]).put("cssHeight",size[1]).put("sourceWidth",size[2]).put("sourceHeight",size[3]);
    }
    private void awaitSourceSize(int width,int height) throws Exception {
        long deadline=SystemClock.elapsedRealtime()+6000;
        boolean[] matched={false};
        do {
            assertDisplayedRevision();
            getInstrumentation().runOnMainSync(()->matched[0]=activity.visibleWidth==width&&activity.visibleHeight==height);
            if(matched[0]){until(STATUS+".inputReady",6000);return;}
            SystemClock.sleep(50);
        } while(SystemClock.elapsedRealtime()<deadline);
        fail("Viewport declaration lost: expected "+width+"x"+height+", status="+js("JSON.stringify("+STATUS+")"));
    }
    private void touch(float x,float y)throws Exception{
        until(STATUS+".inputReady",6000);
        float[] location=new float[2];
        getInstrumentation().runOnMainSync(()->{
            int[] origin=new int[2];activity.video.getLocationOnScreen(origin);
            location[0]=origin[0]+x*activity.videoClip.getWidth()/Math.max(1f,activity.visibleWidth);
            location[1]=origin[1]+y*activity.videoClip.getHeight()/Math.max(1f,activity.visibleHeight);
        });
        long now=SystemClock.uptimeMillis();
        MotionEvent down=MotionEvent.obtain(now,now,MotionEvent.ACTION_DOWN,location[0],location[1],0);
        MotionEvent up=MotionEvent.obtain(now,now+10,MotionEvent.ACTION_UP,location[0],location[1],0);
        try{getInstrumentation().sendPointerSync(down);getInstrumentation().sendPointerSync(up);}finally{down.recycle();up.recycle();}
    }
    private Bitmap capture(String name)throws Exception{
        Bitmap full=Bitmap.createBitmap(Math.max(1,activity.video.getWidth()),Math.max(1,activity.video.getHeight()),Bitmap.Config.ARGB_8888);CountDownLatch done=new CountDownLatch(1);int[] result={-1};
        getInstrumentation().runOnMainSync(()->PixelCopy.request(activity.video,full,value->{result[0]=value;done.countDown();},new Handler(Looper.getMainLooper())));
        assertTrue(done.await(3,TimeUnit.SECONDS));assertEquals(PixelCopy.SUCCESS,result[0]);
        Bitmap image=activity.videoClip.getHeight() < full.getHeight() ? Bitmap.createBitmap(full,0,0,full.getWidth(),activity.videoClip.getHeight()) : full;
        File directory=new File(activity.getFilesDir(),"network-evidence");assertTrue(directory.isDirectory()||directory.mkdirs());
        try(var output=new FileOutputStream(new File(directory,name+".png"))){assertTrue(image.compress(Bitmap.CompressFormat.PNG,100,output));}
        return image;
    }
    private int darkInputGlyphs(Bitmap image) {
        int count=0;
        // Interior CSS coordinates exclude the input border and focus outline.
        int left=5*image.getWidth()/activity.visibleWidth,right=195*image.getWidth()/activity.visibleWidth;
        int top=104*image.getHeight()/activity.visibleHeight,bottom=134*image.getHeight()/activity.visibleHeight;
        for(int y=top;y<bottom;y++)for(int x=left;x<right;x++){
            int color=image.getPixel(x,y);
            if(Color.red(color)<80&&Color.green(color)<80&&Color.blue(color)<80)count++;
        }
        return count;
    }
    public void testRealNetworkControlAndFrames()throws Exception{
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try{
            until("!!document.getElementById('connect')",10000);click("connect");
            until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",15000);
            until("document.body.innerText.includes('观察模式')",5000);
            JSONObject viewport=viewport();
            String session=js(STATUS+".sessionId");
            long framesBeforeRejection=activity.annex.snapshot().getLong("renderedFrames");
            getInstrumentation().runOnMainSync(()->activity.network.command(1,Long.MAX_VALUE,0,0,0,0,""));
            until("String("+STATUS+".error).includes('STALE_CONTROL')",6000);
            assertEquals("Host rejection keeps the connection", "\"connected\"",js(STATUS+".connectionState"));
            assertEquals("Host rejection keeps the Session",session,js(STATUS+".sessionId"));
            until(STATUS+".renderedFrames>"+framesBeforeRejection+" && "+STATUS+".inputReady",6000);
            Bitmap before=capture("before");
            assertTrue("Real red Host button",Color.red(before.getPixel(30,30))>160&&Color.green(before.getPixel(30,30))<100);
            touch(30,30);SystemClock.sleep(500);
            Bitmap observer=capture("observer");
            assertTrue("Observer touch cannot mutate Host",Color.red(observer.getPixel(30,30))>160&&Color.green(observer.getPixel(30,30))<100);
            click("takeover");until(STATUS+".controlMode==='control'",6000);
            touch(30,30);
            Bitmap after=null;
            long paintDeadline=SystemClock.elapsedRealtime()+5000;
            do { SystemClock.sleep(150); after=capture("after"); }
            while (Color.green(after.getPixel(30,30))<=160 && SystemClock.elapsedRealtime()<paintDeadline);
            assertTrue("Remote input changes native pixels",Color.green(after.getPixel(30,30))>160&&Color.red(after.getPixel(30,30))<100);
            touch(30,125);SystemClock.sleep(250);
            until(STATUS+".inputReady",6000);
            int emptyGlyphs=darkInputGlyphs(capture("text-before"));
            String accepted=js("(()=>{const s="+STATUS+";return !JSON.parse(ProbeNative.request(JSON.stringify({op:'input_text',epoch:s.epoch,text:'native-network-proof'}))).rejection})()");
            assertEquals("true",accepted);
            until(STATUS+".inputReady",6000);
            long textDeadline=SystemClock.elapsedRealtime()+5000;
            int paintedGlyphs;
            do {SystemClock.sleep(100);paintedGlyphs=darkInputGlyphs(capture("text-after"));}
            while(paintedGlyphs<=emptyGlyphs+30&&SystemClock.elapsedRealtime()<textDeadline);
            assertTrue("Entered text must paint in the native Surface: before="+emptyGlyphs+", after="+paintedGlyphs,
                paintedGlyphs>emptyGlyphs+30);
            int width=viewport.getInt("cssWidth"),height=viewport.getInt("cssHeight");
            // Hold the session monitor so the status command cannot complete
            // before both declarations arrive. Only the latest should survive.
            getInstrumentation().runOnMainSync(()->{
                synchronized(activity.network){
                    activity.network.command(0,0,0,0,0,0,"");
                    activity.network.declareViewport(width-20,height-20,false);
                    activity.network.declareViewport(width-10,height-10,false);
                }
            });
            awaitSourceSize(width-10,height-10);
            getInstrumentation().runOnMainSync(()->activity.network.declareViewport(width,height,false));
            awaitSourceSize(width,height);
            viewport();
            SystemClock.sleep(700);click("release");until("document.body.innerText.includes('观察模式')",6000);
            Bitmap screenshot=getInstrumentation().getUiAutomation().takeScreenshot();assertNotNull(screenshot);
            try(var output=new FileOutputStream(new File(activity.getFilesDir(),"network-evidence/screen.png"))){assertTrue(screenshot.compress(Bitmap.CompressFormat.PNG,100,output));}
            click("disconnect");until(STATUS+".released",6000);
            long frames=activity.annex.snapshot().getLong("renderedFrames");SystemClock.sleep(500);
            assertEquals("No stale decoder callback after disconnect",frames,activity.annex.snapshot().getLong("renderedFrames"));
            click("connect");until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",15000);
            assertEquals("Reconnect preserves live document",session,js(STATUS+".sessionId"));
            Bitmap reconnected=capture("reconnected");assertTrue(Color.green(reconnected.getPixel(30,30))>160&&Color.red(reconnected.getPixel(30,30))<100);
            getInstrumentation().runOnMainSync(()->assertTrue(activity.moveTaskToBack(true)));
            until(STATUS+".released",6000);
            JSONObject result=new JSONObject().put("networkFrames",true).put("observerTouchIgnored",true).put("takeoverPixels",true)
                .put("runId",((android.test.InstrumentationTestRunner)getInstrumentation()).getArguments().getString("runId"))
                .put("viewport",viewport)
                .put("busyViewportCoalesced",true)
                .put("hostRejectionKeepsConnection",true)
                .put("reconnectPreservesDocument",true).put("backgroundRelease",true).put("staleCallbacksFenced",true)
                .put("textSubmitted",true).put("sessionId",new org.json.JSONTokener(session).nextValue());
            result.put("textPainted",true).put("inputGlyphsBefore",emptyGlyphs).put("inputGlyphsAfter",paintedGlyphs);
            result.put("displayedRevisionFenced",true);
            try(var output=new FileOutputStream(new File(activity.getFilesDir(),"network-evidence/result.json"))){output.write(result.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));}
        }finally{getInstrumentation().runOnMainSync(()->activity.finish());}
    }
}
