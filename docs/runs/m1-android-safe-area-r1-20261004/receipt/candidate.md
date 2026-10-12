# Android native safe area r1 receipt

Status: `INCOMPLETE_UI_OWNER_HANDOFF`

This receipt does not claim `INSTALLED_GEOMETRY_AUTHOR_VERIFIED` or M1 PASS.
The native owner fix is built, installed, and geometry-verified. The installed
no-config toolbar remains under the status bar because the UI local-shell CSS
does not consume the typed safe area. That write belongs to the UI/domain owner.

## Candidate identity

- Branch: `codex/m1-android-safe-area-r1-20261004`
- Frozen input: `fe8193fc48aa58a0e817fe9215a605dd2785f24f` /
  tree `5c22861b44dfd6f643a8c346d7b9fa05d1434ead`
- Source commit: `71aa70cb94e2f1f6d797bfd60bc776a92f7080b7` /
  tree `4da374f4ca9458e803bbcda9a2017450c012a617`
- Changed product paths:
  `apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java`,
  `apps/android/app/src/test/java/com/agentbrowser/probe/NativeLayoutGeometryTest.java`,
  `apps/android/app/src/androidTest/java/com/agentbrowser/probe/LayoutDeviceTest.java`
- Base `origin/main`: `bc9d67ad037be2c7119bee692f9ad362417ffd05`
- No merge or push was performed.

## Causal root fix

The installed RED process rewrote `FrameLayout.LayoutParams` for stage,
videoClip, and SurfaceView on every layout callback even when the desired
frame was unchanged. That scheduled a second layout pass, posted the next
frame, and repeated every 15-20 ms.

The fix compares the desired frame against the actual `LayoutParams` and writes
only when width, height, left, top, or gravity differ. Legitimate rotation,
inset, and frame-size changes still write once. No time debounce, dropped
layout notification, cached layout truth, timeout extension, or second
coordinate path was added.

## Build and test evidence

- `npm ci`: exit 0, `raw/npm-ci.log`
- `bash scripts/android.sh --rerun-tasks testDebugUnitTest assembleDebug
  assembleDebugAndroidTest`: exit 0, 68 unit tests, `raw/gradle-build.log`
- App APK: `ab56723fd316b9c171b6b4f4a66dbed5f4ddacb54f9235724749b078a9f04740`
- Android test APK:
  `4589d642a31746322593982b86ffc7298d0540dedb0b658ef9289fdadac5a192`
- Installed `LayoutDeviceTest`: `am instrument` exit 0, `OK (2 tests)`,
  `raw/instrument-layout.log`
- Stable installed log window: zero `requestLayout() improperly called`
  entries, `raw/logcat-green-stable.txt`

## Installed binding

- Device: `emulator-5554`, SDK 36, targetSdk 36
- `adb install -r`: exit 0, `raw/adb-install.log`
- Installed base APK hash equals the candidate APK hash,
  `raw/installed-base-sha256.txt`
- versionCode `1`, versionName `0.1.0-probe`, signature `10bb648d`,
  firstInstallTime `2026-10-03 15:58:37` preserved

## Typed geometry freeze

Installed no-config portrait projection:

```json
{
  "drawableWindowRect": {"x": 0, "y": 0, "width": 411, "height": 914},
  "safeContentRect": {"x": 0, "y": 24, "width": 411, "height": 866},
  "chromeRect": {"x": 0, "y": 24, "width": 411, "height": 56},
  "advertisedViewportRect": {"x": 0, "y": 80, "width": 411, "height": 810},
  "presentedContentRect": {"x": 0, "y": 80, "width": 411, "height": 810},
  "webRect": {"x": 0, "y": 0, "width": 411, "height": 914},
  "visibleContent": "webview",
  "currentTabId": null,
  "presentationViewport": {"x": 0, "y": 80, "width": 411, "height": 810},
  "geometryMatches": false,
  "presentation": "browsing",
  "revision": 5
}
```

The UI receives this projection and computes `--safe-top:24px` and
`--safe-bottom:24px`. It renders `main.local-shell` with `data-remote=false`;
`.browser-chrome` is `y=0` and `#address` is `y=5px`, still under the 24dp
status bar. Evidence: `raw/cdp-green-layout.json`,
`raw/cdp-green-ui.json`, `raw/cdp-post-rotation-layout.json`.

## First failing gate and owner handoff

First failing edge: `packages/ui-plugins/probe.css` local-shell toolbar rules.
The installed app is correctly local (no remote Host), but the local-shell rule
uses only `env(safe-area-inset-top)`, which is empty in this Android WebView,
while `var(--safe-top)` is already `24px`.

Minimal successor change:

```css
main[data-remote="false"] .browser-chrome {
  padding-top: max(6px, env(safe-area-inset-top), var(--safe-top, 0px));
}
@media (max-width: 560px) {
  main[data-remote="false"] .browser-chrome {
    padding-top: max(4px, env(safe-area-inset-top), var(--safe-top, 0px));
  }
}
```

Owner: UI/domain (`packages/ui-plugins/probe.css`). This worker did not write
outside the allowed product scope. After that UI fix, rebuild/install the same
entry and replay the installed no-config toolbar before accepting the shell
geometry.

## Preserved behavior and remaining gates

- Predictive Back cleanup and the existing frame identity/JNI behavior were
  preserved; no JNI, NetworkFrame, or NetworkSession change was made.
- Drawer open/close and system Back passed on the installed candidate.
- Portrait/landscape/restore stayed bounded with no cumulative native inset.
- Address IME is not applicable while the unconfigured address is disabled.
- Paired remote video, touch, caret, remote IME, BB01/03/05/06/08 remain
  `REMOTE_E2E_WAIT`.
- No independent implementation review, merge, push, OTA, or M1 PASS is
  claimed.
