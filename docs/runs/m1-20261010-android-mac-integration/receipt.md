# M1 Android + Mac integration receipt

Milestone: Android client + Mac client against one owned Obscura r8 Host, over
the local loopback path. This receipt closes the task-1 revision 5 review
findings: correlation stage bookkeeping, an explicit claim-set decision, and a
committed receipt for evidence that lives outside Git.

Result: **partial**. The Android + Mac transport milestone bar is satisfied.
Full M1 and the user-slice gates are **not** complete.

## Candidate identity

| Item | Value |
| --- | --- |
| AgentBrowser branch | `codex/m1-android-navigation-fix-r1-20261009` |
| AgentBrowser commit | `d2dcc1b30a0d755750dc2326e033c2e0bd6abcfb` |
| AgentBrowser tree | `3e4a7668e8e1a29aa9f5ed3ef266b728b8f68f5c` |
| Integrated mainline | merge of `origin/main` `f05eadd` (hook repair + mainline audit evidence) |
| Obscura commit | `0cf48e5a22c57a4059f6045482baaf17cbd79d3a` |
| Obscura tree | `753b6f0874b9545718e9787ad648d1ab80a29fe1` |
| Obscura binaries | `/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/target/release` |
| Environment | `darwin-arm64-emulator-5554`, loopback bind `127.0.0.1` |

Every gate below was run at `d2dcc1b`. This receipt is carried by a later
docs-only commit that adds no code to the measured candidate.

Artifact digests (SHA-256):

| Artifact | Digest |
| --- | --- |
| `target/release/examples/device_fixture` | `88d303c09de1a82770a7df19d278f9609a4301f4a1d40d7140511b41543320a4` |
| `target/release/agentbrowser-macos-bridge` | `760d89f2642557c191fab9b794d0c36d331ff7bb28301384101ba2a5d1cbdc5b` |
| `obscura-host` | `d8e956a22809629b50366a51298d94552284ac7519a06e4ab17c000bde62dd21` |
| `obscura-endpoint` | `2f1f1ec76b18f5ca023fccebb2017db922026d30d047cac2c68d2d3658ac6a03` |
| `obscura-media` | `db01b5805a127bdd21dda540d0d7f29fd583a48c832f78be20a56671a65daee4` |
| `com.agentbrowser.probe` APK (built + installed) | `2d8cd5e24d11edf42dc980158bb2b6e89a9323e5d39040e7d8983335a40ea426` |
| `com.agentbrowser.probe.test` APK (built + installed) | `2e535ebed2ba29dc8061dbd77d79358aaf40557b9b64a5c4f52d41585a9e76cc` |

The APK artifact hash equals the installed hash for both packages, so the
deployed entrypoint is the built artifact of this candidate.

## Gates rerun at this candidate

| Gate | Command | Result | Evidence |
| --- | --- | --- | --- |
| Repository verification | `npm run verify:local` | PASS (typecheck, 34/34 tsx, build:ui) | commit hook output |
| Dual runner unit tests | `python3 scripts/test-m1-dual-client-runner.py` | PASS 23/23 | `scripts/test-m1-dual-client-runner.py` |
| Android network replay | `python3 scripts/network-replay.py` | **PASS** | `evidence/network/a9742364ea114174998c51688e0fdeba/` |
| Mac bridge replay | `python3 scripts/local-direct/replay.py --mode bridge` | **bridge_transport_pass** | `evidence/local-direct/replay-integrated-d2dcc1b/evidence.json` |
| Dual client integration | `python3 scripts/m1-dual-client-runner.py` | **partial** (all stages passed) | `/tmp/agentbrowser-m1-dual/dual-r10-20261010T133822/` |

### Android network replay (independent Host DOM evidence)

`network-result.json`: 26/26 boolean flags true. Host DOM evidence
(`network-dom.json`): `clicked=1`, `text="中文native-network-proof"`,
`scrollY=0`, `maxScroll=250`; session `07039cff-61d7-4c4c-8b6d-27252f524b80`
matches the client status. `rotationPreservesState=true`, `frameAck`
`{ticket: 143, renderedFrames: 2, displayed: true}`.

This run replaces the earlier `f7818cfc…` evidence, which predates commit
`455b48b` and therefore no longer describes the current Android test source.

### Mac bridge replay

