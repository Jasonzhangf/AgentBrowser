# Receipt: Android native browser layout r1

Status: `SOURCE_BUILT_PARTIAL` / `NATIVE_SHELL_E2E_BLOCKED` / `REMOTE_E2E_WAIT`

## Candidate identity

- worktree: `/Volumes/Intel/playground/agentbrowser/m1-android-native-layout-r1-20261004`
- branch: `codex/m1-android-native-layout-r1-20261004`
- input HEAD: `a31c1d9379d0710814315492955291a753ea0004` / tree `710a973dd2bb0c0c20b9193691198770ab6a2e50`
- design: `65f1b90` / tree `5a75141c5b7c5535ef3dad95e0e8696f2909a599`
- protocol root: `/Volumes/Intel/playground/obscura/m1-profile-navigation-impl-r1-20261003/protocol/browser`
- protocol `src/lib.rs` sha256: `2344ad88b52e34af1e8d6ccbd6de998770bbeca080e6ec57cd0e047aeb6edab9`
- leased Cargo target: `/Volumes/Intel/playground/agentbrowser/m1-android-native-input-r1-20261003/target`

## Owned product diff (allowlist only)

| File | sha256 |
| --- | --- |
| `apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java` | `10654b33044d7273d55ae5361992eac93839a2429422f11327761aa395c0de69` |
| `apps/android/app/src/main/AndroidManifest.xml` | `58a6b8f3d933dcdf5757b6e1a4298f80a33bd1143028f0587ca80082243ad3ea` |
| `apps/android/app/src/test/java/com/agentbrowser/probe/NativeLayoutGeometryTest.java` | `ee5044c899673039ab672a5dc827a73e7624fa70a800cb218e793e1a3923ba01` |

Read-only files (`NetworkSession.java`, `android-bridge`, `client-connection`,
protocol, UI/domain, scripts, SDK, hooks, AGENTS) have zero diff in this run.

## Implemented contract

1. Single edge-to-edge `FrameLayout` root with no padding; insets listener reads
   `systemBars()`/`ime()` only for derivation.
2. Root-local dp rects: `drawableWindowRect`, `safeContentRect` (bars once),
   `chromeRect` (thin 56dp strip), `advertisedViewportRect` (excludes IME),
   `presentedContentRect` (subtracts effective IME overlap once).
3. Parent-space application: `stage` gets `presentedContentRect` root-local px;
   `videoClip` gets stage-local aspect-fit/center; `SurfaceView` gets clip-local
   coded/source geometry; `RemoteImeView` stays a stage-local 1x1 anchor.
4. `reportViewport()` reads only `advertisedViewportRect`; trigger set is
   physical window/bars/chrome change. IME, drawer, presentation and connection
   changes do not declare.
5. Removed fixed 370dp/136dp `layoutChrome()` panel semantics.
6. Manifest `android:windowSoftInputMode="adjustResize"`.
7. Synchronous typed `set_presentation` on the main thread preserves caller
   `id`/`state` and returns `applied` with `{webRect, revision}` or `rejected`
   with one of `NO_AUTHORITY`/`ACTIVITY_NOT_RESUMED`/`GEOMETRY_UNAVAILABLE`;
   unknown fields are rejected.
8. Overlay raises the WebView to `drawableWindowRect`; does not change stage
   size, request insets, reload, declare viewport, or mutate connection/control
   state. Back closes overlay first, keeps the app foreground, never injects a
   remote event.
9. Permanent listeners removed in `onDestroy`; `RemoteImeView.deactivate()` on
   `onStop`/`onDestroy`.

## Evidence

| Evidence | Result |
| --- | --- |
| `npm ci` | PASS |
| `bash scripts/android.sh --rerun-tasks testDebugUnitTest assembleDebug assembleDebugAndroidTest` | FAIL exit 1 at `:app:buildNative` |
| `cargo test --locked --release -p agentbrowser-android --config <frozen patch>` | FAIL exit 101, same boundary |
| `javac` owned Android sources vs `android-36/android.jar` | PASS exit 0 |
| `NativeLayoutGeometryTest` (5 tests, JUnit 4.13.2) | PASS `OK (5 tests)` |
| APK build / install / device UI | NOT RUN (blocked) |

Raw logs: `../raw/gradle.log`, `../raw/cargo-test.log`,
`../raw/javac-main.log`, `../raw/javac-test.log`, `../raw/junit-layout.log`.

## First blocked boundary (out of scope)

`packages/android-bridge/src/lib.rs:279` (read-only) builds
`agentbrowser_connection::PresentedFrame` with the pre-Host11 field set; the
frozen `PresentedFrame` also requires `tab_id` and `presentation_revision`.

```
error[E0063]: missing fields `presentation_revision` and `tab_id`
in initializer of `PresentedFrame`
  --> packages/android-bridge/src/lib.rs:279:42
```

This blocks the native `.so`, so no APK can be assembled and no install/device
shell or remote replay can be produced in this run. The same failure reproduces
against `origin/main`.

Minimal successor fix (owner: android-bridge / client-connection): copy
`displayed.tab_id` and `displayed.presentation_revision` into the `PresentedFrame`
literal in the `editing` path. `DisplayedFrame` already carries both fields; no
protocol change is required.

## Second boundary (native-video visibility)

The design requires `displayedFrame.tabId == currentTabId` from the same
Host/display owner. The read-only Android projection exposes no independent tab
identity, so this run emits `currentTabId: null`, omits `displayedFrame`, and
keeps `visibleContent = 'webview'` / `geometryMatches = false`. Required
successor: expose Host `active_tab_id` and the presented frame `tab_id` through
the read-only `NetworkSession`/android-bridge status/frame projection.

## Not claimed

- No installed APK, package hash, version, or installed path.
- No `NATIVE_SHELL_E2E` device captures (no-config, waiting/error, drawer
  open/close/retry, portrait/landscape, address IME).
- No remote video geometry/touch/caret/system-IME composition, no BB01/03/05/06/08.
- No functional/review/M1 PASS.

Emulator `emulator-5554` was not operated (no install, no taps, no data change);
no device settings, Host/browser, or network state was touched. Own Cargo target
and emulator are retained for intake.
