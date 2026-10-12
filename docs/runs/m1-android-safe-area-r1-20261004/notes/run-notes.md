# Android native safe area r1 run notes

## 2026-10-04T00:00:00Z / intake / READY

- Goal: fix the installed no-config status-bar overlap and the native relayout
  loop without duplicating UI geometry, then verify the installed launcher path
  on emulator-5554.
- Input: branch `codex/m1-android-safe-area-r1-20261004`, frozen source
  `fe8193fc48aa58a0e817fe9215a605dd2785f24f` / tree
  `5c22861b44dfd6f643a8c346d7b9fa05d1434ead`.
- Allowed product path: `apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java`
  and focused Android layout regression tests. Other source remains read-only.
- Known RED input: installed screenshot `14-final-portrait.png` SHA-256
  `c98a87f4da1539492d03a0ec13b70aaef99dcd010be94abb52d8b9d5b9316cca`;
  APK SHA-256 `e1fffe6f0d34b72a7e51270f8a7be243272aba54619bc589db3129ebceef2fdd`.
- Source observation: `rootLayoutListener -> recomputeLayout -> applyGeometry`
  always sets `stage` params; `stageLayoutListener -> layoutVideo` always sets
  clip/video params. The exact causal loop and the first missing geometry edge
  still require same-entry runtime evidence.
- Next: capture installed launcher screenshot, window XML, package identity and
  typed layout snapshot; preserve the raw evidence before source edits.

## 2026-10-04T04:24Z / installed RED reproduction / CONFIRMED

- Installed no-config launch `01-red-portrait.png` SHA-256
  `0aa862ba96aa05d11ffd674062012f14a50ed2462f20612f0a7c5a715f4c4343`;
  matching XML SHA-256
  `cbd1ff184b5f532f1a29add458c2a0d3f7d46186427343f5cd748ff63dac55ad`.
  Address bounds `[42,10][627,126]` are under the `[0,0][1080,63]` status bar.
- Installed APK was `e1fffe6f...`; package versionCode `1`, versionName
  `0.1.0-probe`, targetSdk `36`, signature `10bb648d`, firstInstallTime
  `2026-10-03 15:58:37`.
- Same-PID logcat showed `requestLayout() improperly called` for stage,
  videoClip and SurfaceView every 15-20 ms. Source path:
  `rootLayoutListener -> recomputeLayout -> applyGeometry -> setLayoutParams`,
  plus `stageLayoutListener -> layoutVideo -> setLayoutParams`.
- Next: isolate the native write edge, prove no recurring pass after a
  conditional write, then re-check the UI projection.

## 2026-10-04T04:26Z / native root fix / SOURCE_BUILT

- Changed `MainActivity` only: `applyFrame()` compares the desired
  `FrameLayout.LayoutParams` against the actual params and writes only when
  width, height, margins or gravity differ. Stage, videoClip and SurfaceView
  all use this guard; no debounce, sleep, cache or second geometry truth was
  added.
- Added pure unit coverage for identical versus changed frame params and an
  installed Activity test that measures recurring root layout passes and real
  system-bar bounds.
- Exact build command with the frozen toolchain:
  `npm ci`; `bash scripts/android.sh --rerun-tasks testDebugUnitTest
  assembleDebug assembleDebugAndroidTest`. Both exited 0; 68 unit tests
  passed. Raw logs: `raw/npm-ci.log`, `raw/gradle-build.log`.
- Candidate source commit: `71aa70cb94e2f1f6d797bfd60bc776a92f7080b7`;
  tree `4da374f4ca9458e803bbcda9a2017450c012a617`. Worktree source diff versus
  the commit is empty.
- App APK SHA-256
  `ab56723fd316b9c171b6b4f4a66dbed5f4ddacb54f9235724749b078a9f04740`;
  test APK SHA-256
  `4589d642a31746322593982b86ffc7298d0540dedb0b658ef9289fdadac5a192`.

