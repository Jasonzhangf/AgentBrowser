package com.agentbrowser.probe;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotSame;
import static org.junit.Assert.assertTrue;

/**
 * Root-local geometry derivation for the native layout owner. These are the
 * typed projection values consumed by the Cordis UI and by reportViewport().
 */
public final class NativeLayoutGeometryTest {
    @Test public void drawableIsRootLocalAndSafeSubtractsSystemBarsOnce() {
        MainActivity.LayoutRects rects = MainActivity.deriveRects(1080, 2400, 3f, 63, 0, 63, 0, 0);
        assertEquals(0, rects.drawableWindowRect.x);
        assertEquals(0, rects.drawableWindowRect.y);
        assertEquals(360, rects.drawableWindowRect.width);
        assertEquals(800, rects.drawableWindowRect.height);
        assertEquals(0, rects.safeContentRect.x);
        assertEquals(21, rects.safeContentRect.y);
        assertEquals(360, rects.safeContentRect.width);
        assertEquals(758, rects.safeContentRect.height);
    }

    @Test public void chromeIsThinAndAdvertisedExcludesIme() {
        MainActivity.LayoutRects rects = MainActivity.deriveRects(1080, 2400, 3f, 63, 0, 63, 0, 900);
        assertEquals(21, rects.chromeRect.y);
        assertEquals(MainActivity.CHROME_HEIGHT_DP, rects.chromeRect.height);
        // Advertised viewport is derived from safe content minus thin chrome and
        // never shrinks with the IME.
        assertEquals(21 + MainActivity.CHROME_HEIGHT_DP, rects.advertisedViewportRect.y);
        assertEquals(rects.safeContentRect.height - MainActivity.CHROME_HEIGHT_DP, rects.advertisedViewportRect.height);
        // Presented content additionally subtracts the IME overlap once.
        assertEquals(423, rects.presentedContentRect.height);
        assertEquals(rects.advertisedViewportRect.width, rects.presentedContentRect.width);
    }

    @Test public void imeBelowSafeContentDoesNotChangeAdvertisedOrSafe() {
        MainActivity.LayoutRects withoutIme = MainActivity.deriveRects(1080, 2400, 3f, 63, 0, 63, 0, 0);
        MainActivity.LayoutRects withIme = MainActivity.deriveRects(1080, 2400, 3f, 63, 0, 63, 0, 63);
        assertTrue(withoutIme.advertisedViewportRect.equalsRect(withIme.advertisedViewportRect));
        assertTrue(withoutIme.safeContentRect.equalsRect(withIme.safeContentRect));
        // IME entirely within the bottom system bar leaves the presented content unchanged.
        assertTrue(withoutIme.presentedContentRect.equalsRect(withIme.presentedContentRect));
    }

    @Test public void nonzeroLeftAndRightBarsKeepRootOrigin() {
        MainActivity.LayoutRects rects = MainActivity.deriveRects(1000, 800, 2f, 40, 30, 60, 20, 0);
        assertEquals(0, rects.drawableWindowRect.x);
        assertEquals(500, rects.drawableWindowRect.width);
        assertEquals(400, rects.drawableWindowRect.height);
        assertEquals(10, rects.safeContentRect.x);
        assertEquals(20, rects.safeContentRect.y);
        assertEquals(475, rects.safeContentRect.width);
        assertEquals(350, rects.safeContentRect.height);
        assertEquals(10, rects.chromeRect.x);
        assertEquals(20, rects.chromeRect.y);
        assertEquals(MainActivity.CHROME_HEIGHT_DP, rects.chromeRect.height);
    }

    @Test public void deriveRectsReturnsIndependentSnapshots() {
        MainActivity.LayoutRects first = MainActivity.deriveRects(1080, 2400, 3f, 0, 0, 0, 0, 0);
        MainActivity.LayoutRects second = MainActivity.deriveRects(1080, 2400, 3f, 0, 0, 0, 0, 0);
        assertNotSame(first, second);
        assertTrue(first.safeContentRect.equalsRect(second.safeContentRect));
    }
}
