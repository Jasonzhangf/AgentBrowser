package com.agentbrowser.probe;

import android.content.Intent;
import android.graphics.Insets;
import android.os.SystemClock;
import android.test.InstrumentationTestCase;
import android.view.View;
import android.view.WindowInsets;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Installed Activity-level checks for the native window/stage geometry owner.
 * These assertions use the real root view and WindowInsets, not private fixture
 * state or a second layout implementation.
 */
public final class LayoutDeviceTest extends InstrumentationTestCase {
    private MainActivity activity;

    private MainActivity launch() throws Exception {
        activity = (MainActivity) getInstrumentation().startActivitySync(
            new Intent(getInstrumentation().getTargetContext(), MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        return activity;
    }

    private View root() {
        View content = activity.findViewById(android.R.id.content);
        return ((android.view.ViewGroup) content).getChildAt(0);
    }

    private void awaitLaidOut(View root) {
        long deadline = SystemClock.elapsedRealtime() + 5000;
        boolean[] ready = {false};
        do {
            getInstrumentation().runOnMainSync(() -> ready[0] = root.getWidth() > 0 && root.getHeight() > 0 && activity.hasWindowFocus());
            if (ready[0]) return;
            SystemClock.sleep(50);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Activity root did not become laid out and focused");
    }

    public void testRepeatedIdenticalGeometryDoesNotScheduleAnotherLayout() throws Exception {
        launch();
        View root = root();
        awaitLaidOut(root);
        getInstrumentation().waitForIdleSync();
        AtomicInteger layoutCount = new AtomicInteger();
        View.OnLayoutChangeListener listener = (view, left, top, right, bottom,
                oldLeft, oldTop, oldRight, oldBottom) -> layoutCount.incrementAndGet();
        getInstrumentation().runOnMainSync(() -> root.addOnLayoutChangeListener(listener));
        try {
            SystemClock.sleep(750);
        } finally {
            getInstrumentation().runOnMainSync(() -> root.removeOnLayoutChangeListener(listener));
        }
        assertTrue("recurring root layout passes: " + layoutCount.get(), layoutCount.get() <= 1);
    }

    public void testActualInsetsKeepStageBetweenSystemBarsWithoutRootPadding() throws Exception {
        launch();
        View root = root();
        awaitLaidOut(root);
        long deadline = SystemClock.elapsedRealtime() + 5000;
        int[] expected = new int[4];
        do {
            getInstrumentation().runOnMainSync(() -> {
                WindowInsets insets = root.getRootWindowInsets();
                if (insets == null) return;
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                float density = activity.getResources().getDisplayMetrics().density;
                int rootWidthDp = Math.round(root.getWidth() / density);
                int rootHeightDp = Math.round(root.getHeight() / density);
                expected[0] = Math.round((Math.round(bars.top / density) + MainActivity.CHROME_HEIGHT_DP) * density);
                expected[1] = Math.round((rootHeightDp - Math.round(bars.bottom / density)) * density);
                expected[2] = Math.round((rootWidthDp - Math.round(bars.left / density)
                    - Math.round(bars.right / density)) * density);
                expected[3] = root.getPaddingTop() + root.getPaddingBottom() + root.getPaddingLeft() + root.getPaddingRight();
            });
            View stage = (View) activity.videoClip.getParent();
            boolean[] matched = {false};
            getInstrumentation().runOnMainSync(() -> matched[0] = stage.getTop() == expected[0]
                && stage.getBottom() == expected[1] && stage.getWidth() == expected[2]);
            if (matched[0] && expected[3] == 0) return;
            SystemClock.sleep(50);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("stage did not match real system-bar insets: " + java.util.Arrays.toString(expected));
    }

    @Override protected void tearDown() throws Exception {
        if (activity != null) {
            getInstrumentation().runOnMainSync(() -> activity.finish());
            activity = null;
        }
        super.tearDown();
    }
}
