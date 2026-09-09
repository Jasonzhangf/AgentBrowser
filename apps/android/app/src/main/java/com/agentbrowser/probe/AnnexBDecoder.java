package com.agentbrowser.probe;

import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Surface;
import org.json.JSONObject;
import org.json.JSONException;
import java.nio.ByteBuffer;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** One pending access unit; all codec operations run on one worker. */
public final class AnnexBDecoder {
    public record Receipt(long generation, long ptsUs, int renderedFrames) { }
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final Handler callbacks=new Handler(Looper.getMainLooper());
    private MediaCodec codec; // worker only
    private Surface surface;
    private boolean foreground, closed, busy, released=true;
    private long epoch, generation, lastPts=-1;
    private int rendered;
    private String state="idle", codecName="", error;
    private AccessUnit format;
    public synchronized void setSurface(Surface value) { surface=value; if(value==null) stop(); }
    public synchronized void foreground(boolean value) { foreground=value; if(!value) stop(); }
    public synchronized boolean released() { return released; }
    public synchronized JSONObject snapshot() {
        try { return new JSONObject().put("state",state).put("generation",generation).put("renderedFrames",rendered)
            .put("released",released).put("codec",codecName).put("source","annexb")
            .put("error",error==null ? JSONObject.NULL : error); }
        catch(JSONException impossible) { throw new AssertionError(impossible); }
    }
    public synchronized CompletableFuture<Receipt> submit(AccessUnit unit) {
        if(closed || !foreground) throw new IllegalStateException("HOST_INACTIVE");
        if(surface==null || !surface.isValid()) throw new IllegalStateException("SURFACE_UNAVAILABLE");
        if(unit.generation<generation || (released && unit.generation==generation)) throw new IllegalArgumentException("STALE_DECODER_GENERATION");
        if(busy || state.equals("stopping")) throw new IllegalStateException("DECODER_BACKPRESSURE");
        if(unit.generation==generation && (!unit.sameGeometry(format) || unit.ptsUs<=lastPts)) throw new IllegalArgumentException("DECODER_GENERATION_OR_PTS_REQUIRED");
        boolean rebuild=unit.generation!=generation;
        if(rebuild) { generation=unit.generation; rendered=0; lastPts=-1; format=unit; epoch++; }
        long token=epoch;
        Surface target=surface;
        busy=true; released=false; state="starting"; error=null;
        CompletableFuture<Receipt> receipt=new CompletableFuture<>();
        worker.execute(() -> decode(unit,target,token,rebuild,receipt));
        return receipt;
    }
    private synchronized boolean current(long token) { return epoch==token && foreground && !closed; }
    private void decode(AccessUnit unit, Surface target, long token, boolean rebuild, CompletableFuture<Receipt> receipt) {
        try {
            if(!current(token)) throw new IllegalStateException("DECODE_CANCELLED");
            if(rebuild) {
                releaseCodec();
                codec=MediaCodec.createDecoderByType("video/avc");
                MediaFormat config=MediaFormat.createVideoFormat("video/avc",unit.codedWidth,unit.codedHeight);
                config.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE,AccessUnit.MAX_BYTES);
                config.setInteger(MediaFormat.KEY_COLOR_STANDARD,MediaFormat.COLOR_STANDARD_BT709);
                config.setInteger(MediaFormat.KEY_COLOR_RANGE,MediaFormat.COLOR_RANGE_LIMITED);
                codec.configure(config,target,null,0);
                codec.start();
            }
            MediaCodec active=codec;
            CountDownLatch presented=new CountDownLatch(1);
            active.setOnFrameRenderedListener((source,pts,nano) -> {
                synchronized(AnnexBDecoder.this) {
                    if(epoch!=token || generation!=unit.generation || pts!=unit.ptsUs || source!=active || presented.getCount()==0) return;
                    rendered++; lastPts=pts; state="playing";
                    presented.countDown();
                }
            },callbacks);
            synchronized(this) { codecName=active.getName(); }
            long deadline=SystemClock.elapsedRealtime()+3000;
            int input=-1;
            while(current(token) && SystemClock.elapsedRealtime()<deadline && input<0) input=active.dequeueInputBuffer(10000);
            if(input<0) throw new IllegalStateException("DECODE_INPUT_TIMEOUT_OR_CANCELLED");
            ByteBuffer buffer=active.getInputBuffer(input);
            byte[] bytes=unit.bytes();
            if(buffer==null || buffer.capacity()<bytes.length) throw new IllegalArgumentException("CODEC_INPUT_CAPACITY");
            buffer.put(bytes);
            active.queueInputBuffer(input,0,bytes.length,unit.ptsUs,0);
            MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();
            boolean output=false;
            while(current(token) && SystemClock.elapsedRealtime()<deadline && !output) {
                int index=active.dequeueOutputBuffer(info,10000);
                if(index==MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat actual=active.getOutputFormat();
                    // Codec buffers can include macroblock padding beyond the SPS display rectangle.
                    int width=actual.getInteger("crop-right",actual.getInteger(MediaFormat.KEY_WIDTH)-1)-actual.getInteger("crop-left",0)+1;
                    int height=actual.getInteger("crop-bottom",actual.getInteger(MediaFormat.KEY_HEIGHT)-1)-actual.getInteger("crop-top",0)+1;
                    if(width!=unit.codedWidth || height!=unit.codedHeight)
                        throw new IllegalArgumentException("BITSTREAM_DIMENSIONS_MISMATCH: "+actual);
                } else if(index>=0) {
                    output=info.size>0;
                    active.releaseOutputBuffer(index,output && current(token));
                }
            }
            while(current(token) && SystemClock.elapsedRealtime()<deadline && !presented.await(10,TimeUnit.MILLISECONDS)) { }
            if(!current(token)) throw new IllegalStateException("DECODE_CANCELLED");
            if(presented.getCount()!=0) throw new IllegalArgumentException("DECODE_OR_PRESENT_TIMEOUT");
            synchronized(this) {
                if(!current(token)) throw new IllegalStateException("DECODE_CANCELLED");
                busy=false; receipt.complete(new Receipt(generation,unit.ptsUs,rendered));
            }
        } catch(Exception failure) {
            try { releaseCodec(); } catch(Exception release) { failure.addSuppressed(release); }
            synchronized(this) {
                busy=false; released=codec==null;
                if(epoch==token) { epoch++; error=failure.toString(); state="error"; }
            }
            receipt.completeExceptionally(failure);
        }
    }
    private void releaseCodec() {
        if(codec!=null) { codec.release(); codec=null; }
    }
    public synchronized void stop() {
        if(closed || released || state.equals("stopping")) return;
        epoch++; state="stopping";
        worker.execute(() -> {
            try { releaseCodec(); synchronized(this) { released=true; state="stopped"; busy=false; } }
            catch(Exception failure) { synchronized(this) { state="error"; error="CODEC_RELEASE_FAILED: "+failure; } }
        });
    }
    public synchronized void close() { if(closed) return; stop(); closed=true; worker.shutdown(); }
}
