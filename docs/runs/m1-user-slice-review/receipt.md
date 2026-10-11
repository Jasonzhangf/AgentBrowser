# Independent review: M1 user-slice milestone

- Review target: `/Volumes/Intel/playground/agentbrowser/m1-user-slice-host-integration-20261010`
- HEAD: `ea7b0ac3e9003d1004f522994916827b1856db24` (branch `codex/m1-user-slice-host-integration-20261010`)
- Reviewer: independent (`milestone-reviewer`, task-9). Read-only: no product source, merge, push, or delete.
- Tree at review start: `git status --porcelain` empty.

## Verdict

**NOT ACCEPTED as an evidenced partial at this HEAD.** Check 1 (claim audit) and check 4
(contract integrity) FAIL, and check 6 (hygiene) FAILs on leftover processes. The failure is
not in the Tailscale evidence itself, which is largely sound, and not in the
`refresh["state"]` source fix, which is correct. The failure is that **this worktree is not
the integration point it is declared to be**: three of the six receipts named for review do
not exist here, the Android source that produced the committed Tailscale instrumentation
evidence is not in this tree, and two raw-evidence directories cited by the committed
Android+Mac receipt exist only in a sibling worktree.

First failing gate: **artifact presence at the review target HEAD.** `git ls-files
'docs/runs/m1-user-slice*'` returns exactly one path
(`docs/runs/m1-user-slice-tailscale/receipt.md`); `docs/runs/m1-user-slice-first-connect/`,
`docs/runs/m1-user-slice-android/`, and `docs/runs/m1-user-slice-host/` do not exist at HEAD
or anywhere in this worktree.

One executable next action: merge `codex/m1-android-navigation-fix-r1-20261009`
(`52e52ac309a77902892458012fdb1e65bce5b21e`) into the integration branch, then re-run this
review against the merged HEAD. Before any evidence force-add, exclude the two
code-bearing XML dumps listed in check 2.

## Per-check result

| # | Check | Result |
| --- | --- | --- |
| 1 | Claim audit (PASS claims traceable to real artifacts) | **FAIL (blocking)** |
| 2 | Secret hygiene in committed receipts/evidence/logs/source | **PASS** (committed set clean) — 1 advisory |
| 3 | Source correctness (`refresh["state"]`, `--include-transfer-code`) | **PASS** — 2 advisory |
| 4 | Contract integrity (Android admission, UiAutomator scope, APK identity) | **FAIL (blocking)** |
| 5 | Honesty of unknowns | **PASS** — 1 advisory overstatement |
| 6 | Hygiene (clean tree, processes, Hosts, forbidden commands) | **FAIL** (leftover processes) |

## Check 1 — Claim audit: FAIL

### 1.1 Named receipts absent at the review target (blocking)

Existence probe (this worktree, HEAD):

```text
ABSENT  docs/runs/m1-user-slice-first-connect/receipt.md
PRESENT docs/runs/m1-user-slice-tailscale/receipt.md
ABSENT  docs/runs/m1-user-slice-android/receipt.md
ABSENT  docs/runs/m1-user-slice-host/receipt.md
PRESENT docs/runs/m1-20261010-android-mac-integration/receipt.md
```

`git ls-files 'docs/runs/m1-user-slice*'` → `docs/runs/m1-user-slice-tailscale/receipt.md`
only. There is no `docs/runs/m1-user-slice-host/receipt.md` anywhere on disk; the host
receipts exist only as uncommitted, gitignored files under `evidence/user-slice/host/*/receipt.md`.

The two absent `docs/runs` receipts exist only on branch
`codex/m1-android-navigation-fix-r1-20261009` (worktree
`/Volumes/Intel/playground/agentbrowser/m1-android-navigation-fix-r1-20261009`, HEAD
`52e52ac`), added by `0b0b7d9`, `8261ba4`, `4d7464a`, `2f3bf15`, `52e52ac`.
`git merge-base --is-ancestor 52e52ac HEAD` → false. Their raw evidence
(`evidence/user-slice/android/**`, `task7-scroll-delta-verified-20261010T1740/`,
`task7-recording-20261010T1730/`) is also absent here.

### 1.2 Committed evidence reports tests that do not exist at HEAD (blocking)

`evidence/user-slice/tailscale/task8-android-final-20261010T1640/instrument.txt:7,14` →
`test=testOrdinaryProtectedImport`; `.../task8-browse-20261010T1645/instrument.txt:7,14` and
`.../instrument-click.txt:7,14` → `test=testOrdinaryTailscaleBrowse`.

At HEAD, `apps/android/app/src/androidTest/java/com/agentbrowser/probe/NetworkDeviceTest.java`
declares only `testImeCompositionCancel` (line 744) and `testRealNetworkControlAndFrames`
(line 836). The two methods the committed evidence names exist only on the sibling branch
(commit `2f3bf15`). The committed evidence is therefore not reproducible from this tree.

### 1.3 Cited raw evidence absent from this worktree

`docs/runs/m1-20261010-android-mac-integration/receipt.md:177-182` retains:

```text
ABSENT  evidence/network/a9742364ea114174998c51688e0fdeba
ABSENT  evidence/local-direct/replay-integrated-d2dcc1b
```

Both exist only under the sibling worktree. I verified their content there: the network
replay has 26/26 boolean flags true with session `07039cff-61d7-4c4c-8b6d-27252f524b80` and
`network-dom.json` `clicked=1`, `maxScroll=250`; the bridge replay has
`result=bridge_transport_pass`, `first_failure=null`, and the `proved`/`unknown` lists the
receipt describes. The receipt's candidate identity is verifiable in this tree:
`git rev-parse d2dcc1b^{tree}` = `3e4a7668e8e1a29aa9f5ed3ef266b728b8f68f5c`, matching
`receipt.md:17`, and `d2dcc1b` is an ancestor of HEAD.

### 1.4 `Task6 root remains untouched` is contradicted by retained evidence

