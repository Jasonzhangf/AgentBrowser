# Mac Native Layout Implementation Notes

Task: `m1-mac-native-layout-r1-20261004`
Scope: macOS content container, native presentation, AppKit-point viewport, bridge presentation projection.

## Node 0 - baseline and admission

Time: 2026-10-04
Node: input identity and design admission
Conclusion: Worktree is clean at `a31c1d9379d0710814315492955291a753ea0004`; frozen design r5 and UI r3 are ancestors. Existing Mac source still uses `NSSplitViewController`, and the bridge snapshot lacks native displayed tab identity.
Evidence: `git status --short --branch`, `git rev-parse HEAD`, frozen design docs, source inspection.
Input: HEAD `a31c1d9379d0710814315492955291a753ea0004`; protocol root `/Volumes/Intel/playground/obscura/m1-profile-navigation-impl-r1-20261003/protocol/browser`; leased target `/Volumes/Intel/playground/agentbrowser/m1-mac-native-input-r2-20261003/target`.
Next: implement the native layout owner and minimal bridge projection.

## Node 1 - source implementation (Mac owner)

Time: 2026-10-04
Node: native content container + presentation binding
Conclusion: `NSSplitViewController` replaced by one `BrowserContentView`; video fills
`presentedContentRect`; WKWebView is full content for empty/error/no valid matching
displayed frame, thin `chromeRect` while browsing native video, and full drawable for
overlay. `set_presentation` is handled on the main thread and returns the frozen typed
`applied`/`rejected` result. `advertisedViewportRect` is AppKit points and `updateViewport`
never multiplies `backingScaleFactor`. Visibility is owned by the displayed frame identity
plus `geometryMatches`, not `inputReady`/`controlMode`/pending operations.
Evidence: `apps/macos/Sources/main.swift`, `apps/macos/connection-bridge/src/main.rs`,
`raw/swift-typecheck-2.log` (exit 0).
Input: HEAD `a31c1d9379d0710814315492955291a753ea0004`.
Next: build and public-consumer exercise.

## Node 2 - pre-existing bridge compile break (in-scope owner repair)

Time: 2026-10-04
Node: bridge presentation identity binding
Conclusion: HEAD did not compile the macos bridge: `a31c1d9` added `tab_id`/`presentation_revision`
to `DisplayedFrame`/`PresentedFrame` and set them in `acknowledge_frame`, but not in
`presented_frame`. Completed the presentation identity binding by populating both fields from
the displayed frame. Also projected `activeTabId`/`viewportPending`/`displayedGeneration`/
`displayedSessionId`/`displayedTabId` in the snapshot; `displayedGeneration` is the app-level
(client) generation that fences the displayed media so it matches snapshot `generation`
(the stored `frame.generation` is the transport generation used only for input fencing).
Evidence: `raw/build-macos-first.log` (E0063), `git show a31c1d9 -- apps/macos/connection-bridge/src/main.rs`.
Input: HEAD `a31c1d9`; error `missing fields presentation_revision and tab_id`.
Next: build with configured target.

## Node 3 - configured-target build owner repair

Time: 2026-10-04
Node: scripts/build-macos.sh target source
Conclusion: With `CARGO_TARGET_DIR` set, the script copied the bridge from the hardcoded
`target/release` and failed (`cp: target/release/agentbrowser-macos-bridge: No such file or
directory`). Minimal owner repair mirrors cargo's `CARGO_TARGET_DIR` default (absolute or
relative to the repo root) so the bundle copies the bridge the build actually wrote, while the
unset case keeps `$root_dir/target`.
Evidence: `raw/build-macos-second.log` (observed failure), `raw/build-macos-third.log` (exit 0).
Input: `CARGO_TARGET_DIR=/Volumes/Intel/playground/agentbrowser/m1-mac-native-input-r2-20261003/target`.
Next: artifact hashes and public-consumer evidence.

## Node 4 - build, artifacts and public consumer

Time: 2026-10-04
Node: SOURCE_BUILT / PUBLIC_CONSUMER_PARTIAL
Conclusion: `npm ci`, `bash scripts/build-macos.sh build` and
`cargo test --locked --release -p agentbrowser-macos-bridge ...` all exit 0. Bridge unit
tests 11/11 and public-consumer tests 6/6 pass. The app bundle and zip were produced from the
leased target. Native-window and remote E2E are not reachable in this scope.
Evidence: `raw/build-macos-third.log`, `raw/cargo-test-macos-bridge.log`, `raw/artifact-hashes.txt`.
Input: HEAD `a31c1d9` + scoped diff.
Next: record receipt and stop before installed E2E (root allocates fixed install/review).

## Resource ledger

- Worktree: `/Volumes/Intel/playground/agentbrowser/m1-mac-native-layout-r1-20261004` (owned; preserve for root intake).
- Leased Rust target: `/Volumes/Intel/playground/agentbrowser/m1-mac-native-input-r2-20261003/target` (shared leased cache; do not remove).
- Generated app/zip: `apps/macos/build/AgentBrowserMac.app`, `generated/modules/macos-shell/lib/AgentBrowserMac.app.zip` (owned outputs; preserve as evidence until root intake).
- `node_modules/` created by `npm ci` in this worktree (ignored; owned disposable).
- Temporary files: `docs/runs/m1-mac-native-layout-r1-20261004/raw` and `artifacts` only.
