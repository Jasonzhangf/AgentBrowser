# Android first connection setup r1 run notes

## 2026-10-04 / 01 intake and owner boundary | READY

- Conclusion: This task owns the Android setup slice only.
- Evidence: admitted design r9
  `/Volumes/Intel/playground/agentbrowser/m1-first-connection-admission-r9-20261004`
  at `9f1522062d169d6feb3d891102463ca74f4902a9` / tree
  `b976bf4ab1d5a8fe8045c8e261469f04665aeee6`; worktree HEAD
  `b6bfdda050c2eadb591f5a35b21f9a353bf7bb88` / tree
  `788a6e06e25f85472f16fde961686cde0a7ed677`, clean.
- Environment: `HOME=/Users/fanzhang`; target lease grants
  `/Volumes/Intel/playground/agentbrowser/m1-android-native-input-r1-20261003/target`;
  device lease grants `emulator-5554` with data-preserving upgrade.
- Next: write the RED public importer test and implement the native owner.

## 2026-10-04 / 02 observed missing edge | RED_CONFIRMED

- Conclusion: The installed UI cannot reach native setup/import today.
- Evidence: `MainActivity.Bridge` has no `open_connection_setup`,
  `import_connection` or `repair_connection_import` dispatch. `NetworkSession`
  derives `networkConfigured` from `files/pairing` directory existence only and
  reads `endpoint.txt`, `ca.der`, `client.der`, `key.der` independently.
- Input: `b6bfdda050c2eadb591f5a35b21f9a353bf7bb88`.
- Next: add RED Android tests for the public owner and validation boundary.

## 2026-10-04 / 03 unit boundary and owner implementation | UNIT_PASS

- Conclusion: Strict bundle parsing, real PKCS#12 validation, immutable active
  binding, `AtomicFile` admission, projection and native setup dispatch are
  implemented. `NetworkSession` now takes one immutable `ActiveConfiguration`
  per NEW connection and no longer derives configuration from `files/pairing`.
- Evidence: `bash scripts/android.sh testDebugUnitTest assembleDebug
  assembleDebugAndroidTest` exit `0`; `ConnectionBundleTest` 11/11 passed with
  real RSA PKCS#12 material labelled MODULE_ONLY.
- Input: `b6bfdda050c2eadb591f5a35b21f9a353bf7bb88`; local edits only; fixture
  `valid.abconnection` SHA-256 recorded in `valid.p12.sha256` companion.
- Next: install source/test APKs on `emulator-5554` and run the device consumer
  class plus `LayoutDeviceTest`.

## 2026-10-04 / 04 device owner consumer and layout | DEVICE_PASS

- Conclusion: The public setup owner handles real JCA PKCS#12 imports in an
  isolated app-private root. It commits an immutable generation and active
  reference, denies wrong code/unknown fields/digest/SAN, preserves prior bytes,
  recovers actual active after process/new-owner construction, and permits only
  explicit repair from a deterministic invalid snapshot.
- Evidence:
  `am instrument -w -r -e class com.agentbrowser.probe.ConnectionSetupDeviceTest
  com.agentbrowser.probe.test/android.test.InstrumentationTestRunner` exit `0`,
  `OK (11 tests)`;
  `... -e class com.agentbrowser.probe.LayoutDeviceTest ...` exit `0`,
  `OK (2 tests)`.
- Artifacts: `app-debug.apk` SHA-256
  `147f3ba35f0e43b72b4d64f1c4628f016585dbf70fbba668b21151dd574a850e`;
  `app-debug-androidTest.apk` SHA-256
  `45d53002d54c3df7e32811c3ee8deaef850c67f7bcd85db1ae1200b37b3aab43`;
  data-preserving `adb install -r` both exit `0`, firstInstallTime unchanged
  `2026-10-03 15:58:37`.
- Input/environment: `emulator-5554`, shared target
  `/Volumes/Intel/playground/agentbrowser/m1-android-native-input-r1-20261003/target`,
  fixture imports labelled `MODULE_ONLY`.
- Next: installed launcher/drawer UI picker, Back/cancel and invalid-content
  evidence with real taps and observed bounds.

## 2026-10-04 / 05 secure dialog owner fix | FIXED

- Conclusion: The first installed picker run exposed a real UI-thread block.
  `showCodeDialog()` waited on its own latch from the main thread, so the
  transfer-code dialog could not render and Android raised an input-dispatch
  ANR after the picker returned. `ConnectionSetupSession.requestCode()` now
  creates the dialog on the main thread and waits on the worker thread.
- Evidence: pre-fix log `ANR in com.agentbrowser.probe` after the picker
  returned; post-fix installed flow shows `Import connection` / `Transfer code`
  and no ANR.
- Input: local edits on top of `b6bfdda050c2eadb591f5a35b21f9a353bf7bb88`.
- Next: rebuild, reinstall and rerun the device consumer plus the installed
  picker/error/cancel flow.

## 2026-10-04 / 06 installed UI acceptance | DEVICE_UI_PASS

- Conclusion: The installed launcher reaches the native setup owner through the
  real system document picker. Back and dialog Cancel preserve the prior state
  and write no pairing bytes. A staged non-secret invalid document reaches the
  secure dialog and produces the bounded typed error
  `BUNDLE_DEVICE_ID_INVALID`; after Cancel and a process restart the owner
  returns to `required` with no active reference.
