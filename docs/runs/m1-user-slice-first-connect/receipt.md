# Task-7 first-connect receipt

Status: **INCOMPLETE / EVIDENCED PARTIAL**. Ordinary admission is proven against the Service-lifecycle Host and over the Tailscale endpoint. Browse is proven against the fixture-launched r8 Host and, for video plus a real click, over the Tailscale endpoint. Same-session lifecycle browse, system Chinese IME, and cross-node Tailscale remain unproven.

## Current consolidated gate results

| Gate | Result | Raw evidence |
| --- | --- | --- |
| Ordinary DocumentsUI picker | PASS | UiDevice instrumentation; `OK (1 test)`; `task7-instrumented-retry-20261010T1650/` |
| Protected dialog and real IMPORT | PASS | `am instrument` exit 0; configured + active-generation-change assertions |
| Service-lifecycle admission | PASS | bundle SHA `a5e7970747f1a119110e3b74c989635f77cbd04147163021159a322166a92cb5`; device SHA identical |
| Fixture-host remote video | PASS | `task7-scroll-delta-verified-20261010T1740/network-result.json`: `frameAck.renderedFrames=3`, `displayed=true`, `networkFrames=true` |
| Fixture-host real click | PASS | `task7-scroll-delta-verified-20261010T1740/network-dom.json`: Host DOM `clicked=1` |
| Fixture-host drag-scroll | PASS (document delta proven) | `task7-scroll-delta-verified-20261010T1740/network-scroll-trace.json`: Host DOM `scrollTrace=[0,250,0]`, `maxScroll=250`, `finalScrollY=0`, `restoredToZero=true` |
| Fixture-host observe/takeover | PASS | `observerTouchIgnored=true`, `takeoverPixels=true`, operation receipts succeeded |
| Tailscale ordinary admission | PASS | bundle SHA `22220a52b5a81b7d93653aa7efbda3072bed57be7baf578c26e1330c3b67fc95`; device SHA identical; `task8-android-final-20261010T1640/` |
| Tailscale ordinary connect + remote video | PASS | `task8-browse-20261010T1645/tailscale-browse-click.json`: endpoint `wss://100.66.1.82:51881/`, `connected`, `renderedFrames=2`, `displayedTicket=42` |
| Tailscale real click | PASS (Host-applied repaint) | same file: `redBeforeClick=true`, `limeAfterClick=true`; no independent Host DOM read-back exists for a service-lifecycle Host |
| System IME focus | FAIL (raw) | `evidence/user-slice/android/ime-20261010T1705/input-method-focus.txt`: `mInputShown=false`, `mServedInputConnection=null` |
| Chinese composition | UNKNOWN (environment-blocked) | `ime-subtype-zh-count.txt`: `zh_subtype_count=0`; only Gboard + Google Voice Typing installed; controlled RemoteIme composition is not a system IME proof |
| Cross-node Tailscale | UNKNOWN | designated 15T `100.104.163.65` offline |

Browse was proven against the fixture-launched r8 Host, while pairing/admission
was proven against the Service-lifecycle Host. It is not a same-session browse
claim. Automated code entry (`setText`/instrumentation argument) stood in for
human typing.

Screen recording: `evidence/user-slice/first-connect/task7-recording-20261010T1730/ordinary-flow-sanity.mp4`
(1080x2400, 14.47 s, 48 frames, SHA-256
`8a95dac96ca0f59bd0be53a736e5f03d8d68c77c03b48337ece55b51d901a1a`). It covers
app launch, the drawer, `配置连接` and the import picker interaction only. It
does **not** cover the protected dialog or `IMPORT`. The earlier
`ordinary-flow.mp4` in the same directory is truncated (`ffprobe: moov atom not
found`) and must not be cited as evidence.

## Instrumented ordinary admission

The installed task-4 APK was admitted through the ordinary Android UI using the
UiDevice instrumentation driver. The driver used accessibility-first lookup,
real DocumentsUI file selection, the protected EditText, and real `IMPORT`.
It passed `transferCode` only as a transient instrumentation argument; the code
was never written to logs, receipts, or evidence. The shipped APK was unchanged
(SHA-256 `8eceb55e404791484451499aac38cce70a2d6fcdd2aa2d1ed72f872a7d63cb45`,
versionCode 2).

