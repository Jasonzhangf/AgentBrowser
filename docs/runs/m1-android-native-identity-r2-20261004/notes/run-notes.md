# Run notes: m1-android-native-identity-r2-20261004

Task: AB-04 Android native identity + reachable shell. Pass the real Host
`active_tab_id` / presented-frame `tab_id` / `presentation_revision` through the
native bridge, `NetworkFrame`, `NetworkSession` and `MainActivity` so the
admitted layout projection can actually advertise `native-video`, and make the
shell reachable on the leased emulator. Fresh standalone GCM implementation
author; no parent transcript, no Collab, no goal, no review/merge/push.

## Frozen identity / inputs

- worktree `/Volumes/Intel/playground/agentbrowser/m1-android-native-identity-r2-20261004`
- branch `codex/m1-android-native-identity-r2-20261004`
- HEAD `034cba5761b37d0a14ff9fef5baedfa608100080` / tree `56f99968a1f280f55a2bb7f823861cbcd615de14`
- latest `origin/main` `bc9d67ad037be2c7119bee692f9ad362417ffd05` (merge-base == HEAD)
- frozen protocol root `/Volumes/Intel/playground/obscura/m1-profile-navigation-impl-r1-20261003/protocol/browser`
  `src/lib.rs` sha256 `2344ad88b52e34af1e8d6ccbd6de998770bbeca080e6ec57cd0e047aeb6edab9`
- admitted design `/Volumes/Intel/playground/agentbrowser/m1-native-chrome-layout-design-r5-20261004/docs/runs/m1-native-chrome-layout-design-r1-20261003/`
  (`repair-design.md`, `implementation-contracts.md`, `blackbox-plan.md`)
- controller intake `/Volumes/Intel/playground/agentbrowser/m1-repair-model-20261002/docs/runs/m1-20261002/android-native-layout-r1-source-acceptance.md`
  and `.contract.md`
- leased Cargo target `/Volumes/Intel/playground/agentbrowser/m1-android-native-input-r1-20261003/target`
- leased device `emulator-5554` (Android SDK 36, sdk_gphone64_arm64)

## Node 0 - input and environment

Time: 2026-10-04.

- Conclusion: clean worktree exactly at the required HEAD/tree; `origin/main`
  is the merge-base (ff-only, no blind import). JDK 17, Android SDK and
  `emulator-5554` are present. No product edit before this node.
- Evidence: `git status --short --branch`, `git rev-parse HEAD HEAD^{tree}`,
  `git merge-base HEAD origin/main`, `adb devices -l`.
- Environment: `JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home`,
  `ANDROID_HOME=$HOME/Library/Android/sdk`,
  `OBSCURA_PROTOCOL_ROOT=<frozen protocol>`, `CARGO_TARGET_DIR=<leased target>`,
  `CARGO_INCREMENTAL=0`, `CARGO_BUILD_JOBS=2`.
- Next: reproduce the actual red (E0063) at the real entry, then implement.

## Node 1 - reproduce the red at the real entry

Time: 2026-10-04.

- Command (cwd = worktree, frozen env above):
  `cargo test --locked --release -p agentbrowser-android --config 'patch.crates-io.obscura-host-protocol.path="<frozen protocol>"'`
- Result: FAIL, exit 101. First error exactly:
  `error[E0063]: missing fields presentation_revision and tab_id in initializer of PresentedFrame`
  at `packages/android-bridge/src/lib.rs:279:42`.
- Confirms the intake: retained `DisplayedFrame` already carries `tab_id` /
  `presentation_revision`; only the `editing` `PresentedFrame` literal and the
  JNI/Java frame projection still omit the identity.
- Evidence: `raw/cargo-test-pre.log`.
- Next: fix the unique projection owner (`lib.rs`, `NetworkFrame.java`,
  `NetworkSession.java`, `MainActivity.java`).

## Node 2 - implementation + build (PASS)

Time: 2026-10-04.

- Conclusion: identity now flows through one projection, no guessed defaults:
  - `packages/android-bridge/src/lib.rs`: `editing` `PresentedFrame` copies
    `displayed.tab_id` / `displayed.presentation_revision`; the `frame` JNI
    projection passes `source.session_id`/`source.tab_id`/`source.presentation_revision`
    into the `NetworkFrame` constructor and includes `presentation_revision` in
    the i64 overflow guard.
  - `NetworkFrame.java`: carries `sessionId`/`tabId`/`presentationRevision` and
    rejects missing/empty identity and negative revision.
  - `NetworkSession.java`: typed read-only Host accessors (`activeTabId`,
    `hostSessionId`, `hostPresentationRevision`, `hostDocumentRevision`,
    `hostViewportRevision`, `hostViewportPending`, `generation`) and a
    main-looper `onStatusSync` that fires only when the identity actually drifts.
  - `MainActivity.java`: `identityMatches` gates the displayed frame against the
    same Host session/tab/presentation/document/viewport and local generation;
    `publishDisplayedFrame` runs on the main looper after the real decoder
    receipt and drops stale/cross-session/tab completions; the layout projection
    now emits the real `currentTabId` and `displayedFrame`.
