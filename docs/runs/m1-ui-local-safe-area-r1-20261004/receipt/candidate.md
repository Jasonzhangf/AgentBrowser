# Android installed local chrome safe area r1 receipt

Status: `INSTALLED_LOCAL_SHELL_AUTHOR_VERIFIED` / `PAIRED_REMOTE_E2E_WAIT`.

This is an author result only. It does not claim independent review, merge,
push, OTA, M1 PASS, or paired remote video/touch/caret/IME.

## Candidate identity

- Worktree:
  `/Volumes/Intel/playground/agentbrowser/m1-ui-local-safe-area-r1-20261004`
- Branch: `codex/m1-ui-local-safe-area-r1-20261004`
- Input commit: `1260172a5aace4cfdb1b1ad1d1e2d4f76b12adbc`
- Input tree: `ab42275c35fe5a6718ebc5dae3548f6c52c3ae6b`
- Base `origin/main`: `bc9d67ad037be2c7119bee692f9ad362417ffd05`
- Only product change: `packages/ui-plugins/probe.css`
- Product CSS blob before commit: `bcddc0f864a431fcd2fd017e42898b1d3afa9ced`

## Root fix

The native layer already published the typed safe-area projection. The
installed WebView reports `env(safe-area-inset-top)` as empty/zero while
`var(--safe-top)` was `24px`. The two local-shell `.browser-chrome` rules now
consume both sources with a non-additive maximum:

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

No fixed status-bar offset, DOM/value injection, fake frame, second input,
viewport reload, or native coordinate path was added. Remote native-video and
WebView selectors were not changed.

## Source and artifact binding

- Product APK SHA-256:
  `941df5ccbc8680e0161034f938a6e3f10ab1ec642840d7ecc62241e3a290d597`
- Android test APK SHA-256:
  `4589d642a31746322593982b86ffc7298d0540dedb0b658ef9289fdadac5a192`
- Bundled `assets/ui/app.css` SHA-256:
  `2365d201cfd00a05e26813e5a89672332d7d7297f653ba2a33635741166ea6b1`
- Installed base APK byte comparison: `match`; evidence
  `raw/64-installed-green-base-binding.txt`
- Installed package: `versionCode=1`, `versionName=0.1.0-probe`, signature
  prefix `10bb648d`, firstInstallTime `2026-10-03 15:58:37`, lastUpdateTime
  `2026-10-04 05:29:37`

The bundled CSS contains both patched rules with
`var(--safe-top, 0px)` inside `max()`.

## Commands and results

- `npm ci`: exit 0, `raw/npm-ci.log`
- `npm run typecheck`: exit 0, `raw/typecheck.log`
- `npm test`: 28 pass / 0 fail, `raw/npm-test.log`
- `npm run build:ui`: exit 0, `raw/build-ui.log`
- `bash scripts/android.sh --rerun-tasks testDebugUnitTest assembleDebug
  assembleDebugAndroidTest`: exit 0, `raw/gradle-layout-rebuild.log`
- `adb -s emulator-5554 install -r .../app-debug.apk`: `Success`,
  `raw/adb-install-green.log`
- `adb -s emulator-5554 install -r .../app-debug-androidTest.apk`: `Success`,
  `raw/60-adb-install-green-test-apk.log`
- `adb -s emulator-5554 shell am instrument -w -r -e class
  com.agentbrowser.probe.LayoutDeviceTest
  com.agentbrowser.probe.test/android.test.InstrumentationTestRunner`:
  `OK (2 tests)`, pipeline exit 0, `raw/62-installed-green-instrument.log`
- `bash docs/runs/m1-ui-local-safe-area-r1-20261004/scripts/assert-local-safe-area.sh`:
  exit 0, `raw/80-local-safe-area-assertions.log`

## Installed black-box result

The replay used the installed launcher entry and visible controls. The
assertion script reads `uiautomator` XML and `dumpsys window displays`; it does
not hardcode `63px`.

Portrait:

```text
statusBars=[0,0][1080,63]
navigationBars=[0,2337][1080,2400]
toolbar=[0,0][1080,317]
address=[42,63][627,178]
menu=[947,63][1065,181]
```

The address and menu top are both `63`, equal to the measured status-bar
bottom. The drawer was opened by tapping the observed menu bounds
`[947,63][1065,181]`; the open drawer measured `[84,63][1080,2338]`. System
Back removed the drawer and kept `com.agentbrowser.probe/.MainActivity`
resumed.

Landscape:

```text
statusBars=[0,0][2400,63]
navigationBars=[0,1017][2400,1080]
toolbar=[0,0][2400,199]
address=[420,63][1942,178]
menu=[2262,63][2380,181]
```

After rotation back to portrait, the address bounds were again
`[42,63][627,178]`; the restored XML SHA-256 equals the post-Back portrait XML
SHA-256, so the inset is not cumulative.

The installed `testRepeatedIdenticalGeometryDoesNotScheduleAnotherLayout`
passed. The post-instrument log contains the initial second-layout-pass
messages emitted while instrumentation launched the activity, but not a
recurring request-layout stream. This is the evidence for the native
stability regression; it does not claim zero framework layout messages.

## RED to GREEN

The original installed RED artifact was
`ab56723fd316b9c171b6b4f4a66dbed5f4ddacb54f9235724749b078a9f04740`; its
address top was `10` while the status bar bottom was `63`. The GREEN artifact
is the rebuilt `941df5cc...` APK with the same native projection and the two
CSS rules changed. Evidence:

- RED screenshot:
  `screenshots/01-installed-red-portrait.png`
- RED XML: `raw/01-installed-red-portrait.xml`
- GREEN portrait screenshot:
  `screenshots/10-installed-green-portrait.png`
- GREEN drawer screenshot:
  `screenshots/20-installed-green-drawer-open.png`
- GREEN Back screenshot:
  `screenshots/30-installed-green-back-closed.png`
- GREEN landscape screenshot:
  `screenshots/40-installed-green-landscape.png`
- GREEN restored screenshot:
  `screenshots/50-installed-green-restored-portrait.png`

## Scope and remaining dependency

MainActivity, native tests/JNI, domain/UI consumer, browser consumer,
connection/Host/protocol/service/build scripts, SDK, hooks, AGENTS and package
files were read-only. The pre-existing `tcp:9229` forward was preserved; app
data was not cleared; the emulator was not wiped. The normal app remains
installed for successor intake.

Paired remote video, touch, caret, remote IME, and BB01/03/05/06/08 remain
`REMOTE_E2E_WAIT` because no Host/auth/network/paired-device grant was
provided. No merge, push, OTA, or independent review was performed.