- Instrumented admission result: **PASS**, `OK (1 test)`, exit 0
- Latest admission bundle SHA-256: `a5e7970747f1a119110e3b74c989635f77cbd04147163021159a322166a92cb5`
- Device bundle SHA-256: identical
- Evidence: `evidence/user-slice/first-connect/task7-instrumented-retry-20261010T1650/`
- Limitation: automated `setText`/instrumentation argument stands in for human typing.


## Handoff rerun (fresh protected bundle)

The fresh task-6 handoff bundle was selected through ordinary DocumentsUI and
reached the real Android transfer-code dialog. The visible field was located by
UiAutomation; no test hook or adb-side text injection was used. The native Mac
handoff code was not available through the approved secure live channel during
this run. Therefore the code was not guessed or entered.

- Bundle: `.../user-slice-host-handoff-20261010T143236/task6-handoff.abconnection`
- Picker gate: **PASS**
- Transfer dialog: **PASS** (`code-dialog.png`, `uiautomator-code.xml`)
- Secure handoff: **FAIL / code unavailable**
- `code_entered`: `false`
- Admission/configured: **NOT REACHED**
- Redacted status: `code-handoff-status.txt`, `code-unavailable.txt`


## Inputs

- Installed task-4 APK was reused; no rebuild or uninstall was performed.
- Task-6 exported bundle was reused exactly:
  `/Volumes/Intel/playground/agentbrowser/m1-user-slice-host-integration-20261010/evidence/user-slice/host/user-slice-host-20261010T1418/task6-mac.abconnection`
- Bundle SHA-256: `ee42a8c22455e0cd021498a480107ed6b12408349a0226197c095e242b3251fb`
- The bundle was already staged at `/sdcard/Download/pairing.abconnection`.
- Installed package remained `com.agentbrowser.probe` versionCode `2`, versionName `0.2.0-probe`.

## Ordinary picker flow

The installed app was launched through `MainActivity`. The ordinary UI was used to open the drawer, select `配置连接`, select `导入`, and open Android DocumentsUI. Picker screenshots and UI dumps are retained under `evidence/user-slice/first-connect/task7-first-connect-20261010T1418/`.

The exported bundle was selected from the real DocumentsUI picker. After selection, the app displayed the ordinary connection panel, but the imported bundle was rejected with the visible error:

`BUNDLE_TRANSFER_CODE_REQUIRED`

The screen showed `未连接 Host`, connection label `Android acceptance device`, and all diagnostic fields remained `unknown`. Therefore ordinary connect and browse were not reached for the task-6 lifecycle bundle.

## Gate results

| Gate | Result | Evidence |
| --- | --- | --- |
| Task-6 bundle identity | PASS | `bundle-identity.txt`; supplied SHA matches `ee42a8...3251fb` |
| Existing task-4 artifact reused | PASS | `artifact-installed.txt` |
| Ordinary DocumentsUI picker interaction | PASS | `documentsui.png`, `uiautomator-documentsui.xml`, `after-import.png` |
| Ordinary bundle import/admission | **FAIL** | visible `BUNDLE_TRANSFER_CODE_REQUIRED` in `after-connect.png` |
| Ordinary connect/video/click/scroll | NOT REACHED | import admission failed |
| Real system IME focus | UNKNOWN | not reached in this run; task-4 retained `mInputShown=false`, `mServedInputConnection=null` |
| Chinese composition | UNKNOWN | task-4 retained no zh subtype evidence; not reached here |
| Same-host Tailscale scope | NOT RUN | first ordinary import gate failed |
| Cross-node Tailscale | UNKNOWN | designated 15T `100.104.163.65` remains offline |

## Tailscale status

Live status was captured in `tailscale-status.txt`:

- `macstudio 100.66.1.82`: online
- designated 15T `100.104.163.65`: offline, last seen approximately 6h ago

Cross-node Tailscale remains **unknown**. No cross-node success is claimed.