- Build evidence:
  - `cargo test --locked --release -p agentbrowser-android --config <frozen patch>`:
    exit 0, 3 passed (was exit 101 E0063).
  - `npm ci`: exit 0.
  - `bash scripts/android.sh --rerun-tasks testDebugUnitTest assembleDebug assembleDebugAndroidTest`:
    exit 0, BUILD SUCCESSFUL; 66 unit tests, 0 failures (13 new).
- Artifacts:
  - `app-debug.apk` sha256 `93bd1f56af381d3e7f3b4b6fc54aaeaceaea426cdb7d79c3396eb4a6678ca03f`
  - `app-debug-androidTest.apk` sha256 `1f722a52f65c8dcfd59e6e83a4f6ba6d71f9c1e7aab57e8a0901130d8a6af39e`
  - JNI frame descriptor `([BIIIIJJJJLjava/lang/String;Ljava/lang/String;J)V`
- Raw logs: `raw/cargo-test.log`, `raw/npm-ci.log`, `raw/gradle.log`.
- Next: install on `emulator-5554` (data-preserving) and capture shell UI.

## Node 3 - installed shell UI + Back regression (PASS, in scope)

Time: 2026-10-04.

- Device: `emulator-5554` only (SDK 36, arm64). Install was
  `adb -s emulator-5554 install -r <app-debug.apk>`; data-preserving
  (`firstInstallTime` stayed 2026-10-03 15:58:37, `lastUpdateTime` moved to
  2026-10-04 03:36:19). Package signature `10bb648d`, `versionCode=1`,
  `versionName=0.1.0-probe`.
- Finding + root cause: system Back on `targetSdk 36` is delivered through the
  predictive-back path, so the legacy `onBackPressed()` override never ran and
  Back finished the activity instead of closing the drawer overlay.
- Fix (allowed file `MainActivity.java`): register an
  `OnBackInvokedCallback` (`>= TIRAMISU`) that dispatches to a shared
  `handleBack()` (overlay -> browsing first, otherwise default back), keep the
  `onBackPressed()` override delegating to the same method, and unregister the
  callback in `onDestroy`. No second Back semantic, no injected remote event.
- Verified on the final installed candidate:
  - no-config shell: toolbar `[0,0][1080,265]`, native video clip
    `[0,210][1079,2336]`, SurfaceView `[0,314][1079,2232]`; no 370/136 panel.
    `visibleContent="webview"`, `currentTabId=null`, no `displayedFrame`
    (no Host identity is fabricated).
  - drawer open: overlay dialog `[84,63][1080,2338]`, `presentation=overlay`.
  - system Back: drawer overlay closed, app stayed foreground
    (`mCurrentFocus=com.agentbrowser.probe/.MainActivity`, not finishing).
  - rotation portrait<->landscape: chrome + full content area preserved, no
    panel, focus retained.
- Evidence (not yet cleaned; cleanups noted below):
  `screenshots/{10-final-nostate,11-final-drawer,12-final-back-closed,13-final-landscape,14-final-portrait}.png`
  and `raw/{10-final-nostate,11-final-drawer,12-final-back-closed,13-final-landscape,14-final-portrait}.xml`;
  earlier back-fix pass in `screenshots/*-backfix.png`.
- Removed the draft `androidTest` shell test (`NativeIdentityDeviceTest.java`)
  before commit: on this emulator the instrumentation harness hung in its
  key-injection/gesture path (logcat shows the runner starts the activity and
  then never returns a status; `am instrument` had to be stopped with an
  explicit PID). It is not committed so no unverified test ships; the Back/UI
  proof is the manual launcher evidence above.
- Remote-only items unchanged and still `REMOTE_E2E_WAIT`: full remote video,
  touch, caret, system-IME composition, and BB01/03/05/06/08.
- Next: receipt + APK hashes, explicit allowlist commit, cleanup.

## Node 4 - build/test re-verify on the committed candidate (PASS)

Time: 2026-10-04.

- `cargo test --locked --release -p agentbrowser-android --config <frozen patch>`:
  exit 0, 3 passed (was exit 101 E0063).
- `npm ci`: exit 0, 0 vulnerabilities.
- `bash scripts/android.sh --rerun-tasks testDebugUnitTest assembleDebug assembleDebugAndroidTest`:
  exit 0, BUILD SUCCESSFUL; 66 unit tests, 0 failures/0 errors (13 new identity
  tests: `NetworkFrameIdentityTest` 5, `NativeIdentityProjectionTest` 8).
- Logs: `raw/cargo-test.log`, `raw/npm-ci.log`, `raw/gradle.log`.

## Node 5 - commit (PASS)

Time: 2026-10-04.

- Committed with hooks retained (`core.hooksPath=.githooks`, `pre-commit` ran
  `npm run verify:local`) and explicit allowlist paths only (no `git add .`).
- Candidate commit is the branch tip of `codex/m1-android-native-identity-r2-20261004`
  (final SHA + tree reported in the delivery output); `git diff --check <base>..HEAD`
  clean; worktree clean.
- No review, merge or push (out of scope for this author role).
