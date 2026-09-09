package com.agentbrowser.probe;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

public class AnnexBDecoderTest {
    private static byte[] annexB(String value) {
        String[] octets=value.split(" ");
        byte[] bytes=new byte[octets.length];
        for(int i=0;i<octets.length;i++) bytes[i]=(byte)Integer.parseInt(octets[i],16);
        return bytes;
    }

    @Test public void spsGeometryKeepsMacroblockPaddingAndDisplayCrop() {
        AnnexBDecoder.SpsGeometry before=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 42 c0 0a dc 28 47 e5 9a 81 01 00 a0 00 00 03 00 20 00 00 03 00 d1 e2 44 f0"));
        assertEquals(160,before.codedWidth());
        assertEquals(128,before.codedHeight());
        assertEquals(160,before.displayWidth());
        assertEquals(120,before.displayHeight());

        AnnexBDecoder.SpsGeometry resized=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 42 c0 16 dc 19 06 be 5a 9a 81 01 00 a0 00 00 03 00 20 00 00 03 00 d1 e2 c5 f0"));
        assertEquals(400,resized.codedWidth());
        assertEquals(848,resized.codedHeight());
        assertEquals(392,resized.displayWidth());
        assertEquals(846,resized.displayHeight());
    }

    @Test public void malformedSpsRemainsAParserError() {
        IllegalArgumentException error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.parseSps(annexB("00 00 00 01 67 42")));
        assertEquals("MALFORMED_SPS",error.getMessage());
    }

    @Test public void acceptsThreeByteStartCodeAndEmulationPrevention() {
        AnnexBDecoder.SpsGeometry value=AnnexBDecoder.parseSps(annexB(
            "00 00 01 67 42 c0 0a dc 28 47 e5 9a 81 01 00 a0 00 00 03 00 20 00 00 03 00 d1 e2 44 f0"));
        assertEquals(160,value.codedWidth());
        assertEquals(128,value.codedHeight());
        assertEquals(160,value.displayWidth());
        assertEquals(120,value.displayHeight());
    }

    @Test public void acceptsHigh444Profile144Geometry() {
        AnnexBDecoder.SpsGeometry value=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 90 00 0a 91 9b 29 2f c4 e0 22 00 00 03 00 02 00 00 03 00 64 1e 24 4b 2c"));
        assertEquals(32,value.codedWidth());
        assertEquals(32,value.codedHeight());
        assertEquals(32,value.displayWidth());
        assertEquals(16,value.displayHeight());
    }

    @Test public void appliesChromaAndInterlacedCropUnits() {
        AnnexBDecoder.SpsGeometry high422=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 7a 00 0a bc d9 49 7a 89 c0 44 00 00 03 00 04 00 00 03 00 c8 3c 48 96 58"));
        assertEquals(32,high422.codedWidth());
        assertEquals(32,high422.codedHeight());
        assertEquals(30,high422.displayWidth());
        assertEquals(24,high422.displayHeight());

        AnnexBDecoder.SpsGeometry interlaced=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 64 00 15 ac d9 4a f5 78 08 80 00 00 03 00 80 00 00 03 01 0f 8a 14 cb"));
        assertEquals(32,interlaced.codedWidth());
        assertEquals(32,interlaced.codedHeight());
        assertEquals(30,interlaced.displayWidth());
        assertEquals(24,interlaced.displayHeight());
    }

    @Test public void rejectsIllegalEmulationPreventionSequence() {
        IllegalArgumentException error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.parseSps(annexB("00 00 01 67 7a 00 0a 00 00 03 04")));
        assertEquals("MALFORMED_SPS",error.getMessage());
    }

    @Test public void boundsPicOrderCycleCountAt255() {
        assertEquals(255,AnnexBDecoder.boundedCount(255));
        IllegalArgumentException error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.boundedCount(256));
        assertEquals("MALFORMED_SPS",error.getMessage());
    }

    @Test public void exposesPreConfigureSpsDimensionMismatch() {
        AnnexBDecoder.SpsGeometry value=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 42 c0 0a dc 28 47 e5 9a 81 01 00 a0 00 00 03 00 20 00 00 03 00 d1 e2 44 f0"));
        AnnexBDecoder.validateSpsDimensions(value,160,120,160,120);
        IllegalArgumentException error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.validateSpsDimensions(value,162,120,162,120));
        assertTrue(error.getMessage().contains("BITSTREAM_DIMENSIONS_MISMATCH"));
    }

    @Test public void acceptsDeclaredDisplayDimensionsForMacroblockPaddedSps() {
        AnnexBDecoder.SpsGeometry value=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 42 c0 16 dc 19 06 be 5a 9a 81 01 00 a0 00 00 03 00 20 00 00 03 00 d1 e2 c5 f0"));
        AnnexBDecoder.validateSpsDimensions(value,392,846,392,846);
        AnnexBDecoder.validateSpsDimensions(value,392,846,391,845);
        IllegalArgumentException error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.validateSpsDimensions(value,400,848,392,846));
        assertTrue(error.getMessage().contains("BITSTREAM_DIMENSIONS_MISMATCH"));
        error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.validateSpsDimensions(value,392,846,393,846));
        assertTrue(error.getMessage().contains("BITSTREAM_DIMENSIONS_MISMATCH"));
        error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.validateSpsDimensions(value,392,846,392,847));
        assertTrue(error.getMessage().contains("BITSTREAM_DIMENSIONS_MISMATCH"));
    }

    @Test public void acceptsCodecPaddingAndDisplayCropSeparately() {
        AnnexBDecoder.SpsGeometry value=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 42 c0 0a dc 28 47 e5 9a 81 01 00 a0 00 00 03 00 20 00 00 03 00 d1 e2 44 f0"));
        AnnexBDecoder.validateCodecOutput(value,160,120,160,128,0,0,159,119);
    }

    @Test public void acceptsResizedCodecPaddingAndDisplayCrop() {
        AnnexBDecoder.SpsGeometry value=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 42 c0 16 dc 19 06 be 5a 9a 81 01 00 a0 00 00 03 00 20 00 00 03 00 d1 e2 c5 f0"));
        AnnexBDecoder.validateCodecOutput(value,392,846,400,848,0,0,391,845);
        AnnexBDecoder.validateCodecOutput(value,392,846,416,864,0,0,391,845);
    }

    @Test public void rejectsCodecOutputSmallerThanSpsCodedGeometry() {
        AnnexBDecoder.SpsGeometry value=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 42 c0 0a dc 28 47 e5 9a 81 01 00 a0 00 00 03 00 20 00 00 03 00 d1 e2 44 f0"));
        IllegalArgumentException error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.validateCodecOutput(value,160,120,160,120,0,0,159,119));
        assertTrue(error.getMessage().contains("BITSTREAM_DIMENSIONS_MISMATCH"));
    }

    @Test public void rejectsCodecOutputWithWrongCrop() {
        AnnexBDecoder.SpsGeometry value=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 42 c0 16 dc 19 06 be 5a 9a 81 01 00 a0 00 00 03 00 20 00 00 03 00 d1 e2 c5 f0"));
        IllegalArgumentException error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.validateCodecOutput(value,392,846,400,848,0,0,390,845));
        assertTrue(error.getMessage().contains("BITSTREAM_DIMENSIONS_MISMATCH"));
        error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.validateCodecOutput(value,392,846,400,848,0,0,391,844));
        assertTrue(error.getMessage().contains("BITSTREAM_DIMENSIONS_MISMATCH"));
    }

    @Test public void rejectsCodecOutputWithDeclaredSpsCodedDimensions() {
        AnnexBDecoder.SpsGeometry value=AnnexBDecoder.parseSps(annexB(
            "00 00 00 01 67 42 c0 16 dc 19 06 be 5a 9a 81 01 00 a0 00 00 03 00 20 00 00 03 00 d1 e2 c5 f0"));
        IllegalArgumentException error=assertThrows(IllegalArgumentException.class,
            () -> AnnexBDecoder.validateCodecOutput(value,400,848,400,848,0,0,391,845));
        assertTrue(error.getMessage().contains("BITSTREAM_DIMENSIONS_MISMATCH"));
    }
}
