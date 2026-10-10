# Task-7 first-connect receipt

Status: **INCOMPLETE / EVIDENCED PARTIAL**. Admission is proven. Browse evidence is retained from the fixture-host fallback; system Chinese IME and same-session lifecycle browse remain incomplete.

## Current consolidated gate results

| Gate | Result | Raw evidence |
| --- | --- | --- |
| Ordinary DocumentsUI picker | PASS | UiDevice instrumentation; `OK (1 test)`; `task7-instrumented-retry-20261010T1650/` |
| Protected dialog and real IMPORT | PASS | `am instrument` exit 0; configured + active-generation-change assertions |
| Service-lifecycle admission | PASS | bundle SHA `a5e7970747f1a119110e3b74c989635f77cbd04147163021159a322166a92cb5`; device SHA identical |
| Fixture-host remote video | PASS | `task7-browse-only-20261010T1720/network-result.json`: `frameAck.renderedFrames=3`, `displayed=true`, `networkFrames=true` |
| Fixture-host real click | PASS | `network-dom-failing.json`: Host DOM `clicked=1` |
| Fixture-host drag-scroll | operation PASS; document delta UNPROVEN | `scrollPixels=true`, `swipeDoesNotClick=true`; Host DOM `scrollY=0`, `maxScroll=250`; no before/after scrollY delta |
| Fixture-host observe/takeover | PASS | `observerTouchIgnored=true`, `takeoverPixels=true`, operation receipts succeeded |
| System IME focus | UNKNOWN | no raw `dumpsys input_method` capture in fixture browse run; prior task-4 raw focus was `mInputShown=false`, `mServedInputConnection=null` |
| Chinese composition | UNKNOWN (environment-blocked) | emulator has no zh-capable IME subtype/package; controlled RemoteIme composition is not a system IME proof |
| Cross-node Tailscale | UNKNOWN | designated 15T `100.104.163.65` offline |

Browse was proven against the fixture-launched r8 Host, while pairing/admission
was proven against the Service-lifecycle Host. It is not a same-session browse
claim. Automated code entry (`setText`/instrumentation argument) stood in for
human typing.

Screen recording: `evidence/user-slice/first-connect/task7-recording-20261010T1730/ordinary-flow.mp4` (SHA-256 `4bea683553cda2997bf2e92465a9eeea65eaa3715faf053dc69fe54f07bd3397`).

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
