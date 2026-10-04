# Android required-configuration projection repair r1

Owner: standalone GCM repair worker, not controller or reviewer.
Layer: local Android producer-to-installed-UI author verification.

## 2026-10-04T17:41:46Z / intake and lease binding | READY

- Conclusion: The owned worktree is clean at the accepted composition
  `88234e5575a774156271d1598b89b9f80c307259`, tree
  `5519d172d266cd1ea60a2454606f0292d9fa40df`. The installed client is the
  preserved failing candidate on `emulator-5554`.
- Evidence: `git status --short --branch`; `git rev-parse HEAD HEAD^{tree}`;
  `adb devices -l`; `adb shell dumpsys package com.agentbrowser.probe`.
- Installed identity: version `0.1.0-probe` code `1`,
  `firstInstallTime=2026-10-03 15:58:37`; package path and base APK bytes are
  the preserved composition install. Existing forwards are only `tcp:9229`
  and `tcp:9331`; both remain protected.
- Inputs: accepted native/UI composition; r9 design contract; preserved
  composition failure sample under
  `/Volumes/Intel/playground/agentbrowser/m1-android-ui-setup-composition-r1-20261004/docs/runs/m1-android-ui-setup-composition-r1-20261004/`.
- First deviation: `ConnectionSetupSession.projection()` emits
  `active_revision:null` when no actual revision exists. The admitted UI
  consumer accepts only `undefined` or a non-negative integer.
- Scope: write only `ConnectionSetupSession.java` and this run directory.
  Optional one installed Activity/user-control regression test is allowed.
- Next: make the minimal producer projection repair, rebuild, install, and
  replay the real installed UI path.

## 2026-10-04T17:49:30Z / projection repair | DONE

- Conclusion: The producer now emits `state`, `error`, `label`, and
  `repair_import_allowed` exactly as before. It adds `active_revision` only
  when the actual revision is non-negative. No state machine, storage,
  reconciliation, or error behavior changed.
- Evidence: `apps/android/app/src/main/java/com/agentbrowser/probe/ConnectionSetupSession.java`
  diff; `git diff --check` exit 0.
- Next: build the candidate and install it with data preservation.

## 2026-10-04T17:51:00Z / first build attempt | BLOCKED_ENV

- Conclusion: The first Gradle attempt reached `:app:bundleUi` and failed
  because this fresh worktree had no `node_modules` (`ERR_MODULE_NOT_FOUND:
  esbuild`). The failure was environmental, before unit/package completion.
- Evidence: `raw/gradle-build.log`; no APK output was produced by that attempt.
- Recovery: run the allowed locked `npm ci`, then rerun the exact Gradle task.

## 2026-10-04T17:57:03Z / candidate build | DONE

- Conclusion: The exact Android task now passes on the repaired source.
- Evidence: `npm ci` exit 0 (`raw/npm-ci.log`); exact Gradle command exit 0
  (`raw/gradle-build.log`, `BUILD SUCCESSFUL in 4s`). Unit XML totals 74 tests,
  0 failures, 0 errors, 0 skipped.
- Artifacts: `app-debug.apk` SHA-256
  `c1056162106e085391626e271ad7d51c79dd2baa1fbac3a71d01663c2f2d9061`;
  `app-debug-androidTest.apk` SHA-256
  `d8210b826e08ee0014bad17e10992e664a1964f965692d43b2ef52ad43e46d1f`.
- Input: repaired source on composition `88234e55`.
- Next: data-preserving `adb -s emulator-5554 install -r` both APKs.

## 2026-10-04T17:58:26Z / install | DONE

- Conclusion: Both repaired APKs installed with data preservation. Installed
  product bytes equal the rebuilt APK; version and `firstInstallTime` are
  unchanged; app files remain empty; protected forwards remain `9229`/`9331`.
- Evidence: `raw/adb-install-product.log`, `raw/adb-install-test.log`,
  `raw/installed-product-hash-after.txt`, `raw/installed-test-hash-after.txt`,
  `raw/installed-package-after.txt`, `raw/forwards-after-install.txt`,
  `raw/app-files-after-install.txt`.
- Next: replay launcher/visible toolbar/drawer through ordinary user controls,
  then the DocumentsUI invalid-document error/recovery path.

## 2026-10-04T18:00:00Z / installed replacement mount | DONE

- Conclusion: The repaired installed launcher now mounts the real browser
  chrome and drawer. Native status omits `active_revision` in `required` state
  exactly as the admitted UI consumer requires.
- Evidence: native status raw
  `raw/green-dom-status.json` shows `"connectionSetup":{"state":"required",
  "error":null,"label":null,"repair_import_allowed":false}` with no
  `active_revision`. DOM shows `toolbar:true`, `drawer:true`,
  `slotChildren:1`. UI hierarchy `raw/green-launcher-chrome.xml` exposes
  toolbar/address/back/forward/reload/drawer-toggle nodes.
- Input: rebuilt candidate `c1056162...`.
- Next: drawer open/close/Back, rotation, picker/Back/cancel, invalid
  document error/recovery.

## 2026-10-04T18:09:00Z / installed device consumer | DONE

- Conclusion: Existing installed public consumer classes still pass after the
  repair on the new APK.
