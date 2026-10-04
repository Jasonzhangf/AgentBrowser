# Run notes: m1-android-native-layout-r1-20261004

Task: AB-04 Android native layout stage (edge-to-edge root, derived rects,
typed layout projection, `set_presentation`, Back handling, adjustResize).

Inputs / frozen identity:

- worktree `/Volumes/Intel/playground/agentbrowser/m1-android-native-layout-r1-20261004`
- branch `codex/m1-android-native-layout-r1-20261004`
- HEAD `a31c1d9379d0710814315492955291a753ea0004` / tree `710a973dd2bb0c0c20b9193691198770ab6a2e50`
- design `65f1b90`, tree `5a75141c5b7c5535ef3dad95e0e8696f2909a599`
- frozen protocol root `/Volumes/Intel/playground/obscura/m1-profile-navigation-impl-r1-20261003/protocol/browser`
  `src/lib.rs` sha256 `2344ad88b52e34af1e8d6ccbd6de998770bbeca080e6ec57cd0e047aeb6edab9`
- leased target `/Volumes/Intel/playground/agentbrowser/m1-android-native-input-r1-20261003/target`

## Node 0 - input and environment

Time: 2026-10-04.

- Conclusion: clean worktree at the required HEAD/tree; JDK, Android SDK and
  `emulator-5554` present; no product edit before this node.
- Evidence: `git status --short --branch`, `git rev-parse HEAD HEAD^{tree}`,
  `adb devices`.
- Environment: `JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home`,
  `ANDROID_HOME=$HOME/Library/Android/sdk`,
  `OBSCURA_PROTOCOL_ROOT=<frozen protocol>`, `CARGO_TARGET_DIR=<leased target>`,
  `CARGO_INCREMENTAL=0`, `CARGO_BUILD_JOBS=2`.
- Next: implement the native layout owner.

## Node 1 - product implementation (owned files)

Time: 2026-10-04.

- Conclusion: implemented the admitted contract in the owned files only.
  - `MainActivity.java`: single edge-to-edge `FrameLayout` root with no padding;
    insets listener reads `systemBars()`/`ime()` for derivation only; typed
    root-local dp rects (`drawableWindowRect`, `safeContentRect`,
    `advertisedViewportRect`, `presentedContentRect`, `chromeRect`, `webRect`);
    stage receives `presentedContentRect` root-local px, `videoClip` receives
    stage-local aspect-fit/center, `SurfaceView` receives clip-local coded/source
    geometry; `reportViewport()` reads only `advertisedViewportRect`; removed the
    fixed 370/136 `layoutChrome()` panel semantics; synchronous typed
    `set_presentation` on the main thread with caller id/state preserved and
    `applied`/`rejected` results; overlay raises the WebView to
    `drawableWindowRect` and does not request insets, reload, or declare
    viewport; Back closes overlay before drawer and never injects remote events;
    permanent listeners removed in `onDestroy`, RemoteIme deactivated in
    `onStop`/`onDestroy`.
  - `AndroidManifest.xml`: added `android:windowSoftInputMode="adjustResize"`.
  - `NativeLayoutGeometryTest.java`: pure root-local geometry derivation tests.
- Evidence: file diffs; `docs/runs/m1-android-native-layout-r1-20261004/raw/javac-main.log`.
- Next: build the product.

## Node 2 - required Gradle build (FAILED, out-of-scope native boundary)

Time: 2026-10-04.

- Command:
  `bash scripts/android.sh --rerun-tasks testDebugUnitTest assembleDebug assembleDebugAndroidTest`
- Result: exit 1 at `:app:buildNative` -> `scripts/build-native.py` -> `cargo build`.
- First boundary (read-only, outside this author's allowlist):
  `packages/android-bridge/src/lib.rs:279` constructs
  `agentbrowser_connection::PresentedFrame` with the pre-Host11 four fields;
  the frozen protocol/`client-connection` `PresentedFrame` now requires
  `tab_id` and `presentation_revision`.
- Exact rustc error: `error[E0063]: missing fields presentation_revision and
  tab_id in initializer of PresentedFrame`.
- Evidence: `raw/gradle.log`, `raw/cargo-test.log`.
- Minimal successor fix: in `packages/android-bridge/src/lib.rs` `editing`
  path, copy `displayed.tab_id` and `displayed.presentation_revision` into the
  `PresentedFrame` literal. `DisplayedFrame` already carries both fields, so no
  protocol or client-connection change is needed.
- Note: the same failure reproduces on `origin/main` (the missing adapter is not
  present upstream), so this is a pre-existing frozen-source defect, not a
  regression from this change.
- Next: verify the owned Java code independently, since Gradle cannot isolate
  Java from the native task.

## Node 3 - owned Java compile + geometry unit tests (PASS)

Time: 2026-10-04.

- Command: `javac` all `apps/android/app/src/main/java` against
  `android-36/android.jar`, then compile/run
  `NativeLayoutGeometryTest` with JUnit 4.13.2 from the Gradle cache.
- Result: `JAVAC_MAIN_EXIT=0`, `JAVAC_TEST_EXIT=0`, `JUNIT_EXIT=0`,
  `OK (5 tests)`.
- Evidence: `raw/javac-main.log`, `raw/javac-test.log`, `raw/junit-layout.log`.
- Note: this verifies source compilation and pure geometry derivation only. It
  is not an installed-APK or device result.
- Next: prepare candidate commit and handoff receipt.

## Node 4 - capability boundary for native-video visibility

Time: 2026-10-04.

- Conclusion: the typed projection emits all owned rects, but
  `displayedFrame` stays absent and `currentTabId` is `null`, so
  `visibleContent` remains `webview` and `geometryMatches` is `false`.
- Reason: the design requires `displayedFrame.tabId == currentTabId` from the
  same Host/display owner. The read-only Android surface exposes `session_id`
  and frame `document_revision`/`viewport_revision` but no independent tab
  identity; fabricating it from session id, URL or DOM is forbidden.
- Effect: the empty/waiting/error WebView path is authoritative and correct.
  `native-video` is not advertised until a real tab identity exists.
- Required successor: expose the Host `active_tab_id` and the presented frame
  `tab_id` through the read-only `NetworkSession`/android-bridge status/frame
  projection, then let `MainActivity` compare them.

## Node 5 - device evidence

Time: 2026-10-04.

- Status: NOT RUN. No APK was produced because the native build failed in Node 2.
  No install, no `set_presentation`/drawer/rotation/IME device capture, no
  `REMOTE_E2E` replay. Emulator operations remain unused and released.