## 2026-10-04T04:28Z / installed binding and public device test / PASS

- `adb -s emulator-5554 install -r` preserved data: firstInstallTime stayed
  `2026-10-03 15:58:37`, version/signature stayed `1` / `0.1.0-probe` /
  `10bb648d`. Installed base APK hash equals the local APK hash above.
- `am instrument -w -r -e class com.agentbrowser.probe.LayoutDeviceTest
  com.agentbrowser.probe.test/android.test.InstrumentationTestRunner` exited
  `0`, reported `OK (2 tests)`, and terminated normally. Raw:
  `raw/instrument-layout.log`.
- Stable log window after rotation restore contains zero
  `requestLayout() improperly called` entries. Initial and real rotation
  transitions still show one bounded pass each, as expected.
- Next: capture the actual typed projection and the UI consumption path.

## 2026-10-04T04:29Z / geometry trace and first missing edge / UI OWNER HANDOFF

- Native typed projection from the installed app (`raw/cdp-green-layout.json`
  and `raw/cdp-post-rotation-layout.json`) is correct:
  `drawableWindowRect=[0,0,411,914]`,
  `safeContentRect=[0,24,411,866]`,
  `chromeRect=[0,24,411,56]`,
  `advertisedViewportRect=[0,80,411,810]`,
  `presentedContentRect=[0,80,411,810]`,
  `webRect=[0,0,411,914]`, `visibleContent=webview`, `currentTabId=null`.
- The UI receives that projection and computes `--safe-top:24px` and
  `--safe-bottom:24px` on `<main>`, but the installed DOM is
  `local-shell` / `data-remote=false`; `.browser-chrome` is at `y=0` and
  `#address` is at `y=5px`, under the 24dp status bar.
- First missing edge is UI-owned: `packages/ui-plugins/probe.css` uses
  `env(safe-area-inset-top)` for `main[data-remote="false"]` and only consumes
  `var(--safe-top)` for `data-remote="true"`. Android WebView reports no CSS
  env inset here, so the typed native safe area is available but unused.
- Minimal exact successor change: make both local-shell rules consume the
  typed value, e.g.
  `padding-top: max(6px, env(safe-area-inset-top), var(--safe-top, 0px));`
  and the mobile override
  `padding-top: max(4px, env(safe-area-inset-top), var(--safe-top, 0px));`.
  This is a UI/domain write, outside this worker's allowed product paths.

## 2026-10-04T04:30Z / drawer, Back, rotation / NATIVE PASS, UI GAP REMAINS

- A lower-half tap on the menu control opened the drawer; drawer bounds were
  `[32.9,24][411.4,890.3]` CSS, safely between the bars, and system Back
  closed it while the app remained foreground. Evidence:
  `screenshots/04-green-drawer-open.png`,
  `raw/04-green-drawer-open-window.xml`,
  `screenshots/05-green-back-closed.png`,
  `raw/05-green-back-closed-window.xml`.
- Landscape used `[0,0][2400,63]` status and `[0,1017][2400,1080]` navigation
  bars. Native geometry stayed bounded; the address still overlapped at
  `[420,15][1942,131]`. Restoring portrait returned `safeContentRect.y=24`
  with no cumulative inset. Evidence:
  `screenshots/06-green-landscape.png`,
  `raw/06-green-landscape-window.xml`,
  `screenshots/07-green-portrait-restored.png`,
  `raw/07-green-portrait-restored-window.xml`.
- Address field IME is not applicable in this no-config run: the address is
  disabled per the unconfigured-chrome contract. Remote video/touch/caret/IME
  remains `REMOTE_E2E_WAIT`.
- Status: `INCOMPLETE` / `UI_OWNER_HANDOFF`; native source fix and installed
  geometry are verified, but the user-visible safe-area acceptance cannot pass
  until the UI owner applies the local-shell safe-top rule above.