## First failing gate and next action

First failing gate: ordinary import/admission of the task-6 lifecycle bundle. Raw app error: `BUNDLE_TRANSFER_CODE_REQUIRED`.

Next executable action: task-6 owner must provide the documented transfer-code (or lifecycle export configuration) required by the installed task-4 artifact, then rerun this same ordinary DocumentsUI flow with the exact replacement bundle. Do not use `startImportForTest`, app-private staging, or a test hook.

## Evidence audit, secret hygiene, and contract integrity

### Removed code-bearing UI dumps (security)

Automated code entry can leave code-bearing UI dumps behind: `uiautomator dump`
writes the live `EditText` text of the protected `Transfer code` dialog into a
plain XML file. Two such dumps existed under the gitignored evidence tree and
each held a 16-character transfer-code-shaped plaintext:

```text
evidence/user-slice/first-connect/task7-final-picker-20261010T1615/entered.xml
evidence/user-slice/first-connect/task7-retry-20261010T1540/dialog-entered.xml
```

Both were deleted from disk after resolving and confirming their absolute paths.
Neither file was ever tracked, and neither was force-added. A follow-up scan of
the whole evidence tree for a 16-character token from the transfer-code alphabet
found no remaining hit, and no committed evidence file contains a transfer code
or a protected-field UI dump. Future runs must treat UI dumps of the protected
dialog as secret material and delete them.

### Force-added evidence paths

`/evidence/` is gitignored, so the artifacts these receipts cite were untracked
and therefore unauditable at HEAD. Only redacted, non-secret artifacts were
force-added (`git add -f`) with this exact path list:

