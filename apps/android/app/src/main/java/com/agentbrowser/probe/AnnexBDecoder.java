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
import java.util.Arrays;

/** One pending access unit; all codec operations run on one worker. */
public final class AnnexBDecoder {
    public record Receipt(long generation, long ptsUs, int renderedFrames) { }
    /** SPS coded frame includes macroblock padding; display frame includes SPS crop. */
    static record SpsGeometry(int codedWidth, int codedHeight, int displayWidth, int displayHeight) { }
    private final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final Handler callbacks=new Handler(Looper.getMainLooper());
    private MediaCodec codec; // worker only
    private Surface surface;
    private boolean foreground, closed, busy, released=true;
    private long epoch, generation, lastPts=-1;
    private int rendered;
    private String state="idle", codecName="", error;
    private AccessUnit format;

    static SpsGeometry parseSps(byte[] annexB) {
        if(annexB==null || annexB.length==0) throw malformedSps();
        int offset=0;
        while(offset<annexB.length) {
            int prefix=startCodeLength(annexB,offset);
            if(prefix==0) throw malformedSps();
            int header=offset+prefix;
            if(header>=annexB.length) throw malformedSps();
            int next=header+1;
            while(next<annexB.length && startCodeLength(annexB,next)==0) next++;
            if((annexB[header]&0x80)!=0) throw malformedSps();
            if((annexB[header]&0x1f)==7) return parseSpsNal(annexB,header+1,next);
            offset=next;
        }
        throw malformedSps();
    }

    private static SpsGeometry parseSpsNal(byte[] annexB,int start,int end) {
        if(start>=end) throw malformedSps();
        byte[] rbsp=new byte[end-start];
        int length=0,zeroRun=0;
        for(int i=start;i<end;i++) {
            int value=annexB[i]&0xff;
            if(zeroRun>=2 && value==3) {
                if(i+1>=end || (annexB[i+1]&0xff)>3) throw malformedSps();
                zeroRun=0;
                continue;
            }
            rbsp[length++]=(byte)value;
            if(value==0) zeroRun++; else zeroRun=0;
        }
        return parseSpsRbsp(Arrays.copyOf(rbsp,length));
    }

