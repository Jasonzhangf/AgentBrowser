package com.agentbrowser.probe;

import android.content.Context;
import android.content.res.AssetFileDescriptor;
import android.media.MediaCodec;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Surface;
import org.json.JSONException;
import org.json.JSONObject;
import java.nio.ByteBuffer;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

// Owns local decode lifecycle; future authenticated media ingress belongs to AB-05.
final class MediaProbe {
    private final Context context;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private Surface surface;
    private boolean foreground, closed, cancel, released = true;
    private String state = "idle", codecName = "", error;
    private long generation;
    private int renderedFrames;

    MediaProbe(Context context) { this.context = context.getApplicationContext(); }
    synchronized void setSurface(Surface value) {
        surface = value;
        if (value == null) stop();
    }
    synchronized void foreground(boolean value) { foreground = value; if (!value) stop(); }
    synchronized void close() { closed = true; stop(); worker.shutdown(); }
    synchronized JSONObject snapshot() {
        try {
            return new JSONObject().put("state", state).put("generation", generation)
                .put("renderedFrames", renderedFrames).put("released", released)
                .put("codec", codecName).put("error", error == null ? JSONObject.NULL : error);
        } catch (JSONException impossible) { throw new AssertionError(impossible); }
    }
    synchronized String request(ProbeCommand command) {
        switch (command.op) {
            case PLAY -> play(command.sample);
            case STOP -> stop();
            case STATUS -> { }
        }
        return snapshot().toString();
    }
    private synchronized void play(String sample) {
        if (closed || !foreground) throw new IllegalStateException("HOST_INACTIVE");
        if (!released) throw new IllegalStateException("MEDIA_BUSY");
        if (surface == null || !surface.isValid()) throw new IllegalStateException("SURFACE_UNAVAILABLE");
        generation++;
        long run = generation;
        Surface target = surface;
        state = "starting"; error = null; codecName = ""; renderedFrames = 0;
        cancel = false; released = false;
        worker.execute(() -> decode(run, target, sample));
    }
    private synchronized void stop() {
        if (!released) { cancel = true; state = "stopping"; }
    }
    private synchronized boolean cancelled(long run) { return cancel || run != generation; }

    private void decode(long run, Surface target, String sample) {
        MediaExtractor extractor = new MediaExtractor();
        MediaCodec codec = null;
        String failure = null;
        boolean releaseFailed = false;
        boolean eos = false;
        try (AssetFileDescriptor input = context.getAssets().openFd("media/" + sample + ".mp4")) {
            extractor.setDataSource(input.getFileDescriptor(), input.getStartOffset(), input.getLength());
            if (extractor.getTrackCount() != 1) throw new IllegalArgumentException("EXPECTED_ONE_VIDEO_TRACK");
            MediaFormat format = extractor.getTrackFormat(0);
            if (!"video/avc".equals(format.getString(MediaFormat.KEY_MIME))) throw new IllegalArgumentException("EXPECTED_H264");
            int width = format.getInteger(MediaFormat.KEY_WIDTH), height = format.getInteger(MediaFormat.KEY_HEIGHT);
            if (width <= 0 || width > 1920 || height <= 0 || height > 1920) throw new IllegalArgumentException("DIMENSION_LIMIT");
            extractor.selectTrack(0);
            codec = MediaCodec.createDecoderByType("video/avc");
            codec.configure(format, target, null, 0);
            codec.setOnFrameRenderedListener((decoder, pts, nano) -> {
                synchronized (MediaProbe.this) { if (run == generation) renderedFrames++; }
            }, main);
            codec.start();
            synchronized (this) { codecName = codec.getName(); if (!cancel) state = "playing"; }
            long startNs = System.nanoTime();
            long deadline = SystemClock.elapsedRealtime() + 18000;
            boolean inputEos = false;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            while (!cancelled(run) && !eos) {
                if (SystemClock.elapsedRealtime() > deadline) throw new IllegalStateException("DECODE_TIMEOUT");
                if (!inputEos) {
                    int index = codec.dequeueInputBuffer(10000);
                    if (index >= 0) {
                        ByteBuffer buffer = codec.getInputBuffer(index);
                        if (buffer == null) throw new IllegalStateException("INPUT_BUFFER_UNAVAILABLE");
                        int size = extractor.readSampleData(buffer, 0);
                        if (size < 0) {
                            codec.queueInputBuffer(index, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inputEos = true;
                        } else {
                            codec.queueInputBuffer(index, 0, size, extractor.getSampleTime(), 0);
                            extractor.advance();
                        }
                    }
                }
                int index = codec.dequeueOutputBuffer(info, 10000);
                if (index >= 0) {
                    eos = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                    long due = startNs + info.presentationTimeUs * 1000;
                    while (!cancelled(run) && System.nanoTime() < due) SystemClock.sleep(5);
                    codec.releaseOutputBuffer(index, info.size > 0 && !cancelled(run));
                }
            }
        } catch (Exception exception) {
            failure = exception.getClass().getSimpleName() + ": " + exception.getMessage();
        } finally {
            if (codec != null) {
                try { codec.release(); }
                catch (RuntimeException exception) { releaseFailed = true; failure = "CODEC_RELEASE_FAILED: " + exception.getMessage(); }
            }
            try { extractor.release(); }
            catch (RuntimeException exception) { releaseFailed = true; failure = "EXTRACTOR_RELEASE_FAILED: " + exception.getMessage(); }
            synchronized (this) {
                if (run == generation) {
                    released = !releaseFailed;
                    error = failure;
                    state = failure != null ? "error" : cancel ? "stopped" : eos ? "completed" : "error";
                }
            }
        }
    }
}
