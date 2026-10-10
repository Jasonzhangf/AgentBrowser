# Task-7 first-connect receipt

Status: **INCOMPLETE**. No first-connect or full-M1 acceptance claim.

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