    private static SpsGeometry parseSpsRbsp(byte[] rbsp) {
        BitReader bits=new BitReader(rbsp);
        int profile=bits.readBits(8);
        bits.readBits(8); // constraint flags and reserved bits
        bits.readBits(8); // level_idc
        bits.readUnsignedExpGolomb(); // seq_parameter_set_id
        int chromaFormat=1;
        if(highProfile(profile)) {
            chromaFormat=asSmallInt(bits.readUnsignedExpGolomb());
            if(chromaFormat>3) throw malformedSps();
            if(chromaFormat==3) bits.readBit(); // separate_colour_plane_flag
            if(asSmallInt(bits.readUnsignedExpGolomb())>6) throw malformedSps(); // bit_depth_luma_minus8
            if(asSmallInt(bits.readUnsignedExpGolomb())>6) throw malformedSps(); // bit_depth_chroma_minus8
            bits.readBit(); // qpprime_y_zero_transform_bypass_flag
            if(bits.readBit()!=0) {
                int lists=chromaFormat==3?12:8;
                for(int i=0;i<lists;i++) if(bits.readBit()!=0) skipScalingList(bits,i<6?16:64);
            }
        }
        if(asSmallInt(bits.readUnsignedExpGolomb())>12) throw malformedSps(); // log2_max_frame_num_minus4
        int picOrderType=asSmallInt(bits.readUnsignedExpGolomb());
        if(picOrderType>2) throw malformedSps();
        if(picOrderType==0) {
            if(asSmallInt(bits.readUnsignedExpGolomb())>12) throw malformedSps();
        } else if(picOrderType==1) {
            bits.readBit();
            bits.readSignedExpGolomb();
            bits.readSignedExpGolomb();
            int cycles=boundedCount(bits.readUnsignedExpGolomb());
            for(int i=0;i<cycles;i++) bits.readSignedExpGolomb();
        }
        bits.readUnsignedExpGolomb(); // max_num_ref_frames
        bits.readBit(); // gaps_in_frame_num_value_allowed_flag
        long widthMbs=bits.readUnsignedExpGolomb()+1;
        long heightMapUnits=bits.readUnsignedExpGolomb()+1;
        if(widthMbs<1 || heightMapUnits<1 || widthMbs>Integer.MAX_VALUE/16L || heightMapUnits>Integer.MAX_VALUE/32L)
            throw malformedSps();
        boolean frameMbsOnly=bits.readBit()!=0;
        if(!frameMbsOnly) bits.readBit(); // mb_adaptive_frame_field_flag
        bits.readBit(); // direct_8x8_inference_flag
        long cropLeft=0,cropRight=0,cropTop=0,cropBottom=0;
        if(bits.readBit()!=0) {
            cropLeft=bits.readUnsignedExpGolomb(); cropRight=bits.readUnsignedExpGolomb();
            cropTop=bits.readUnsignedExpGolomb(); cropBottom=bits.readUnsignedExpGolomb();
        }
        if(!bits.hasRemaining()) throw malformedSps();
        bits.readBit(); // vui_parameters_present_flag

        long codedWidth=widthMbs*16L;
        long codedHeight=(frameMbsOnly?1L:2L)*heightMapUnits*16L;
        long cropUnitX,cropUnitY;
        switch(chromaFormat) {
            case 0 -> { cropUnitX=1; cropUnitY=frameMbsOnly?2:4; }
            case 1 -> { cropUnitX=2; cropUnitY=frameMbsOnly?2:4; }
            case 2 -> { cropUnitX=2; cropUnitY=frameMbsOnly?1:2; }
            case 3 -> { cropUnitX=1; cropUnitY=frameMbsOnly?2:4; }
            default -> throw malformedSps();
        }
        long displayWidth=codedWidth-(cropLeft+cropRight)*cropUnitX;
        long displayHeight=codedHeight-(cropTop+cropBottom)*cropUnitY;
        if(displayWidth<1 || displayHeight<1 || codedWidth>Integer.MAX_VALUE || codedHeight>Integer.MAX_VALUE
                || displayWidth>Integer.MAX_VALUE || displayHeight>Integer.MAX_VALUE)
            throw malformedSps();
        return new SpsGeometry((int)codedWidth,(int)codedHeight,(int)displayWidth,(int)displayHeight);
    }

    private static boolean highProfile(int profile) {
        return switch(profile) {
            case 44,83,86,100,110,118,122,128,134,135,138,139,144,244 -> true;
            default -> false;
        };
    }

    private static void skipScalingList(BitReader bits,int size) {
        int lastScale=8,nextScale=8;
        for(int i=0;i<size;i++) {
            if(nextScale!=0) {
                long delta=bits.readSignedExpGolomb();
                if(delta<Integer.MIN_VALUE || delta>Integer.MAX_VALUE) throw malformedSps();
                nextScale=(int)((lastScale+delta+256)%256);
            }
            lastScale=nextScale==0?lastScale:nextScale;
        }
    }

    private static int asSmallInt(long value) {
        if(value<0 || value>Integer.MAX_VALUE) throw malformedSps();
        return (int)value;
    }

    static int boundedCount(long value) {
        if(value<0 || value>255) throw malformedSps();
        return (int)value;
    }

    /**
     * AccessUnit coded dimensions are the encoder's even source dimensions;
     * SPS coded dimensions may be larger because of macroblock padding.
     */
    static void validateSpsDimensions(SpsGeometry sps,int declaredWidth,int declaredHeight,int visibleWidth,int visibleHeight) {
        if(sps.displayWidth!=declaredWidth || sps.displayHeight!=declaredHeight
                || visibleWidth<1 || visibleHeight<1
                || visibleWidth>sps.displayWidth || visibleHeight>sps.displayHeight)
            throw new IllegalArgumentException("BITSTREAM_DIMENSIONS_MISMATCH: display="
                +sps.displayWidth+"x"+sps.displayHeight+" coded="+sps.codedWidth+"x"+sps.codedHeight
                +" declared="+declaredWidth+"x"+declaredHeight);
    }