```text
evidence/user-slice/host/user-slice-host-20261010T1405/receipt.md
evidence/user-slice/host/user-slice-host-20261010T1418/receipt.md
evidence/user-slice/host/user-slice-host-20261010T1424/receipt.md
evidence/user-slice/host/user-slice-host-handoff-20261010T143236/receipt.md
evidence/user-slice/android/ime-20261010T1705/enabled-imes.txt
evidence/user-slice/android/ime-20261010T1705/ime-list.txt
evidence/user-slice/android/ime-20261010T1705/ime-subtype-zh-count.txt
evidence/user-slice/android/ime-20261010T1705/input-method-focus.txt
evidence/user-slice/android/recon/state-01-launched.png
evidence/user-slice/android/recon/state-02-drawer.png
evidence/user-slice/android/recon/state-03-picker.png
evidence/user-slice/android/recon/state-04-imported.png
evidence/user-slice/android/recon/state-05-picker.png
evidence/user-slice/android/recon/state-06-connected.png
evidence/user-slice/android/recon/state-07-connected.png
evidence/user-slice/android/recon/state-08-retry.png
evidence/user-slice/android/recon/state-09-video.png
evidence/user-slice/android/upgrade-v2/drawer.png
evidence/user-slice/android/upgrade-v2/install-exit.txt
evidence/user-slice/android/upgrade-v2/install-r.txt
evidence/user-slice/android/upgrade-v2/package-after.txt
evidence/user-slice/android/upgrade-v2/package-before.txt
evidence/user-slice/android/user-slice-android-20261010T1402/clicked.png
evidence/user-slice/android/user-slice-android-20261010T1402/connected-before.png
evidence/user-slice/android/user-slice-android-20261010T1402/default-ime.txt
evidence/user-slice/android/user-slice-android-20261010T1402/field-screen.png
evidence/user-slice/android/user-slice-android-20261010T1402/full-flow.mp4
evidence/user-slice/android/user-slice-android-20261010T1402/hashes.sha256
evidence/user-slice/android/user-slice-android-20261010T1402/ime-after-field.txt
evidence/user-slice/android/user-slice-android-20261010T1402/ime-before-field.txt
evidence/user-slice/android/user-slice-android-20261010T1402/ime-committed.png
evidence/user-slice/android/user-slice-android-20261010T1402/ime-focused-final.png
evidence/user-slice/android/user-slice-android-20261010T1402/ime-focused-final.txt
evidence/user-slice/android/user-slice-android-20261010T1402/ime-focused-rerun.png
evidence/user-slice/android/user-slice-android-20261010T1402/ime-list.txt
evidence/user-slice/android/user-slice-android-20261010T1402/ime-raised.png
evidence/user-slice/android/user-slice-android-20261010T1402/input-method.txt
evidence/user-slice/android/user-slice-android-20261010T1402/run.json
evidence/user-slice/android/user-slice-android-20261010T1402/scrolled.png
evidence/user-slice/android/user-slice-android-20261010T1402/signing.txt
evidence/user-slice/android/user-slice-android-20261010T1402/window-after-field.txt
evidence/user-slice/android/user-slice-android-20261010T1402/window-after-flow.txt
evidence/user-slice/android/user-slice-android-20261010T1402/window-before-field.txt
evidence/user-slice/first-connect/task7-instrumented-retry-20261010T1650/instrument.txt
evidence/user-slice/first-connect/task7-instrumented-retry-20261010T1650/redacted.txt
evidence/user-slice/first-connect/task7-recording-20261010T1730/ordinary-flow-sanity.mp4
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/adb-reverse.json
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/fixture-pairing.json
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/installed-apks.json
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-after.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-before.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-cancel-after.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-cancel-before.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-chrome-touch.txt
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-dom.json
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-host.log
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-ime-text.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-ime-trace.logcat.txt
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-ime-trace.txt
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-landscape.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-path-evidence.json
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-portrait-restored.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-reconnected.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-result.json
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-rotation-visual.txt
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-screen.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-scroll-restored.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-scroll-trace.json
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-scrolled.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-test.log
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-text-after-cancel.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-text-after.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-text-before-composition.png
evidence/user-slice/first-connect/task7-scroll-delta-verified-20261010T1740/network-text-before.png
evidence/user-slice/tailscale/task8-android-final-20261010T1640/instrument.txt
evidence/user-slice/tailscale/task8-android-final-20261010T1640/redacted.txt
evidence/user-slice/tailscale/task8-browse-20261010T1645/instrument-click.txt
evidence/user-slice/tailscale/task8-browse-20261010T1645/instrument.txt
evidence/user-slice/tailscale/task8-browse-20261010T1645/redacted.txt
evidence/user-slice/tailscale/task8-browse-20261010T1645/tailscale-after.png
evidence/user-slice/tailscale/task8-browse-20261010T1645/tailscale-before.png
evidence/user-slice/tailscale/task8-browse-20261010T1645/tailscale-browse-click.json
evidence/user-slice/tailscale/task8-browse-20261010T1645/tailscale-browse.json
```

Explicitly **not** force-added, because they carry private connection material or
are superseded: every `*.abconnection` bundle
(`task7-instrumented-retry-20261010T1650/pairing.abconnection`,
`task8-android-final-20261010T1640/task8-tailscale.abconnection`), every
protected-dialog UI dump, the truncated `ordinary-flow.mp4`, and all Host run
artifacts other than their receipts.

### Ordinary-path contract integrity

The ordinary-path proofs never use the test hook and never stage into
app-private storage:

- `NetworkDeviceTest.testOrdinaryProtectedImport()` — `apps/android/app/src/androidTest/java/com/agentbrowser/probe/NetworkDeviceTest.java:885`
- `NetworkDeviceTest.testOrdinaryTailscaleBrowse()` — same file, line `899`
- both call `importOrdinaryPairing()` — same file, lines `300`-`342`, which
  launches `MainActivity` and drives the real launcher, real DocumentsUI file
  picker, the real protected `EditText` and the real `IMPORT` control, and
  asserts `connectionSetup` becomes `configured` with a changed
  `files/pairing/active-generation.json`
- the only app-private read in that method is the read-only comparison of the
  active-generation file at line `331`; nothing is staged there
- `startImportForTest` is called only by `importFixturePairing()` at line `350`,
  which the ordinary-path methods never call. The hook stays in product source
  (`apps/android/app/src/main/java/com/agentbrowser/probe/ConnectionSetupSession.java:193`)
  as an existing test hook for the fixture replay suite.
