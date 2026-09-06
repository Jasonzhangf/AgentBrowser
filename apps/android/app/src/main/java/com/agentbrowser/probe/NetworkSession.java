package com.agentbrowser.probe;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import org.json.JSONObject;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/** Platform lifetime only. Host status remains an authoritative projection. */
final class NetworkSession {
    interface FrameSink { CompletableFuture<AnnexBDecoder.Receipt> submit(NetworkFrame frame,long token,long generation); }
    private final Context context;
    private final FrameSink sink;
    private final Runnable release;
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ScheduledExecutorService worker=Executors.newSingleThreadScheduledExecutor();
    private long handle,token,generation,shownEpoch;
    private record Viewport(int width,int height,boolean landscape) { }
    private Viewport requestedViewport,submittedViewport;
    private boolean running,closed,framePending,commandPending;
    private NetworkFrame displayed;
    private JSONObject host;
    private String state="idle",error,shownMode="observe";
    NetworkSession(Context context,FrameSink sink,Runnable release){this.context=context.getApplicationContext();this.sink=sink;this.release=release;}
    synchronized boolean active(){return running||state.equals("connecting")||state.equals("stopping");}
    synchronized boolean connected(){return running&&handle!=0;}
    synchronized boolean current(long value){return connected()&&token==value;}
    synchronized boolean inputReady(){return connected()&&!commandPending&&displayed!=null
        &&java.util.Objects.equals(requestedViewport,submittedViewport)&&host!=null&&!host.optBoolean("viewport_pending")
        &&displayed.documentRevision==host.optLong("document_revision",-1)
        &&displayed.viewportRevision==host.optLong("viewport_revision",-1);}
    synchronized long epoch(){return shownEpoch;}
    synchronized boolean humanShown(){return shownMode.equals("control");}
    synchronized void declareViewport(int cssWidth,int cssHeight,boolean landscape){
        if(cssWidth<=0||cssHeight<=0||cssWidth>4096||cssHeight>4096||(long)cssWidth*cssHeight>4194304)
            throw new IllegalArgumentException("INVALID_VIEWPORT");
        requestedViewport=new Viewport(cssWidth,cssHeight,landscape);
        flushViewport();
    }
    private synchronized void flushViewport(){
        if(!connected()||commandPending||requestedViewport==null||requestedViewport.equals(submittedViewport))return;
        Viewport viewport=requestedViewport;
        command(6,0,viewport.width(),viewport.height(),viewport.landscape()?1:0,0,"");
        submittedViewport=viewport;
    }
    private static long next(long value){if(value==Long.MAX_VALUE)throw new IllegalStateException("GENERATION_EXHAUSTED");return value+1;}
    synchronized JSONObject snapshot(JSONObject media){
        try{
            String mode="observe";long epoch=0;
            if(host!=null){
                JSONObject control=host.getJSONObject("control"),phase=control.getJSONObject("phase");epoch=control.getLong("epoch");
                boolean own=phase.optLong("attachment_id",-1)==host.optLong("attachment_id",-2);
                if(own&&phase.getString("type").equals("human"))mode="control";
                else if(own&&phase.getString("type").equals("waiting"))mode="waiting";
            }
            shownEpoch=epoch;shownMode=mode;
            String viewState=switch(state){case "connecting"->"starting";case "error"->"error";case "stopped"->"stopped";case "stopping"->"stopping";default->media.optString("state","idle");};
            JSONObject value=new JSONObject().put("state",viewState).put("generation",generation)
                .put("renderedFrames",media.optInt("renderedFrames",0)).put("released",!active()&&handle==0&&media.optBoolean("released"))
                .put("codec",media.optString("codec","")).put("error",error==null?JSONObject.NULL:error)
                .put("source","network").put("connectionState",state).put("controlMode",mode).put("epoch",epoch)
                .put("inputReady",inputReady()).put("pending",commandPending||framePending?"busy":JSONObject.NULL)
                .put("networkConfigured",new File(context.getFilesDir(),"pairing").isDirectory());
            if(host!=null)value.put("sessionId",host.getString("session_id")).put("documentRevision",host.getLong("document_revision")).put("viewportRevision",host.getLong("viewport_revision"));
            if(displayed!=null)value.put("displayedPtsUs",displayed.ptsUs).put("displayedTicket",displayed.ticket)
                .put("displayedDocumentRevision",displayed.documentRevision).put("displayedViewportRevision",displayed.viewportRevision);
            return value;
        }catch(org.json.JSONException invalid){throw new IllegalStateException("INVALID_HOST_STATUS",invalid);}
    }
    synchronized void connect(){
        if(closed)throw new IllegalStateException("HOST_INACTIVE");
        if(active()||handle!=0)throw new IllegalStateException("NETWORK_BUSY");
        token=next(token);generation=next(generation);long expected=token;
        state="connecting";error=null;host=null;displayed=null;shownEpoch=0;shownMode="observe";
        submittedViewport=null;
        worker.execute(()->open(expected));
    }
    private void open(long expected){
        long opened=0;
        try{
            NativeConnection.load();
            opened=NativeConnection.open(new String(read("endpoint.txt"),StandardCharsets.UTF_8).trim(),read("ca.der"),read("client.der"),read("key.der"));
            JSONObject status=new JSONObject(NativeConnection.command(opened,0,0,0,0,0,0,0,""));
            synchronized(this){if(closed||token!=expected){NativeConnection.close(opened);return;}handle=opened;host=status;running=true;state="connected";flushViewport();}
            poll(expected);
        }catch(Exception failure){
            if(opened!=0){try{NativeConnection.close(opened);}catch(Exception close){failure.addSuppressed(close);}}
            synchronized(this){if(token==expected){handle=0;failState(failure);}}
            main.post(release);
        }
    }
    private void poll(long expected){
        long nativeHandle;synchronized(this){if(!current(expected))return;nativeHandle=handle;}
        try{
            NetworkFrame frame=NativeConnection.frame(nativeHandle);
            if(frame!=null){
                long decodeGeneration;
                synchronized(this){
                    if(!current(expected))return;
                    if(displayed==null||displayed.codedWidth!=frame.codedWidth||displayed.codedHeight!=frame.codedHeight
                        ||displayed.visibleWidth!=frame.visibleWidth||displayed.visibleHeight!=frame.visibleHeight)generation=next(generation);
                    decodeGeneration=generation;framePending=true;
                }
                CompletableFuture<AnnexBDecoder.Receipt> rendered=new CompletableFuture<>();
                main.post(()->{
                    try{
                        if(!current(expected))throw new IllegalStateException("STALE_CONNECTION");
                        sink.submit(frame,expected,decodeGeneration).whenComplete((receipt,failure)->{
                            if(failure!=null)rendered.completeExceptionally(failure);else rendered.complete(receipt);
                        });
                    }catch(Exception failure){rendered.completeExceptionally(failure);}
                });
                rendered.get(6,TimeUnit.SECONDS); // Only the worker waits; never the main looper.
                synchronized(this){if(!current(expected))return;}
                NativeConnection.acknowledge(nativeHandle,frame.ticket);
                synchronized(this){if(!current(expected))return;displayed=frame;framePending=false;}
            }
            JSONObject status=new JSONObject(NativeConnection.command(nativeHandle,0,0,0,0,0,0,0,""));
            synchronized(this){if(!current(expected))return;host=status;}
            worker.schedule(()->poll(expected),30,TimeUnit.MILLISECONDS);
        }catch(Exception failure){terminate(expected,failure);}
    }
    synchronized void command(int op,long epoch,double x,double y,double dx,double dy,String text){
        if(!connected())throw new IllegalStateException("NETWORK_NOT_CONNECTED");
        if(commandPending)throw new IllegalStateException("OPERATION_PENDING");
        if(op>=3&&op!=6&&!inputReady())throw new IllegalStateException("DISPLAY_NOT_READY");
        long expected=token,nativeHandle=handle,ticket=displayed==null?0:displayed.ticket;commandPending=true;error=null;
        worker.execute(()->{
            try{
                synchronized(this){if(!current(expected))return;}
                String result=NativeConnection.command(nativeHandle,op,epoch,ticket,x,y,dx,dy,text==null?"":text);
                JSONObject status=new JSONObject(op<=2||op==6?result:NativeConnection.command(nativeHandle,0,0,0,0,0,0,0,""));
                synchronized(this){if(current(expected)){host=status;commandPending=false;flushViewport();}}
            }catch(Exception failure){terminate(expected,failure);}
        });
    }
    synchronized void disconnect(){
        if(!active()&&handle==0)return;
        token=next(token);generation=next(generation);running=false;framePending=false;commandPending=false;state="stopping";
        long old=handle;handle=0;main.post(release);
        worker.execute(()->{
            try{if(old!=0)NativeConnection.close(old);synchronized(this){state="stopped";}}
            catch(Exception failure){synchronized(this){failState(failure);}}
        });
    }
    synchronized void close(){if(closed)return;disconnect();closed=true;worker.shutdown();}
    private void terminate(long expected,Exception failure){
        long old;synchronized(this){if(token!=expected)return;old=handle;handle=0;token=next(token);failState(failure);}
        try{if(old!=0)NativeConnection.close(old);}catch(Exception close){failure.addSuppressed(close);synchronized(this){error=failure+"; close: "+close;}}
        main.post(release);
    }
    private void failState(Exception failure){running=false;framePending=false;commandPending=false;state="error";error=failure.toString();}
    private byte[] read(String name)throws Exception{
        File file=new File(context.getFilesDir(),"pairing/"+name);
        if(!file.isFile()||file.length()>65536)throw new IllegalStateException("PAIRING_FILE_MISSING_OR_OVERSIZED:"+name);
        return Files.readAllBytes(file.toPath());
    }
}