    /** MediaCodec output can retain additional raw macroblock padding. */
    static void validateCodecOutput(SpsGeometry sps,int declaredWidth,int declaredHeight,
            int actualCodedWidth,int actualCodedHeight,int cropLeft,int cropTop,int cropRight,int cropBottom) {
        long displayWidth=(long)cropRight-cropLeft+1L;
        long displayHeight=(long)cropBottom-cropTop+1L;
        if(sps.displayWidth!=declaredWidth || sps.displayHeight!=declaredHeight
                || actualCodedWidth<sps.codedWidth || actualCodedHeight<sps.codedHeight
                || cropLeft<0 || cropTop<0 || cropRight<cropLeft || cropBottom<cropTop
                || cropRight>=actualCodedWidth || cropBottom>=actualCodedHeight
                || displayWidth!=sps.displayWidth || displayHeight!=sps.displayHeight)
            throw new IllegalArgumentException("BITSTREAM_DIMENSIONS_MISMATCH: output="
                +actualCodedWidth+"x"+actualCodedHeight+" crop="+cropLeft+","+cropTop+"-"+cropRight+","+cropBottom
                +" sps="+sps.codedWidth+"x"+sps.codedHeight+"/"+sps.displayWidth+"x"+sps.displayHeight
                +" declared="+declaredWidth+"x"+declaredHeight);
    }

    private static int startCodeLength(byte[] bytes,int offset) {
        if(offset+2>=bytes.length || bytes[offset]!=0 || bytes[offset+1]!=0) return 0;
        if(bytes[offset+2]==1) return 3;
        return offset+3<bytes.length && bytes[offset+2]==0 && bytes[offset+3]==1?4:0;
    }

    private static IllegalArgumentException malformedSps() { return new IllegalArgumentException("MALFORMED_SPS"); }

    private static final class BitReader {
        private final byte[] bytes;
        private final int bitLength;
        private int position;
        BitReader(byte[] bytes) { this.bytes=bytes; bitLength=bytes.length*8; }
        boolean hasRemaining() { return position<bitLength; }
        int readBit() {
            if(!hasRemaining()) throw malformedSps();
            int value=(bytes[position>>>3] >>> (7-(position&7)))&1;
            position++;
            return value;
        }
        int readBits(int count) {
            if(count<0 || count>32 || count>bitLength-position) throw malformedSps();
            int value=0;
            for(int i=0;i<count;i++) value=(value<<1)|readBit();
            return value;
        }
        long readUnsignedExpGolomb() {
            int leading=0;
            while(readBit()==0) {
                if(++leading>31) throw malformedSps();
            }
            long suffix=leading==0?0:readBits(leading)&0xffffffffL;
            return (1L<<leading)-1L+suffix;
        }
        long readSignedExpGolomb() {
            long code=readUnsignedExpGolomb();
            return (code&1L)==0?-(code/2L):(code+1L)/2L;
        }
    }
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
            byte[] bytes=unit.bytes();
            SpsGeometry sps=parseSps(bytes);
            validateSpsDimensions(sps,unit.codedWidth,unit.codedHeight,unit.visibleWidth,unit.visibleHeight);
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
                    int actualCodedWidth=actual.getInteger(MediaFormat.KEY_WIDTH);
                    int actualCodedHeight=actual.getInteger(MediaFormat.KEY_HEIGHT);
                    int cropLeft=actual.getInteger("crop-left",0);
                    int cropTop=actual.getInteger("crop-top",0);
                    int cropRight=actual.getInteger("crop-right",actualCodedWidth-1);
                    int cropBottom=actual.getInteger("crop-bottom",actualCodedHeight-1);
                    validateCodecOutput(sps,unit.codedWidth,unit.codedHeight,
                        actualCodedWidth,actualCodedHeight,cropLeft,cropTop,cropRight,cropBottom);
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
