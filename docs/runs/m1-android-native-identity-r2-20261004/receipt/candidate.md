# Candidate Receipt - m1-android-native-identity-r2-20261004

Status: SOURCE_BUILT / NATIVE_SHELL_E2E_PARTIAL / REMOTE_E2E_WAIT

Author role: fresh standalone GCM implementation author (MainActivity writer,
handed from terminal r1). Not goalcontroller/reviewer/Collab master. No review,
merge or push performed.

## Source

| Item | Value |
| --- | --- |
| worktree | /Volumes/Intel/playground/agentbrowser/m1-android-native-identity-r2-20261004 |
| branch | codex/m1-android-native-identity-r2-20261004 |
| input HEAD | 034cba5761b37d0a14ff9fef5baedfa608100080 |
| input tree | 56f99968a1f280f55a2bb7f823861cbcd615de14 |
| origin/main at intake | bc9d67ad037be2c7119bee692f9ad362417ffd05 |
| merge-base HEAD origin/main | bc9d67ad037be2c7119bee692f9ad362417ffd05 |
| candidate commit | branch tip `codex/m1-android-native-identity-r2-20261004` (final SHA + tree reported in the delivery output; a commit cannot embed its own hash) |
| candidate tree | reported alongside the final SHA in the delivery output |
| frozen protocol root | /Volumes/Intel/playground/obscura/m1-profile-navigation-impl-r1-20261003/protocol/browser |
| frozen libhash | 2344ad88b52e34af1e8d6ccbd6de998770bbeca080e6ec57cd0e047aeb6edab9 |
| sole target dir | /Volumes/Intel/playground/agentbrowser/m1-android-native-input-r1-20261003/target |

Input integrity note: the frozen source tree hash recorded at intake was
computed while the tree contained the pre-improvement edits; the frozen
`src/lib.rs` sha256 `2344ad88b52e34af1e8d6ccbd6de998770bbeca080e6ec57cd0e047aeb6edab9`
was recomputed from disk at build time and matches the value above.

## Build environment

```
JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
ANDROID_HOME="$HOME/Library/Android/sdk"
OBSCURA_PROTOCOL_ROOT=/Volumes/Intel/playground/obscura/m1-profile-navigation-impl-r1-20261003/protocol/browser
CARGO_TARGET_DIR=/Volumes/Intel/playground/agentbrowser/m1-android-native-input-r1-20261003/target
CARGO_INCREMENTAL=0
CARGO_BUILD_JOBS=2
```

## Commands and results

| Command | Exit | Result | Log |
| --- | --- | --- | --- |
| `cargo test --locked --release -p agentbrowser-android --config 'patch.crates-io.obscura-host-protocol.path="<frozen>"'` | 0 | 3 passed, 0 failed | `raw/cargo-test.log` |
| `npm ci` | 0 | 0 vulnerabilities | `raw/npm-ci.log` |
| `bash scripts/android.sh --rerun-tasks testDebugUnitTest assembleDebug assembleDebugAndroidTest` | 0 | BUILD SUCCESSFUL; 66 unit tests, 0 failures, 0 errors | `raw/gradle.log` |

Pre-fix red (Node 1): same `cargo test` command failed with exit 101 and
`error[E0063]: missing fields presentation_revision and tab_id in initializer of
PresentedFrame` at `packages/android-bridge/src/lib.rs:279:42`
(`raw/cargo-test-pre.log`).

## Product changes

| File | sha256 | Change |
| --- | --- | --- |
| `packages/android-bridge/src/lib.rs` | 372816e5bdc87422378323bb33329e054129748b5e8168acbe0198137fedde1f | `editing` `PresentedFrame` copies retained `displayed.tab_id` / `presentation_revision`; frame JNI projection passes `session_id`/`tab_id`/`presentation_revision`; i64 overflow guard includes `presentation_revision` |
| `apps/android/app/src/main/java/com/agentbrowser/probe/NetworkFrame.java` | f9c1f86e0d12d14da31b5e4a958bad10867ae1160496251e4573ddd3dcead203 | carries `sessionId`/`tabId`/`presentationRevision`; rejects missing/empty identity and negative revision (`NETWORK_FRAME_IDENTITY`) |
| `apps/android/app/src/main/java/com/agentbrowser/probe/NetworkSession.java` | 911cbb840def894666b16714da6fa3f98f758126582578b4e42164db9c4a8c16 | typed read-only Host identity accessors; main-looper `onStatusSync` fires only on real identity drift |
| `apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java` | 26b915af4381f48e0ca9fe8373b7c5816400f39cab24de639b3aa314a488de86 | `identityMatches` + `publishDisplayedFrame` gate the displayed frame against one Host identity; layout projection emits real `currentTabId`/`displayedFrame`; predictive-back callback shared with `onBackPressed()` |

