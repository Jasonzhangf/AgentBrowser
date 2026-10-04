# Android local shell safe area r1 run notes

## 2026-10-04T05:15:00Z / intake / READY

- Goal: repair only the two local-shell `.browser-chrome` safe-top rules in
  `packages/ui-plugins/probe.css` so the installed Android no-config toolbar
  consumes the native typed `--safe-top` value as well as the platform
  environment inset, using non-additive `max()`.
- Input: branch `codex/m1-ui-local-safe-area-r1-20261004`, frozen source
  `1260172a5aace4cfdb1b1ad1d1e2d4f76b12adbc` / tree
  `ab42275c35fe5a6718ebc5dae3548f6c52c3ae6b`, which contains accepted native
  fix `71aa70cb94e2f1f6d797bfd60bc776a92f7080b7`.
- Read prior RED: installed APK
  `ab56723fd316b9c171b6b4f4a66dbed5f4ddacb54f9235724749b078a9f04740`,
  screenshot `02-green-portrait.png` SHA-256
  `4db601148063beb6ce742f6b9302d75ad193e56ad334719ced73cc5b90e0f242`;
  native typed `safeTop=24px`, UI CSS var `24px`, address `y=5px`.
- Live preflight: `emulator-5554` only; pre-existing forward
  `tcp:9229 localabstract:webview_devtools_remote_9588` preserved; current
  package is installed data-preserving with `versionCode=1`,
  `versionName=0.1.0-probe`, signature `10bb648d`, firstInstallTime
  `2026-10-03 15:58:37`.
- Allowed product write: `packages/ui-plugins/probe.css`. All other product
  source, MainActivity, native tests/JNI, browser consumer/domain/connection,
  Host/protocol/service/build scripts and package files remain read-only.
- Next: capture exact current installed RED screenshot/XML/APK hash and typed
  geometry, then apply the two-rule CSS change.

## 2026-10-04T05:22:00Z / RED captured and CSS patched / SOURCE_READY

- Captured installed RED with same candidate APK
  `ab56723fd316b9c171b6b4f4a66dbed5f4ddacb54f9235724749b078a9f04740`.
  Evidence screenshot hash `e5c7c611541c220b1d801dd36eaf97a051c68309d2b16eb06f1751c4272e8009`,
  XML hash `cbd1ff184b5f532f1a29add458c2a0d3f7d46186427343f5cd748ff63dac55ad`.
- Current installed RED UI XML: address `[42,10][627,126]`, status bar
  `[0,0][1080,63]`, so the address top is below 10px but the toolbar starts at 0
  and the field still overlaps the status area. Native stage actual
  `[0,210][1079,2336]`.
- CSS patch: both local-shell rules now use
  `max(6px|4px, env(safe-area-inset-top), var(--safe-top, 0px))`. No other
  product path changed; diff is 2 insertions / 2 deletions.
- `npm ci` exit 0; `npm run typecheck` exit 0; `npm test` 28 pass/0 fail; `npm
  run build:ui` exit 0. Logs under `raw/`.
- Next: run Gradle `--rerun-tasks testDebugUnitTest assembleDebug
  assembleDebugAndroidTest` with frozen target/Java/Android/OBSCURA env, then
  install the new APK and replay the same no-config toolbar, drawer/Back,
  rotation, stability and instrument tests.

## 2026-10-04T05:25:00Z / exact rebuild / BUILD_READY

- Exact Gradle command with granted `CARGO_TARGET_DIR`, `CARGO_INCREMENTAL=0`,
  `BUILD_JOBS=2`, JDK 17, runtime Android SDK and frozen
  `OBSCURA_PROTOCOL_ROOT` exited 0. Log:
  `raw/gradle-layout-rebuild.log`.
- Product APK SHA-256
  `941df5ccbc8680e0161034f938a6e3f10ab1ec642840d7ecc62241e3a290d597`;
  Android test APK unchanged from prior candidate at
  `4589d642a31746322593982b86ffc7298d0540dedb0b658ef9289fdadac5a192`.
- The rebuilt APK's `assets/ui/app.css` contains both patched local-shell
  rules with `var(--safe-top, 0px)` in non-additive `max()`, confirmed by
  unzip, so source-to-artifact bundling is proven.
- Next: data-preserving `adb -s emulator-5554 install -r` the new product APK,
  verify installed base hash/version/signature/firstInstallTime, then replay
  no-config portrait toolbar and observe measured bounds.

## 2026-10-04T05:39:30-07:00 / installed GREEN replay / AUTHOR_VERIFIED

- Data-preserving `adb -s emulator-5554 install -r` installed the GREEN APK;
  installed base SHA-256 `941df5ccbc8680e0161034f938a6e3f10ab1ec642840d7ecc62241e3a290d597`
  byte-matches the candidate. Version `1` / `0.1.0-probe`, signature
  `10bb648d`, firstInstallTime `2026-10-03 15:58:37` preserved.
- Portrait measured bounds: status `[0,0][1080,63]`, toolbar
  `[0,0][1080,317]`, address `[42,63][627,178]`, menu
  `[947,63][1065,181]`. Address and menu top equal the measured status-bar
  bottom, so the prior `y=10` overlap is gone.
- Drawer was opened from the visible menu bounds and measured
  `[84,63][1080,2338]`; system Back removed it and kept
  `com.agentbrowser.probe/.MainActivity` resumed.
- Landscape measured status `[0,0][2400,63]`, toolbar `[0,0][2400,199]`,
  address `[420,63][1942,178]`, menu `[2262,63][2380,181]`. Rotation restore
  produced the same portrait address bounds and identical XML hash.
- Installed `LayoutDeviceTest` reported `OK (2 tests)`, pipeline exit 0;
  includes the no-recurring-layout regression. The logcat contains the initial
  second-pass messages from instrumentation launch, not a recurring stream.
- Bounded parser/assertions:
  `scripts/assert-local-safe-area.sh` exit 0, log
  `raw/80-local-safe-area-assertions.log`; raw screenshot/XML/dumpsys evidence
  under `screenshots/` and `raw/`.
- Remote uiautomator temporary XML files were removed from `/sdcard`; app data,
  installed app, pre-existing `tcp:9229` forward, source, APK, raw evidence and
  target were preserved. Rotation settings were restored to original
  `accelerometer_rotation=0`, `user_rotation=0`.
- Receipt: `receipt/candidate.md`; geometry:
  `receipt/geometry-green.json`.
- Next: stage only `packages/ui-plugins/probe.css` and this task's evidence
  directory, run normal-hook commit, then report
  `INSTALLED_LOCAL_SHELL_AUTHOR_VERIFIED` / `PAIRED_REMOTE_E2E_WAIT`.