Stages: `preflight`, `fixture_start`, `socket_validation`, `agent_start`,
`connected`, `video_transport`, `atomic_operations`, `disconnect`, `reconnect`,
`cleanup` all passed. Proved: fixture ready record, Host socket plus endpoint
socket directory readiness with the exact-session encoded socket asserted after
first admission, agent process identity, loopback WSS connection with
`source=network`, length-framed Annex B access unit with a completed transport
acknowledgement, click/input/scroll snapshots, disconnect, reconnect, and page
state retention.

Unknown by design in this mode: `daemon_process_identity` (`ps` exposed no
Obscura child row; socket evidence remains structural), `appkit_video_displayed`
(bridge mode does not decode or paint Annex B with VideoToolbox), and
`appkit_ui_surface` (no AppKit window replay).

### Dual client integration

All nine stages passed: `preflight`, `fixture_start`, `android_install`,
`mac_connect`, `android_replay`, `mac_control`, `mac_reconnect`, `correlation`,
`cleanup`. `android-instrumentation.log` ends with `OK (2 tests)`
(`testImeCompositionCancel`, `testRealNetworkControlAndFrames`) and
`android-result.json` has 26/26 boolean flags true with `runId` bound to this
run.

## Claim-set decision

The run writes an explicit `claim_set_decision` (`evidence.json`,
`correlation.json`): `milestone = android-mac-transport`,
`decision = transport_milestone_bar_satisfied`.

In-bar claims, all proved: `same_session`,
`three_side_viewport_revision`, `three_side_document_revision`,
`host_android_control_epoch_at_android_completion` (host `16` = Android `16`
at observation point `android_complete`), `mac_observe_takeover_release`,
`android_observe_takeover_release` (derived from the exported typed receipts),
`disconnect_reconnect`.

Excluded claims, each still `unknown` with its raw reason, never reported as
pass:

| Claim | Raw reason |
| --- | --- |
| `viewport_and_source_dimensions` | Host SessionStatus emits viewport but no encoded source dimensions |
| `three_side_source_dimensions` | `source_dimensions` is not emitted by every side (Host missing) |
| `three_side_frame_ack` | only Android emits a native display acknowledgement; the runner instantiates neither AppKit nor VideoToolbox |
| `rotation` | Android `rotationPreservesState=true`; the Mac bridge has no rotation command, so orientation identity is unprovable there |

The decision cannot hide a failure: any `failed` claim, excluded or not, makes
the decision `failed`. The overall run result stays `partial` while any claim
is unproved.

## Scope extension (review finding)

The review extended task-1 scope to `RemoteImeView.java`,
`scripts/m1-dual-client-runner.py`, and `scripts/test-m1-dual-client-runner.py`.
Changes at this candidate:

- `scripts/m1-dual-client-runner.py`: correlation is recorded through
  `stage_start`/`stage_finish` instead of staying `not_run`; the explicit
  claim-set decision is written; the cross-side control epoch is compared only
  at a shared observation point (`same_point_claim`), with raw skew preserved;
  both operation-receipt claims derive from exported typed receipts
  (`operation_receipts_claim`); the fixture pairing bundle digest is hashed and
  passed as `-e bundleSha256`; helper imports no longer leave `__pycache__`
  entries that dirty the candidate.
- `apps/android/app/src/androidTest/java/com/agentbrowser/probe/NetworkDeviceTest.java`:
  exports `completionStatus`, the status observed at client completion, so the
  epoch claim has a shared observation point. No assertion was weakened,
  skipped, or removed.
- `apps/android/app/src/main/java/com/agentbrowser/probe/RemoteImeView.java`:
  keeps the real `inputConnectionReady` / `showInputWhenReady` fix; the
  diagnostic trace was removed.

## Known failure and residual risk

`dual-r9-20261010T133721` failed at `android_replay` with
`ANDROID_INSTRUMENTATION_FAILED`:

```text
junit.framework.AssertionFailedError: Condition: document.querySelector('main').dataset.drawerOpen==='false'
  at com.agentbrowser.probe.NetworkDeviceTest.until(NetworkDeviceTest.java:54)
  at com.agentbrowser.probe.NetworkDeviceTest.testRealNetworkControlAndFrames(NetworkDeviceTest.java:854)
Tests run: 2,  Failures: 1,  Errors: 0
```

