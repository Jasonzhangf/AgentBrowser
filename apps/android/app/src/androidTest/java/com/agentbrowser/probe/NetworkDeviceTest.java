package com.agentbrowser.probe;

import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.test.InstrumentationTestCase;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.PixelCopy;
import android.view.WindowInsets;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.InputMethodManager;
import androidx.test.uiautomator.By;
import androidx.test.uiautomator.UiDevice;
import androidx.test.uiautomator.UiObject2;
import androidx.test.uiautomator.Until;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.util.concurrent.Callable;
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
    private boolean unfinishedCompositionDropped;
    private boolean disconnectCompositionReleased;
    private boolean reconnectCompositionAccepted;
    private boolean disconnectCompositionCancelled;
    private String lastPixelPoll="";
    private int chineseInkPixels;
    private int chineseInkAdded;
    private String js(String script)throws Exception{
        CountDownLatch done=new CountDownLatch(1);AtomicReference<String> result=new AtomicReference<>();
        getInstrumentation().runOnMainSync(()->activity.webView.evaluateJavascript(script,value->{result.set(value);done.countDown();}));
        assertTrue("JS deadline",done.await(3,TimeUnit.SECONDS));return result.get();
    }
    private void until(String script,long timeout)throws Exception{until(script,timeout,null);}
    /** `note` carries the ingress state that explains a timeout in the failure itself. */
    private void until(String script,long timeout,String note)throws Exception{
        long end=SystemClock.elapsedRealtime()+timeout;
        do{assertDisplayedRevision();if("true".equals(js(script)))return;SystemClock.sleep(50);}while(SystemClock.elapsedRealtime()<end);
        fail("Condition: "+script+(note==null?"":"\nnote="+note)+"\nstatus="+js("JSON.stringify("+STATUS+")")+"\n"+js("document.body.innerText"));
    }
    private void click(String id)throws Exception{js("document.getElementById('"+id+"').click()");}
    /** A disabled chrome control ignores the click, so wait for the UI projection. */
    private void clickEnabled(String id)throws Exception{
        until("(()=>{const control=document.getElementById('"+id+"');return !!control&&!control.disabled;})()",8000);
        click(id);
    }
    private void navigateFromAddress(String url) throws Exception {
        until("!document.getElementById('address').disabled",6000);
        long previous=Long.parseLong(js(STATUS+".documentRevision"));
        // The chrome submits navigation through its address form; the real user
        // path is the native value setter, an input event, then form submit.
        js("(()=>{const input=document.getElementById('address');"
            +"Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,"
            +JSONObject.quote(url)+");input.dispatchEvent(new Event('input',{bubbles:true}));"
            +"input.form.requestSubmit();})()");
        until(STATUS+".documentRevision==="+(previous+1)+" && "+STATUS+".inputReady",15000);
    }
    /** The observer projection disables every navigation control. */
    private static final String UI_NAV_DISABLED=
        "['address','reload','back','forward'].every(id=>document.getElementById(id).disabled)";
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
    /** The chrome's own control-mode projection, independent of label wording. */
    private static final String UI_MODE="document.querySelector('[data-control-mode]').dataset.controlMode";
    private void assertDisplayedRevision() throws Exception {
        assertEquals("Input requires the actually displayed Host revisions", "true", js("(s=>!s.inputReady||(s.documentRevision===s.displayedDocumentRevision&&s.viewportRevision===s.displayedViewportRevision))("+STATUS+")"));
    }
    private JSONObject viewport() throws Exception {
        int[] size=new int[4];
        boolean[] ime={false};
        getInstrumentation().runOnMainSync(()->{
            android.view.View stage=(android.view.View)activity.videoClip.getParent();
            float density=activity.getResources().getDisplayMetrics().density;
            size[0]=Math.round(stage.getWidth()/density);size[1]=Math.round(stage.getHeight()/density);
            size[2]=activity.visibleWidth;size[3]=activity.visibleHeight;
            WindowInsets insets=activity.remoteIme.getRootWindowInsets();
            ime[0]=insets!=null&&insets.isVisible(WindowInsets.Type.ime());
        });
        // The stage is the presented page area. It keeps the Host page width and never
        // exceeds the Host page, and it equals the Host page whenever the keyboard is
        // not up: the keyboard shrinks only the presented area, never the source.
        assertEquals("Host page width matches phone stage",size[0],size[2]);
        assertTrue("Presented page must not exceed the Host page: stage="+size[1]+", source="+size[3],
            size[1]<=size[3]);
        if(!ime[0])assertEquals("Host page height matches phone stage",size[1],size[3]);
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
        String entryError=js("JSON.stringify("+STATUS+".error)");
        String entryState=localGestureState();
        getInstrumentation().runOnMainSync(()->{
            MotionEvent down=MotionEvent.obtain(time,time,MotionEvent.ACTION_DOWN,
                30f*activity.videoClip.getWidth()/width,30f*activity.videoClip.getHeight()/height,0);
            try{activity.video.dispatchTouchEvent(down);}finally{down.recycle();}
        });
        // The press opens one Host gesture lease, and the session polls that lease
        // before any interruption arrives. A real interruption never races the
        // press, so the lease must be visible here, not merely requested.
        until("!"+STATUS+".inputReady",3000);
        String pressError=js("JSON.stringify("+STATUS+".error)");
        String pressState=localGestureState();
        assertEquals("The interrupted press must open a Host gesture lease: "+js("JSON.stringify("+STATUS+")")
            +"\n"+touchGeometry()+"\nentryState="+entryState+" pressState="+pressState,entryError,pressError);
        if(resize){
            // A viewport declared under this press is deferred until the press ends, so
            // the operation keeps one fencing and its terminal event stays valid.
            getInstrumentation().runOnMainSync(()->activity.network.declareViewport(width-10,height-10,false));
            SystemClock.sleep(300);
            int[] observed=new int[2];
            getInstrumentation().runOnMainSync(()->{observed[0]=activity.visibleWidth;observed[1]=activity.visibleHeight;});
            assertEquals("A viewport declared under an open gesture must wait for it: expected "
                +width+"x"+height+", source="+observed[0]+"x"+observed[1],
                width+"x"+height,observed[0]+"x"+observed[1]);
        }else{
            getInstrumentation().runOnMainSync(()->{
                MotionEvent cancel=MotionEvent.obtain(time,SystemClock.uptimeMillis(),MotionEvent.ACTION_CANCEL,30,30,0);
                try{activity.video.dispatchTouchEvent(cancel);}finally{cancel.recycle();}
            });
        }
        String interruptionError=js("JSON.stringify("+STATUS+".error)");
        getInstrumentation().runOnMainSync(()->{
            MotionEvent up=MotionEvent.obtain(time,SystemClock.uptimeMillis(),MotionEvent.ACTION_UP,
                30f*activity.videoClip.getWidth()/width,30f*activity.videoClip.getHeight()/height,0);
            try{activity.video.dispatchTouchEvent(up);}finally{up.recycle();}
        });
        until(STATUS+".inputReady",6000,"resize="+resize+" entryError="+entryError+" pressError="+pressError
            +" interruptionError="+interruptionError+" hostFenced="+hostLeaseFencesAgents()
            +"\nentryState="+entryState+"\npressState="+pressState+"\ncancelState="+localGestureState()+"\n"+touchGeometry());
        if(resize){
            // The press ended, so the deferred declaration applies now.
            awaitSourceSize(width-10,height-10);
            getInstrumentation().runOnMainSync(()->activity.network.declareViewport(width,height,false));
            awaitSourceSize(width,height);
        }
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
        Bitmap rotatedCapture=capture(landscape?"landscape":"portrait-restored");
         String bounds=darkBounds(rotatedCapture);
         int darkPixels=darkPixelCount(rotatedCapture);
         String layout=js("JSON.stringify("+STATUS+".layout)");
         appendEvidence("rotation-visual.txt","orientation="+(landscape?"landscape":"portrait")+" css="+activity.visibleWidth+"x"+activity.visibleHeight+" source="+rotatedCapture.getWidth()+"x"+rotatedCapture.getHeight()+" bounds="+bounds+" darkPixels="+darkPixels+" layout="+layout+"\n");
         assertTrue("Rotation preserves visible input: nonblank dark pixels="+darkPixels+" bounds="+bounds+" layout="+layout,darkPixels>10);
    }
    /** Drive the real launcher/picker/import path. No app test hooks or private staging. */
    private void importOrdinaryPairing() throws Exception {
        UiDevice device=UiDevice.getInstance(getInstrumentation());
        device.pressHome();
        device.executeShellCommand("am start -n com.agentbrowser.probe/.MainActivity");
        assertTrue("MainActivity",device.wait(Until.hasObject(By.pkg("com.agentbrowser.probe")),10000));
        // WebView content is not exposed as child accessibility nodes on this
        // device. Read each control's live DOM rect and map it through the
        // native WebView bounds; no device-specific coordinates or OCR.
        touchChromeVerified("drawer-toggle",()->"true".equals(js("document.querySelector('main').dataset.drawerOpen==='true'")));
        until("document.querySelector('main').dataset.drawerOpen==='true'",6000);
        touchChromeVerified("connection-import",()->UiDevice.getInstance(getInstrumentation()).hasObject(By.pkg("com.google.android.documentsui")));
        assertTrue("DocumentsUI",device.wait(Until.hasObject(By.pkg("com.google.android.documentsui")),10000));
        UiObject2 download=device.wait(Until.findObject(By.text("Download")),10000);
        if(download!=null) download.click();
        UiObject2 file=device.wait(Until.findObject(By.text("pairing.abconnection")),15000);
        assertNotNull("DocumentsUI pairing file",file); file.click();
        assertTrue("Return to app",device.wait(Until.hasObject(By.pkg("com.agentbrowser.probe")),10000));
        long deadline=SystemClock.elapsedRealtime()+15000;
        while(SystemClock.elapsedRealtime()<deadline){
            if("configured".equals(activity.connectionSetup.projection().optString("state"))){
                assertPairingMaterials(); return;
            }
            SystemClock.sleep(100);
        }
        fail("Ordinary import did not configure: "+activity.connectionSetup.projection());
    }

    /**
     * The frozen minimum contract writes exactly two files after an import:
     * `endpoint.txt` and `token`. A configured projection that published
     * neither would be a success claim without the necessary connection
     * configuration behind it.
     */
    private void assertPairingMaterials()throws Exception{
        assertTrue("Imported pairing must publish endpoint.txt: "+pairingMaterial("endpoint.txt").getAbsolutePath(),
            pairingMaterial("endpoint.txt").isFile());
        assertTrue("Imported pairing must publish token: "+pairingMaterial("token").getAbsolutePath(),
            pairingMaterial("token").isFile());
        assertTrue("Imported pairing must publish a non-empty endpoint",
            !admittedEndpoint().isEmpty());
    }
    private File pairingMaterial(String name){
        return new File(new File(activity.getFilesDir(),"pairing"),name);
    }

    private void importFixturePairing() throws Exception {
        File staged=new File(getInstrumentation().getTargetContext().getFilesDir(),"network-replay-input/pairing.abconnection");
        assertTrue("Staged fixture bundle is present",staged.isFile());
        assertTrue("Staged fixture bundle is not empty",staged.length()>0);
        getInstrumentation().runOnMainSync(()->activity.connectionSetup.startImportForTest(
            Uri.fromFile(staged)));
        long deadline=SystemClock.elapsedRealtime()+15000;
        AtomicReference<JSONObject> settled=new AtomicReference<>();
        do{
            getInstrumentation().runOnMainSync(()->settled.set(activity.connectionSetup.projection()));
            String state=settled.get().optString("state");
            if("configured".equals(state)){
                assertPairingMaterials();
                return;
            }
            if("error".equals(state))fail("Fixture pairing import failed: "+settled.get());
            SystemClock.sleep(50);
        }while(SystemClock.elapsedRealtime()<deadline);
        fail("Fixture pairing import did not settle: "+settled.get());
    }
    /** The remote IME bridge must own the Host editable target. */
    private boolean remoteImeActive(){
        boolean[] value={false};
        getInstrumentation().runOnMainSync(()->value[0]=activity.remoteIme.active()&&activity.remoteIme.onCheckIsTextEditor());
        return value[0];
    }
    private void awaitRemoteIme()throws Exception{
        long deadline=SystemClock.elapsedRealtime()+8000;
        do{
            if(remoteImeActive())return;
            SystemClock.sleep(50);
        }while(SystemClock.elapsedRealtime()<deadline);
        AtomicReference<String> context=new AtomicReference<>();
        getInstrumentation().runOnMainSync(()->context.set(String.valueOf(activity.network.editorContext())));
        fail("Remote IME never bound the Host editable target: editor_context="+context.get()
            +", status="+js("JSON.stringify("+STATUS+")"));
    }
    /** Screen geometry of the native video ingress, so a failing tap names its own coordinates. */
    private String touchGeometry(){
        String[] value={""};
        getInstrumentation().runOnMainSync(()->{
            int[] video=new int[2],clip=new int[2],web=new int[2];
            activity.video.getLocationOnScreen(video);
            activity.videoClip.getLocationOnScreen(clip);
            activity.webView.getLocationOnScreen(web);
            StringBuilder children=new StringBuilder();
            try{
                java.lang.reflect.Field field=MainActivity.class.getDeclaredField("root");
                field.setAccessible(true);
                android.view.ViewGroup root=(android.view.ViewGroup)field.get(activity);
                for(int index=0;index<root.getChildCount();index++){
                    android.view.View child=root.getChildAt(index);
                    children.append(index).append(':').append(child.getClass().getSimpleName())
                        .append('@').append(child.getLeft()).append(',').append(child.getTop())
                        .append(' ').append(child.getWidth()).append('x').append(child.getHeight())
                        .append(' ').append(child.getVisibility()==android.view.View.VISIBLE?"shown":"hidden").append(' ');
                }
            }catch(Exception reflection){children.append("reflection failed: ").append(reflection);}
            value[0]="video="+video[0]+","+video[1]+" clip="+clip[0]+","+clip[1]
                +" clipSize="+activity.videoClip.getWidth()+"x"+activity.videoClip.getHeight()
                +" videoSize="+activity.video.getWidth()+"x"+activity.video.getHeight()
                +" web="+web[0]+","+web[1]+" "+activity.webView.getWidth()+"x"+activity.webView.getHeight()
                +" visible="+activity.visibleWidth+"x"+activity.visibleHeight
                +" windowFocused="+activity.hasWindowFocus()+" children=["+children.toString().trim()+"]";
        });
        return value[0];
    }
    /**
     * The app's own gesture counter proves the test ingress reached the native
     * video surface: MainActivity advances it for every press the surface
     * accepts, so an unchanged counter means the event never arrived.
     */
    private long gestureCounter()throws Exception{
        java.lang.reflect.Field field=MainActivity.class.getDeclaredField("NEXT_GESTURE");
        field.setAccessible(true);
        return ((java.util.concurrent.atomic.AtomicLong)field.get(null)).get();
    }
    /**
     * The topmost child of the activity's root owns every system-routed touch. The
     * chrome WebView fills the window, so the presentation owner must keep exactly
     * one of {native stage, chrome} on top.
     */
    private String topChild(){
        String[] value={""};
        getInstrumentation().runOnMainSync(()->{
            try{
                java.lang.reflect.Field field=MainActivity.class.getDeclaredField("root");
                field.setAccessible(true);
                android.view.ViewGroup root=(android.view.ViewGroup)field.get(activity);
                value[0]=root.getChildAt(root.getChildCount()-1).getClass().getSimpleName();
            }catch(Exception reflection){value[0]="reflection failed: "+reflection;}
        });
        return value[0];
    }
    private void appendEvidence(String name,String value)throws Exception{
        try(FileOutputStream out=new FileOutputStream(new File(evidenceDirectory(),name),true)){
            out.write(value.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        }
    }
    private void touchChrome(String id)throws Exception{
        awaitInputWindow();
        String before=js("(()=>{const e=document.getElementById('"+id+"'),r=e.getBoundingClientRect(),h=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return JSON.stringify({id:'"+id+"',cssX:r.left+r.width/2,cssY:r.top+r.height/2,rect:{x:r.x,y:r.y,w:r.width,h:r.height},target:h&&h.id,tag:h&&h.tagName,drawerOpen:document.querySelector('main')?.dataset.drawerOpen||null});})()");
        appendEvidence("chrome-touch.txt","CHROME_TOUCH_BEFORE "+before+" webView="+touchGeometry()+"\n");
        JSONObject box=new JSONObject(js("(()=>{const r=document.getElementById('"+id+"').getBoundingClientRect();"
            +"return {x:r.left+r.width/2,y:r.top+r.height/2,width:window.innerWidth,height:window.innerHeight};})()"));
        float[] location=new float[2];
        getInstrumentation().runOnMainSync(()->{
            int[] origin=new int[2];activity.webView.getLocationOnScreen(origin);
            float scaleX=activity.webView.getWidth()/(float)box.optDouble("width",1);
            float scaleY=activity.webView.getHeight()/(float)box.optDouble("height",1);
            location[0]=origin[0]+(float)box.optDouble("x",0)*scaleX;
            location[1]=origin[1]+(float)box.optDouble("y",0)*scaleY;
        });
        long now=SystemClock.uptimeMillis();
        MotionEvent down=MotionEvent.obtain(now,now,MotionEvent.ACTION_DOWN,location[0],location[1],0);
        MotionEvent up=MotionEvent.obtain(now,now+10,MotionEvent.ACTION_UP,location[0],location[1],0);
        try{injectPointer(down);injectPointer(up);}finally{down.recycle();up.recycle();}
        String after=js("(()=>{const e=document.getElementById('"+id+"'),r=e.getBoundingClientRect(),h=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return JSON.stringify({id:'"+id+"',cssX:r.left+r.width/2,cssY:r.top+r.height/2,rect:{x:r.x,y:r.y,w:r.width,h:r.height},open:document.querySelector('main')?.dataset.drawerOpen||null,target:h&&h.id,tag:h&&h.tagName,presentation:"+STATUS+".layout?.presentation||null});})()");
         appendEvidence("chrome-touch.txt","CHROME_TOUCH_AFTER "+after+" webView="+touchGeometry()+"\n");
    }
    /** Local gesture ownership, for a failing remote event that the Host refused. */
         /** Verify observable delivery after Android 16 InputDispatcher races. */
     private void touchChromeVerified(String id,Callable<Boolean> delivered)throws Exception{
         for(int attempt=0;attempt<2;attempt++){
             touchChrome(id);
             long deadline=SystemClock.elapsedRealtime()+4000;
             while(SystemClock.elapsedRealtime()<deadline){ if(delivered.call())return; SystemClock.sleep(50); }
         }
     }
     private String localGestureState(){
        StringBuilder out=new StringBuilder();
        try{
            for(String name:new String[]{"gestureOperationId","gestureSessionId","openOperationId"}){
                java.lang.reflect.Field field=NetworkSession.class.getDeclaredField(name);
                field.setAccessible(true);
                out.append(name).append('=').append(field.get(activity.network)).append(' ');
            }
            for(String name:new String[]{"gestureOperationId","gestureEventSequence","gestureEpoch"}){
                java.lang.reflect.Field field=MainActivity.class.getDeclaredField(name);
                field.setAccessible(true);
                out.append(name).append('=').append(field.get(activity)).append(' ');
            }
            java.lang.reflect.Field host=NetworkSession.class.getDeclaredField("host");
            host.setAccessible(true);
            Object value=host.get(activity.network);
            org.json.JSONObject status=value instanceof org.json.JSONObject?(org.json.JSONObject)value:null;
            out.append("hostLease=").append(status==null?null:status.opt("input_lease")).append(' ');
            out.append("hostRunning=").append(status!=null&&status.optBoolean("operation_running",false));
        }catch(Exception reflection){out.append("reflection failed: ").append(reflection);}
        return out.toString();
    }
    /**
     * The Host owns engine focus: its text insert is accepted only while
     * `document.activeElement` is an enabled, writable text input. The receipt is
     * therefore the focus proof the remote IME binding depends on, and the Host
     * publishes the confirmed editor context with it.
     */
    private String hostFieldText(String what)throws Exception{
        // The cached projection can lag one revision behind a just-settled
        // composition, so the authoritative read is a fresh Host status
        // round-trip whose response replaces the cached editor context.
        getInstrumentation().runOnMainSync(()->activity.network.command(0,0,0,0,0,0,""));
        until(STATUS+".inputReady",6000);
        JSONObject context=activity.network.editorContext();
        JSONObject surrounding=context==null?null:context.optJSONObject("surrounding");
        if(surrounding!=null)return recordedHostFieldText(what,"status",surrounding.optString("text",""));
        String projection=js("(()=>{const s="+STATUS+";return ProbeNative.request(JSON.stringify({op:'input_text',epoch:s.epoch,text:''}));})()");
        JSONObject receipt=new JSONObject(projection);
        JSONObject editor=receipt.optJSONObject("editing");
        surrounding=editor==null?null:editor.optJSONObject("surrounding");
        assertNotNull(what+" must expose Host surrounding text: "+projection,surrounding);
        return recordedHostFieldText(what,"insert_receipt",surrounding.optString("text",""));
    }
    /** Records which Host source produced the field text, so the evidence names it. */
    private String recordedHostFieldText(String what,String source,String text){
        android.util.Log.e("IME_DIAGNOSTIC_TRACE","HOST_FIELD_TEXT what="+what+" source="+source+" text="+text);
        return text;
    }
    /**
     * Diagnostic-only evidence writer. It preserves the ordered IME trace and the
     * Host-projected values before an assertion can fail, and the replay collects
     * the file even when instrumentation fails.
     */
    private void writeEvidence(String name,String detail)throws Exception{
        String value=detail+" pixelPoll="+lastPixelPoll+" status="+js("JSON.stringify("+STATUS+")")+"\n";
        System.out.println("IME_DIAGNOSTIC_TRACE "+name+" "+value);
        android.util.Log.e("IME_DIAGNOSTIC_TRACE",name+" "+value);
        File directory=evidenceDirectory();
        try(java.io.FileOutputStream out=new java.io.FileOutputStream(new java.io.File(directory,name))){
            out.write(value.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        }catch(java.io.IOException failure){throw new AssertionError(failure);}
    }
    private File evidenceDirectory(){
        File directory=new File(activity.getFilesDir(),"network-evidence");
        assertTrue(directory.isDirectory()||directory.mkdirs());
        return directory;
    }
    private void writeImeTrace(String detail)throws Exception{writeEvidence("ime-trace.txt",detail);}
    private void assertHostFieldFocused(String what)throws Exception{
        String projection=js("(()=>{const s="+STATUS+";const r=JSON.parse(ProbeNative.request(JSON.stringify({op:'input_text',epoch:s.epoch,text:''})));"
            +"return {rejection:r.rejection||null};})()");
        assertTrue(what+" must submit the Host text insert: "+projection,new JSONObject(projection).isNull("rejection"));
        until(STATUS+".inputReady",6000);
        // The Host rejects a text insert without a focused writable target, and the
        // rejection is the session's error projection once the command has settled.
        // The probe projects a JS value as JSON text, so a null error is the string
        // literal "null" once the WebView JSON-encodes it.
        assertEquals(what+" must focus the Host text input: "+js("JSON.stringify("+STATUS+")")
            +"\n"+touchGeometry(),"\"null\"",js("JSON.stringify("+STATUS+".error)"));
        // A focused writable target is also the editor context the Host publishes,
        // which is what makes the session eligible for remote IME input.
        long editorDeadline=SystemClock.elapsedRealtime()+3000;
        while(activity.network.editorContext()==null&&SystemClock.elapsedRealtime()<editorDeadline)SystemClock.sleep(50);
        assertNotNull(what+" must publish the Host editor context: "+js("JSON.stringify("+STATUS+")"),
            activity.network.editorContext());
    }
    /**
     * A pending composition is one Host lease. The Host fences every other Agent
     * operation while that lease is open, so this is the in-process projection of
     * the Host's own `input_lease`.
     */
    private boolean hostLeaseFencesAgents(){
        boolean[] fenced={false};
        getInstrumentation().runOnMainSync(()->fenced[0]=!activity.network.gestureEligible("probe-lease"));
        return fenced[0];
    }
    /** The Host status projection is polled, so lease edges are awaited, not sampled once. */
    private void awaitHostLease(String what,boolean held)throws Exception{
        long deadline=SystemClock.elapsedRealtime()+6000;
        do{
            if(hostLeaseFencesAgents()==held)return;
            SystemClock.sleep(50);
        }while(SystemClock.elapsedRealtime()<deadline);
        fail(what+": the Host "+(held?"does not hold the composition lease":"still fences other Agent operations"));
    }
    private void awaitRemoteImeReleased(String what)throws Exception{
        long deadline=SystemClock.elapsedRealtime()+6000;
        do{
            if(!remoteImeActive())return;
            SystemClock.sleep(50);
        }while(SystemClock.elapsedRealtime()<deadline);
        fail(what);
    }
    /** Bind the production remote IME owner and return its live connection. */
    private InputConnection openRemoteIme()throws Exception{
        awaitRemoteIme();
        AtomicReference<InputConnection> input=new AtomicReference<>();
        getInstrumentation().runOnMainSync(()->{
            activity.remoteIme.requestFocus();
            activity.getSystemService(InputMethodManager.class)
                .showSoftInput(activity.remoteIme,InputMethodManager.SHOW_IMPLICIT);
            input.set(activity.remoteIme.onCreateInputConnection(new EditorInfo()));
        });
        assertNotNull("Remote IME input connection",input.get());
        return input.get();
    }
    /**
     * The keyboard is a presentation inset only. The layout contract keeps
     * AdvertisedViewportRect free of the IME and shrinks PresentedContentRect, so the
     * keyboard must shrink the presented stage and must not resize the source viewport
     * (milestone-1-plan: portrait, landscape and keyboard keep one viewport revision
     * and one source size).
     */
    private int stageHeight()throws Exception{
        int[] height={0};
        getInstrumentation().runOnMainSync(()->height[0]=((android.view.View)activity.videoClip.getParent()).getHeight());
        return height[0];
    }
    private void awaitImeHidden(long timeout)throws Exception{
        boolean[] hidden={false};
        long deadline=SystemClock.elapsedRealtime()+timeout;
        do{
            getInstrumentation().runOnMainSync(()->{
                WindowInsets insets=activity.remoteIme.getRootWindowInsets();
                hidden[0]=insets==null||!insets.isVisible(WindowInsets.Type.ime());
            });
            if(hidden[0])return;
            SystemClock.sleep(50);
        }while(SystemClock.elapsedRealtime()<deadline);
        assertTrue("Keyboard must be hidden before the IME presentation gate",hidden[0]);
    }
    private void awaitImeVisible(int sourceHeight,int baselineHeight,long timeout)throws Exception{
        int[] baseline={baselineHeight},stageHeight={baselineHeight};
        boolean[] visible={false},fits={false},presentedShrunk={false},sourceIntact={false};
        long deadline=SystemClock.elapsedRealtime()+timeout;
        do{
            getInstrumentation().runOnMainSync(()->{
                WindowInsets insets=activity.remoteIme.getRootWindowInsets();
                visible[0]=insets!=null&&insets.isVisible(WindowInsets.Type.ime());
                android.view.View stage=(android.view.View)activity.videoClip.getParent();
                stageHeight[0]=stage.getHeight();
                presentedShrunk[0]=stageHeight[0]<baseline[0];
                sourceIntact[0]=activity.visibleHeight==sourceHeight;
                if(visible[0]){
                    int[] position=new int[2];stage.getLocationOnScreen(position);
                    int keyboardTop=activity.getWindow().getDecorView().getHeight()
                        -insets.getInsets(WindowInsets.Type.ime()).bottom;
                    fits[0]=position[1]+stage.getHeight()<=keyboardTop;
                }
            });
            if(visible[0]&&fits[0]&&presentedShrunk[0]&&sourceIntact[0])return;
            SystemClock.sleep(50);
        }while(SystemClock.elapsedRealtime()<deadline);
        assertTrue("Real IME must be visible",visible[0]);
        assertTrue("Keyboard must leave the Host page fully above it",fits[0]);
        assertTrue("Keyboard must shrink the presented page area: stage="+baseline[0]+"->"+stageHeight[0],
            presentedShrunk[0]);
        assertTrue("Keyboard must not resize the source viewport: "+sourceHeight+"->"+activity.visibleHeight,
            sourceIntact[0]);
    }
    private void hideIme(){
        getInstrumentation().runOnMainSync(()->activity.getWindow().getInsetsController().hide(WindowInsets.Type.ime()));
    }
    private void imeEdit(InputConnection input,
            java.util.function.Consumer<InputConnection> edit)throws Exception{
        // A view-based BaseInputConnection has no handler of its own: AOSP
        // BaseInputConnection.getHandler() returns null, and the input method calls the
        // connection directly on the main thread. Drive that ingress, and never let a
        // failure escape the looper: capture it and report it on the test thread.
        AtomicReference<Throwable> failure=new AtomicReference<>();
        getInstrumentation().runOnMainSync(()->{try{edit.accept(input);}catch(Throwable error){failure.set(error);}});
        if(failure.get()!=null)throw new AssertionError("IME edit failed",failure.get());
    }
    private static void escapeComposition(InputConnection connection){
        long now=SystemClock.uptimeMillis();
        // The escape key cancels the remote composition on ACTION_DOWN. The key up has no
        // remote effect, and a view-based BaseInputConnection has no InputMethodManager,
        // so its super call reports false by design and is not asserted.
        assertTrue("Remote IME must cancel the composition on escape",connection.sendKeyEvent(
            new KeyEvent(now,now,KeyEvent.ACTION_DOWN,KeyEvent.KEYCODE_ESCAPE,0)));
        connection.sendKeyEvent(new KeyEvent(now,now+10,KeyEvent.ACTION_UP,KeyEvent.KEYCODE_ESCAPE,0));
        connection.endBatchEdit();
    }
    /**
     * Frames are asynchronous: the Host's page state can already be committed
     * while the presented frame is still the previous one. Every pixel gate
     * therefore waits for a frame produced after the state it asserts on.
     */
    private void awaitNextFrame(String what)throws Exception{
        long ticket=Long.parseLong(js(STATUS+".displayedTicket"));
        long pts=Long.parseLong(js(STATUS+".displayedPtsUs"));
        long deadline=SystemClock.elapsedRealtime()+5000;
        while((Long.parseLong(js(STATUS+".displayedTicket"))<=ticket||Long.parseLong(js(STATUS+".displayedPtsUs"))<=pts)
                &&SystemClock.elapsedRealtime()<deadline)SystemClock.sleep(50);
        assertTrue(what+" must render a frame after the observed state: before="+ticket+"/"+pts
            +" status="+js("JSON.stringify("+STATUS+")"),
            Long.parseLong(js(STATUS+".displayedTicket"))>ticket&&Long.parseLong(js(STATUS+".displayedPtsUs"))>pts);
    }
    private int pollGlyphs(String name,java.util.function.IntPredicate satisfied)throws Exception{
        StringBuilder frames=new StringBuilder();
        long deadline=SystemClock.elapsedRealtime()+5000;
        int glyphs;
        do{
            SystemClock.sleep(100);
            glyphs=darkInputGlyphs(capture(name));
            frames.append(js(STATUS+".displayedTicket")).append('/').append(js(STATUS+".displayedPtsUs"))
                .append('=').append(glyphs).append(' ');
        }while(!satisfied.test(glyphs)&&SystemClock.elapsedRealtime()<deadline);
        lastPixelPoll=name+" frames="+frames.toString().trim();
        android.util.Log.e("IME_DIAGNOSTIC_TRACE","PIXEL_POLL "+lastPixelPoll);
        return glyphs;
    }
    private int stableGlyphs(String name)throws Exception{
        long deadline=SystemClock.elapsedRealtime()+5000;
        int previous=-1,glyphs=previous;
        do{
            SystemClock.sleep(100);
            glyphs=darkInputGlyphs(capture(name));
            if(previous>=0&&Math.abs(glyphs-previous)<=5)return glyphs;
            previous=glyphs;
        }while(SystemClock.elapsedRealtime()<deadline);
        return glyphs;
    }
    private void keyboardArea()throws Exception{
        int width=activity.visibleWidth,height=activity.visibleHeight;
        String session=js(STATUS+".sessionId"),document=js(STATUS+".documentRevision");
        until(STATUS+".inputReady",6000);
        assertHostFieldFocused("The keyboard area");
        hideIme();
        awaitImeHidden(4000);
        int imeBaseline=stageHeight();
        InputConnection input=openRemoteIme();
        awaitImeVisible(height,imeBaseline,8000);
        imeVisible=true;
        until(STATUS+".inputReady",6000);
        viewport();
        assertEquals(session,js(STATUS+".sessionId"));assertEquals(document,js(STATUS+".documentRevision"));
        // The baseline must show the Host's current page, not a frame captured
        // before the earlier text insert.
        int glyphsBeforeComposition=stableGlyphs("text-before-composition");
        // setComposingText returns true only for a Host `editing` receipt whose
        // state is `pending`: the Host holds the unfinished composition open.
        imeEdit(input,connection->{assertTrue(connection.beginBatchEdit());assertTrue(connection.setComposingText("zhongwen",1));});
        compositionStarted=true;
        awaitHostLease("A pending composition must fence other Agent operations",true);
        compositionSendDisabled=true;
        imeEdit(input,NetworkDeviceTest::escapeComposition);
        compositionCancelled=true;
        awaitHostLease("Cancelled composition must release the Host lease",false);
        String cancelledText=hostFieldText("Cancelled composition");
        assertFalse("Cancelled Host field must not contain zhongwen: "+cancelledText,cancelledText.contains("zhongwen"));
        int glyphsAfterCancel=pollGlyphs("text-after-cancel",value->true);
        appendEvidence("ime-trace.txt","cancel-pixel-diagnostic baseline="+glyphsBeforeComposition+" after="+glyphsAfterCancel+"\n");
        // Semantic cancel proof is the fresh Host surrounding text, cancelled
        // receipt/composition=null, and released lease. Pixels remain evidence only.
        unfinishedCompositionDropped=true;
        // A commit receipt must be `applied`, and the committed CJK text must
        // reach the Host page pixels.
        imeEdit(input,connection->{
            assertTrue(connection.beginBatchEdit());
            assertTrue(connection.setComposingText("zhongwen",1));
            assertTrue(connection.commitText("中文",1));
            assertTrue(connection.finishComposingText());
            connection.endBatchEdit();
        });
        compositionCommitted=true;
        awaitHostLease("Committed composition must release the Host lease",false);
        // The Host editor context is the authoritative DOM truth for the field. The
        // commit replaces the preedit with the committed text exactly once, so the
        // field text must be the cancelled text with one inserted `中文`.
        String committedText=hostFieldText("Committed composition");
        assertEquals("Committed Host field must contain exactly one committed text: "+committedText,
            1,committedText.split("中文",-1).length-1);
        assertEquals("Committed Host field must be the pre-commit text with the commit inserted: "+committedText,
            cancelledText,committedText.replace("中文",""));
        long commitTicket=Long.parseLong(js(STATUS+".displayedTicket"));
        long commitPts=Long.parseLong(js(STATUS+".displayedPtsUs"));
        long commitDeadline=SystemClock.elapsedRealtime()+5000;
        while((Long.parseLong(js(STATUS+".displayedTicket"))<=commitTicket || Long.parseLong(js(STATUS+".displayedPtsUs"))<=commitPts)
                &&SystemClock.elapsedRealtime()<commitDeadline)SystemClock.sleep(50);
        assertTrue("Committed receipt must be followed by a newer displayed frame: before="+commitTicket+"/"+commitPts
            +" status="+js("JSON.stringify("+STATUS+")"),Long.parseLong(js(STATUS+".displayedTicket"))>commitTicket
            &&Long.parseLong(js(STATUS+".displayedPtsUs"))>commitPts);
        chineseInkPixels=pollGlyphs("ime-text",value->true);
        chineseInkAdded=chineseInkPixels-glyphsAfterCancel;
        appendEvidence("ime-trace.txt","commit-pixel-diagnostic baseline="+glyphsAfterCancel+" after="+chineseInkPixels+" added="+chineseInkAdded+"\n");
        // Commit semantics are the fresh Host exact-once text and applied receipt;
        // pixels and glyph counts remain diagnostic evidence only.
        hideIme();
        awaitSourceSize(width,height);
    }
    public void testImeCompositionCancel()throws Exception{
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try{
            importFixturePairing();
            until("!!document.getElementById('connect')",10000);
            clickEnabled("connect");
            until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",15000);
            clickEnabled("takeover");
            until(UI_MODE+"==='control' && "+STATUS+".inputReady",6000);
            // Focus the Host's own editable field through the real remote input path.
            // The press must first reach the native video surface: without that the
            // tap gates below would pass vacuously.
            long gesturesBefore=gestureCounter();
            touch(100,125);
            assertEquals("The injected press must reach the native video ingress: gestures="
                +gesturesBefore+"->"+gestureCounter()+"\n"+touchGeometry(),
                gesturesBefore+1,gestureCounter());
            until(STATUS+".inputReady",6000);
            assertHostFieldFocused("The field tap");
            hideIme();
            awaitImeHidden(4000);
            int imeBaseline=stageHeight();
            InputConnection input=openRemoteIme();
            awaitImeVisible(activity.visibleHeight,imeBaseline,8000);
            until(STATUS+".inputReady",6000);
            viewport();
            String fieldBeforeComposition=hostFieldText("Pre-composition Host field");
            int glyphsBefore=stableGlyphs("cancel-before");
            imeEdit(input,connection->{assertTrue(connection.beginBatchEdit());assertTrue(connection.setComposingText("zhongwen",1));});
            awaitHostLease("A pending composition must fence other Agent operations",true);
            imeEdit(input,NetworkDeviceTest::escapeComposition);
            awaitHostLease("Raw Escape must release the Host composition lease",false);
            String cancelText=hostFieldText("Cancelled Host editor context");
            assertEquals("A raw Escape must restore the exact pre-composition field text",fieldBeforeComposition,cancelText);
            assertFalse("Cancelled Host field must not contain the preedit: "+cancelText,cancelText.contains("zhongwen"));
            writeImeTrace("hostTextBefore="+fieldBeforeComposition+" hostTextAfterCancel="+cancelText);
            long ticketAtCancel=Long.parseLong(js(STATUS+".displayedTicket"));
            long ptsAtCancel=Long.parseLong(js(STATUS+".displayedPtsUs"));
            long frameDeadline=SystemClock.elapsedRealtime()+5000;
            while((Long.parseLong(js(STATUS+".displayedTicket"))<=ticketAtCancel || Long.parseLong(js(STATUS+".displayedPtsUs"))<=ptsAtCancel) && SystemClock.elapsedRealtime()<frameDeadline)SystemClock.sleep(50);
            assertTrue("Cancel frame must advance: before="+ticketAtCancel+"/"+ptsAtCancel+" status="+js("JSON.stringify("+STATUS+")"), Long.parseLong(js(STATUS+".displayedTicket"))>ticketAtCancel && Long.parseLong(js(STATUS+".displayedPtsUs"))>ptsAtCancel);
            writeImeTrace("hostTextBefore="+fieldBeforeComposition+" hostTextAfterCancel="+cancelText
                +" frameBeforeCancel="+ticketAtCancel+"/"+ptsAtCancel
                +" frameAtCapture="+js(STATUS+".displayedTicket")+"/"+js(STATUS+".displayedPtsUs"));
            int glyphsAfter=darkInputGlyphs(capture("cancel-after"));
            appendEvidence("ime-trace.txt","cancel-pixel-diagnostic baseline="+glyphsBefore+" after="+glyphsAfter+"\n");
            hideIme();
        }finally{getInstrumentation().runOnMainSync(()->activity.finish());}
    }
    private Bitmap capture(String name)throws Exception{
        Bitmap full=Bitmap.createBitmap(Math.max(1,activity.video.getWidth()),Math.max(1,activity.video.getHeight()),Bitmap.Config.ARGB_8888);CountDownLatch done=new CountDownLatch(1);int[] result={-1};
        getInstrumentation().runOnMainSync(()->PixelCopy.request(activity.video,full,value->{result[0]=value;done.countDown();},new Handler(Looper.getMainLooper())));
        assertTrue(done.await(3,TimeUnit.SECONDS));assertEquals(PixelCopy.SUCCESS,result[0]);
        Bitmap image=activity.videoClip.getHeight() < full.getHeight() ? Bitmap.createBitmap(full,0,0,full.getWidth(),activity.videoClip.getHeight()) : full;
        File directory=evidenceDirectory();
        try(var output=new FileOutputStream(new File(directory,name+".png"))){assertTrue(image.compress(Bitmap.CompressFormat.PNG,100,output));}
        return image;
    }
    private String darkBounds(Bitmap image){
        Bitmap normalized=Bitmap.createScaledBitmap(image,activity.visibleWidth,activity.visibleHeight,true);
        int minX=normalized.getWidth(),minY=normalized.getHeight(),maxX=-1,maxY=-1;
        for(int y=0;y<normalized.getHeight();y++)for(int x=0;x<normalized.getWidth();x++){
            int c=normalized.getPixel(x,y);
            if(Color.red(c)<80&&Color.green(c)<80&&Color.blue(c)<80){minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y);}
        }
        return minX+","+minY+"-"+maxX+","+maxY;
    }
    /** Nonblank dark-pixel presence over the whole presented capture. */
    private int darkPixelCount(Bitmap image){
        Bitmap normalized=Bitmap.createScaledBitmap(image,activity.visibleWidth,activity.visibleHeight,true);
        int count=0;
        for(int y=0;y<normalized.getHeight();y++)for(int x=0;x<normalized.getWidth();x++){
            int c=normalized.getPixel(x,y);
            if(Color.red(c)<80&&Color.green(c)<80&&Color.blue(c)<80)count++;
        }
        return count;
    }
    private int darkInputGlyphs(Bitmap image){return darkInputGlyphs(image,activity.visibleWidth,activity.visibleHeight);}
    private int darkInputGlyphs(Bitmap image,int cssWidth,int cssHeight) {
        int count=0;
        Bitmap normalized=Bitmap.createScaledBitmap(image,cssWidth,cssHeight,true);
        int left=5,right=Math.min(195,cssWidth);
        int top=104,bottom=Math.min(134,cssHeight);
        android.util.Log.e("IME_DIAGNOSTIC_TRACE","GLYPH_SAMPLE image="+image.getWidth()+"x"+image.getHeight()
            +" css="+cssWidth+"x"+cssHeight+" region="+left+","+top+"-"+right+","+bottom);
        for(int y=top;y<bottom;y++)for(int x=left;x<right;x++){
            int color=normalized.getPixel(x,y);
            if(Color.red(color)<80&&Color.green(color)<80&&Color.blue(color)<80)count++;
        }
        return count;
    }

    public void testOrdinaryProtectedImport() throws Exception {
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        importOrdinaryPairing();
        JSONObject panel=activity.connectionSetup.projection();
        assertEquals("Ordinary import must configure", "configured", panel.optString("state"));
    }

    /**
     * Task-12 admission: the ordinary DocumentsUI import of the no-code
     * bundle, which carries only the necessary connection configuration.
     */
    public void testOrdinaryRealPathAdmission() throws Exception {
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try{
            importOrdinaryPairing();
            JSONObject panel=activity.connectionSetup.projection();
            assertEquals("Ordinary import must configure","configured",panel.optString("state"));
            writeRaw("admission.json",new JSONObject()
                .put("panelState",panel.optString("state"))
                .put("endpoint",admittedEndpoint())
                .put("endpointPublished",pairingMaterial("endpoint.txt").isFile())
                .put("tokenPublished",pairingMaterial("token").isFile()).toString(2)+"\n");
        }finally{getInstrumentation().runOnMainSync(()->activity.finish());}
    }

    /**
     * Task-12 real user path on the already-admitted pairing: the ordinary
     * address bar loads a real HTTPS document, one real touch focuses its text
     * input, and the raised system IME types ASCII that must land in the remote
     * page. Every gate is a Host- or system-owned projection, never a local
     * mirror.
     */
    public void testOrdinaryRealHttpsAndRemoteIme() throws Exception {
        String pageUrl=argument("pageUrl","https://www.selenium.dev/selenium/web/web-form.html");
        String ascii=argument("asciiText","hello");
        float tapX=Float.parseFloat(argument("tapX","196"));
        float tapY=Float.parseFloat(argument("tapY","107"));
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try{
            assertEquals("Admitted pairing must be configured","configured",activity.connectionSetup.projection().optString("state"));
            until("!!document.getElementById('connect')",10000);
            clickEnabled("connect");
            until(STATUS+".connectionState==='connected'",20000,"connect to the admitted endpoint");
            until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",20000);
            clickEnabled("takeover");
            until(UI_MODE+"==='control'",8000);
            JSONObject connected=new JSONObject(js(STATUS));
            long framesBefore=connected.optLong("renderedFrames",-1);
            long documentBefore=connected.optLong("documentRevision",-1);

            // Gate: the ordinary address bar loads a real HTTPS document.
            navigateFromAddress(pageUrl);
            JSONObject navigation=awaitNavigation("https:",8000);
            String finalUrl=navigation.optString("url","");
            String finalScheme=finalUrl.contains(":")?finalUrl.substring(0,finalUrl.indexOf(':')):"";
            JSONObject loaded=new JSONObject(js(STATUS));
            Bitmap beforeTap=capture("real-path-before-tap");

            // Establish causal evidence: hide the IME and verify it is down
            // before the remote-field tap.
            forceImeHiddenBeforeTap();

            // Gate: one real touch on the remote text input element.
            touch(tapX,tapY);

            // showSoftInput is asynchronous. Poll a bounded interval and retain
            // every raw sample; a single immediate dump is timing-sensitive.
            JSONObject ime=pollImeRaised(8000);
            final int[] attempts=new int[1];
            getInstrumentation().runOnMainSync(()->attempts[0]=activity.remoteIme.showAttempts());
            ime.put("showAttempts",attempts[0]);
            writeRaw("logcat-focus.txt",rawShell("logcat -d -v threadtime -t 400"));
            writeRaw("ime-state.json",ime.toString(2)+"\n");
            JSONObject editor=awaitEditableContext(8000);
            Bitmap afterTap=capture("real-path-after-tap");
            boolean frameChanged=!beforeTap.sameAs(afterTap);

            // Gate: ASCII typed through the raised system IME lands in the Host page.
            String imePackage=ime.optString("mCurMethodId","");
            imePackage=imePackage.contains("/")?imePackage.substring(0,imePackage.indexOf('/')):imePackage;
            boolean imeWindow=device().hasObject(By.pkg(imePackage));
            String fieldText="";
            String typingNote=null;
            if(imeWindow||!"".equals(imePackage)){
                try{
                    typeThroughSystemIme(imePackage,ascii);
                    fieldText=awaitHostFieldText(ascii,15000);
                }catch(AssertionError typingFailure){typingNote=String.valueOf(typingFailure.getMessage());}
            }else{
                typingNote="the system IME window is not present";
            }
            writeRaw("dumpsys-input-method-after-typing.txt",rawShell("dumpsys input_method"));
            JSONObject afterTyping=new JSONObject(js(STATUS));

            JSONObject result=new JSONObject()
                .put("pageUrl",pageUrl)
                .put("finalUrl",finalUrl)
                .put("finalScheme",finalScheme)
                .put("navigationTitle",navigation.optString("title",""))
                .put("canGoBack",navigation.optBoolean("can_go_back",false))
                .put("documentRevisionBefore",documentBefore)
                .put("documentRevisionAfter",loaded.optLong("documentRevision",-1))
                .put("renderedFramesBefore",framesBefore)
                .put("renderedFramesAfter",loaded.optLong("renderedFrames",-1))
                .put("renderedFramesAfterTyping",afterTyping.optLong("renderedFrames",-1))
                .put("displayedTicket",loaded.optLong("displayedTicket",-1))
                .put("tapX",tapX).put("tapY",tapY)
                .put("frameChangedAfterTap",frameChanged)
                .put("editorTargetId",editor.optString("target_id",""))
                .put("editorKind",editor.optString("kind",""))
                .put("editorEditable",editor.optBoolean("editable",false))
                .put("editorInputType",editor.optString("input_type",""))
                .put("imeInputShown",ime.opt("mInputShown"))
                .put("imeServedInputConnection",ime.opt("mServedInputConnection"))
                .put("imeServedView",ime.opt("mServedView"))
                .put("imeCurMethodId",ime.opt("mCurMethodId"))
                .put("imeCurFocusedWindow",ime.opt("mCurFocusedWindow"))
                .put("imeWindowVisible",imeWindow)
                .put("asciiText",ascii)
                .put("hostFieldText",fieldText)
                .put("typingNote",typingNote==null?JSONObject.NULL:typingNote);
            writeRaw("real-path.json",result.toString(2)+"\n");

            // Gate 9: release input and disconnect through the ordinary controls.
            clickEnabled("release");
            until(UI_MODE+"!=='control'",8000);
            clickEnabled("disconnect");
            until(STATUS+".connectionState!=='connected'",15000);
            writeRaw("release.json",new JSONObject()
                .put("controlModeAfterRelease",js(UI_MODE).replace("\"",""))
                .put("connectionStateAfterDisconnect",new JSONObject(js(STATUS)).optString("connectionState")).toString(2)+"\n");

            assertEquals("The ordinary address bar must load an https document: "+finalUrl,"https",finalScheme);
            assertTrue("A real touch must focus the remote editable target: "+editor,editor.optBoolean("editable",false));
            assertTrue("The raised system IME must report mInputShown=true: "+ime,"true".equals(String.valueOf(ime.opt("mInputShown"))));
            assertTrue("The raised system IME must serve an input connection: "+ime,
                ime.opt("mServedInputConnection")!=null&&ime.opt("mServedInputConnection")!=JSONObject.NULL);
            assertTrue("The served view must belong to the app package: "+ime,
                String.valueOf(ime.opt("mServedView")).contains("com.agentbrowser.probe"));
            if(typingNote!=null)fail("ASCII typing through the raised system IME did not run: "+typingNote+"\n"+result);
            assertTrue("ASCII typed through the system IME must land in the Host page: "+result,fieldText.contains(ascii));
        }finally{getInstrumentation().runOnMainSync(()->activity.finish());}
    }

    /**
     * BB05 re-raise: the real user path where the keyboard is dismissed and the
     * same remote field is tapped again. The first tap raises the IME, the
     * client dismisses it exactly like a user dismissal, and the second tap on
     * the same field must raise it again. A previous run that leaves the remote
     * field focused reaches this path on its very first tap.
     */
    public void testRemoteImeReraiseSlice() throws Exception {
        String url=argument("pageUrl","https://www.selenium.dev/selenium/web/web-form.html");
        float tapX=Float.parseFloat(argument("tapX","196")),tapY=Float.parseFloat(argument("tapY","107"));
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try{
            assertEquals("Admitted pairing must be configured","configured",activity.connectionSetup.projection().optString("state"));
            until("!!document.getElementById('connect')",10000); clickEnabled("connect");
            until(STATUS+".connectionState==='connected'",20000,"connect to the admitted endpoint");
            until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",20000); clickEnabled("takeover");
            until(UI_MODE+"==='control'",8000);
            navigateFromAddress(url); awaitNavigation("https:",15000);
            JSONObject evidence=new JSONObject().put("case","BB05-reraise").put("url",url)
                .put("tapX",tapX).put("tapY",tapY);

            // First raise: one real tap on the remote editable target.
            touch(tapX,tapY);
            JSONObject first=pollImeRaised(8000);
            evidence.put("firstRaiseElapsedMs",first.optLong("raiseElapsedMs",-1));
            // Let the bounded show retry settle, so the dismissal below is a real
            // user dismissal and not an in-flight app show request.
            evidence.put("attemptsBeforeDismiss",awaitShowRetriesSettled(12000));

            // The user dismisses the keyboard. The hide request is asynchronous,
            // so wait for the system to report it down before the second tap.
            getInstrumentation().runOnMainSync(()->activity.getSystemService(InputMethodManager.class)
                .hideSoftInputFromWindow(activity.remoteIme.getWindowToken(),0));
            JSONObject down=awaitImeDown(6000);
            evidence.put("dismissElapsedMs",down.optLong("hideElapsedMs",-1))
                .put("mInputShownAfterDismiss",down.opt("mInputShown"))
                .put("stateAfterDismiss",focusState());
            assertEquals("The keyboard must go down after a user dismissal: "+down,"false",String.valueOf(down.opt("mInputShown")));

            // The same remote field is tapped again; the keyboard must return.
            touch(tapX,tapY);
            JSONObject second;
            try{
                second=pollImeRaised(8000);
            }catch(AssertionError raiseFailure){
                writeRaw("ime-reraise.json",evidence.put("failure",String.valueOf(raiseFailure.getMessage()))
                    .toString(2)+"\n");
                throw raiseFailure;
            }
            evidence.put("secondRaiseElapsedMs",second.optLong("raiseElapsedMs",-1));
            writeRaw("ime-reraise.json",evidence.toString(2)+"\n");
        }finally{getInstrumentation().runOnMainSync(()->activity.finish());}
    }

    /**
     * Negative control for the tap hook. On a page whose document has no
     * writable editor, a completed real tap must NOT raise the keyboard: the
     * hook is a response to tapping a remote editor, not an unconditional
     * retry. If this fails, the fix has become a retry that masks a race.
     */
    public void testRemoteImeNoEditorSlice() throws Exception {
        String url=argument("pageUrl","https://example.com/");
        float tapX=Float.parseFloat(argument("tapX","196")),tapY=Float.parseFloat(argument("tapY","300"));
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try{
            assertEquals("Admitted pairing must be configured","configured",activity.connectionSetup.projection().optString("state"));
            until("!!document.getElementById('connect')",10000); clickEnabled("connect");
            until(STATUS+".connectionState==='connected'",20000,"connect to the admitted endpoint");
            until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",20000); clickEnabled("takeover");
            until(UI_MODE+"==='control'",8000);
            navigateFromAddress(url); awaitNavigation("https:",15000);
            // The remote document has no writable editor, so the bridge must
            // never become eligible and the tap hook must stay a no-op.
            JSONObject evidence=new JSONObject().put("case","IME-negative-control").put("url",url)
                .put("tapX",tapX).put("tapY",tapY).put("eligibleBeforeTap",remoteEditorState());
            touch(tapX,tapY);
            String shown="false";
            long deadline=SystemClock.elapsedRealtime()+4000;
            while(SystemClock.elapsedRealtime()<deadline){
                shown=String.valueOf(parseImeState(rawShell("dumpsys input_method")).opt("mInputShown"));
                if("true".equals(shown))break;
                SystemClock.sleep(200);
            }
            evidence.put("mInputShownAfterTap",shown)
                .put("attemptsAfterTap",showAttempts())
                .put("eligibleAfterTap",remoteEditorState())
                .put("stateAfterTap",focusState());
            writeRaw("ime-no-editor.json",evidence.toString(2)+"\n");
            assertEquals("A tap on a page with no writable remote editor must not raise the keyboard: "+evidence,
                "false",shown);
        }finally{getInstrumentation().runOnMainSync(()->activity.finish());}
    }

    private int showAttempts()throws Exception{
        final int[] value=new int[1];
        getInstrumentation().runOnMainSync(()->value[0]=activity.remoteIme.showAttempts());
        return value[0];
    }
    /** Wait until the bounded show retry stops issuing new attempts. */
    private int awaitShowRetriesSettled(long timeout)throws Exception{
        long deadline=SystemClock.elapsedRealtime()+timeout;
        int last=-1,stable=0;
        while(SystemClock.elapsedRealtime()<deadline){
            int now=showAttempts();
            stable=(now==last)?stable+1:0;
            if(stable>=8)return now;
            last=now;
            SystemClock.sleep(100);
        }
        return showAttempts();
    }
    /** Wait for the asynchronous client hide request to reach the system. */
    private JSONObject awaitImeDown(long timeout)throws Exception{
        long started=SystemClock.elapsedRealtime();
        String dump="";
        JSONObject state=null;
        do{
            dump=rawShell("dumpsys input_method");
            state=parseImeState(dump);
            if("false".equals(String.valueOf(state.opt("mInputShown")))){
                writeRaw("dumpsys-input-method-pre-tap.txt",imeLines(dump)+"focus "+focusState()+"\n");
                return state.put("hideElapsedMs",SystemClock.elapsedRealtime()-started);
            }
            SystemClock.sleep(100);
        }while(SystemClock.elapsedRealtime()-started<timeout);
        writeRaw("dumpsys-input-method-pre-tap.txt",imeLines(dump)+"focus "+focusState()+"\n");
        return state.put("hideElapsedMs",-1L);
    }
    private String argument(String name,String fallback){
        String value=((android.test.InstrumentationTestRunner)getInstrumentation()).getArguments().getString(name);
        return value==null||value.isEmpty()?fallback:value;
    }
    private UiDevice device(){return UiDevice.getInstance(getInstrumentation());}
    private String rawShell(String command)throws Exception{return device().executeShellCommand(command);}
    private void writeRaw(String name,String content)throws Exception{
        try(FileOutputStream out=new FileOutputStream(new File(evidenceDirectory(),name))){
            out.write(content.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        }
    }
    /** The endpoint the imported pairing actually published, never a guess. */
    private String admittedEndpoint()throws Exception{
        File endpoint=pairingMaterial("endpoint.txt");
        return endpoint.isFile()
            ? new String(java.nio.file.Files.readAllBytes(endpoint.toPath()),java.nio.charset.StandardCharsets.UTF_8).trim()
            : "";
    }
    /** The Host is the only navigation owner, so its projection is the URL proof. */
    private JSONObject awaitNavigation(String requiredScheme,long timeout)throws Exception{
        long deadline=SystemClock.elapsedRealtime()+timeout;
        JSONObject navigation=null;
        do{
            navigation=activity.network.navigation();
            if(navigation!=null&&navigation.optString("url","").startsWith(requiredScheme))return navigation;
            SystemClock.sleep(100);
        }while(SystemClock.elapsedRealtime()<deadline);
        fail("The Host never published a "+requiredScheme+" navigation state: "+(navigation==null?"null":navigation.toString()));
        return navigation;
    }
    /** Engine-confirmed editable target: the remote page really received the tap. */
    private JSONObject awaitEditableContext(long timeout)throws Exception{
        long deadline=SystemClock.elapsedRealtime()+timeout;
        JSONObject context=null;
        do{
            context=activity.network.editorContext();
            if(context!=null&&context.optBoolean("editable",false))return context;
            SystemClock.sleep(100);
        }while(SystemClock.elapsedRealtime()<deadline);
        fail("The remote tap did not focus an editable Host target: "+(context==null?"null":context.toString())
            +"\n"+touchGeometry());
        return context;
    }
    /** Polls the Host projection; the app never caches a local copy of the text. */
    private String awaitHostFieldText(String expected,long timeout)throws Exception{
        long deadline=SystemClock.elapsedRealtime()+timeout;
        String text="";
        do{
            JSONObject surrounding=activity.network.surrounding();
            text=surrounding==null?"":surrounding.optString("text","");
            if(text.contains(expected))return text;
            SystemClock.sleep(150);
        }while(SystemClock.elapsedRealtime()<deadline);
        return text;
    }
    /** Raw focus ownership across the tap -> eligible -> raise window. */
    private String focusState()throws Exception{
        final String[] out=new String[1];
        getInstrumentation().runOnMainSync(()->{
            android.view.View focused=activity.getCurrentFocus();
            RemoteImeView ime=activity.remoteIme;
            out[0]="windowFocus="+activity.hasWindowFocus()
                +" currentFocus="+(focused==null?"null":focused.getClass().getSimpleName()
                    +"@"+Integer.toHexString(System.identityHashCode(focused))
                    +(focused.getId()==android.view.View.NO_ID?"":"#"+focused.getId()))
                +" imeHasFocus="+ime.hasFocus()+" imeIsFocused="+ime.isFocused()
                +" imeFocusable="+ime.isFocusable()+"/"+ime.isFocusableInTouchMode()
                +" imeAttached="+ime.isAttachedToWindow()+" imeWindowFocus="+ime.hasWindowFocus()
                +" attempts="+ime.showAttempts();
        });
        return out[0]+" "+remoteEditorState();
    }
    /** The Host-owned editor projection the local IME bridge keys on. */
    private String remoteEditorState(){
        final String[] out=new String[1];
        getInstrumentation().runOnMainSync(()->{
            try{
                java.lang.reflect.Field field=NetworkSession.class.getDeclaredField("host");
                field.setAccessible(true);
                JSONObject host=(JSONObject)field.get(activity.network);
                RemoteInput.EditorContext editor=RemoteInput.EditorContext.parse(
                    host==null?null:host.optJSONObject("editor_context"));
                out[0]="eligible="+activity.network.remoteImeEligible()
                    +" focusRevision="+(editor==null?-1:editor.focusRevision())
                    +" editorRevision="+(editor==null?-1:editor.editorRevision())
                    +" identity="+RemoteInput.editorIdentity(editor);
            }catch(Exception reflection){out[0]="remote editor read failed: "+reflection;}
        });
        return out[0];
    }
    private void forceImeHiddenBeforeTap()throws Exception{
        getInstrumentation().runOnMainSync(()->activity.getSystemService(InputMethodManager.class)
            .hideSoftInputFromWindow(activity.remoteIme.getWindowToken(),0));
        String dump=rawShell("dumpsys input_method");
        JSONObject state=parseImeState(dump);
        writeRaw("dumpsys-input-method-pre-tap.txt",imeLines(dump)+"focus "+focusState()+"\n");
        assertEquals("IME must be hidden before causal remote tap: "+imeLines(dump),"false",String.valueOf(state.opt("mInputShown")));
    }
    private String selectedImePackage()throws Exception{
        String selected=rawShell("settings get secure default_input_method").trim();
        return selected.contains("/")?selected.substring(0,selected.indexOf('/')):selected;
    }
    private JSONObject pollImeRaised(long timeout)throws Exception{
        long started=SystemClock.elapsedRealtime();
        StringBuilder trace=new StringBuilder();
        StringBuilder focusTrace=new StringBuilder();
        JSONObject latest=null;
        String imePackage=selectedImePackage();
        do{
            String dump=rawShell("dumpsys input_method");
            long elapsed=SystemClock.elapsedRealtime()-started;
            JSONObject parsed=parseImeState(dump);
            boolean served=String.valueOf(parsed.opt("mServedView")).contains("RemoteImeView")
                &&parsed.opt("mServedInputConnection")!=JSONObject.NULL;
            boolean keyboardWindow=device().hasObject(By.pkg(imePackage));
            latest=parsed.put("elapsedMs",elapsed)
                .put("imeWindowPresent",keyboardWindow)
                .put("servedRemoteIme",served);
            String lines=imeLines(dump);
            String focus=focusState();
            focusTrace.append("elapsedMs=").append(elapsed).append(" ").append(focus).append('\n');
            latest.put("focus",focus);
            trace.append("elapsedMs=").append(elapsed).append(" imeWindowPresent=").append(keyboardWindow)
                .append(" ").append(focus).append('\n').append(lines).append('\n');
            boolean flagShown="true".equals(String.valueOf(parsed.opt("mInputShown")));
            boolean servedFocused="true".equals(String.valueOf(parsed.opt("servedViewFocused")));
            // Strict: the IME is really up AND our served view holds focus.
            if(served&&servedFocused&&flagShown){
                writeRaw("dumpsys-input-method.txt",trace.toString());
                writeRaw("focus-trace.txt",focusTrace.toString());
                writeRaw("ime-keyboard-raised.txt",rawShell("uiautomator dump /sdcard/ime-raised.xml >/dev/null 2>&1; cat /sdcard/ime-raised.xml"));
                latest.put("raiseElapsedMs",elapsed)
                    .put("raiseSignal","mInputShown+focusedRemoteImeView");
                return latest;
            }
            SystemClock.sleep(250);
        }while(SystemClock.elapsedRealtime()-started<timeout);
        writeRaw("dumpsys-input-method.txt",trace.toString());
        writeRaw("focus-trace.txt",focusTrace.toString());
        fail("System IME did not raise within "+timeout+"ms: "+latest);
        return latest;
    }
    private static String imeLines(String dump){
        StringBuilder lines=new StringBuilder();
        for(String line:dump.split("\\n")){
            if(line.contains("mInputShown")||line.contains("mCurId")||line.contains("mCurMethodId")
                ||line.contains("mServedView")||line.contains("mServedInputConnection"))lines.append(line).append('\n');
        }
        return lines.toString();
    }
    private JSONObject parseImeState(String dump)throws Exception{
        return new JSONObject()
            .put("mInputShown",orNull(firstMatch(dump,"mInputShown=(\\S+)")))
            .put("mIsInputViewShown",orNull(firstMatch(dump,"mIsInputViewShown=(\\S+)")))
            .put("mShowRequested",orNull(firstMatch(dump,"mShowRequested=(\\S+)")))
            .put("mServedInputConnection",orNull(firstMatch(dump,"mServedInputConnection=(\\S+)")))
            .put("mServedView",orNull(firstMatch(dump,"(?m)^\\s*mServedView=(.+)$")))
            .put("mCurMethodId",orNull(firstMatch(dump,"mCurMethodId=(\\S+)")!=null?firstMatch(dump,"mCurMethodId=(\\S+)"):firstMatch(dump,"mCurId=(\\S+)")))
            .put("mCurFocusedWindow",orNull(firstMatch(dump,"mCurFocusedWindow=(\\S+)")))
            .put("mCurFocusedWindowClient",orNull(firstMatch(dump,"mCurFocusedWindowClient=(\\S+)")))
            .put("mCurId",orNull(firstMatch(dump,"mCurId=(\\S+)")))
            .put("servedViewFocused",servedViewFocused(dump));
    }
    /** The second flag group of the served view carries Android's focused flag. */
    private static boolean servedViewFocused(String dump){
        java.util.regex.Matcher matcher=java.util.regex.Pattern
            .compile("(?m)^\\s*mServedView=\\S*\\{[0-9a-f]+\\s+(\\S+)\\s+(\\S+)").matcher(dump);
        return matcher.find()&&matcher.group(2).contains("F");
    }
    private static Object orNull(String value){return value==null?JSONObject.NULL:value;}
    private static String firstMatch(String text,String pattern){
        java.util.regex.Matcher matcher=java.util.regex.Pattern.compile(pattern).matcher(text);
        return matcher.find()?matcher.group(1):null;
    }
    /**
     * Real touches on the raised system keyboard's own keys. The IME commits the
     * text through the app's InputConnection, which is the same path a user's
     * finger takes; no test-only IME call is used.
     */
    private void typeThroughSystemIme(String imePackage,String text)throws Exception{
        StringBuilder trace=new StringBuilder();
        writeRaw("ime-keyboard.txt",rawShell("uiautomator dump /sdcard/ime-window.xml >/dev/null 2>&1; cat /sdcard/ime-window.xml"));
        if(imePackage==null||imePackage.isEmpty())imePackage=selectedImePackage();
        for(int index=0;index<text.length();index++){
            char character=text.charAt(index);
            UiObject2 key=imeKey(imePackage,character);
            if(key==null){
                writeRaw("ime-keyboard.txt",rawShell("uiautomator dump /sdcard/ime-window.xml >/dev/null 2>&1; cat /sdcard/ime-window.xml"));
                fail("The raised system IME has no key for '"+character+"' in "+imePackage);
            }
            android.graphics.Rect bounds=key.getVisibleBounds();
            trace.append(character).append(' ').append(bounds.toShortString()).append(' ')
                .append(String.valueOf(key.getContentDescription())).append('\n');
            key.click();
            SystemClock.sleep(250);
        }
        writeRaw("ime-key-touches.txt",trace.toString());
    }
    private UiObject2 imeKey(String imePackage,char character){
        String lower=String.valueOf(character).toLowerCase(java.util.Locale.ROOT);
        String upper=lower.toUpperCase(java.util.Locale.ROOT);
        for(String candidate:new String[]{lower,upper}){
            UiObject2 key=device().findObject(By.pkg(imePackage).desc(candidate));
            if(key==null)key=device().findObject(By.pkg(imePackage).text(candidate));
            if(key==null)key=device().findObject(By.res(imePackage,"key_"+candidate));
            if(key!=null)return key;
        }
        // LatinIME changes resource ids across images. Inspect its real
        // accessibility nodes and match visible labels or content descriptions.
        for(UiObject2 candidate:device().findObjects(By.pkg(imePackage))){
            String text=candidate.getText(),description=String.valueOf(candidate.getContentDescription());
            if(lower.equalsIgnoreCase(text)||lower.equalsIgnoreCase(description))return candidate;
        }
        return null;
    }

    /** Coarse real-render signature of the Host video surface (never a local DOM read). */
    private String frameSignature(Bitmap image){
        Bitmap normalized=Bitmap.createScaledBitmap(image,32,32,true);
        StringBuilder out=new StringBuilder();
        for(int y=0;y<32;y++)for(int x=0;x<32;x++){
            int c=normalized.getPixel(x,y);
            out.append((char)('a'+((Color.red(c)+Color.green(c)+Color.blue(c))/3)*25/255));
        }
        return out.toString();
    }
    private static int signatureDistance(String a,String b){
        int different=0;
        for(int i=0;i<Math.min(a.length(),b.length());i++)if(a.charAt(i)!=b.charAt(i))different++;
        return different;
    }
    /** Host-published editor projection; null when no editable target is focused. */
    private JSONObject editableContext(){return activity.network.editorContext();}
    private boolean editableFocused(){JSONObject context=editableContext();return context!=null&&context.optBoolean("editable",false);}

    /**
     * BB04 bounded slice. The row asks for real drags on the remote video, content
     * that follows the drag, release termination, and no phantom tap. Every
     * assertion below reads real remote state: the Host-published editor
     * projection, the Host gesture lease, or the Host-encoded video surface.
     */
    public void testBb04GestureSlice() throws Exception{
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try{
            assertEquals("Admitted pairing must be configured","configured",activity.connectionSetup.projection().optString("state"));
            until("!!document.getElementById('connect')",10000); clickEnabled("connect");
            until(STATUS+".connectionState==='connected'",20000,"connect to the admitted endpoint");
            until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",20000); clickEnabled("takeover");
            until(UI_MODE+"==='control'",8000);
            String url=argument("pageUrl","https://www.selenium.dev/selenium/web/web-form.html");
            float tapX=Float.parseFloat(argument("tapX","196")),tapY=Float.parseFloat(argument("tapY","107"));
            navigateFromAddress(url); awaitNavigation("https:",15000);
            JSONObject evidence=new JSONObject().put("case","BB04").put("url",url)
                .put("tapX",tapX).put("tapY",tapY).put("hostScrollProjectionAvailable",false);
            assertFalse("Precondition: no editable target before any touch",editableFocused());

            // A real tap on the remote input proves the tap path and gives the
            // element identity the drag contrast is measured against.
            touch(tapX,tapY); awaitEditableContext(8000);
            evidence.put("tapEditable",editableFocused()).put("tapTarget",String.valueOf(editableContext().optString("target_id")));
            assertTrue("A real tap must focus the remote editable target",editableFocused());

            // Blank-space tap must end the old edit (BB08 overlap, real Host state).
            touch(tapX,Float.parseFloat(argument("blankY","520")));
            long blankDeadline=SystemClock.elapsedRealtime()+6000;
            while(editableFocused()&&SystemClock.elapsedRealtime()<blankDeadline)SystemClock.sleep(100);
            evidence.put("blankTapEditable",editableFocused());
            assertFalse("Tapping blank space must terminate the old remote edit",editableFocused());

            // A real drag whose release point is the editable target must not click it.
            swipe(tapX,Math.max(20f,tapY-90f),tapY);
            until(STATUS+".inputReady",6000); SystemClock.sleep(700);
            evidence.put("dragOverTargetEditable",editableFocused())
                .put("dragGestureState",localGestureState()).put("dragLeaseFencesAgents",hostLeaseFencesAgents());
            assertFalse("A drag released over the target must not click it",editableFocused());
            assertFalse("A drag must leave no open Host gesture lease",hostLeaseFencesAgents());

            // The same point still clicks after the drag, so the refusal above is
            // drag-versus-tap discrimination and not a broken tap path.
            touch(tapX,tapY); awaitEditableContext(8000);
            evidence.put("tapAfterDragEditable",editableFocused());
            assertTrue("A real tap after the drag must still focus the target",editableFocused());

            // Real drags must move the presented remote content, and the reverse
            // drag must restore it. The signature comes from the Host-encoded
            // video surface, so it is real remote rendering, not a local proxy.
            Bitmap start=capture("bb04-before-drag");
            String startSignature=frameSignature(start);
            swipe(tapX,Float.parseFloat(argument("dragFromY","300")),Float.parseFloat(argument("dragToY","90")));
            until(STATUS+".inputReady",6000); SystemClock.sleep(1200);
            Bitmap dragged=capture("bb04-after-drag");
            String draggedSignature=frameSignature(dragged);
            swipe(tapX,Float.parseFloat(argument("dragToY","90")),Float.parseFloat(argument("dragFromY","300")));
            until(STATUS+".inputReady",6000); SystemClock.sleep(1200);
            Bitmap restored=capture("bb04-after-reverse-drag");
            String restoredSignature=frameSignature(restored);
            int forward=signatureDistance(startSignature,draggedSignature);
            int back=signatureDistance(startSignature,restoredSignature);
            evidence.put("renderedFrames",activity.annex.snapshot().getLong("renderedFrames"))
                .put("forwardDragChangedCells",forward).put("reverseDragChangedCells",back)
                .put("scrollDragGestureState",localGestureState())
                .put("scrollLeaseFencesAgents",hostLeaseFencesAgents())
                .put("inputReadyAfterDrag",js(STATUS+".inputReady"));
            assertTrue("A real drag must change the presented remote content: forward="+forward,forward>16);
            assertTrue("The reverse drag must move the content back toward the start: forward="+forward+" back="+back,back<forward);
            assertFalse("Release must terminate the gesture with no open Host lease",hostLeaseFencesAgents());
            until(STATUS+".inputReady",6000);
            writeRaw("bb04-gesture.json",evidence.toString(2)+"\n");
        }finally{getInstrumentation().runOnMainSync(()->activity.finish());}
    }

    private long documentRevision()throws Exception{return new JSONObject(js(STATUS)).optLong("documentRevision",-1);}
    private String hostUrl(){JSONObject navigation=activity.network.navigation();return navigation==null?"":navigation.optString("url","");}

    /**
     * BB10 bounded slice: real client close/reopen, a real network interrupt and
     * restore, and stale-generation fencing. Every assertion reads the
     * Host-published session/document projection or the Host input lease.
     */
    public void testBb10ReconnectSlice() throws Exception{
        String pageUrl=argument("pageUrl","https://www.selenium.dev/selenium/web/web-form.html");
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        JSONObject evidence=new JSONObject().put("case","BB10").put("url",pageUrl);
        try{
            assertEquals("Admitted pairing must be configured","configured",activity.connectionSetup.projection().optString("state"));
            until("!!document.getElementById('connect')",10000); clickEnabled("connect");
            until(STATUS+".connectionState==='connected'",20000,"connect to the admitted endpoint");
            until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",20000); clickEnabled("takeover");
            until(UI_MODE+"==='control'",20000);
            navigateFromAddress(pageUrl); awaitNavigation("https:",15000);
            String sessionBefore=js(STATUS+".sessionId");
            String urlBefore=hostUrl();
            long revisionBefore=documentRevision();
            evidence.put("sessionBefore",sessionBefore).put("urlBefore",urlBefore).put("revisionBefore",revisionBefore);

            // Gate A: close the client and reopen it. Detach must not delete the
            // Host session, and the same document must come back.
            getInstrumentation().runOnMainSync(()->activity.finish());
            SystemClock.sleep(4000);
            activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            assertEquals("Reopened client keeps the admitted pairing","configured",activity.connectionSetup.projection().optString("state"));
            until("!!document.getElementById('connect')",10000); clickEnabled("connect");
            until(STATUS+".connectionState==='connected'",20000,"reconnect after client reopen");
            until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",20000);
            boolean control=false;
            clickEnabled("takeover");
            long takeoverDeadline=SystemClock.elapsedRealtime()+20000;
            while(SystemClock.elapsedRealtime()<takeoverDeadline){
                if("true".equals(js(UI_MODE+"==='control'"))){control=true;break;}
                SystemClock.sleep(200);
            }
            evidence.put("controlAfterReopen",control).put("uiModeAfterReopen",js(UI_MODE));
            String sessionAfterReopen=js(STATUS+".sessionId");
            String urlAfterReopen=hostUrl();
            evidence.put("sessionAfterReopen",sessionAfterReopen).put("urlAfterReopen",urlAfterReopen)
                .put("reopenSameSession",sessionBefore.equals(sessionAfterReopen))
                .put("reopenSameDocument",urlBefore.equals(urlAfterReopen));
            assertEquals("Detach must not delete the Host session",sessionBefore,sessionAfterReopen);
            assertEquals("Reconnect must return the same document",urlBefore,urlAfterReopen);
            writeRaw("bb10-reconnect.json",evidence.toString(2)+"\n");

            // Gate B: one real remote click must produce exactly one Host-side
            // effect, and a real network interrupt plus restore must not replay it.
            // The one-time effect is the Host-published editor focus projection.
            String contextAfterClick=null;
            long revisionAfterClick=documentRevision();
            if(control){
                touch(196,107); awaitEditableContext(8000);
                contextAfterClick=String.valueOf(editableContext());
                revisionAfterClick=documentRevision();
                evidence.put("clickTarget",editableContext().optString("target_id"));
            }
            evidence.put("editorContextAfterClick",contextAfterClick).put("revisionAfterClick",revisionAfterClick);

            rawShell("cmd connectivity airplane-mode enable");
            long offlineDeadline=SystemClock.elapsedRealtime()+20000;
            String offlineState=js(STATUS+".connectionState");
            while("\"connected\"".equals(offlineState)&&SystemClock.elapsedRealtime()<offlineDeadline){
                SystemClock.sleep(500); offlineState=js(STATUS+".connectionState");
            }
            evidence.put("stateWhileOffline",offlineState);
            rawShell("cmd connectivity airplane-mode disable");
            long restoreDeadline=SystemClock.elapsedRealtime()+45000;
            boolean restored=false;
            while(SystemClock.elapsedRealtime()<restoreDeadline){
                SystemClock.sleep(1000);
                if("\"connected\"".equals(js(STATUS+".connectionState"))&&"true".equals(js(STATUS+".inputReady"))){restored=true;break;}
            }
            evidence.put("autoRecovered",restored).put("stateAfterRestore",js(STATUS+".connectionState"))
                .put("errorAfterRestore",new JSONObject(js(STATUS)).optString("error"));
            if(!restored){
                // Distinguish "no automatic recovery" from "cannot recover at all"
                // with one real user reconnect action through the ordinary UI.
                clickEnabled("connect");
                long manualDeadline=SystemClock.elapsedRealtime()+30000;
                while(SystemClock.elapsedRealtime()<manualDeadline){
                    SystemClock.sleep(1000);
                    if("\"connected\"".equals(js(STATUS+".connectionState"))&&"true".equals(js(STATUS+".inputReady")))break;
                }
                evidence.put("manualReconnectState",js(STATUS+".connectionState"))
                    .put("manualReconnectInputReady",js(STATUS+".inputReady"));
            }
            String sessionAfterRestore=js(STATUS+".sessionId");
            long revisionAfterRestore=documentRevision();
            String contextAfterRestore=String.valueOf(editableContext());
            evidence.put("restored",restored).put("sessionAfterRestore",sessionAfterRestore)
                .put("revisionAfterRestore",revisionAfterRestore)
                .put("editorContextAfterRestore",contextAfterRestore).put("urlAfterRestore",hostUrl());
            writeRaw("bb10-reconnect.json",evidence.toString(2)+"\n");
            assertTrue("The client must recover after the network is restored",restored);
            assertEquals("Recovery must keep the same Host session",sessionBefore,sessionAfterRestore);
            assertEquals("Recovery must not replay the click or submit",revisionAfterClick,revisionAfterRestore);
            if(control)assertEquals("Recovery must not re-fire the click's Host effect",contextAfterClick,contextAfterRestore);

            // Gate C: an old generation cannot write; the live session survives it.
            String liveSession=js(STATUS+".sessionId");
            long framesBeforeRejection=activity.annex.snapshot().getLong("renderedFrames");
            getInstrumentation().runOnMainSync(()->activity.network.command(1,Long.MAX_VALUE,0,0,0,0,""));
            until("String("+STATUS+".error).includes('STALE_CONTROL')",8000);
            until(STATUS+".renderedFrames>"+framesBeforeRejection+" && "+STATUS+".inputReady",8000);
            evidence.put("staleWriteError",new JSONObject(js(STATUS)).optString("error"))
                .put("sessionAfterStaleWrite",js(STATUS+".sessionId"))
                .put("stateAfterStaleWrite",js(STATUS+".connectionState"));
            assertEquals("A stale generation must not keep the connection", "\"connected\"",js(STATUS+".connectionState"));
            assertEquals("A stale generation must not replace the session",liveSession,js(STATUS+".sessionId"));
            writeRaw("bb10-reconnect.json",evidence.toString(2)+"\n");
        }finally{
            rawShell("cmd connectivity airplane-mode disable");
            getInstrumentation().runOnMainSync(()->activity.finish());
        }
    }

    /** Raw transport sample: the app projection plus endpoint reachability. */
    private JSONObject transportSample(String phase,int index,long startedAt)throws Exception{
        JSONObject status=new JSONObject(js(STATUS));
        return new JSONObject().put("phase",phase).put("index",index)
            .put("elapsedMs",SystemClock.elapsedRealtime()-startedAt)
            .put("connectionState",status.opt("connectionState")).put("state",status.opt("state"))
            .put("generation",status.opt("generation")).put("renderedFrames",status.opt("renderedFrames"))
            .put("inputReady",status.opt("inputReady")).put("error",status.opt("error"))
            .put("transport",status.opt("transport")).put("sessionId",status.opt("sessionId"))
            .put("documentRevision",status.opt("documentRevision"));
    }
    private String endpointReachability(){
        try{return rawShell("ping -c 1 -W 2 192.168.0.3 >/dev/null 2>&1; echo $?").trim();}
        catch(Exception failure){return "unavailable";}
    }

    /**
     * BB10 isolation probe: raw transport state across a real network interrupt
     * and restore. The probe only records, so a stall still leaves the complete
     * sample series on disk for diagnosis.
     */
    public void testBb10TransportProbe() throws Exception{
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        JSONArray samples=new JSONArray();
        long startedAt=SystemClock.elapsedRealtime();
        try{
            until("!!document.getElementById('connect')",10000);
            clickEnabled("connect");
            boolean ready=false;
            for(int i=0;i<40;i++){
                samples.put(transportSample("connect",i,startedAt));
                if("true".equals(js(STATUS+".inputReady"))&&Long.parseLong(js(STATUS+".renderedFrames"))>=2){ready=true;break;}
                SystemClock.sleep(1000);
            }
            samples.put(new JSONObject().put("phase","connect-result").put("ready",ready)
                .put("endpointReachability",endpointReachability()).put("elapsedMs",SystemClock.elapsedRealtime()-startedAt));
            if(ready){
                clickEnabled("takeover");
                long controlDeadline=SystemClock.elapsedRealtime()+20000;
                while(SystemClock.elapsedRealtime()<controlDeadline){
                    if("true".equals(js(UI_MODE+"==='control'")))break;
                    SystemClock.sleep(200);
                }
                samples.put(new JSONObject().put("phase","control").put("uiMode",js(UI_MODE))
                    .put("elapsedMs",SystemClock.elapsedRealtime()-startedAt));
                samples.put(transportSample("pre-interrupt",0,startedAt)
                    .put("endpointReachability",endpointReachability()));
                rawShell("cmd connectivity airplane-mode enable");
                for(int i=0;i<20;i++){
                    SystemClock.sleep(1000);
                    samples.put(transportSample("offline",i,startedAt)
                        .put("endpointReachability",i%5==0?endpointReachability():JSONObject.NULL));
                }
                rawShell("cmd connectivity airplane-mode disable");
                for(int i=0;i<60;i++){
                    SystemClock.sleep(1000);
                    samples.put(transportSample("restore",i,startedAt)
                        .put("endpointReachability",i%5==0?endpointReachability():JSONObject.NULL));
                    if("true".equals(js(STATUS+".inputReady")))break;
                }
            }
            writeRaw("bb10-transport.json",samples.toString(2)+"\n");
            writeRaw("bb10-transport-logcat.txt",rawShell("logcat -d -v threadtime -t 1200"));
        }finally{
            rawShell("cmd connectivity airplane-mode disable");
            getInstrumentation().runOnMainSync(()->activity.finish());
        }
    }

    public void testRealNetworkControlAndFrames()throws Exception{
        activity=(MainActivity)getInstrumentation().startActivitySync(new Intent(getInstrumentation().getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        try{
            importFixturePairing();
            until("!!document.getElementById('connect')",10000);clickEnabled("connect");
            until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",15000);
            until(UI_MODE+"==='observe'",5000);
            assertEquals("Observer cannot navigate from the UI","true",js(UI_NAV_DISABLED));
            // The chrome WebView fills the window, so the presentation owner must keep
            // exactly one of {native stage, chrome} on top, and real touches must reach
            // whichever of them is presented.
            assertEquals("Browsing must keep the native stage above the chrome: "+touchGeometry(),
                "FrameLayout",topChild());
            touchChromeVerified("drawer-toggle",()->"true".equals(js("document.querySelector('main').dataset.drawerOpen==='true'")));
            until("document.querySelector('main').dataset.drawerOpen==='true'",6000);
            assertEquals("The overlay presentation must raise the chrome above the native stage: "+touchGeometry(),
                "WebView",topChild());
            touchChrome("drawer-close");
            until("document.querySelector('main').dataset.drawerOpen==='false'",6000);
            assertEquals("Closing the drawer must restore the native stage: "+touchGeometry(),
                "FrameLayout",topChild());
            String initialUrl=new String(android.util.Base64.decode(
                ((android.test.InstrumentationTestRunner)getInstrumentation()).getArguments().getString("initialUrlBase64"),
                android.util.Base64.DEFAULT),java.nio.charset.StandardCharsets.UTF_8);
            String navigationSession=js(STATUS+".sessionId");
            clickEnabled("takeover");until(UI_MODE+"==='control' && "+STATUS+".inputReady",6000);
            queueNavigationBehindViewport();
            navigateFromAddress("about:blank");
            navigateFromAddress(initialUrl);
            assertEquals("Navigation retains the Host Session",navigationSession,js(STATUS+".sessionId"));
            until("!document.getElementById('release').disabled",5000);
            clickEnabled("release");until(UI_MODE+"==='observe'",6000);
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
            clickEnabled("takeover");until(UI_MODE+"==='control'",6000);
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
            long scrollBefore=Long.parseLong(js("window.scrollY"));
            swipe(30,350,100);
            until(STATUS+".inputReady",6000);
            assertFalse("A drag must leave no open Host lease: "+localGestureState(),hostLeaseFencesAgents());
            long scrollDeadline=SystemClock.elapsedRealtime()+5000;
            Bitmap scrolled;
            do {SystemClock.sleep(100);scrolled=capture("scrolled");}
            while(Color.blue(scrolled.getPixel(30,30))<160&&SystemClock.elapsedRealtime()<scrollDeadline);
            assertTrue("Swipe exposes lower blue page content",Color.blue(scrolled.getPixel(30,30))>160
                &&Color.red(scrolled.getPixel(30,30))<100&&Color.green(scrolled.getPixel(30,30))<100);
            swipe(30,100,350);
            until(STATUS+".inputReady",6000);
            assertFalse("The reverse drag must leave no open Host lease: "+localGestureState(),hostLeaseFencesAgents());
            long restoreDeadline=SystemClock.elapsedRealtime()+5000;
            Bitmap restored;
            do {SystemClock.sleep(100);restored=capture("scroll-restored");}
            while(Color.green(restored.getPixel(30,30))<160&&SystemClock.elapsedRealtime()<restoreDeadline);
            assertTrue("Reverse swipe restores the same changed button",Color.green(restored.getPixel(30,30))>160
                &&Color.red(restored.getPixel(30,30))<100);
            touch(100,125);SystemClock.sleep(250);
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
            SystemClock.sleep(700);clickEnabled("release");until(UI_MODE+"==='observe'",6000);
            Bitmap screenshot=getInstrumentation().getUiAutomation().takeScreenshot();assertNotNull(screenshot);
            try(var output=new FileOutputStream(new File(activity.getFilesDir(),"network-evidence/screen.png"))){assertTrue(screenshot.compress(Bitmap.CompressFormat.PNG,100,output));}
            clickEnabled("takeover");until(UI_MODE+"==='control' && "+STATUS+".inputReady",6000);
            // The Host keeps engine focus across takeover, so the remote IME
            // bridge must own the same target again.
            assertHostFieldFocused("The pre-disconnect takeover");
            InputConnection disconnectInput=openRemoteIme();
            imeEdit(disconnectInput,connection->{assertTrue(connection.beginBatchEdit());assertTrue(connection.setComposingText("disconnect-me",1));});
            awaitHostLease("Pre-disconnect composition must be pending on the Host",true);
            // Close the IME batch while the connection is alive; the disconnect
            // immediately afterwards owns the terminal event of the open lease.
            imeEdit(disconnectInput,InputConnection::endBatchEdit);
            clickEnabled("disconnect");
            until(STATUS+".released",6000);
            awaitRemoteImeReleased("Disconnect must release the remote IME target");
            disconnectCompositionReleased=true;
            long frames=activity.annex.snapshot().getLong("renderedFrames");SystemClock.sleep(500);
            assertEquals("No stale decoder callback after disconnect",frames,activity.annex.snapshot().getLong("renderedFrames"));
            clickEnabled("connect");until(STATUS+".renderedFrames>=2 && "+STATUS+".inputReady",15000);
            assertEquals("Reconnect preserves live document",session,js(STATUS+".sessionId"));
            JSONObject statusEvidence=new JSONObject(js(STATUS));
            Bitmap reconnected=capture("reconnected");assertTrue(Color.green(reconnected.getPixel(30,30))>160&&Color.red(reconnected.getPixel(30,30))<100);
            // Disconnecting the human controller suspends Agent control. Restore
            // it only through the real explicit takeover/release UI protocol.
            clickEnabled("takeover");until(UI_MODE+"==='control' && "+STATUS+".inputReady",6000);
            // The Host owns the terminal event for the lease whose attachment went
            // away. A fresh composition that the Host accepts is the proof that
            // the abandoned lease is gone; it is cancelled again below.
            assertHostFieldFocused("The post-reconnect takeover");
            InputConnection reconnectInput=openRemoteIme();
            imeEdit(reconnectInput,connection->{assertTrue(connection.beginBatchEdit());assertTrue(connection.setComposingText("probe",1));});
            reconnectCompositionAccepted=true;
            imeEdit(reconnectInput,NetworkDeviceTest::escapeComposition);
            disconnectCompositionCancelled=disconnectCompositionReleased&&reconnectCompositionAccepted;
            assertTrue("Disconnect must terminate the abandoned composition lease",disconnectCompositionCancelled);
            until("!document.getElementById('release').disabled",5000);
            clickEnabled("release");until(UI_MODE+"==='observe'",6000);
            // Assert on the test thread: a failure thrown inside runOnMainSync escapes the
            // looper and kills the process instead of reporting the gate.
            boolean[] backgrounded={false};
            getInstrumentation().runOnMainSync(()->backgrounded[0]=activity.moveTaskToBack(true));
            assertTrue("The probe must be able to leave the foreground",backgrounded[0]);
            until(STATUS+".released",6000);
            // The status the client observes at its own completion point. The runner
            // compares this epoch with the Host epoch recorded when this process
            // exited; a mid-run snapshot is not comparable after a later takeover.
            JSONObject completionStatus=new JSONObject(js(STATUS));
            JSONObject result=new JSONObject().put("networkFrames",true).put("observerTouchIgnored",true).put("takeoverPixels",true).put("addressNavigation",true)
                .put("runId",((android.test.InstrumentationTestRunner)getInstrumentation()).getArguments().getString("runId"))
                .put("viewport",viewport)
                .put("statusEvidence",statusEvidence)
                .put("completionStatus",completionStatus)
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
                .put("compositionCommitted",compositionCommitted).put("unfinishedCompositionDropped",unfinishedCompositionDropped)
                .put("disconnectCompositionCancelled",disconnectCompositionCancelled)
                .put("imeTextEvidence",new File(evidenceDirectory(),"ime-text.png").isFile())
                .put("imeTextPainted",compositionCommitted&&chineseInkPixels>=0).put("chineseInkPixels",chineseInkPixels).put("chineseInkAdded",chineseInkAdded);
            // The Host is the only composition owner: each flag above is the
            // Host's own receipt or lease projection, never a local mirror.
            result.put("imeEvidence",new JSONObject()
                .put("owner","RemoteImeView")
                .put("compositionStarted","Host editing receipt state=pending for composition_begin")
                .put("compositionSendDisabled","Host input_lease fences other Agent operations while the composition is pending")
                .put("compositionCancelled","Host editing receipt state=cancelled for composition_cancel")
                .put("compositionCommitted","Host editing receipt state=applied for composition_commit")
                .put("unfinishedCompositionDropped","cancelled composition left no ink on the Host field")
                .put("disconnectCompositionCancelled","disconnect released the bridge target and the Host accepted a fresh composition")
                .put("importedEndpoint",admittedEndpoint()));
            result.put("displayedRevisionFenced",true);
            result.put("swipeDoesNotClick",true).put("scrollPixels",true);
            result.put("cancelledGestureIgnored",true).put("staleViewportGestureIgnored",true);
            result.put("rotationPreservesState",true);
            result.put("landscapeViewport",landscapeViewport).put("portraitViewport",portraitViewport);
            try(var output=new FileOutputStream(new File(activity.getFilesDir(),"network-evidence/result.json"))){output.write(result.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));}
        }finally{getInstrumentation().runOnMainSync(()->activity.finish());}
    }
}
