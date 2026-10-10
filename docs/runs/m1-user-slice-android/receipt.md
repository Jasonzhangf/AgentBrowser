# Android user-slice artifact receipt

Status: **INCOMPLETE**. This receipt records partial evidence only. It does not
claim `USER_SLICE_ACCEPTED` or full M1.

## Artifact and upgrade

- Application ID: `com.agentbrowser.probe`
- APK: `/Volumes/Intel/playground/agentbrowser/m1-android-navigation-fix-r1-20261009/apps/android/app/build/outputs/apk/debug/app-debug.apk`
- APK SHA-256: `8eceb55e404791484451499aac38cce70a2d6fcdd2aa2d1ed72f872a7d63cb45`
- Badging: versionCode `2`, versionName `0.2.0-probe`
- Signing: debug signature `10bb648d` (non-production; unchanged from the installed build)
- Before: versionCode `1`, versionName `0.1.0-probe`, signature `10bb648d`
- Upgrade command: `adb -s emulator-5554 install -r <app-debug.apk>`
- Upgrade result: exit code `0`; no uninstall was performed
- After: versionCode `2`, versionName `0.2.0-probe`, signature `10bb648d`
- Raw package evidence: `evidence/user-slice/android/upgrade-v2/package-before.txt`, `package-after.txt`, `install-r.txt`, `install-exit.txt`

**Upgrade gate: PASS.** The version and signing identity prove an in-place
upgrade from v1 to v2.

## Ordinary installed-app path

The installed app was launched through `MainActivity` and paired through the
ordinary UI path:

`launcher -> drawer -> 配置连接 -> 导入 -> DocumentsUI -> pairing.abconnection -> 连接`

The live r8 fixture used loopback endpoint `wss://127.0.0.1:52691`, session
`27bd14a8-bf38-4cec-ac5f-0c84e5c3ddca`, and device ID
`f7c200e5-e8d8-421e-b558-a7a6eaf25c48`. The bundle was selected through the
ordinary DocumentsUI picker. No instrumentation entrypoint was used.

Evidence: `evidence/user-slice/android/recon/` and
`evidence/user-slice/android/user-slice-android-20261010T1402/`.

- Ordinary pairing: **PASS**
- Connected remote video visible: **PASS** (`state-09-video.png`)
- Real remote click attempt: **PASS** (recorded in `clicked.png`; the page was connected and the red Host button was present)
- Real drag-to-scroll attempt: **PASS** (`scrolled.png`)
- Observe/takeover ordinary UI path: **PARTIAL evidence**; drawer controls were reached, but no independent Host assertion was recorded for the takeover state.

The full-flow recording is retained at
`evidence/user-slice/android/user-slice-android-20261010T1402/full-flow.mp4`.
SHA-256: `6c5c0e178798d1d5e95fa916127585b0f991dffca5ced409f38cc8a0c4007121`.

## Later ordinary-path gates on the same installed artifact

The same installed v2 APK was driven again through the ordinary UI with a
UiDevice instrumentation driver (accessibility-first, real DocumentsUI file,
real protected `EditText`, real `IMPORT`). No app-side test hook was added and
the shipped APK bytes did not change.

- Protected-bundle admission (Service-lifecycle Host): **PASS**
  - bundle SHA-256 `a5e7970747f1a119110e3b74c989635f77cbd04147163021159a322166a92cb5`, device SHA identical
  - `am instrument -e transferCode ...` result `OK (1 test)`, exit `0`
  - evidence: `evidence/user-slice/first-connect/task7-instrumented-retry-20261010T1650/`
- Protected-bundle admission over the Tailscale endpoint: **PASS**
  - bundle SHA-256 `22220a52b5a81b7d93653aa7efbda3072bed57be7baf578c26e1330c3b67fc95`, device SHA identical
  - evidence: `evidence/user-slice/tailscale/task8-android-final-20261010T1640/`
- Remote video and real click over the Tailscale endpoint (`wss://100.66.1.82:51881/`): **PASS**
  - raw: `connectionState=connected`, `renderedFrames=2`, `displayedTicket=42`, `redBeforeClick=true`, `limeAfterClick=true`
  - evidence: `evidence/user-slice/tailscale/task8-browse-20261010T1645/tailscale-browse-click.json`
- Fixture-host browse (independent Host DOM): remote video PASS (`renderedFrames=3`), real click PASS (`clicked=1`), drag-scroll PASS (`scrollTrace=[0,250,0]`)
  - evidence: `evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/`

Scope note: the Tailscale browse proves the same physical Mac reached over its
Tailscale interface. Cross-node Tailscale stays **UNKNOWN** (designated 15T
`100.104.163.65` offline).

Ordinary-flow recording (app launch, drawer, `配置连接`, `导入`, picker
interaction): `evidence/user-slice/first-connect/task7-recording-20261010T1730/ordinary-flow-sanity.mp4`,
1080x2400, 14.47 s, 48 frames, SHA-256
`8a95dac96ca0f59bd0be53a736e5f03d8d68c77c03b48337ece55b51d901a1a`. It does
not cover the protected dialog or `IMPORT`; those steps are proven by the
instrumentation runs above, not by this recording. The earlier
`ordinary-flow.mp4` in the same directory is truncated (`ffprobe: moov atom not
found`) and must not be cited as evidence.

## IME gates

A focused rerun tapped the actual remote field center at approximately
`(260,525)` after the drawer was closed. Raw `dumpsys input_method` evidence:

- `mInputShown=false`
- `mServedInputConnection=null`

The focused screenshot is `ime-focused-final.png`. It shows no system keyboard.

**Real IME focus: FAIL.** The installed ordinary RemoteIme path did not
establish a served system input connection in this run.

**Chinese composition: UNKNOWN, not PASS (environment-blocked).** The device has
only Gboard and Google Voice Typing. A fresh raw enumeration on the same
emulator confirms the gate:

- `evidence/user-slice/android/ime-20261010T1705/input-method-focus.txt`:
  `mInputShown=false`, `mServedInputConnection=null`
- `evidence/user-slice/android/ime-20261010T1705/ime-subtype-zh-count.txt`:
  `zh_subtype_count=0` across the whole `ime list -a` enumeration
- `evidence/user-slice/android/ime-20261010T1705/enabled-imes.txt`: only
  `com.google.android.inputmethod.latin` and `com.google.android.tts` are enabled;
  default is `com.google.android.inputmethod.latin`
- older raw files: `ime-list.txt`, `default-ime.txt`, `input-method.txt`,
  `ime-focused-final.txt`

The two raw `dumpsys input_method` lines above are the *IME focus* observation
(`FAIL`, recorded raw). Chinese composition itself is `UNKNOWN`: no
zh-capable subtype or package exists on this emulator, so the gate cannot be
exercised. The controlled `RemoteImeView` composition used by the instrumentation
suite is not a system-IME Chinese proof and is never reported as PASS.

The earlier `ime-raised.png` and `ime-committed.png` are identical and contain
no keyboard. They remain partial evidence only and are not used as a pass.

## Limitation and next action

The next executable action is to provision or enable a Chinese-capable system
IME (Gboard zh-CN/Pinyin language pack or an approved Chinese IME) on the
acceptance emulator, then rerun the focused ordinary flow and diagnose why the
remote field tap did not create a served `RemoteImeView` connection. Until then,
the user slice remains incomplete.
