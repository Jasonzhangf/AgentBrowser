# m1-mac-native-input-r2 (SOURCE_BUILT phase) run notes

Contract: controller `m1-repair-model-20261002/docs/runs/m1-20261002/mac-native-input-r1.contract.md`
(r1 contract inherited; worktree/home/notes re-bound to r2).

Worktree: /Volumes/Intel/playground/agentbrowser/m1-mac-native-input-r2-20261003
Branch: codex/m1-mac-native-input-r2-20261003

## Node 0 - input identity

time 2026-10-03
node git+input identity
conclusion IN_PROGRESS -> confirmed below
evidence: git status/log/rev-parse, patch sha256
inputs:
- HEAD b02415f7b4bfcef865f35d608a741cf62112ab44 / tree 22304910c154ffd3888b6be13f533322189be7c8
- branch codex/m1-mac-native-input-r2-20261003 (ahead 3 of origin/main)
- origin/main fresh base bc9d67ad037be2c7119bee692f9ad362417ffd05
- composed W6 3e5d140 + Chrome 14a8b3b/e174a81
- working tree dirty: only apps/macos/Sources/main.swift (132/0) and
  apps/macos/connection-bridge/src/main.rs (259/8) => 391 insertions / 8 deletions
- partial patch sha256 1d71a3ce79d9921ca875a51efc13dd9c06482afd69386e55e28f57cd4fb02030
  (matches controller identity; NOT re-applied)
- protocol (read-only) /Volumes/Intel/playground/obscura/m1-remote-editing-w2-r2-20261003/protocol/browser
  lib.rs hash e161714435610c73a89ed6c5391c0ff57d0f4427379466219ff0a55713cd6f4e
next: red build to capture actual compile state of partial patch

## Node 1 - debug partial patch (red)

time 2026-10-03
node red build of controller patch
conclusion PARTIAL PATCH BROKEN, root causes found
evidence: logs/red-cargo-test.log, logs/red-swift-typecheck.log
findings:
- Rust: `field` NOT a compile error; cargo test release -p agentbrowser-macos-bridge
  builds ok (bridge + client-connection + protocol). 10/11 pass; the injected
  test `typed_editing_command_reaches_connection_owner` FAILED with
  left Err("DISPLAY_NOT_READY") vs right Err("NETWORK_NOT_CONNECTED") because
  `editing`/`select` arms called `presented_frame(...)?` before checking session.
- Swift: main.swift:390 invalid redeclaration of activeGeneration (dup stored prop).
- connection-bridge/src/main.rs:311 stray indentation de-indented `app.session = Some`.
fixes so far (r2 author):
- Swift: removed dup activeGeneration; added editingOutcome to Snapshot; implemented
  real NSTextInputClient (VideoSurfaceView) with composition begin/update/commit/
  cancel, insertText, doCommandBySelector, setMarkedText/unmarkText, selection,
  UTF16 geometry; wired mouseDown/Dragged/Up gesture lease, scroll/drag share
  pageCoordinates inverse transform leaving display margin non-triggering.
- Rust: editing/select return NETWORK_NOT_CONNECTED before DISPLAY_NOT_READY;
  submit_editing refreshes status after accepted outcome; fixed indentation.
- Rust test: fix expectation to NETWORK_NOT_CONNECTED.
next: green cargo test + full scripts/build-macos.sh build

## Node 2 - semantic debug of the wired editing path (r2 author)

time 2026-10-03
node debug|wiring audit of the partial patch (before build acceptance)
conclusion COMPLETE; defects found and fixed in the authorized files
evidence: apps/macos/Sources/main.swift, apps/macos/connection-bridge/src/main.rs
findings/fixes (all in authorized scope):
- `EditorContext.surrounding` is a relative UTF-16 window (`offset`/`before_length`/
  `after_length`) and password targets carry no plaintext. Local IME state now keeps
  only that window and maps remote offsets through `editValueOffset`; password
  contexts keep an empty local buffer. `localRange` clamps remote ranges into the
  local window instead of truncating mid-offset.
- Termination was conflated with cancellation: `endEditing` sent
  `composition_cancel` even on epoch/session/focus/connection invalidation, which
  could replay old state. Split into local termination vs explicit
  `cancelRemote` while the same editor is authoritative; invalidation drops state
  without emitting a stale event. `applyEditorContext(nil)` also clears the
  retained target/value/selection.
- Transport/Host `{"rejection": ...}` was ignored, so an optimistic marked-text
  commit could look accepted. Added explicit `refused(...)` handling for
  composition commit/cancel, standalone edit and selection that terminates the
  local edit state on refusal.
- Enter now emits a real `key_down` (Enter) so a single-line input does not get a
  forced newline; added caret movement / selection extension / word delete /
  select-all / paste / Escape-cancel using UTF-16-safe stepping.
- IME candidate geometry now reuses the same display transform
  (`viewPoint`) and converts to screen space; no second inverse transform.
next: green cargo tests, Swift typecheck, full build and artifact hashes

## Node 3 - green development tests

time 2026-10-03
node green build/test
conclusion SOURCE_BUILT (development evidence only; no Host/runtime)
evidence:
- logs/green-cargo-test-final.log: cargo test --locked --release -p
  agentbrowser-macos-bridge (with protocol patch) -> EXIT 0; 11 passed / 0 failed
  (unit incl. typed_editing_command_reaches_connection_owner), 6 passed / 0 failed
  (public_consumer real bridge process)
- logs/green-public-consumer.log: public bridge consumer EXIT 0, 6 passed
- logs/green-swift-typecheck.log: swiftc -typecheck main.swift EXIT 0, 0 errors
  (only pre-existing javaScriptEnabled deprecation warning)
- logs/npm-ci.log: npm ci EXIT 0
- logs/build-macos-final.log: bash scripts/build-macos.sh build EXIT 0; produced
  Swift app binary, Rust bridge, UI bundle and zip
artifact identity:
- Swift app: apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMac
  sha256 940908e8db5d68e25058a5149866c8e01df46b1435df1aa7c915e72210499cac
- Rust bridge: apps/macos/build/.../MacOS/AgentBrowserMacBridge and
  target/release/agentbrowser-macos-bridge
  sha256 44f801cc96647e57539fda009883026a83e77aa4310229e7c931dd54c811b8b1
- app zip: generated/modules/macos-shell/lib/AgentBrowserMac.app.zip
  sha256 833c14d22b14c553ee495b078861f17a602b0a8adf8ca8a65db4866f3ebeefa5
- protocol: /Volumes/Intel/playground/obscura/m1-remote-editing-w2-r2-20261003/protocol/browser
  src/lib.rs sha256 e161714435610c73a89ed6c5391c0ff57d0f4427379466219ff0a55713cd6f4e
source anchor: HEAD b02415f7b4bfcef865f35d608a741cf62112ab44 /
tree 22304910c154ffd3888b6be13f533322189be7c8 plus the allowlisted working diff.
next: E2E_WAIT for real Host/runtime lease; BB03-BB08 + BB09/BB10 not executed.
