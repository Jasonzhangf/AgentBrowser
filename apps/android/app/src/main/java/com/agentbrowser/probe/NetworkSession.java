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
    record Viewport(int width,int height,boolean landscape) { }
    record CommandRequest(int op,long epoch,double x,double y,double dx,double dy,String text) { }
    private Viewport requestedViewport,submittedViewport,rejectedViewport;
    private boolean running,closed,framePending,commandPending;
    private int commandPendingOp=-1;
    private CommandRequest queuedCommand;
    private NetworkFrame displayed;
    private JSONObject host;
    private String state="idle",error,shownMode="observe",selectedTransport="";
    NetworkSession(Context context,FrameSink sink,Runnable release){this.context=context.getApplicationContext();this.sink=sink;this.release=release;}
    synchronized boolean active(){return running||state.equals("connecting")||state.equals("stopping");}
    synchronized boolean connected(){return running&&handle!=0;}
    synchronized String transport(){return selectedTransport;}
    synchronized boolean current(long value){return connected()&&token==value;}
    synchronized boolean inputReady(){return connected()&&!commandPending&&queuedCommand==null&&displayed!=null
        &&java.util.Objects.equals(requestedViewport,submittedViewport)&&hostReady(host)
        &&displayed.documentRevision==host.optLong("document_revision",-1)
        &&displayed.viewportRevision==host.optLong("viewport_revision",-1);}
    static boolean hostReady(JSONObject host){
        return host!=null&&!host.optBoolean("viewport_pending")&&!host.optBoolean("operation_running");
    }
    synchronized long epoch(){return shownEpoch;}
    synchronized boolean humanShown(){return shownMode.equals("control");}
    record InputContext(long connection,long epoch,long document,long viewport) { }
    synchronized InputContext inputContext(){
        return inputReady()&&humanShown()
            ?new InputContext(token,shownEpoch,displayed.documentRevision,displayed.viewportRevision):null;
    }
    synchronized void declareViewport(int cssWidth,int cssHeight,boolean landscape){
        if(cssWidth<=0||cssHeight<=0||cssWidth>4096||cssHeight>4096||(long)cssWidth*cssHeight>4194304)
            throw new IllegalArgumentException("INVALID_VIEWPORT");
        Viewport viewport=new Viewport(cssWidth,cssHeight,landscape);
        if(!viewport.equals(requestedViewport))rejectedViewport=null;
        requestedViewport=viewport;
        flushViewport();
    }
    static boolean viewportNeedsSubmission(Viewport requested,Viewport submitted,Viewport rejected){
        return requested!=null&&!requested.equals(submitted)&&!requested.equals(rejected);
    }
    static boolean queuesBehindViewport(int op,int pendingOp){return pendingOp==6&&op==7;}
    private synchronized boolean canQueueNavigation(long epoch){
        if(!connected()||displayed==null||host==null||!hostReady(host))return false;
        if(displayed.documentRevision!=host.optLong("document_revision",-1)
                ||displayed.viewportRevision!=host.optLong("viewport_revision",-1))return false;
        try{
            JSONObject control=host.getJSONObject("control"),phase=control.getJSONObject("phase");
            return epoch==control.getLong("epoch")&&"human".equals(phase.getString("type"))
                    &&phase.optLong("attachment_id",-1)==host.optLong("attachment_id",-2);
        }catch(org.json.JSONException invalid){return false;}
    }
    static boolean queuedCommandWaitsForViewport(CommandRequest queued,Viewport requested,Viewport submitted){
        return queued!=null&&!java.util.Objects.equals(requested,submitted);
    }
    enum CommandAdvance { SUBMIT_VIEWPORT, START_QUEUED, BLOCK_QUEUED, IDLE }
    static CommandAdvance nextCommandAdvance(CommandRequest queued,Viewport requested,Viewport submitted,Viewport rejected){
        if(queuedCommandWaitsForViewport(queued,requested,submitted))
            return viewportNeedsSubmission(requested,submitted,rejected)?CommandAdvance.SUBMIT_VIEWPORT:CommandAdvance.BLOCK_QUEUED;
        if(queued!=null)return CommandAdvance.START_QUEUED;
        return viewportNeedsSubmission(requested,submitted,rejected)?CommandAdvance.SUBMIT_VIEWPORT:CommandAdvance.IDLE;
    }
    private synchronized void flushViewport(){
        if(!connected()||commandPending||!viewportNeedsSubmission(requestedViewport,submittedViewport,rejectedViewport))return;
        Viewport viewport=requestedViewport;
        command(6,0,viewport.width(),viewport.height(),viewport.landscape()?1:0,0,"");
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
                .put("transport",selectedTransport)
                .put("inputReady",inputReady()).put("pending",commandPending||queuedCommand!=null||framePending?"busy":JSONObject.NULL)
                .put("networkConfigured",new File(context.getFilesDir(),"pairing").isDirectory());
            if(host!=null)value.put("sessionId",host.getString("session_id")).put("documentRevision",host.getLong("document_revision")).put("viewportRevision",host.getLong("viewport_revision"));
            if(displayed!=null)value.put("displayedPtsUs",displayed.ptsUs).put("displayedTicket",displayed.ticket)
                .put("displayedDocumentRevision",displayed.documentRevision).put("displayedViewportRevision",displayed.viewportRevision)
                .put("displayedCodedWidth",displayed.codedWidth).put("displayedCodedHeight",displayed.codedHeight)
                .put("displayedVisibleWidth",displayed.visibleWidth).put("displayedVisibleHeight",displayed.visibleHeight);
            return value;
        }catch(org.json.JSONException invalid){throw new IllegalStateException("INVALID_HOST_STATUS",invalid);}
    }

    static NativeConnection.TransportConfig parseTransportConfig(String raw) {
        if (raw == null || raw.isBlank()) throw new IllegalArgumentException("TRANSPORT_CONFIG_EMPTY");
        try {
            JSONObject value = new JSONObject(raw);
            java.util.Iterator<String> keys = value.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                if (!key.equals("transport") && !key.equals("bind_ip"))
                    throw new IllegalArgumentException("UNKNOWN_TRANSPORT_CONFIG_FIELD");
            }
            String transport = value.optString("transport", "");
            return switch (transport) {
                case "wss" -> {
                    if (value.has("bind_ip")) throw new IllegalArgumentException("WSS_BIND_IP_FORBIDDEN");
                    yield NativeConnection.TransportConfig.wss();
                }
                case "webrtc" -> {
                    if (!value.has("bind_ip")) throw new IllegalArgumentException("WEBRTC_BIND_IP_REQUIRED");
                    yield NativeConnection.TransportConfig.webRtc(value.getString("bind_ip"));
                }
                default -> throw new IllegalArgumentException("UNKNOWN_TRANSPORT");
            };
        } catch (org.json.JSONException invalid) {
            throw new IllegalArgumentException("INVALID_TRANSPORT_CONFIG", invalid);
        }
    }

    private NativeConnection.TransportConfig transportConfig() throws Exception {
        File file = new File(context.getFilesDir(), "pairing/transport.json");
        if (!file.exists()) return NativeConnection.TransportConfig.wss();
        if (!file.isFile() || file.length() > 4096) throw new IllegalStateException("TRANSPORT_CONFIG_MISSING_OR_OVERSIZED");
        return parseTransportConfig(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
    }

    synchronized void connect(){
        if(closed)throw new IllegalStateException("HOST_INACTIVE");
        if(active()||handle!=0)throw new IllegalStateException("NETWORK_BUSY");
        token=next(token);generation=next(generation);long expected=token;
        state="connecting";error=null;host=null;displayed=null;shownEpoch=0;shownMode="observe";selectedTransport="";
        submittedViewport=null;rejectedViewport=null;queuedCommand=null;commandPendingOp=-1;
        worker.execute(()->open(expected));
    }
    private void open(long expected){
        long opened=0;
        try{
            NativeConnection.load();
            NativeConnection.TransportConfig config=transportConfig();
            opened=NativeConnection.open(new String(read("endpoint.txt"),StandardCharsets.UTF_8).trim(),read("ca.der"),read("client.der"),read("key.der"),config);
            JSONObject status=new JSONObject(NativeConnection.command(opened,0,0,0,0,0,0,0,""));
            synchronized(this){if(closed||token!=expected){NativeConnection.close(opened);return;}handle=opened;host=status;running=true;state="connected";selectedTransport=config.transport().name().toLowerCase(java.util.Locale.ROOT);flushViewport();}
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
            synchronized(this){if(!current(expected))return;host=status;startQueuedIfReady();}
            worker.schedule(()->poll(expected),30,TimeUnit.MILLISECONDS);
        }catch(Exception failure){terminate(expected,failure);}
    }
    synchronized void command(int op,long epoch,double x,double y,double dx,double dy,String text){
        if(!connected())throw new IllegalStateException("NETWORK_NOT_CONNECTED");
        if(commandPending){
            if(queuesBehindViewport(op,commandPendingOp)&&queuedCommand==null&&canQueueNavigation(epoch)){
                queuedCommand=new CommandRequest(op,epoch,x,y,dx,dy,text==null?"":text);
                return;
            }
            throw new IllegalStateException("OPERATION_PENDING");
        }
        if(queuedCommand!=null&&op!=6)throw new IllegalStateException("OPERATION_PENDING");
        if(op>=3&&op!=6&&!inputReady())throw new IllegalStateException("DISPLAY_NOT_READY");
        startCommand(new CommandRequest(op,epoch,x,y,dx,dy,text==null?"":text));
    }
    private synchronized void startCommand(CommandRequest request){
        long expected=token,nativeHandle=handle,ticket=displayed==null?0:displayed.ticket;
        commandPending=true;commandPendingOp=request.op;error=null;
        worker.execute(()->{
            try{
                synchronized(this){if(!current(expected))return;}
                String result=NativeConnection.command(nativeHandle,request.op,request.epoch,ticket,request.x,request.y,request.dx,request.dy,request.text);
                JSONObject status=new JSONObject(request.op<=2?result:NativeConnection.command(nativeHandle,0,0,0,0,0,0,0,""));
                synchronized(this){if(current(expected)){
                    host=status;
                    if(request.op==6){
                        submittedViewport=new Viewport((int)request.x,(int)request.y,request.dx!=0.0);
                        rejectedViewport=null;
                    }
                    commandPending=false;commandPendingOp=-1;
                    advanceAfterCommand();
                }}
            }catch(HostCommandException rejection){
                try{
                    synchronized(this){if(!current(expected))return;}
                    JSONObject status=new JSONObject(NativeConnection.command(nativeHandle,0,0,0,0,0,0,0,""));
                    synchronized(this){if(current(expected)){
                        host=status;commandPending=false;commandPendingOp=-1;error=rejection.toString();
                        if(request.op==6){
                            rejectedViewport=new Viewport((int)request.x,(int)request.y,request.dx!=0.0);
                            if(queuedCommand!=null){
                                CommandRequest dropped=queuedCommand;queuedCommand=null;
                                error += "; queued operation op="+dropped.op+" was not submitted";
                            }
                        }
                        advanceAfterCommand();
                    }}
                }catch(Exception failure){failure.addSuppressed(rejection);terminate(expected,failure);}
            }catch(Exception failure){terminate(expected,failure);}
        });
    }
    private synchronized void advanceAfterCommand(){
        switch(nextCommandAdvance(queuedCommand,requestedViewport,submittedViewport,rejectedViewport)){
            case SUBMIT_VIEWPORT -> flushViewport();
            case START_QUEUED -> startQueuedIfReady();
            case BLOCK_QUEUED,IDLE -> { }
        }
    }
    private synchronized void startQueuedIfReady(){
        if(commandPending||queuedCommand==null||!hostReady(host)
                ||!java.util.Objects.equals(requestedViewport,submittedViewport))return;
        CommandRequest next=queuedCommand;queuedCommand=null;
        if(!canQueueNavigation(next.epoch)){
            error="STALE_CONTROL";
            return;
        }
        if(next.op>=3&&next.op!=6&&!inputReady()){queuedCommand=next;return;}
        startCommand(next);
    }
    synchronized void disconnect(){
        if(!active()&&handle==0)return;
        token=next(token);generation=next(generation);running=false;framePending=false;commandPending=false;commandPendingOp=-1;queuedCommand=null;state="stopping";
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
    private void failState(Exception failure){running=false;framePending=false;commandPending=false;commandPendingOp=-1;queuedCommand=null;state="error";error=failure.toString();}
    private byte[] read(String name)throws Exception{
        File file=new File(context.getFilesDir(),"pairing/"+name);
        if(!file.isFile()||file.length()>65536)throw new IllegalStateException("PAIRING_FILE_MISSING_OR_OVERSIZED:"+name);
        return Files.readAllBytes(file.toPath());
    }
}
