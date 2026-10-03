# W6 connection input development notes

Run: `m1-connection-input-w6-r1-20261003`
Author: GCM implementation author (not controller/reviewer)
Worktree: `/Volumes/Intel/playground/agentbrowser/m1-connection-input-w6-r1-20261003`
Branch: `codex/m1-connection-input-w6-r1-20261003`

## Bindings

| Item | Value |
| --- | --- |
| base source SHA | `bc9d67ad037be2c7119bee692f9ad362417ffd05` |
| base tree | `4ee512388b4a6687a7a620ca7b0a6309af1b6993` |
| protocol root | `/Volumes/Intel/playground/obscura/m1-remote-editing-w2-r2-20261003/protocol/browser` |
| protocol source SHA256 | `e161714435610c73a89ed6c5391c0ff57d0f4427379466219ff0a55713cd6f4e` |
| protocol versions | Host10 / WebRTC9 |
| runtime bin dir | `/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release` |
| runtime host SHA256 | `e4d3aa1d07c353ce7169d87d2f658f3cc269cb9a67219c6592dd1565cc20bfa7` |
| runtime endpoint SHA256 | `f47f1add5b5bcacfb2fe8d132dfecbcfe36c56fbf45745e5215b579b8e79d193` |
| runtime worker SHA256 | `120bc191e004c91c6746f881517466a7f34eb136318ac925ddd745ec1e61d73f` |
| runtime media SHA256 | `64e715c188ab885d205858856af161846254ac475d9b472db6b260cea4c0034f` |
| cargo target lease | `<worktree>/.w6-target` |
| relay-host fixture bin | `<worktree>/.w6-target/release/agentbrowser-relay-host` |
| relay-host fixture SHA256 | `076606cd1aab045421b61defdc694ef89d6dbe0a209f9f876c0acc8120fec8e8` |

## Node log

### 2026-10-03 context binding

- Read global `~/.agents/AGENTS.md`, project `AGENTS.md`, controller
  `w6-connection.contract.md`, dispatch `w6-connection-r1.dispatch.md`.
- Reused original W1 graphs (`m1-editing`, `m1-connection`, `m1-delivery`) from
  `m1-repair-model-20261002/docs/dagpipe/graphs`. Current task maps to existing
  nodes `agentbrowser.transport.deliver_edit` and
  `agentbrowser.native.bind_edit_context`; no new graph.
- Evidence: this file. Next: red build.

### 2026-10-03 red build (first incompatibility)

- Command: `cargo build --locked --release -p agentbrowser-relay-host
  --config 'patch.crates-io.obscura-host-protocol.path="<protocol root>"'`.
- Result: FAIL, exit 101.
  `error[E0063]: missing field operation_id in initializer of Operation`
  at `packages/client-connection/src/transport.rs:649` and `:692`.
- Evidence: `docs/runs/m1-connection-input-w6-r1-20261003/red-build.log`.
- Next: implement Host10 typed input API.

### 2026-10-03 runtime protocol probe (read-only)

- The runtime binaries' `.d` files point at source root
  `m1-remote-editing-w2-20261002`; its `protocol/browser/src/lib.rs` is byte
  identical to the bound protocol root (SHA256 `e1617144...`).
- Probed the live Host unix socket with a read-only connect:
  `{"type":"ready","version":10,...}`. Runtime Host emits version 10, so the
  client version gate must move from the legacy literal 4 to
  `HOST_PROTOCOL_VERSION`.
- Next: land typed input + version gate, then run public consumers.

### 2026-10-03 implementation and green consumers

- Public API added in `packages/client-connection`: `PresentedFrame`,
  `EditingInput`, `EditingOutcome`, and `Connection::editing`. Host10
  `Operation.operation_id` is populated for legacy Navigate/Input operations
  and for typed editing; `establish_control` and the Relay backend now gate on
  `HOST_PROTOCOL_VERSION` (10) instead of the legacy literal 4.
- Lease semantics: one `operation_id` pins one Host `sequence` for the whole
  gesture/composition, continuation events keep that sequence, a fresh
  operation reads `next_sequence` from Host status, and a successful terminal
  receipt stays cached so an identical replay returns the cached receipt.
- `Failure::Host` refusals surface unchanged (`CONTROL_REQUIRED`,
  `STALE_CONTROL`, `STALE_EDITOR_TARGET`, `STALE_EDITOR_REVISION`,
  `INVALID_UTF16_RANGE`, `OPERATION_EXPIRED`, `OPERATION_BUSY`); a lost
  mutation reply is `Failure::OutcomeUnknown`, marks the connection fatal, and
  is never replayed. `ResultValue` variants other than `Input`/`Editing` are
  rejected as `Failure::Protocol`, never coerced into success.
- Real public consumer `packages/client-connection/tests/endpoint.rs::
  real_host_typed_editing_lease_and_fencing` runs the real Host/endpoint/media
  binaries over mTLS. It asserts actual DOM value/selection/text and
  `beforeinput` side effects via read-only `Evaluate`; all mutations go
  through the public connection input API. Protocol-layer coverage: BB03
  observer injection is refused, BB05 takeover waits for the open lease,
  BB06 typed gesture focus/EditorContext and contenteditable,
  BB07 composition begin/update/commit/cancel plus cached replay,
  BB09 replace/delete/selection, BB10 stale epoch/target/revision rejection,
  BB13 disconnect cancels the lease and a new generation rejects the old
  frame. System IME and user product layer remain NOT_EXECUTED.
- Relay-host fixture built against the bound protocol root; exit 0.
  Evidence: `docs/runs/m1-connection-input-w6-r1-20261003/relay-host-build.log`.
- Official test entry
  `python3 scripts/connection.py test --protocol-root <bound>`: exit 0,
  `22 tests run: 22 passed, 1 skipped`, no leaky marks.
  Evidence: `docs/runs/m1-connection-input-w6-r1-20261003/green-test.log`.
- Official build entry
  `python3 scripts/connection.py build --protocol-root <bound>`: exit 0.
  Derived artifacts under `generated/modules/client-connection/lib/`
  (gitignored, not committed):
  `libagentbrowser_connection.rlib` `7a04463dbb89ee61449170708e858b08e11d900db80fcac597f99c3744cfefd6`,
  `connection-acceptance` `e77c4435398b774984b5d99c71e86c43209e246cffc8379adc673fa7841f9fa2`,
  `webrtc-acceptance` `7a42cadafb572e900f36a1b1e764f6b71b7d2cc549424870ade209427185f46e`,
  `relay-acceptance` `a0e3e89dc3ba3a9bd4d89a24087a4699611d6795298345e6890412a51de4c76c`,
  `relay-connection-acceptance` `348748bc1bea86a8181563caf37126765b4e98192e1e06b6ce3dc4ceaeedfdc9`.
  Evidence: `docs/runs/m1-connection-input-w6-r1-20261003/green-build.log`.
- Resource state: test fixtures use only `/tmp/ac-*` roots and per-run
  Host/endpoint PIDs with `kill_on_drop`; no leftover `ac-*`, `wmp-*`, or
  task-owned `obscura-*` process remains. `.w6-target` and
  `services/relay/node_modules` are task-owned/gitignored and are retained for
  controller acceptance; the worktree and target are handed to the controller
  for later closure.