`docs/runs/m1-user-slice-tailscale/receipt.md:14` ("The original task6 root was not
modified") and `:35` ("Task6 root remains untouched").

Retained (uncommitted) evidence in the same tree:
`evidence/user-slice/tailscale/tailscale-20261010T160551/host-restart.json` reports the
`com.agentbrowser.task6` service running as PID `70574` at 16:05, and `host-stop.json` in the
same directory reports PID `70574` stopped at 16:06. The task6 root itself changed:
`$HOME/Library/Application Support/AgentBrowser/host-service/trust` mtime 16:05:52 and
`.../host-service/run` mtime 16:06:57. The task6 trust registry advanced from revision 1
(14:16) to revision 6 (`tailscale-20261010T160551/trust-status.json`). This is the
milestone's own dedicated Host, not a shared one, but the "untouched" claim is false.

### 1.5 The committed task8 stop receipt does not evidence the stop

`docs/runs/m1-user-slice-tailscale/receipt.md:35` cites
`evidence/user-slice/tailscale/tailscale-20261010T1615/host-stop.json` as the stop receipt.
That file is `{"service_label": "com.agentbrowser.obscura-host", "state": "stopped"}` — the
`state is None` branch of `command_stop` (`scripts/host-service/host_service.py:1454-1456`),
which names the *default* label and carries no PID, so it records "nothing was recorded to
stop at this root". The task8 label was `com.agentbrowser.task8`
(`tailscale-20261010T1615/install.json`). The Hosts are nevertheless stopped now; see check 6.

### 1.6 Other traceability gaps (advisory)

- `docs/runs/m1-user-slice-tailscale/receipt.md:21` — "Emulator-side TCP probe: PASS, as
  recorded in `task8-android-final-20261010T1640/instrument.txt`". `instrument.txt` contains
  only instrumentation status lines; `grep -in 'tcp\|port\|51881\|socket'` returns nothing.
  The probe result is not in the cited file.
- `docs/runs/m1-20261010-android-mac-integration/receipt.md:46-47` — `npm run verify:local`
  PASS and dual-runner 23/23 PASS are cited to "commit hook output" and to the test script
  itself, i.e. to no retained result artifact.
- Evidence directory labels do not match file times: `task8-android-final-20261010T1640/`
  mtime 16:20:31 and `task8-browse-20261010T1645/` mtime 16:24:16.
- `docs/runs/m1-20261010-android-mac-integration/receipt.md:65-67` lists 10 bridge stages;
  the raw `evidence.json` stage map also contains `ui_driver` and `video_displayed`.

### 1.7 Claims that ARE traceable (for balance)

- `tailscale-20261010T1615/status.json` matches receipt line 16 (socket
  `/tmp/abn-task8-runtime/host-eca88be0`, mode `0o600`, state running).
- `tailscale-20261010T1615/endpoint-refresh.json` matches receipt line 17 exactly
  (`authorization_state=refreshed`, `bound_address=100.66.1.82:51881`,
  `endpoint_generation=2`, `endpoint_pid=59549`).
- `tailscale-20261010T1615/membership.json` shows the `export_eligible` receipt with catalog
  revision 2 (receipt line 18).
- Receipt line 19 bundle SHA-256 `7adcb281cc779554e2d99c9eedb12c17c8f97cfad51d5f0933addc23a74897f9`
  equals `shasum -a 256 evidence/user-slice/tailscale/tailscale-20261010T1615/task8-tailscale.abconnection`.
  The Android-side bundle SHA `22220a52b5a81b7d93653aa7efbda3072bed57be7baf578c26e1330c3b67fc95`
  equals `.../task8-android-final-20261010T1640/task8-tailscale.abconnection`.
- Receipt line 20 TCP probe matches `tailscale-connectivity.txt` + `tailscale-connectivity.exit` = 0.
- Receipt lines 28-29 match the three `instrument.txt` files (`OK (1 test)`) and
  `task8-browse-20261010T1645/tailscale-browse-click.json`
  (`renderedFrames=2`, `navigatedFixturePage=true`, `redBeforeClick=true`,
  `limeAfterClick=true`, `nativePixelsChanged=true`).
- Dual-runner claim: `/tmp/agentbrowser-m1-dual/dual-r10-20261010T133822/android-result.json`
  has 26/26 booleans true with matching `runId`; `android-instrumentation.log` ends `OK (2 tests)`
  with `testImeCompositionCancel` + `testRealNetworkControlAndFrames`; `evidence.json` and
  `correlation.json` both carry
  `{"milestone": "android-mac-transport", "decision": "transport_milestone_bar_satisfied"}`.

## Check 2 — Secret hygiene: PASS (committed set), 1 advisory

Scans run (committed set = `git grep` over the HEAD tree; result counts only, no values printed):

```text
git grep -I -c -E -- '-----BEGIN [A-Z ]*PRIVATE KEY-----'      -> 0 files
git grep -I -n -E -- '\b[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{16}\b' -> 0 lines
git grep -I -n -i -E -- '(password|passphrase|api[_-]?key|secret|bearer [A-Za-z0-9._-]{8,}|credential)[[:space:]]*[:=]' -> 108 lines (source-code identifiers, no values; reviewed as non-secret)
git grep -I -c -E -- 'p12_base64|BEGIN CERTIFICATE'            -> 10 files
grep -rI -l -E -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' evidence/user-slice docs/runs/m1-user-slice-tailscale -> 0 files
```

The transfer-code shape is 16 characters from `TRANSFER_CODE_ALPHABET` (`host_service.py:99-100`).
**No transfer code, PEM private key, or credential value is present in any committed receipt,
evidence text, log, or source file.**

ADVISORY (uncommitted, must not be committed): two gitignored uiautomator dumps store a
16-character transfer-code-shaped string in the plaintext `text=` attribute of the protected
`android.widget.EditText`:

```text
evidence/user-slice/first-connect/task7-final-picker-20261010T1615/entered.xml
evidence/user-slice/first-connect/task7-retry-20261010T1540/dialog-entered.xml
```

Neither is tracked by git in this worktree or in the sibling worktree
(`git ls-files --error-unmatch` fails for both; `git log --all -- 'evidence/user-slice/first-connect/**'`
is empty), so the committed boundary is clean today. A `git add -f evidence/user-slice/first-connect/`
in the style used for the Tailscale evidence (`1b1a292`) would commit it. Value not reproduced here.

Also advisory: the committed bundle fixtures carry an encrypted `p12_base64`; the encryption
password (one-time code) is not committed, so the private key is not recoverable. The
Android unit-test fixture `apps/android/app/src/androidTest/assets/first-connection/valid.p12`
is synthetic (`ConnectionBundleTest.java:15` code `ABCDEFGHJKMN`, device
`11111111-2222-4333-8444-555555555555`), not a production credential.

## Check 3 — Source correctness: PASS

**`refresh["state"]` is the correct key.** The CLI `endpoint-refresh` command emits
`refresh_endpoint(...)`, whose payload is `{"state": "refreshed", ...}`
(`scripts/host-service/host_service.py:3372-3381`), and the raw artifact confirms it:
`evidence/user-slice/host/user-slice-host-handoff-20261010T143236/refresh.json` contains
`"state": "refreshed"`. The fix at `apps/macos/Sources/main.swift:2028` reads
`refresh["state"] as? String == "refreshed"`.

**No fallback or both-keys weakening.** That is the single occurrence of `refresh[` in
`main.swift`; there is no `?? refresh["status"]` and no second key check.

Root-cause note (advisory): the word "refreshed" appears under two different keys in two
layers — `state` in the CLI payload (`host_service.py:3373`) and `status` in the internal
applied receipt consumed by `export_bundle` (`host_service.py:2813`, checked at
`host_service.py:3748`). The Swift fix correctly targets the CLI layer.

**`--include-transfer-code` default redaction intact.** `host_service.py:3910-3911` strips
`transfer_code` unless the flag is set; `host_service.py:3994-3998` declares the flag.
`grep -rn 'include.transfer.code'` over the tree finds it only in
`apps/macos/Sources/main.swift:1618` (native owner), the service source, and build copies of
the service. The bundle document (`host_service.py:3768-3779`) never contains `transfer_code`.

The built Mac app matches HEAD source: `apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMac`
SHA-256 `053adf8ad875a384605181dac38217dc30c2bb4e371af6d7448cae57209cd00c` equals the guide's
claim, its bundled `Resources/host-service/host_service.py` is byte-identical to
`scripts/host-service/host_service.py` (both `fcac13f2d899c785cd210e4b3d370d8143cf13a2ef3c2ec4b1eafaf1a638df6c`),
and the binary contains the handoff strings added by `8055db4` (`传输码已复制`,
`请在 Android 的 Transfer code 对话框中粘贴`, `--include-transfer-code`).

Advisory: `--include-transfer-code` is an ordinary CLI flag. Nothing at the CLI boundary
restricts it to the native owner; the restriction is a convention held by in-tree callers only.

Advisory: the live copy-control proof is weaker than the guide states; see check 5.

## Check 4 — Contract integrity: FAIL (blocking)

### 4.1 The UiAutomator change and the ordinary-import driver are absent at HEAD

`apps/android/app/build.gradle:10-11` at HEAD:

```text
versionCode 1
versionName '0.1.0-probe'
```

and the dependency block (`apps/android/app/build.gradle:21`) has no `androidTestImplementation`.
`grep -i uiautomator apps/android/app/build.gradle` returns nothing at HEAD.

On the sibling branch the change is correct in isolation: `2f3bf15` adds
`androidTestImplementation 'androidx.test.uiautomator:uiautomator:2.3.0'` (androidTest scope
only, sibling `build.gradle:24`), and `0b0b7d9` bumps to `versionCode 2` /
`versionName '0.2.0-probe'` with the same applicationId `com.agentbrowser.probe`. None of this
is in the review target, so the claim cannot be verified at HEAD.

The guide's shipped-artifact identity also contradicts HEAD source:
`docs/user-slice-connection-guide.md:9` claims versionCode `2` / versionName `0.2.0-probe` /
SHA-256 `8eceb55e404791484451499aac38cce70a2d6fcdd2aa2d1ed72f872a7d63cb45`, while
`apps/android/app/build.gradle` at HEAD declares versionCode 1. The SHA does match the sibling
worktree's built APK (`/Volumes/Intel/playground/agentbrowser/m1-android-navigation-fix-r1-20261009/apps/android/app/build/outputs/apk/debug/app-debug.apk`),
and the emulator currently carries `versionCode=2`, `versionName=0.2.0-probe`,
`lastUpdateTime=2026-10-10 16:38:00` — i.e. the deployed artifact is the sibling branch's
build, not a build of this HEAD.

### 4.2 `startImportForTest` is present in product source at HEAD

- `apps/android/app/src/main/java/com/agentbrowser/probe/ConnectionSetupSession.java:187`
  `requestPickerForTest(...)` and `:193` `startImportForTest(Uri, IntentType)` — both
  package-private instrumentation entrypoints in product source, documented as
  "Instrumentation entrypoint: same path as the SAF callback".
- Callers at HEAD: `ConnectionSetupDeviceTest.java:93,131,275,298` and
  `NetworkDeviceTest.java:301`.
- The fixture network-replay path also stages app-privately:
  `NetworkDeviceTest.java:299` builds
  `new File(getInstrumentation().getTargetContext().getFilesDir(), "network-replay-input/pairing.abconnection")`
  and `:301` admits it through `startImportForTest`.

Provenance: introduced by `d1738c7` ("feat(android): add first-connection setup owner"), not
by the user-slice commits, and not present in `origin/main`. So this is a pre-existing hook,
not a weakening introduced by this milestone — but the check requires its absence, and the
ordinary-DocumentsUI replacement evidence that would supersede it (the `2f3bf15` UiDevice
test) is not in this tree. The condition is unproven at HEAD.

### 4.3 No parser change for the ordinary path

`apps/android/app/src/main/java/com/agentbrowser/probe/ConnectionBundle.java` keeps the typed
failures (`BUNDLE_TRANSFER_CODE_REQUIRED` at lines 157 and 221,
`BUNDLE_TRANSFER_CODE_INVALID` at 158 and 222); `ConnectionSetupSession.java:353-378` maps
them without a no-code fallback. This part is consistent with the receipts. PASS for this
sub-item.

## Check 5 — Honesty of unknowns: PASS (1 advisory)

The receipt set does **not** claim any of the five prohibited items. Raw confirmations:

- Real system IME focus: recorded as FAIL.
  `docs/runs/m1-user-slice-android/receipt.md:85-91` (`mInputShown=false`,
  `mServedInputConnection=null`); `docs/user-slice-connection-guide.md:85`.
- Chinese composition: recorded as UNKNOWN, environment-blocked
  (`.../m1-user-slice-android/receipt.md:93-111`; guide line 84).
- Cross-node Tailscale: recorded as UNKNOWN with the offline peer.
  `docs/runs/m1-user-slice-tailscale/receipt.md:3`, `:37`;
  `task8-browse-20261010T1645/redacted.txt:3` `cross_node=UNKNOWN`;
  `tailscale-20261010T1615/tailscale-status.txt` shows `100.104.163.65 ... offline, last seen 8h ago`.
- Same-session lifecycle browse: explicitly disclaimed.
  `.../m1-user-slice-first-connect/receipt.md:23-26` and guide line 89 separate
  pairing/admission (service-lifecycle Host) from local browse (fixture-launched r8 Host).
- Independent Host DOM read-back on the service Host: explicitly disclaimed.
  `docs/runs/m1-user-slice-tailscale/receipt.md:31`; guide line 90;
  `task8-browse-20261010T1645/redacted.txt:4` `independent_host_dom=unavailable-service-host`.

ADVISORY (overstatement): `docs/user-slice-connection-guide.md:88` states the Mac side is
proven to display the transfer code and to offer a copy entry. The retained evidence for that
run records `evidence/user-slice/host/user-slice-host-handoff-20261010T1450/redacted-state.txt:2`
`copy_action_available=implemented_not_visible`, and `copy-handoff-receipt.md` asserts the
pasteboard handoff at source level. The copy control was never observed live in the retained
evidence. (I did not open `handoff-sheet.png`; it may contain the code.)

## Check 6 — Hygiene: FAIL (leftover processes); other items PASS

Verified now, not assumed:

- Worktree clean: `git status --porcelain` empty at review start.
- **task6/task8 Hosts stopped: PASS.** `ps -eo pid,command | grep -E 'abn-task6|abn-task8'`
  is empty; `launchctl list | grep -iE 'task6|task8'` is empty;
  `$HOME/Library/Application Support/AgentBrowser/host-service/state.json` does not exist;
  `.../host-service-task8/` has no `state.json`; `/tmp/abn-task6-runtime` and
  `/tmp/abn-task8-runtime` hold only `endpoint-applied`.
- **Shared Host untouched: PASS.** The pre-existing launchd-managed pair
  (`com.agentbrowser.obscura-host` PID 79521/79525, root `/Users/fanzhang/.agentbrowser`,
  elapsed 1d04h) is still running and predates the milestone.
- **Leftover milestone processes: FAIL.**
  - PID 31780 (started 15:22:51, elapsed 1h21m)
    `/Volumes/Intel/playground/agentbrowser/m1-user-slice-host-integration-20261010/apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMac --pairing-dir /tmp/nonexistent-task6`,
    with child PID 31904 `AgentBrowserMacBridge` — an artifact of this milestone's worktree.
  - PID 21538 (r8 `obscura-host`) and PID 21556 (`obscura-endpoint --listen 127.0.0.1:52691`),
    started 13:56:01, root `/tmp/an-9aa68747020f4211988fd9215c20eadb`, whose socket is
    `session-27bd14a8-bf38-4cec-ac5f-0c84e5c3ddca.sock` — the exact session cited by
    `.../m1-user-slice-android/receipt.md:30`. This is the fixture Host from the Android
    user-slice run. Its runtime directory still holds `key.der`, `server-key.der`, and
    `pairing.abconnection`.
  - Unattributed but running with the r8 binary: PID 79682/71228 under
    `/private/tmp/abnocode-f13310de` (created 2026-10-09, before this milestone). Not charged
    to this milestone; listed for completeness.
- **Forbidden commands: PASS.** `grep -rnE 'pkill|killall|xargs[[:space:]]+kill|--no-verify|git[[:space:]]+(restore|reset|checkout)'`
  over `scripts apps packages docs evidence` returns only five prose hits, all in
  `docs/runs/m1-mainline-delivery-audit-r1-20261004/` and
  `docs/runs/m1-native-chrome-layout-design-r{3,4,5}-20261004/` — one rule statement plus
  historical notes that `--no-verify` was **not** used. None is in an executable script.
  Milestone scripts use explicit-PID termination only
  (`scripts/local-direct/replay.py:2339` `os.kill(pid, SIGTERM)`, `:2362`
  `os.kill(pid, SIGKILL)`; `scripts/network-replay.py:239` and
  `scripts/m1-dual-client-runner.py:1770` use `Popen.terminate()` handles). No
  `git restore/reset/checkout/clean/stash` in `scripts/`.
- Hooks remain enabled (`core.hooksPath=.githooks`; `.githooks/pre-commit` runs
  `npm run verify:local` and refuses `main`).

## Gates that remain unproven for full M1

From `docs/user-slice-connection-guide.md:113` and confirmed by this review:

1. BB01–BB14 full acceptance suite.
2. Real HTTPS login and profile restart.
3. Weak-network and quality behavior.
4. Local / LAN / Tailscale / Relay matrix — the whole Relay and LAN layer is untouched here;
   cross-node Tailscale is unproven (peer `15T`/`100.104.163.65` offline).
5. Core regression.
6. Latest-main end-to-end.
7. Independent review — this review is a FAIL, so this gate is not met.
8. Dual-repository remote delivery.
9. Version package and OTA.
10. Baseline and project memory.
11. Resource cleanup (leftover processes above).

Additional user-slice gates that stay open:

- Real system IME focus: raw FAIL (`mInputShown=false`, `mServedInputConnection=null`).
- Chinese composition: UNKNOWN, no zh-capable IME on the acceptance emulator.
- Same-session lifecycle browse: not attempted; pairing and browse ran on different Hosts.
- Independent Host DOM read-back on the service-lifecycle Host: unavailable by design.
- The reviewed tree does not contain the Android ordinary-import driver or the UiAutomator
  dependency at all, so the Android side of the user slice is not reviewable here.

## What this review did not verify

- Screenshots and videos were not opened (`handoff-sheet.png`, `tailscale-*.png`, `*.mp4`);
  the handoff screenshot may contain the one-time code.
- No bundle contents were reproduced in this receipt.
- No test, build, or Host command was run; all statements are source reads, git queries, file
  hashes, and live process inspection.

---

# Re-review 2026-10-10 (task-10) — HEAD `9811e5f`

Scope: only the findings recorded above, re-checked at the new integration HEAD
`9811e5f049035f8a6d8b4092de9f8938f8d75c3c` (branch
`codex/m1-user-slice-host-integration-20261010`) against fix commits `d591303` (merge of
`codex/m1-android-navigation-fix-r1-20261009`), `1f5b94e`, and `9811e5f`. The original
findings above are unchanged; nothing was rewritten. Tree clean at start
(`git status --porcelain` empty).

## Re-review verdict

**ACCEPTED as an evidenced partial**, with **one remaining blocking receipt defect** that
does not change the milestone outcome. The two original blocking findings that invalidated
the milestone (claim-audit artifact presence and contract integrity) are resolved, and the
hygiene finding is resolved. The remaining defect is confined to the Host-cleanup sentence of
`docs/runs/m1-user-slice-tailscale/receipt.md:35`, whose cited stop evidence is still not
real stop evidence; the underlying fact (task8 stopped) is independently verified live.

## Original finding status

| Original finding | Status at `9811e5f` |
| --- | --- |
| 1.1 Named receipts absent at HEAD (blocking) | **RESOLVED** |
| 1.2 Committed evidence reports tests absent at HEAD (blocking) | **RESOLVED** |
| 1.3 Cited sibling-worktree evidence absent (blocking) | **PARTIAL** — judgement below |
| 1.4 `Task6 root remains untouched` contradicted | **RESOLVED** (wording) — residual: the cited first-run directory is untracked |
| 1.5 Committed task8 stop receipt is not stop evidence (blocking) | **NOT RESOLVED** — blocking |
| 1.6 Advisory traceability gaps | **NOT ADDRESSED** (still advisory) |
| 2 Secret-hygiene advisory (code-bearing XML dumps) | **RESOLVED** |
| 3 Contract integrity FAIL (blocking) | **RESOLVED** |
| 4 Hygiene FAIL (leftover processes) | **RESOLVED** |
| 5 Guide copy-control overstatement (advisory) | **NOT ADDRESSED** (still advisory) |
| 6 Bundle SHA retention after untracking | **RESOLVED** |

## 1.1 Receipt and evidence presence: RESOLVED

```text
git ls-files 'docs/runs/m1-user-slice*'
docs/runs/m1-user-slice-android/receipt.md
docs/runs/m1-user-slice-first-connect/receipt.md
docs/runs/m1-user-slice-host/receipt.md
docs/runs/m1-user-slice-review/receipt.md
docs/runs/m1-user-slice-tailscale/receipt.md
```

All four previously named receipts now exist at HEAD, plus the review receipt. Tracked
evidence grew from 24 files at `ea7b0ac` to 96 files at `9811e5f` (net +72; `1f5b94e` removed
2 tracked bundles and `9811e5f` added 74 evidence files — the Lead's summary said 83, the
actual count in `9811e5f` is 74 evidence paths plus 1 docs path).

`docs/runs/m1-user-slice-host/receipt.md` (47 lines) is an honest index: it declares itself
index-only (lines 3-4), names the four force-added `evidence/user-slice/host/*/receipt.md`
files with the gates each proves (lines 14-17), points to the three sibling `docs/runs`
receipts (lines 21-25), and states which Host artifacts stay untracked and why (lines 45-47).
Every path it lists is tracked.

I resolved every `evidence/...` path cited across the five receipts and the guide against
`git ls-files`. The only unresolved citations are the ones listed in 1.3, 1.4-residual, and
1.5; the other two unresolved hits are correct by design:

- `evidence/user-slice/first-connect/task7-final-picker-20261010T1615/entered.xml` and
  `.../task7-retry-20261010T1540/dialog-entered.xml` — cited at
  `m1-user-slice-first-connect/receipt.md:128-129` precisely because they were deleted as
  secret material.
- `evidence/user-slice/host/user-slice-host-20261010T1418/task6-mac.abconnection`
  (`.../receipt.md:74`) — an exported bundle, correctly untracked, and the receipt says so.

## 1.2 Instrumentation methods now exist at HEAD: RESOLVED

`apps/android/app/src/androidTest/java/com/agentbrowser/probe/NetworkDeviceTest.java`:

- `testOrdinaryProtectedImport()` at line 885
- `testOrdinaryTailscaleBrowse()` at line 899

These are exactly the two test names the committed Tailscale evidence reports
(`task8-android-final-20261010T1640/instrument.txt:7,14`;
`task8-browse-20261010T1645/instrument.txt:7,14`). The committed evidence is now
reproducible from this tree.

## 1.3 The two sibling-worktree citations: judgement

Still absent at `9811e5f`:

```text
NOT-TRACKED  evidence/network/a9742364ea114174998c51688e0fdeba   (not on disk in this worktree)
NOT-TRACKED  evidence/local-direct/replay-integrated-d2dcc1b    (not on disk in this worktree)
```

`docs/runs/m1-20261010-android-mac-integration/receipt.md:175-184` still reads "`/evidence/`
is ignored by Git, so the raw evidence is retained on disk at:" followed by these two
relative paths and two `/tmp/...` paths. Both directories still exist in the sibling worktree
`/Volumes/Intel/playground/agentbrowser/m1-android-navigation-fix-r1-20261009`
(27 files and 2 files).

**Recommended fix: reword the citation to name the sibling worktree explicitly. Do not
force-add the directories.** Reasons, in order of weight:

1. The paragraph is explicitly a retention map of paths *outside* Git — two of its four
   bullets are `/tmp/agentbrowser-m1-dual/...` paths that can never be tracked. Force-adding
   two of the four bullets leaves the paragraph just as unreadable.
2. These artifacts were produced in another worktree. Force-adding them duplicates provenance
   into this branch and inflates the diff by 29 files (15 PNGs, 2 logs, 4 txt, 8 json) for
   evidence whose candidate commit `d2dcc1b` is already an ancestor of this HEAD, so the
   candidate identity is verifiable here without them.
3. They are secret-free — I scanned them: 0 hits for `-----BEGIN ... PRIVATE KEY-----` or
   `p12_base64`, 0 hits for the 16-character `TRANSFER_CODE_ALPHABET` shape — so force-adding
   is possible, but it is not *necessary*.
4. `docs/runs/m1-user-slice-first-connect/receipt.md:74` already shows the correct pattern in
   this same receipt set: it qualifies an untracked bundle with the full absolute worktree
   path.

Caveat: if the project's acceptance rule is "every PASS claim must resolve inside the
reviewed tree", then the minimum sufficient force-add is the four files the receipt actually
reasons about (`network-result.json`, `network-dom.json` for the Android replay;
`evidence.json`, `context.json` for the bridge replay) — not the whole directories. That is a
rule decision for the Lead, not a defect in the receipt.

## 1.4 Task6 wording: RESOLVED, with a residual citation

`docs/runs/m1-user-slice-tailscale/receipt.md:14` now reads: "The first Tailscale attempt
reused and cycled the task6 instance (restart PID 70574; stop evidence is retained under the
first-run evidence directory), then the run moved to the dedicated Task8 instance. The task6
trust root material was not modified." Line 35 repeats the qualification. This matches the
retained evidence: `evidence/user-slice/tailscale/tailscale-20261010T160551/host-restart.json`
shows `com.agentbrowser.task6` PID 70574 at 16:05 and `host-stop.json` in the same directory
shows PID 70574 stopped at 16:06. The "untouched" claim is now correctly narrowed to trust
root material.

Residual (advisory): the phrase "stop evidence is retained under the first-run evidence
directory" points at `evidence/user-slice/tailscale/tailscale-20261010T160551/`, which is
**not tracked at HEAD** (`git ls-files evidence/user-slice/tailscale/tailscale-20261010T160551`
= 0 files). The claim is true on disk but not auditable at HEAD, the same class as 1.3.

## 1.5 Task8 stop evidence: NOT RESOLVED (blocking)

`docs/runs/m1-user-slice-tailscale/receipt.md:35` now says: "The initial stop file was the
no-live-state branch and is retained for audit; the corrected stop receipt is
`evidence/user-slice/tailscale/tailscale-20261010T1615/host-stop-real.json`, followed by
status `state=stopped`, `endpoint_pid=null`."

Three problems:

1. **Not tracked.** `git cat-file -e HEAD:evidence/user-slice/tailscale/tailscale-20261010T1615/host-stop-real.json`
   → `fatal: path ... exists on disk, but not in 'HEAD'`. `git ls-files | grep host-stop`
   returns only `host-stop.json`. `git log --all -- '*host-stop-real*'` is empty. The file
   exists on disk (mtime 16:54) but was not included in the `9811e5f` force-add.
2. **Not real stop evidence.** Its content is
   `{"service_label": "com.agentbrowser.obscura-host", "state": "stopped"}` — the same shape
   as the original `host-stop.json`, i.e. still the `state is None` branch of
   `command_stop` (`scripts/host-service/host_service.py:1454-1456`): default label, no PID.
   It records that nothing was recorded to stop, not that the running task8 Host was stopped.
3. **The follow-up status does not exist.** No post-stop status artifact is tracked. The only
   tracked `status.json` shows `{"state": "running", "endpoint_pid": null, "pid": 42645}`
   captured at 16:16, before the stop.

The underlying fact is nevertheless true, and I verified it live: no `abn-task6`/`abn-task8`
process exists, `launchctl list` has no task6/task8 label, and neither
`$HOME/Library/Application Support/AgentBrowser/host-service/state.json` nor
`.../host-service-task8/state.json` exists. So the *claim* is true and the *citation* is
false.

Required correction (either is sufficient): point line 35 at the real retained task6 stop
receipt under the first-run directory (which has a PID) and add a live post-stop `ps` capture
for task8; or delete the `host-stop-real.json` citation and state that task8 is verified
stopped by the absence of its state file, launchd entry, and process.

## 2 Secret hygiene advisory: RESOLVED

- Both code-bearing dumps are gone from disk in this worktree
  (`ls evidence/user-slice/first-connect/task7-final-picker-20261010T1615/entered.xml` and
  `.../task7-retry-20261010T1540/dialog-entered.xml` → "No such file or directory").
- Neither appears in any ref: `git log --all --oneline -- '*entered.xml'` is empty, and
  `git ls-files | grep -i entered` is empty.
- Scan over the whole tracked evidence set (96 files): 16-character
  `TRANSFER_CODE_ALPHABET` shape → **0 hits**; PEM private-key markers → **0**;
  `p12_base64` / `BEGIN CERTIFICATE` → **0**; non-empty `android.widget.EditText` dumps →
  **0**. The 25 credential-keyword hits are all `password` inside `dumpsys input_method`
  and Host `EditorContext` debug lines (`password=false`,
  `enable_number_row_in_password=false`) — no values.
- Exported bundles untracked as intended: `git ls-files | grep abconnection` now returns only
  the three test fixtures
  (`apps/android/app/src/androidTest/assets/first-connection/{nocode,valid}.abconnection`,
  `apps/android/app/src/test/resources/first-connection/valid.abconnection`), which are
  untouched. The two exported `task8-tailscale.abconnection` files were removed by `1f5b94e`.
- Their SHA-256 values are retained in receipts, as required:
  `7adcb281cc779554e2d99c9eedb12c17c8f97cfad51d5f0933addc23a74897f9`
  (`m1-user-slice-tailscale/receipt.md:19`) and
  `22220a52b5a81b7d93653aa7efbda3072bed57be7baf578c26e1330c3b67fc95`
  (`m1-user-slice-first-connect/receipt.md:16`, `m1-user-slice-android/receipt.md:59`).

## 3 Contract integrity: RESOLVED

At HEAD `9811e5f`, `apps/android/app/build.gradle`:

```text
 7:        applicationId 'com.agentbrowser.probe'
10:        versionCode 2
11:        versionName '0.2.0-probe'
22:        testImplementation 'junit:junit:4.13.2'
23:        testImplementation 'org.json:json:20240303'
24:        androidTestImplementation 'androidx.test.uiautomator:uiautomator:2.3.0'
```

The UiAutomator dependency is `androidTestImplementation` only, and the shipped identity now
matches the guide (`docs/user-slice-connection-guide.md:9`, versionCode 2 /
`0.2.0-probe` / `8eceb55e...`). The emulator carries `versionCode=2`,
`versionName=0.2.0-probe`, `lastUpdateTime=2026-10-10 16:38:00`, and the sibling-built APK
still hashes to `8eceb55e404791484451499aac38cce70a2d6fcdd2aa2d1ed72f872a7d63cb45`.

Ordinary path, read directly from source:

- `testOrdinaryProtectedImport()` (line 885) requires the `transferCode` argument (lines
  886-887) and calls `importOrdinaryPairing()` (line 889).
- `importOrdinaryPairing()` (lines 300-342) drives `device.pressHome()`, a real
  `am start -n com.agentbrowser.probe/.MainActivity` (line 305), the real drawer control, the
  real `导入` control, asserts the real `com.google.android.documentsui` picker (line 320),
  selects `pairing.abconnection` (lines 323-324), fills the real
  `android.widget.EditText` (lines 326-327), clicks the real `IMPORT` (lines 328-329), and
  asserts the panel becomes `configured` with a changed
  `files/pairing/active-generation.json` (`ACTIVE_FILE` =
  `active-generation.json`, `ConnectionSetupSession.java:52`). Its only app-private access is
  the read-only generation comparison (lines 331-336). No `startImportForTest`, no staging.
- `testOrdinaryTailscaleBrowse()` (line 899) does not call the hook; it reads the admitted
  endpoint read-only from `pairing/.generations` (lines 905-909) and drives the ordinary
  connect/browse UI.

`startImportForTest` remains in product source as a pre-existing test hook
(`ConnectionSetupSession.java:193`; `requestPickerForTest` at line 188). Remaining callers,
explicitly:

```text
apps/android/app/src/androidTest/java/com/agentbrowser/probe/ConnectionSetupDeviceTest.java:93
apps/android/app/src/androidTest/java/com/agentbrowser/probe/ConnectionSetupDeviceTest.java:131
apps/android/app/src/androidTest/java/com/agentbrowser/probe/ConnectionSetupDeviceTest.java:275
apps/android/app/src/androidTest/java/com/agentbrowser/probe/ConnectionSetupDeviceTest.java:298
apps/android/app/src/androidTest/java/com/agentbrowser/probe/NetworkDeviceTest.java:350   (importFixturePairing)
requestPickerForTest: ConnectionSetupDeviceTest.java:236
```

The two ordinary-path methods are not among them. `importFixturePairing()` (lines 344-351)
still stages the fixture bundle app-privately and admits it through the hook; that is the
fixture-replay path, unchanged and disclosed.

ADVISORY (new): the contract-integrity record at
`docs/runs/m1-user-slice-first-connect/receipt.md:245` says "both call
`importOrdinaryPairing()`". Only `testOrdinaryProtectedImport()` does (line 889);
`testOrdinaryTailscaleBrowse()` does not. The record's substance is unaffected.

## 4 Hygiene: RESOLVED

- Leftover processes gone: `ps -p 31780,31904,21538,21556` returns only the header. No
  `abn-task6`/`abn-task8` process exists.
- task6/task8 stopped: no launchctl task6/task8 label; both
  `$HOME/Library/Application Support/AgentBrowser/host-service/state.json` and
  `.../host-service-task8/state.json` are absent.
- Shared Host untouched: the pre-existing launchd pair `com.agentbrowser.obscura-host`
  (PID 79521/79525, root `/Users/fanzhang/.agentbrowser`, elapsed 1d04h) still runs.
  Remaining unrelated processes are the pre-existing `m1-mac-native-export-r2` worker Hosts
  and the `/private/tmp/abnocode-f13310de` pair; none is milestone-owned.
- Worktree clean at start.
- Forbidden commands: re-scanned `scripts apps packages docs evidence`. The only new hits are
  my own review receipt's prose. No `pkill`, `killall`, `xargs kill`, `--no-verify`, or
  destructive git command appears in any executable script.

## 5 and 1.6 Advisories not addressed

Both remain as originally recorded, and the files are unchanged since `ea7b0ac`
(`git log --oneline ea7b0ac..HEAD -- docs/user-slice-connection-guide.md
docs/runs/m1-20261010-android-mac-integration/receipt.md` is empty):

- `docs/user-slice-connection-guide.md:88` still says the Mac side is proven to display the
  transfer code and to offer a copy entry, while the retained evidence records
  `copy_action_available=implemented_not_visible`.
- `docs/runs/m1-user-slice-tailscale/receipt.md:21` still cites
  `task8-android-final-20261010T1640/instrument.txt` for the emulator-side TCP probe;
  `instrument.txt` contains no probe output.
- The guide's APK path `apps/android/app/build/outputs/apk/debug/app-debug.apk` does not exist
  in this worktree (build outputs are untracked); the artifact exists only in the sibling
  worktree. Same class as 1.3 — a retention path, not a false claim.

## Remaining unproven gates for full M1

Unchanged from the original review; none of the fix commits touched them:

1. BB01–BB14 full acceptance suite.
2. Real HTTPS login and profile restart.
3. Weak-network and quality behavior.
4. Local / LAN / Tailscale / Relay matrix — cross-node Tailscale unproven (`15T`
   `100.104.163.65` offline); Relay and LAN layers untouched.
5. Core regression.
6. Latest-main end-to-end.
7. Independent review — now an evidenced partial at `9811e5f`; still not a full-M1 pass.
8. Dual-repository remote delivery.
9. Version package and OTA.
10. Baseline and project memory.
11. Resource cleanup — the milestone's own leftovers are cleaned; the pre-existing unrelated
    Hosts are not this milestone's to remove.

User-slice gates that stay open: real system IME focus (raw FAIL), Chinese composition
(UNKNOWN, no zh-capable IME), same-session lifecycle browse (pairing and browse still ran on
different Hosts), independent Host DOM read-back on the service-lifecycle Host (unavailable
by design).

---

# Closure note 2026-10-10 (task-10 follow-up) — HEAD `49263dd`

Single-finding closure check of finding 1.5 after `4c47aaa` and `49263dd`. Earlier sections
unchanged. Exact HEAD confirmed: `49263dd98074584c9291c535f46973183d971766`, tree clean.

**Finding 1.5: STILL OPEN (narrowed).** `docs/runs/m1-user-slice-tailscale/receipt.md:35` no
longer cites `host-stop-real.json`; the replacement reads: "Task8 is verified stopped after
the Android run: its service state is `stopped`, its recorded endpoint PID is absent, and the
post-run process capture records `pid 70574 absent`. The launchd plist remains only as an
installed declaration; no live task8 process exists. The initial stop file is retained for
audit but is not cited as stop proof." The three files it leans on are now tracked
(`evidence/user-slice/tailscale/tailscale-20261010T160551/{host-stop.json,post-stop-ps.txt,task8-launchd-entry.txt}`),
so the unresolvable-citation defect is fixed. But the replacement attributes the **task6**
instance to task8: `post-stop-ps.txt` is `pid 70574 absent`, and `host-stop.json` in that same
directory is `{"pid": 70574, "service_label": "com.agentbrowser.task6", "state": "stopped"}` —
70574 is task6's PID, not task8's. Task8's service PID is `42645`
(`tailscale-20261010T1615/status.json`), and its only tracked status reads `state: running`,
`endpoint_pid: null`, captured at 16:16 before the stop. The only task8-side stop artifact is
`tailscale-20261010T1615/host-stop.json` =
`{"service_label": "com.agentbrowser.obscura-host", "state": "stopped"}` — the no-state branch
with the default label, which the receipt itself declines to cite as proof.
`task8-launchd-entry.txt` is an `ls -l` of the plist path and says nothing about a live
process. No artifact records PID 42645 absent.

The fact itself holds and I re-verified it live at this HEAD: `ps -p 42645,70574` is empty, no
`abn-task6`/`abn-task8` process exists, `launchctl list` has no task6/task8 label,
`$HOME/Library/Application Support/AgentBrowser/host-service-task8/state.json` is absent, and
the plist file does remain. So the claim is true and its cited chain still is not. Required
correction: add a post-stop capture of PID 42645 (or state the proof as absence of state file
+ launchd job + PID 42645), or relabel the sentence so the cited PID is named as the task6
instance.

**Advisory 1.6 (TCP probe citation): STILL OPEN.** Line 21 now points at
`task8-android-final-20261010T1640/redacted.txt`, but that file holds only `bundle_sha=`,
`device_sha=`, `exit=0`, `scope=same-physical-mac-tailscale-100.66.1.82:51881` — no probe
output. The only probe output in the tracked Tailscale evidence is
`tailscale-20261010T1615/tailscale-connectivity.txt` =
`Connection to 100.66.1.82 port 51881 [tcp/*] succeeded!`, which is the Mac-side probe already
quoted on the "Mac TCP reachability" line. The emulator-side probe claim has no raw artifact
at HEAD.

**No new secret, bundle content, or protected-field dump.** `4c47aaa` and `49263dd` add no
PEM/p12/certificate material. Tracked evidence is now 99 files: 0 hits for the 16-character
`TRANSFER_CODE_ALPHABET` shape, 0 PEM/p12, 0 `android.widget.EditText` dumps; the only tracked
`.abconnection` files remain the three test fixtures. The three newly tracked files are
stop/`ps`/plist-listing text.

**Other changes in the range check out.** `49263dd` qualifies the two sibling-worktree
citations as a retention map outside Git with absolute paths — the reword fix recommended in
the 1.3 judgement above. `4c47aaa` corrects the contract-integrity record to name
`importOrdinaryPairing()`'s single caller and the explicit `startImportForTest` caller list,
matching the source read. `c46ca30` corrects the guide's copy-control line, and its new
scroll/video/click claims are backed by tracked `network-scroll-trace.json`
(`scrollTrace=[0,250,0]`, `maxScroll=250`, `restoredToZero=true`), `network-dom.json`
(`clicked=1`) and `network-result.json` (`frameAck.renderedFrames=3`, `displayed=true`).