- Evidence: `raw/instrument-connection-setup.log`
  (`OK (11 tests)`, `INSTRUMENTATION_CODE: -1`),
  `raw/instrument-layout.log` (`OK (2 tests)`, `INSTRUMENTATION_CODE: -1`).
- Next: continue real launcher/drawer/picker/cancel/invalid flow.

## 2026-10-04T18:14:00Z / installed chrome and drawer | DONE

- Conclusion: The repaired installed launcher exposes the real toolbar,
  address, navigation controls, tabs control, and drawer. The drawer opens and
  closes with the ordinary hamburger/close/Back controls. Landscape rotation
  preserves the chrome and returns to portrait with the same state.
- Evidence: `raw/green-launcher-chrome.xml`, `raw/green-drawer-open.xml`,
  `raw/green-drawer-back-closed.xml`, `raw/green-landscape.xml`,
  `raw/green-portrait-restored.xml`; screenshots `02-green-launcher.png`
  through `06-portrait-restored.png`.
- Input: installed repaired product APK `c1056162...`.
- Next: exercise the ordinary Import action with the system picker.

## 2026-10-04T18:27:00Z / system picker and cancel | DONE

- Conclusion: The ordinary drawer Import action opens the real system
  `DocumentsUI` picker (`com.google.android.documentsui/...PickActivity`).
  Pressing Back cancels the picker, returns to the open drawer, and leaves the
  app in `required` with no error and no `files/pairing` directory.
- Evidence: `raw/green-picker-open.xml`, `raw/green-picker-activity.txt`,
  `raw/green-picker-cancel.xml`, `raw/green-picker-cancel-activity.txt`,
  `raw/green-picker-cancel-pairing-check.txt`; screenshot
  `evidence/ui/07-documentsui-picker.png`.
- Next: select the own non-secret invalid document through the same picker.

## 2026-10-04T18:33:00Z / invalid document error and recovery | DONE

- Conclusion: Selecting the own invalid document and entering a non-secret
  dummy transfer code through the native secure dialog produces the
  actionable `BUNDLE_JSON_INVALID` error in the installed UI. The drawer stays
  open with `配置连接` and `导入` available, `repair_import_allowed` stays
  false, no pairing bytes are written, and no connection is started. The
  error and recovery surface persist across landscape/portrait rotation.
- Evidence: `raw/green-invalid-import-dialog-keyboard.xml`,
  `raw/green-invalid-import-after3.json`, `raw/green-invalid-import-error.xml`,
  `raw/green-invalid-import-error-private-files.txt`,
  `raw/green-invalid-import-error-activity.txt`,
  `raw/green-invalid-import-error-recovery.json`,
  `raw/green-invalid-import-landscape.json`,
  `raw/green-invalid-import-portrait-restored.json`; screenshot
  `evidence/ui/08-invalid-import-error.png`,
  `evidence/ui/09-invalid-import-landscape.png`.
- First deviation note: the first two picker attempts were cancelled by
  unintended drawer-state taps or by submitting the secure dialog before the
  injected code was committed. Neither attempt wrote pairing bytes. The final
  state-aware flow above is the accepted run.
- Next: final source/installed identity, cleanup of own resources, and commit.

## 2026-10-04T18:34:54Z / commit and resource ledger | DONE

- Conclusion: The scoped repair is committed with normal hooks. The normal
  hook ran typecheck, 34 package tests, and the UI build with exit 0.
- Commit: `f0824d04ad74d24899376827b4d5d6a48f1775c2`, tree
  `ac209d9ac53efdf8b9a9f58eee1ce5cf508f60a6`. Branch
  `codex/m1-android-active-revision-r1-20261004`.
- Candidate inputs: source
  `ConnectionSetupSession.java` SHA-256
  `a4c6ee3fb91b6e7d4cb58ef5b28f61062a96282959544e94032fda63a23be337`;
  product APK SHA-256
  `c1056162106e085391626e271ad7d51c79dd2baa1fbac3a71d01663c2f2d9061`;
  androidTest APK SHA-256
  `d8210b826e08ee0014bad17e10992e664a1964f965692d43b2ef52ad43e46d1f`.
- Installed identity: `com.agentbrowser.probe` version `0.1.0-probe` code `1`,
  `firstInstallTime=2026-10-03 15:58:37`, installed `base.apk` SHA-256 equals
  the rebuilt product APK.
- Build/test receipt: exact Gradle command exit 0 (`BUILD SUCCESSFUL`);
  `testDebugUnitTest` totals 74 tests, 0 failures, 0 errors, 0 skipped;
  installed `ConnectionSetupDeviceTest` `OK (11 tests)` and
  `LayoutDeviceTest` `OK (2 tests)`.
- Resource ledger: own `tcp:9332` forward removed; own device file
  `/sdcard/Download/invalid-connection-active-test.txt` removed. Protected
  `tcp:9229` and `tcp:9331` remain untouched. Raw evidence, screenshots,
  fixtures, rebuilt APKs, and the run notes are preserved for controller
  review. No other worker state, app data, Trime, rotation, or forward was
  modified.
- Remaining product path: this layer verifies producer-to-installed-UI
  behavior only. Paired Host E2E, remote video, remote IME, and BB01 remain
  open and are not claimed by this worker.