`NativeConnection.java` did not require changes: the frame constructor call site
already matched the widened descriptor.

JNI frame constructor descriptor: `([BIIIIJJJJLjava/lang/String;Ljava/lang/String;J)V`
(pinned by `NetworkFrameIdentityTest.constructorMatchesNativeJniDescriptor`).

## Tests

- `NetworkFrameIdentityTest` (5): JNI descriptor equality, identity carry-over,
  missing session/tab rejection, negative revision rejection, access-unit
  geometry/generation preservation.
- `NativeIdentityProjectionTest` (8): matching identity validates; stale
  generation, cross-session, cross-tab, stale presentation revision (A->B->A),
  stale document/viewport revision and missing Host/frame tab identity are all
  refused; `identityStatusChanged` fires only on real drift.

## Installed artifact

| Item | Value |
| --- | --- |
| device | emulator-5554 (SDK 36, arm64) |
| command | `adb -s emulator-5554 install -r apps/android/app/build/outputs/apk/debug/app-debug.apk` |
| app-debug.apk sha256 | e1fffe6f0d34b72a7e51270f8a7be243272aba54619bc589db3129ebceef2fdd |
| installed base.apk sha256 | e1fffe6f0d34b72a7e51270f8a7be243272aba54619bc589db3129ebceef2fdd (pulled from `/data/app/.../base.apk`, byte-identical) |
| app-debug-androidTest.apk sha256 | 1f722a52f65c8dcfd59e6e83a4f6ba6d71f9c1e7aab57e8a0901130d8a6af39e |
| package | com.agentbrowser.probe, versionCode=1, versionName=0.1.0-probe, targetSdk=36 |
| signature | `10bb648d` (`PackageSignatures{f48fbc6 version:2}`) |
| firstInstallTime | 2026-10-03 15:58:37 (preserved) |
| lastUpdateTime | 2026-10-04 03:36:19 |

No `clear data`, no wipe, no provisioning, no credential or network grant.
`networkConfigured=false`.

## Device evidence (bound to the installed candidate)

| State | Result | Screenshot | UI XML |
| --- | --- | --- | --- |
| launcher no-config | toolbar `[0,0][1080,265]`, video clip `[0,210][1079,2336]`, SurfaceView `[0,314][1079,2232]`; no 370/136 panel; `visibleContent=webview`, `currentTabId=null`, no `displayedFrame` | `screenshots/10-final-nostate.png` | `raw/10-final-nostate.xml` |
| drawer open | overlay dialog `[84,63][1080,2338]`, `presentation=overlay` | `screenshots/11-final-drawer.png` | `raw/11-final-drawer.xml` |
| system Back | overlay closed, app stayed foreground (`mCurrentFocus=com.agentbrowser.probe/.MainActivity`), not finishing | `screenshots/12-final-back-closed.png` | `raw/12-final-back-closed.xml` |
| landscape | chrome + full content area preserved, focus retained | `screenshots/13-final-landscape.png` | `raw/13-final-landscape.xml` |
| portrait (restored) | chrome + full content area preserved | `screenshots/14-final-portrait.png` | `raw/14-final-portrait.xml` |

Earlier intermediate pass before the final build: `screenshots/*-backfix.png`,
`raw/*-backfix.xml`, `raw/back-step1-open.xml`.

Back root cause and fix: `targetSdk 36` routes system Back through
`OnBackInvokedCallback`, so the legacy `onBackPressed()` override never ran and
the app exited to the prior task. A callback shared with the legacy override now
closes the overlay first, then falls through to default back, and is unregistered
in `onDestroy`.

## Not run / out of scope

- `androidTest` shell test not shipped: the draft instrumentation harness hung on
  this emulator (runner launched the activity, then no status was ever
  returned; `am instrument` had to be stopped with an explicit PID). It was
  removed so no unverified test is committed; device proof is the manual
  launcher evidence above.
- Remote video, touch, caret, system-IME composition, normal setup and
  BB01/03/05/06/08 remain `REMOTE_E2E_WAIT` until the controller grants a paired
  repaired Host and provisioning.
- No Host, browser, credential, network grant, 15T or other device touched.
- No review, merge or push.

## Cleanup

- Device `/sdcard` captures created by this run removed by explicit path.
- Large logcat dumps removed from `raw/`; UI XML dumps and screenshots retained.
- The leftover `com.agentbrowser.probe.test` instrumentation package from the
  discarded shell test was uninstalled; only `com.agentbrowser.probe` remains.
- Host temp extraction dirs created by this run were removed.
- Source, built APKs, leased Cargo target and the emulator are retained for the
  next phase.
- `adb forward` entries on emulator-5554 are pre-existing (webview devtools
  sockets) and were left untouched.
- No broad process kill used; the stuck instrumentation was stopped with an
  explicit PID.
