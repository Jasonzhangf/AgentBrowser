package com.agentbrowser.probe;

import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.test.InstrumentationTestCase;
import android.view.InputDevice;
import android.view.KeyEvent;
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
    private boolean imeVisible;
    private boolean compositionStarted;
    private boolean compositionSendDisabled;
    private boolean compositionCancelled;
    private boolean compositionCommitted;
    private boolean disconnectCompositionCancelled;
    private int chineseInkPixels;
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
    private void navigateFromAddress(String url) throws Exception {
        until("!document.getElementById('address').disabled",6000);
        long previous=Long.parseLong(js(STATUS+".documentRevision"));
        js("(()=>{const input=document.getElementById('address');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,"
            +JSONObject.quote(url)+");input.dispatchEvent(new Event('input',{bubbles:true}));})()");
        until("!document.getElementById('navigate').disabled",6000);
        click("navigate");
        until(STATUS+".documentRevision==="+(previous+1)+" && "+STATUS+".inputReady",15000);
    }
    private void queueNavigationBehindViewport() throws Exception {
        long previousDocument=Long.parseLong(js(STATUS+".documentRevision"));
        long epoch=Long.parseLong(js(STATUS+".epoch"));
        int originalWidth=activity.visibleWidth,originalHeight=activity.visibleHeight;
        int width=Math.max(1,originalWidth-10),height=Math.max(1,originalHeight-10);
        AtomicReference<String> queuedSnapshot=new AtomicReference<>();
        AtomicReference<String> secondFailure=new AtomicReference<>();
        AtomicReference<Exception> dispatchFailure=new AtomicReference<>();
        getInstrumentation().runOnMainSync(()->{
            try{
                // Hold the NetworkSession owner monitor so its internal op=6
                // cannot settle before the UI navigation dispatches.
                synchronized(activity.network){
                    activity.network.declareViewport(width,height,width>height);
                    queuedSnapshot.set(activity.dispatchNetwork(new JSONObject()
                        .put("op","navigate").put("epoch",epoch).put("url","about:blank")));
                    try{
                        activity.dispatchNetwork(new JSONObject()
                            .put("op","navigate").put("epoch",epoch).put("url","about:blank#second"));
                    }catch(IllegalStateException rejected){secondFailure.set(rejected.getMessage());}
                }
            }catch(Exception failure){dispatchFailure.set(failure);}
        });
        if(dispatchFailure.get()!=null)throw dispatchFailure.get();
        String queuedRaw=queuedSnapshot.get();
        assertTrue("Queued response must be a JSON object: "+JSONObject.quote(queuedRaw),queuedRaw.startsWith("{"));
        JSONObject queued=new JSONObject(queuedRaw);
        assertEquals("Queued navigation remains visibly busy","busy",queued.optString("pending"));
        assertFalse("Queued navigation cannot expose input readiness",queued.optBoolean("inputReady"));
        assertEquals("Only one navigation may wait behind a viewport","OPERATION_PENDING",secondFailure.get());
        until(STATUS+".documentRevision==="+(previousDocument+1)+" && "+STATUS+".inputReady",15000);
        JSONObject settled=new JSONObject(js(STATUS));
        assertEquals("Exactly one queued navigation reaches Host",previousDocument+1,settled.getLong("documentRevision"));
        assertEquals("Queued navigation keeps the live Session","connected",settled.getString("connectionState"));
        getInstrumentation().runOnMainSync(()->activity.network.declareViewport(originalWidth,originalHeight,originalWidth>originalHeight));
        awaitSourceSize(originalWidth,originalHeight);
    }
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
    private void awaitInputWindow() throws Exception {
        long deadline=SystemClock.elapsedRealtime()+6000;
        boolean[] ready={false};
        do {
            getInstrumentation().runOnMainSync(()->{
                android.view.View decor=activity.getWindow().getDecorView();
                ready[0]=activity.hasWindowFocus()&&decor.isShown()&&activity.video.isShown()
                    &&activity.video.getWindowToken()!=null&&activity.video.getWidth()>0&&activity.video.getHeight()>0;
            });
            if(ready[0])return;
            SystemClock.sleep(50);
        } while(SystemClock.elapsedRealtime()<deadline);
        fail("Activity Surface window did not become focused for real input");
    }
    private void injectPointer(MotionEvent event) {
        if((event.getSource()&InputDevice.SOURCE_CLASS_POINTER)==0)event.setSource(InputDevice.SOURCE_TOUCHSCREEN);
        // Instrumentation.sendPointerSync is targeted at the instrumentation UID on Android 16.
        // UiAutomation is the supported cross-window test ingress; focus and geometry are checked
        // by awaitInputWindow and the caller before any event is sent.
        assertTrue("UiAutomation input injection",getInstrumentation().getUiAutomation().injectInputEvent(event,true));
    }
    private void touch(float x,float y)throws Exception{
        until(STATUS+".inputReady",6000);
        awaitInputWindow();
        float[] location=new float[2];
        getInstrumentation().runOnMainSync(()->{
            int[] origin=new int[2];activity.video.getLocationOnScreen(origin);
            location[0]=origin[0]+x*activity.videoClip.getWidth()/Math.max(1f,activity.visibleWidth);
            location[1]=origin[1]+y*activity.videoClip.getHeight()/Math.max(1f,activity.visibleHeight);
        });
        long now=SystemClock.uptimeMillis();
        MotionEvent down=MotionEvent.obtain(now,now,MotionEvent.ACTION_DOWN,location[0],location[1],0);
        MotionEvent up=MotionEvent.obtain(now,now+10,MotionEvent.ACTION_UP,location[0],location[1],0);
        try{injectPointer(down);injectPointer(up);}finally{down.recycle();up.recycle();}
    }
    private void swipe(float x,float fromY,float toY)throws Exception{
        until(STATUS+".inputReady",6000);
        awaitInputWindow();
        float[] location=new float[3];
        getInstrumentation().runOnMainSync(()->{
            int[] origin=new int[2];activity.video.getLocationOnScreen(origin);
            float sx=activity.videoClip.getWidth()/(float)activity.visibleWidth;
            float sy=activity.videoClip.getHeight()/(float)activity.visibleHeight;
            location[0]=origin[0]+x*sx;location[1]=origin[1]+fromY*sy;location[2]=origin[1]+toY*sy;
        });
        long time=SystemClock.uptimeMillis();
        MotionEvent down=MotionEvent.obtain(time,time,MotionEvent.ACTION_DOWN,location[0],location[1],0);
        MotionEvent move=MotionEvent.obtain(time,time+50,MotionEvent.ACTION_MOVE,location[0],location[2],0);
        MotionEvent up=MotionEvent.obtain(time,time+100,MotionEvent.ACTION_UP,location[0],location[2],0);
        try{injectPointer(down);injectPointer(move);injectPointer(up);}
        finally{down.recycle();move.recycle();up.recycle();}
    }
    private void interruptedTouch(boolean resize)throws Exception{
        until(STATUS+".inputReady",6000);
        int width=activity.visibleWidth,height=activity.visibleHeight;
        long time=SystemClock.uptimeMillis();
        getInstrumentation().runOnMainSync(()->{
            MotionEvent down=MotionEvent.obtain(time,time,MotionEvent.ACTION_DOWN,
                30f*activity.videoClip.getWidth()/width,30f*activity.videoClip.getHeight()/height,0);
            try{activity.video.dispatchTouchEvent(down);}finally{down.recycle();}
        });
        if(resize){
            getInstrumentation().runOnMainSync(()->activity.network.declareViewport(width-10,height-10,false));
            awaitSourceSize(width-10,height-10);
            getInstrumentation().runOnMainSync(()->activity.network.declareViewport(width,height,false));
            awaitSourceSize(width,height);
        }else{
            getInstrumentation().runOnMainSync(()->{
                MotionEvent cancel=MotionEvent.obtain(time,SystemClock.uptimeMillis(),MotionEvent.ACTION_CANCEL,30,30,0);
                try{activity.video.dispatchTouchEvent(cancel);}finally{cancel.recycle();}
            });
        }
        getInstrumentation().runOnMainSync(()->{
            MotionEvent up=MotionEvent.obtain(time,SystemClock.uptimeMillis(),MotionEvent.ACTION_UP,
                30f*activity.videoClip.getWidth()/width,30f*activity.videoClip.getHeight()/height,0);
            try{activity.video.dispatchTouchEvent(up);}finally{up.recycle();}
        });
        until(STATUS+".inputReady",6000);
        SystemClock.sleep(300);
        Bitmap unchanged=capture(resize?"stale-gesture":"cancelled-gesture");
        assertTrue("Interrupted gesture must not click: resize="+resize,
            Color.red(unchanged.getPixel(30,30))>160&&Color.green(unchanged.getPixel(30,30))<100);
    }
    private void rotate(boolean landscape)throws Exception{
        String session=js(STATUS+".sessionId"),document=js(STATUS+".documentRevision");
        getInstrumentation().runOnMainSync(()->activity.setRequestedOrientation(landscape
            ?android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
            :android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT));
        long deadline=SystemClock.elapsedRealtime()+8000;
        boolean[] matched={false},destroyed={false};
        do{
            getInstrumentation().runOnMainSync(()->{
                destroyed[0]=activity.isDestroyed();
                android.view.View stage=(android.view.View)activity.videoClip.getParent();
                boolean orientation=activity.getResources().getConfiguration().orientation==android.content.res.Configuration.ORIENTATION_LANDSCAPE;
                float density=activity.getResources().getDisplayMetrics().density;
                matched[0]=orientation==landscape&&(stage.getWidth()>stage.getHeight())==landscape
                    &&activity.visibleWidth==Math.round(stage.getWidth()/density)
                    &&activity.visibleHeight==Math.round(stage.getHeight()/density);
            });
            assertFalse("Rotation must retain the Activity and its connection",destroyed[0]);
            if(matched[0])break;
            SystemClock.sleep(50);
        }while(SystemClock.elapsedRealtime()<deadline);
        assertTrue("Rotation must negotiate the actual phone area",matched[0]);
        until(STATUS+".inputReady",6000);
        assertEquals("Rotation preserves Session",session,js(STATUS+".sessionId"));
        assertEquals("Rotation preserves document",document,js(STATUS+".documentRevision"));
        assertEquals("Rotation preserves takeover","\"control\"",js(STATUS+".controlMode"));
        assertTrue("Rotation preserves visible input",darkInputGlyphs(capture(landscape?"landscape":"portrait-restored"))>30);
    }
    private void imeEdit(android.view.inputmethod.InputConnection input,
            java.util.function.Consumer<android.view.inputmethod.InputConnection> edit)throws Exception{
        Handler handler=input.getHandler();
        assertNotNull("WebView IME handler",handler);
        CountDownLatch done=new CountDownLatch(1);AtomicReference<Throwable> failure=new AtomicReference<>();
        handler.post(()->{try{edit.accept(input);}catch(Throwable error){failure.set(error);}finally{done.countDown();}});
        assertTrue("IME edit deadline",done.await(3,TimeUnit.SECONDS));
        if(failure.get()!=null)throw new AssertionError("IME edit failed",failure.get());
    }
    private void keyboardArea()throws Exception{
        int width=activity.visibleWidth,height=activity.visibleHeight;
        String session=js(STATUS+".sessionId"),document=js(STATUS+".documentRevision");
        js("document.getElementById('input-text').focus()");
        getInstrumentation().runOnMainSync(()->{
            activity.webView.requestFocus();
            activity.getSystemService(android.view.inputmethod.InputMethodManager.class)
                .showSoftInput(activity.webView,android.view.inputmethod.InputMethodManager.SHOW_IMPLICIT);
        });
        boolean[] visible={false},fits={false};
        long deadline=SystemClock.elapsedRealtime()+8000;
        do{
            getInstrumentation().runOnMainSync(()->{
                android.view.WindowInsets insets=activity.webView.getRootWindowInsets();
                visible[0]=insets!=null&&insets.isVisible(android.view.WindowInsets.Type.ime());
                if(visible[0]){
                    int[] position=new int[2];activity.webView.getLocationOnScreen(position);
                    int keyboardTop=activity.getWindow().getDecorView().getHeight()-insets.getInsets(android.view.WindowInsets.Type.ime()).bottom;
                    fits[0]=position[1]+activity.webView.getHeight()<=keyboardTop&&activity.visibleHeight<height;
                }
            });
            if(visible[0]&&fits[0])break;
            SystemClock.sleep(50);
        }while(SystemClock.elapsedRealtime()<deadline);
        assertTrue("Real IME must be visible",visible[0]);
        assertTrue("Keyboard must leave page and text controls visible",fits[0]);
        imeVisible=visible[0];
        until(STATUS+".inputReady",6000);
        viewport();
        assertEquals(session,js(STATUS+".sessionId"));assertEquals(document,js(STATUS+".documentRevision"));
        AtomicReference<android.view.inputmethod.InputConnection> input=new AtomicReference<>();
        getInstrumentation().runOnMainSync(()->input.set(activity.webView.onCreateInputConnection(new android.view.inputmethod.EditorInfo())));
        assertNotNull("Focused WebView input connection",input.get());
        imeEdit(input.get(),connection->{assertTrue(connection.beginBatchEdit());assertTrue(connection.setComposingText("zhongwen",1));});
        until("document.getElementById('input-text').value==='zhongwen' && document.getElementById('input-text').parentElement.dataset.composition==='composing'",5000);
        compositionStarted=true;
        compositionSendDisabled="true".equals(js("document.getElementById('send-text').disabled"));
        assertTrue("Do not send unfinished composition",compositionSendDisabled);
        click("send-text");
        assertEquals("true",js("document.getElementById('input-text').value==='zhongwen'"));
        imeEdit(input.get(),connection->{
            long now=SystemClock.uptimeMillis();
            assertTrue(connection.sendKeyEvent(new KeyEvent(now,now,KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_ESCAPE,0)));
            assertTrue(connection.sendKeyEvent(new KeyEvent(now,now+10,KeyEvent.ACTION_UP,KeyEvent.KEYCODE_ESCAPE,0)));
            connection.endBatchEdit();
        });
        until("document.getElementById('input-text').value==='' && document.getElementById('input-text').parentElement.dataset.composition==='cancelled'",5000);
        compositionCancelled=true;
        assertEquals("true",js("document.getElementById('input-text').parentElement.dataset.compositionCancelled==='true'"));
        js("document.getElementById('input-text').focus()");
        getInstrumentation().runOnMainSync(()->{
            activity.webView.requestFocus();
            activity.getSystemService(android.view.inputmethod.InputMethodManager.class)
                .showSoftInput(activity.webView,android.view.inputmethod.InputMethodManager.SHOW_IMPLICIT);
        });
        AtomicReference<android.view.inputmethod.InputConnection> committedInput=new AtomicReference<>();
        getInstrumentation().runOnMainSync(()->committedInput.set(activity.webView.onCreateInputConnection(new android.view.inputmethod.EditorInfo())));
        assertNotNull("Focused WebView input connection after composition cancel",committedInput.get());
        imeEdit(committedInput.get(),connection->{
            assertTrue(connection.beginBatchEdit());
            assertTrue(connection.setComposingText("zhongwen",1));
            assertTrue(connection.commitText("中文",1));
            assertTrue(connection.finishComposingText());
            connection.endBatchEdit();
        });
        until("document.getElementById('input-text').value==='中文' && document.getElementById('input-text').parentElement.dataset.composition==='committed' && !document.getElementById('send-text').disabled",5000);
        compositionCommitted=true;
        until(STATUS+".inputReady && !document.getElementById('send-text').disabled",6000);
        assertEquals("Committed text must be dispatched through an enabled UI button", "true",
            js("(()=>{const button=document.getElementById('send-text');if(button.disabled)return false;button.click();return true})()"));
        until(STATUS+".inputReady",6000);
        long paintDeadline=SystemClock.elapsedRealtime()+5000;
        do{
            SystemClock.sleep(100);
            Bitmap painted=capture("ime-text");chineseInkPixels=0;
            // The Host field's CJK suffix is sampled after the fixed Latin prefix;
            // the broad vertical window tolerates keyboard-induced page scrolling.
            int left=112*painted.getWidth()/activity.visibleWidth,right=140*painted.getWidth()/activity.visibleWidth;
            int top=95*painted.getHeight()/activity.visibleHeight,bottom=130*painted.getHeight()/activity.visibleHeight;
            for(int y=top;y<bottom;y++)for(int x=left;x<right;x++){
                int color=painted.getPixel(x,y);
                if(Color.red(color)<180&&Color.green(color)<180&&Color.blue(color)<180)chineseInkPixels++;
            }
        }while(chineseInkPixels<=30&&SystemClock.elapsedRealtime()<paintDeadline);
        assertTrue("Chinese text must paint in the CJK glyph region: "+chineseInkPixels
            +"\ninput="+js("JSON.stringify({text:document.getElementById('input-text').value,phase:document.getElementById('input-text').parentElement.dataset.composition,disabled:document.getElementById('send-text').disabled,error:document.querySelector('[role=alert]')?.textContent})")
            +"\nstatus="+js(STATUS),chineseInkPixels>30);
        getInstrumentation().runOnMainSync(()->activity.getWindow().getInsetsController().hide(android.view.WindowInsets.Type.ime()));
        js("document.getElementById('input-text').blur()");
        awaitSourceSize(width,height);
    }
    public void testImeCompositionCancel()throws Exception{
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try{
            until("!!document.getElementById('connect')",10000);
            click("connect");
            until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",15000);
            click("takeover");
            until(STATUS+".controlMode==='control' && "+STATUS+".inputReady",6000);
            until("!document.getElementById('input-text').disabled",5000);
            js("window.__imeEvents=[];const input=document.getElementById('input-text');for(const type of ['keydown','beforeinput','input','compositionstart','compositionupdate','compositionend','keyup'])input.addEventListener(type,event=>window.__imeEvents.push({type,key:event.key||'',inputType:event.inputType||'',data:event.data||'',value:input.value}),{capture:true})");
            js("document.getElementById('input-text').focus()");
            getInstrumentation().runOnMainSync(()->{
                activity.webView.requestFocus();
                activity.getSystemService(android.view.inputmethod.InputMethodManager.class)
                    .showSoftInput(activity.webView,android.view.inputmethod.InputMethodManager.SHOW_IMPLICIT);
            });
            int width=activity.visibleWidth,height=activity.visibleHeight;
            boolean[] visible={false},fits={false};
            long deadline=SystemClock.elapsedRealtime()+8000;
            do{
                getInstrumentation().runOnMainSync(()->{
                    android.view.WindowInsets insets=activity.webView.getRootWindowInsets();
                    visible[0]=insets!=null&&insets.isVisible(android.view.WindowInsets.Type.ime());
                    if(visible[0]){
                        int[] position=new int[2];activity.webView.getLocationOnScreen(position);
                        int keyboardTop=activity.getWindow().getDecorView().getHeight()-insets.getInsets(android.view.WindowInsets.Type.ime()).bottom;
                        fits[0]=position[1]+activity.webView.getHeight()<=keyboardTop&&activity.visibleHeight<height;
                    }
                });
                if(visible[0]&&fits[0])break;
                SystemClock.sleep(50);
            }while(SystemClock.elapsedRealtime()<deadline);
            assertTrue("Real IME must be visible in focused regression",visible[0]);
            assertTrue("Keyboard must leave page and text controls visible in focused regression",fits[0]);
            until(STATUS+".inputReady",6000);
            viewport();
            until("document.activeElement===document.getElementById('input-text') && !document.getElementById('input-text').disabled && document.getElementById('input-text').parentElement.dataset.composition!=='cancelled'",5000);
            AtomicReference<android.view.inputmethod.InputConnection> input=new AtomicReference<>();
            getInstrumentation().runOnMainSync(()->input.set(activity.webView.onCreateInputConnection(new android.view.inputmethod.EditorInfo())));
            assertNotNull("Focused WebView input connection",input.get());
            imeEdit(input.get(),connection->{assertTrue(connection.beginBatchEdit());assertTrue(connection.setComposingText("zhongwen",1));});
            imeEdit(input.get(),connection->{
                long now=SystemClock.uptimeMillis();
                assertTrue(connection.sendKeyEvent(new KeyEvent(now,now,KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_ESCAPE,0)));
                assertTrue(connection.sendKeyEvent(new KeyEvent(now,now+10,KeyEvent.ACTION_UP,KeyEvent.KEYCODE_ESCAPE,0)));
                connection.endBatchEdit();
            });
            String events=js("JSON.stringify(window.__imeEvents)");
            assertEquals("Raw WebView Escape must cancel composition: "+events,
                "true", js("document.getElementById('input-text').value==='' && document.getElementById('input-text').parentElement.dataset.composition==='cancelled'"));
            assertEquals("Raw WebView Escape must reach the DOM event path: "+events,
                "true", js("window.__imeEvents.some(event=>event.key==='Escape')"));
        }finally{getInstrumentation().runOnMainSync(()->activity.finish());}
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
            assertEquals("Observer cannot navigate from the UI","true",js("document.getElementById('address').disabled && document.getElementById('navigate').disabled"));
            String initialUrl=new String(android.util.Base64.decode(
                ((android.test.InstrumentationTestRunner)getInstrumentation()).getArguments().getString("initialUrlBase64"),
                android.util.Base64.DEFAULT),java.nio.charset.StandardCharsets.UTF_8);
            String navigationSession=js(STATUS+".sessionId");
            click("takeover");until(STATUS+".controlMode==='control' && "+STATUS+".inputReady",6000);
            queueNavigationBehindViewport();
            navigateFromAddress("about:blank");
            navigateFromAddress(initialUrl);
            assertEquals("Navigation retains the Host Session",navigationSession,js(STATUS+".sessionId"));
            until("!document.getElementById('release').disabled",5000);
            click("release");until(STATUS+".controlMode==='observe'",6000);
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
            interruptedTouch(false);
            interruptedTouch(true);
            until(STATUS+".inputReady",6000);
            swipe(30,20,80);
            until(STATUS+".inputReady",6000);
            SystemClock.sleep(500);
            Bitmap swiped=capture("swipe-no-click");
            assertTrue("A swipe must not click the button under its release point",
                Color.red(swiped.getPixel(30,30))>160&&Color.green(swiped.getPixel(30,30))<100);
            touch(30,30);
            Bitmap after=null;
            long paintDeadline=SystemClock.elapsedRealtime()+5000;
            do { SystemClock.sleep(150); after=capture("after"); }
            while (Color.green(after.getPixel(30,30))<=160 && SystemClock.elapsedRealtime()<paintDeadline);
            assertTrue("Remote input changes native pixels",Color.green(after.getPixel(30,30))>160&&Color.red(after.getPixel(30,30))<100);
            swipe(30,350,100);
            long scrollDeadline=SystemClock.elapsedRealtime()+5000;
            Bitmap scrolled;
            do {SystemClock.sleep(100);scrolled=capture("scrolled");}
            while(Color.blue(scrolled.getPixel(30,30))<160&&SystemClock.elapsedRealtime()<scrollDeadline);
            assertTrue("Swipe exposes lower blue page content",Color.blue(scrolled.getPixel(30,30))>160
                &&Color.red(scrolled.getPixel(30,30))<100&&Color.green(scrolled.getPixel(30,30))<100);
            swipe(30,100,350);
            long restoreDeadline=SystemClock.elapsedRealtime()+5000;
            Bitmap restored;
            do {SystemClock.sleep(100);restored=capture("scroll-restored");}
            while(Color.green(restored.getPixel(30,30))<160&&SystemClock.elapsedRealtime()<restoreDeadline);
            assertTrue("Reverse swipe restores the same changed button",Color.green(restored.getPixel(30,30))>160
                &&Color.red(restored.getPixel(30,30))<100);
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
            rotate(true);
            JSONObject landscapeViewport=viewport();
            rotate(false);
            JSONObject portraitViewport=viewport();
            keyboardArea();
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
            click("takeover");until(STATUS+".controlMode==='control' && "+STATUS+".inputReady",6000);
            until("!document.getElementById('input-text').disabled",5000);
            js("document.getElementById('input-text').focus()");
            until("document.activeElement===document.getElementById('input-text')",5000);
            AtomicReference<android.view.inputmethod.InputConnection> disconnectInput=new AtomicReference<>();
            getInstrumentation().runOnMainSync(()->{
                activity.webView.requestFocus();
                activity.getSystemService(android.view.inputmethod.InputMethodManager.class)
                    .showSoftInput(activity.webView,android.view.inputmethod.InputMethodManager.SHOW_IMPLICIT);
                disconnectInput.set(activity.webView.onCreateInputConnection(new android.view.inputmethod.EditorInfo()));
            });
            assertNotNull("Focused WebView input connection before disconnect",disconnectInput.get());
            imeEdit(disconnectInput.get(),connection->{assertTrue(connection.beginBatchEdit());assertTrue(connection.setComposingText("disconnect-me",1));});
            until("document.getElementById('input-text').value==='disconnect-me' && document.getElementById('input-text').parentElement.dataset.composition==='composing'",5000);
            assertEquals("true",js("document.getElementById('send-text').disabled"));
            // Close the IME batch while the WebView connection is alive; the
            // disconnect immediately afterwards owns composition cancellation.
            imeEdit(disconnectInput.get(),connection->connection.endBatchEdit());
            click("disconnect");
            until(STATUS+".released",6000);
            until("document.getElementById('input-text').value==='' && document.getElementById('input-text').parentElement.dataset.composition==='cancelled'",5000);
            disconnectCompositionCancelled=true;
            long frames=activity.annex.snapshot().getLong("renderedFrames");SystemClock.sleep(500);
            assertEquals("No stale decoder callback after disconnect",frames,activity.annex.snapshot().getLong("renderedFrames"));
            click("connect");until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",15000);
            assertEquals("Reconnect preserves live document",session,js(STATUS+".sessionId"));
            JSONObject statusEvidence=new JSONObject(js(STATUS));
            Bitmap reconnected=capture("reconnected");assertTrue(Color.green(reconnected.getPixel(30,30))>160&&Color.red(reconnected.getPixel(30,30))<100);
            // Disconnecting the human controller suspends Agent control. Restore
            // it only through the real explicit takeover/release UI protocol.
            click("takeover");until(STATUS+".controlMode==='control' && "+STATUS+".inputReady",6000);
            until("!document.getElementById('release').disabled",5000);
            click("release");until(STATUS+".controlMode==='observe'",6000);
            getInstrumentation().runOnMainSync(()->assertTrue(activity.moveTaskToBack(true)));
            until(STATUS+".released",6000);
            JSONObject result=new JSONObject().put("networkFrames",true).put("observerTouchIgnored",true).put("takeoverPixels",true).put("addressNavigation",true)
                .put("runId",((android.test.InstrumentationTestRunner)getInstrumentation()).getArguments().getString("runId"))
                .put("viewport",viewport)
                .put("statusEvidence",statusEvidence)
                .put("sourceDimensions",new JSONObject().put("codedWidth",statusEvidence.optInt("displayedCodedWidth",-1)).put("codedHeight",statusEvidence.optInt("displayedCodedHeight",-1)))
                .put("frameAck",new JSONObject().put("ticket",statusEvidence.optLong("displayedTicket",-1)).put("renderedFrames",statusEvidence.optInt("renderedFrames",-1)).put("displayed",true))
                .put("operationReceipts",new org.json.JSONArray().put(new JSONObject().put("operation","observe").put("result","succeeded")).put(new JSONObject().put("operation","takeover").put("result","succeeded")).put(new JSONObject().put("operation","release").put("result","succeeded")))
                .put("busyViewportCoalesced",true)
                .put("hostRejectionKeepsConnection",true)
                .put("reconnectPreservesDocument",true).put("backgroundRelease",true).put("staleCallbacksFenced",true)
                .put("textSubmitted",true).put("sessionId",new org.json.JSONTokener(session).nextValue());
            result.put("textPainted",true).put("inputGlyphsBefore",emptyGlyphs).put("inputGlyphsAfter",paintedGlyphs);
            result.put("keyboardVisible",imeVisible).put("compositionStarted",compositionStarted)
                .put("compositionSendDisabled",compositionSendDisabled).put("compositionCancelled",compositionCancelled)
                .put("compositionCommitted",compositionCommitted).put("unfinishedCompositionDropped",compositionCancelled&&compositionSendDisabled)
                .put("disconnectCompositionCancelled",disconnectCompositionCancelled)
                .put("imeTextEvidence",new File(activity.getFilesDir(),"network-evidence/ime-text.png").isFile())
                .put("imeTextPainted",chineseInkPixels>30).put("chineseInkPixels",chineseInkPixels);
            result.put("displayedRevisionFenced",true);
            result.put("swipeDoesNotClick",true).put("scrollPixels",true);
            result.put("cancelledGestureIgnored",true).put("staleViewportGestureIgnored",true);
            result.put("rotationPreservesState",true);
            result.put("landscapeViewport",landscapeViewport).put("portraitViewport",portraitViewport);
            try(var output=new FileOutputStream(new File(activity.getFilesDir(),"network-evidence/result.json"))){output.write(result.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));}
        }finally{getInstrumentation().runOnMainSync(()->activity.finish());}
    }
}
