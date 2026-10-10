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

## IME gates

A focused rerun tapped the actual remote field center at approximately
`(260,525)` after the drawer was closed. Raw `dumpsys input_method` evidence:

- `mInputShown=false`
- `mServedInputConnection=null`

The focused screenshot is `ime-focused-final.png`. It shows no system keyboard.

**Real IME focus: FAIL.** The installed ordinary RemoteIme path did not
establish a served system input connection in this run.

**Chinese composition: UNKNOWN, not PASS.** The device has only Gboard and
Google Voice Typing. Gboard is the enabled IME, but its subtype dump has no
`mSubtypeLocale=zh*` or `mSubtypeLanguageTag=zh*`; no Chinese/Pinyin IME package
is installed. Evidence: `ime-list.txt`, `default-ime.txt`,
`input-method.txt`, and `ime-focused-final.txt`.

The earlier `ime-raised.png` and `ime-committed.png` are identical and contain
no keyboard. They remain partial evidence only and are not used as a pass.

## Limitation and next action

The next executable action is to provision or enable a Chinese-capable system
IME (Gboard zh-CN/Pinyin language pack or an approved Chinese IME) on the
acceptance emulator, then rerun the focused ordinary flow and diagnose why the
remote field tap did not create a served `RemoteImeView` connection. Until then,
the user slice remains incomplete.