- Evidence (all in this task run directory):
  - `evidence/ui/04-system-picker.png` SHA-256
    `056062ffbca75d47261bcf793035b237b3b9e5961210f4946f5366f7824a5220`:
    real DocumentsUI picker after a measured tap on `配置连接`.
  - `evidence/ui/05-picker-cancel-back.png` SHA-256
    `6bc277145fd9dcf7df6a4d3682cbe359e7e3ef75d0bf5219bc67245171cdca54`:
    Back from the picker returns to the prior drawer state.
  - `evidence/ui/06-secure-transfer-code-dialog.png` SHA-256
    `554b9bc58dbe635a277f59164ac9c44a280355d4aef3c494559566c2211226cc`:
    `FLAG_SECURE` dialog is absent from the screenshot while its
    `Import connection` / `Transfer code` nodes are present in the measured UI
    hierarchy.
  - `evidence/ui/07-invalid-import-error.png` SHA-256
    `46347d0137822dc9c6e942063b63b14fbd9efac25487e8447dadea9f6637fd9d`:
    bounded `BUNDLE_DEVICE_ID_INVALID` error after Import.
  - `evidence/ui/08-dialog-cancel-recovered.png` SHA-256
    `1a8223c15aa2228de225511e1037bb66df7a30fb03eb31d5010cf89580553f49`:
    Cancel then process restart returns to `required`; the drawer shows the
    ordinary setup buttons and no error.
- Side effects: `run-as com.agentbrowser.probe find files` stayed at the empty
  `files` root for the whole flow. No `files/pairing` directory or active
  reference was created. `logcat` contains no literal transfer code.
- Input/environment: `emulator-5554`, installed `app-debug.apk`; the invalid
  document was staged under `/sdcard/Download` and removed after the run.
- Next: record final artifact hashes and commit the allowlisted source and
  evidence files.

## 2026-10-04 / 07 final gates and artifacts | AUTHOR_GATES_PASS

- Conclusion: The final source passes the UI gates, the Android unit suite, the
  rebuilt APK build and both device consumer classes.
- Commands and exits:
  - `npm ci` exit `0`.
  - `npm run typecheck` exit `0`.
  - `npm test` exit `0`, `28/28` pass.
  - `npm run build:ui` exit `0`.
  - `JAVA_HOME=... ANDROID_HOME=... CARGO_TARGET_DIR=... CARGO_INCREMENTAL=0
    BUILD_JOBS=2 OBSCURA_PROTOCOL_ROOT=... bash scripts/android.sh --rerun-tasks
    testDebugUnitTest assembleDebug assembleDebugAndroidTest` exit `0`, `74/74`
    unit tests pass.
  - `am instrument -w -r -e class com.agentbrowser.probe.ConnectionSetupDeviceTest
    com.agentbrowser.probe.test/android.test.InstrumentationTestRunner` exit `0`,
    `OK (11 tests)`.
  - `am instrument -w -r -e class com.agentbrowser.probe.LayoutDeviceTest
    com.agentbrowser.probe.test/android.test.InstrumentationTestRunner` exit `0`,
    `OK (2 tests)`.
- Artifacts: `app-debug.apk` SHA-256
  `a927d9a55da956b16c8a1b7c4e6d023bca523799fe41cfe4ec46da7d37f913e4`;
  `app-debug-androidTest.apk` SHA-256
  `d8210b826e08ee0014bad17e10992e664a1964f965692d43b2ef52ad43e46d1f`.
  `versionName=0.1.0-probe`, `versionCode=1`, signed by Android Debug
  certificate SHA-256
  `ecd63a2c2070970735cc079b0bb090427ca0b59200da0ebc07c80b50a1dfffda`.
  Data-preserving `adb -s emulator-5554 install -r` both APKs exit `0`;
  `firstInstallTime` stayed `2026-10-03 15:58:37`.
- MODULE_ONLY fixture: `valid.abconnection` SHA-256
  `53094c5b0168ee5da2e26908f615485ff5bb9d39c2c97fe20bb4725674c7b81d`;
  `valid.p12` SHA-256
  `be1ddcda4207327314d2ce11e54a8d75b17e7253dd9069c7da769f1df6cd6500`.
- Input: local source edits; Android target lease
  `/Volumes/Intel/playground/agentbrowser/m1-android-native-input-r1-20261003/target`.
- Next: commit the allowlisted files with normal hooks and report the handoff.

## 2026-10-04 / 08 status and handoff | ANDROID_SETUP_LOCAL_AUTHOR_VERIFIED

- Conclusion: The Android setup slice is implemented and verified at the
  `ANDROID_SETUP_LOCAL_AUTHOR_VERIFIED` layer. The real producer export, Host
  service and paired UI connection are not available in this task, so the
  first-connection feature remains `PRODUCER_PAIRED_E2E_WAIT`.
- Integration dependency: the native projection can emit `unresolved` and
  `unavailable`. The installed UI/domain still parses only `required`,
  `configured`, `opening`, `importing` and `error`. The old UI therefore cannot
  present those recovery states yet. This task did not change UI source and did
  not emit a false compatible state. UI recovery owns that presentation.
- Source handoff: `MainActivity.dispatchConnectionSetup` accepts
  `open_connection_setup`, `import_connection`, `repair_connection_import` and
  `retry_connection_setup`. `NetworkSession` consumes one immutable
  `ActiveConfiguration` and keeps its lease; the legacy `files/pairing`
  directory/four-file reader is removed.
- Cleanup: removed the staged `/sdcard/Download` invalid document, device UI
  dump files, host temporary fixture/images, `/tmp/ab-connection-fixture.*`,
  `/tmp/ab-p12test.*` and own forward `tcp:9330`. Preserved preexisting forward
  `tcp:9229`, app data, Trime and rotation settings. Retained source, APKs,
  raw evidence and the shared native target for intake.