The drawer opened (the `drawerOpen==='true'` gate at line 850 passed) but the
real `drawer-close` touch did not close it within the 6 s window. That run
executed the Mac bridge replay concurrently on the same host; `dual-r10` ran the
same gate alone at the same candidate and passed, and the Android network replay
passed the same gate minutes earlier at the same source. The leading explanation
is host CPU contention stretching the touch-to-DOM path past the 6 s window; the
contention was self-inflicted by running the two gates together. This is not
proved, so the residual risk stands: the dual runner must not share the host with
another replay, and a drawer-close timeout must be reported as a failure, not
retried away.

## Limitations

- Bridge mode proves transport flow control only. No AppKit surface, no
  VideoToolbox decode, and no native display were exercised.
- The Host SessionStatus does not emit encoded source dimensions, so a
  three-side source-dimension claim is impossible with this Host.
- No three-side native display acknowledgement exists: the Host and the Mac
  bridge do not produce one.
- The Mac bridge has no rotation command, so orientation identity is Android
  only.
- Relay, Tailscale, LAN, public-network, installed-product admission, and the
  remaining user-slice gates are separate evidence layers and are untouched by
  this receipt.
- Full M1 is **incomplete**. Nothing here may be reported as a full M1 pass.

## Evidence retention

`/evidence/` is ignored by Git, so the raw evidence is retained on disk. This
paragraph is a retention map of paths outside Git; none of these paths is
force-added, and two of them are `/tmp/` paths that can never be tracked.

The two Git-ignored evidence directories live only in the sibling worktree
`/Volumes/Intel/playground/agentbrowser/m1-android-navigation-fix-r1-20261009`:

- `/Volumes/Intel/playground/agentbrowser/m1-android-navigation-fix-r1-20261009/evidence/network/a9742364ea114174998c51688e0fdeba/`
- `/Volumes/Intel/playground/agentbrowser/m1-android-navigation-fix-r1-20261009/evidence/local-direct/replay-integrated-d2dcc1b/` (candidate `d2dcc1b` is already an ancestor of the integration HEAD)

The two dual-client runs are transient `/tmp` paths:

- `/tmp/agentbrowser-m1-dual/dual-r10-20261010T133822/` (passing run)
- `/tmp/agentbrowser-m1-dual/dual-r9-20261010T133721/` (failing run, preserved)

Earlier dual runs `dual-r5`, `dual-r6`, `dual-r7`, `dual-r8` are also retained
under `/tmp/agentbrowser-m1-dual/`. No credentials, private keys, or bundle
contents are recorded here.

## Reproduce

```sh
export ANDROID_SERIAL=emulator-5554
export OBSCURA_PROTOCOL_ROOT=/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/protocol/browser
export OBSCURA_BIN_DIR=/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/target/release
export OBSCURA_ENDPOINT_BIND_IP=127.0.0.1

npm run verify:local
python3 scripts/test-m1-dual-client-runner.py
python3 scripts/network-replay.py
python3 scripts/local-direct/replay.py --mode bridge \
  --fixture-bin target/release/examples/device_fixture \
  --obscura-bin-dir "$OBSCURA_BIN_DIR" \
  --agent-bin target/release/agentbrowser-macos-bridge \
  --evidence-dir evidence/local-direct/replay-integrated-d2dcc1b \
  --run-id m1-r8-mac-bridge-integrated-d2dcc1b
python3 scripts/m1-dual-client-runner.py \
  --fixture-bin target/release/examples/device_fixture \
  --obscura-bin-dir "$OBSCURA_BIN_DIR" \
  --mac-bridge-bin target/release/agentbrowser-macos-bridge \
  --android-serial "$ANDROID_SERIAL" \
  --agent-commit d2dcc1b30a0d755750dc2326e033c2e0bd6abcfb \
  --agent-tree 3e4a7668e8e1a29aa9f5ed3ef266b728b8f68f5c \
  --obscura-commit 0cf48e5a22c57a4059f6045482baaf17cbd79d3a \
  --obscura-tree 753b6f0874b9545718e9787ad648d1ab80a29fe1 \
  --run-id dual-r10-20261010T133822 \
  --evidence-dir /tmp/agentbrowser-m1-dual/dual-r10-20261010T133822
```
