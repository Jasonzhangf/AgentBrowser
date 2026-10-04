# Mac Native Layout Receipt

Task: `m1-mac-native-layout-r1-20261004`
Status: `SOURCE_BUILT` / `PUBLIC_CONSUMER_PARTIAL` / `INSTALLED_E2E_WAIT`
Input: HEAD `a31c1d9379d0710814315492955291a753ea0004` / tree
`710a973dd2bb0c0c20b9193691198770ab6a2e50` (not committed; candidate source tree below).

## Scope and candidate

Allowed source files only:

- `apps/macos/Sources/main.swift`
- `apps/macos/connection-bridge/src/main.rs` (presentation binding)
- `scripts/build-macos.sh` (configured-target source repair, proven by an observed failure)
- New `docs/runs/m1-mac-native-layout-r1-20261004/{notes,receipt,raw,artifacts}`

No other tracked path is modified. The protected mainline checkout is untouched; all work is
in this leased worktree. No install, no Host, no network/auth/TCC/FDA, no second target/cache.

## What changed

`apps/macos/Sources/main.swift`

1. `NSSplitViewController` dual 320pt panel removed; one `BrowserContentView` (flipped `NSView`)
   now hosts `VideoSurfaceView` as a direct subview plus the single `WKWebView`.
2. Container rules: `VideoSurfaceView.frame == presentedContentRect`; `webRect` is the full
   `drawableWindowRect` for empty/error/no-valid-matching-displayed-frame and for overlay;
   `webRect == chromeRect` (thin 56pt top bar) only while browsing native video. Overlay puts
   the WebView above the video and hides the video. Drawer safe edges come from the frozen UI
   `safeInsets` computation over the typed rects; `safeContentRect` is the drawable content on
   Mac (no system bars / soft keyboard), so the drawer is not artificially inset.
3. `advertisedViewportRect == presentedContentRect` in AppKit points.
   `updateViewport()` reads `advertisedViewportRect` and no longer multiplies
   `backingScaleFactor`, no longer derives from `videoView.bounds`; it does not advertise when
   no session owns the viewport, and the fence is cleared on disconnect so reconnect re-advertises.
4. `set_presentation` is intercepted on the main thread in
   `runJavaScriptTextInputPanelWithPrompt`; it applies the layout and returns
   `{id, op:'set_presentation', status:'applied', geometry:{webRect, revision}}` or the typed
   `{id, op:'set_presentation', status:'rejected', reason:'GEOMETRY_UNAVAILABLE'}`. Every other
   bridge command is still forwarded synchronously so `responseTransform` only reads AppKit
   state on the main thread.
5. `visibleContent`/`geometryMatches` are computed from the displayed frame identity
   (generation/session/tab/document/viewport) plus the local geometry match only. `inputReady`,
   `controlMode`, pending operations and `connected` alone never decide video visibility.
   Mouse/scroll for the native surface are gated on browsing + native video; the overlay hides
   the video so overlay clicks cannot become remote input. `pageCoordinates`/`viewPoint` and
   `firstRect(forCharacterRange:)` keep the single content-local fit transform. UTF-16
   composition/receipt/focus/epoch handling is unchanged.

`apps/macos/connection-bridge/src/main.rs`

1. Completed the presentation identity binding introduced by `a31c1d9`: `presented_frame` now
   supplies the required `tab_id`/`presentation_revision` (HEAD did not compile without this).
2. Snapshot projection adds `activeTabId`, `viewportPending`, `displayedGeneration`,
   `displayedSessionId`, `displayedTabId` for the native presentation owner. `displayedGeneration`
   is the app-level (client) generation so it matches snapshot `generation` for the Swift fence.

`scripts/build-macos.sh`

1. Bridge copy source now mirrors cargo's `CARGO_TARGET_DIR` (default `$root_dir/target`),
   fixing the copy step when a configured target is used. Default behavior is preserved.

## Actual commands and exits

Environment for every command:

```
export OBSCURA_PROTOCOL_ROOT=/Volumes/Intel/playground/obscura/m1-profile-navigation-impl-r1-20261003/protocol/browser
export CARGO_TARGET_DIR=/Volumes/Intel/playground/agentbrowser/m1-mac-native-input-r2-20261003/target
export CARGO_INCREMENTAL=0
export CARGO_BUILD_JOBS=2
```

| Step | Command | Exit | Evidence |
| --- | --- | --- | --- |
| Swift typecheck | `swiftc -typecheck apps/macos/Sources/main.swift -framework AppKit -framework WebKit -framework VideoToolbox -framework CoreMedia -framework CoreVideo -framework CoreImage -framework QuartzCore` | 0 | `raw/swift-typecheck-2.log` |
| Install deps | `npm ci` | 0 | raw log not retained (17 packages) |
| First build (observed failure) | `bash scripts/build-macos.sh build` | fail (bridge E0063 pre-existing break) | `raw/build-macos-first.log` |
| Second build (observed failure) | `bash scripts/build-macos.sh build` | fail (`cp target/release/...: No such file or directory`) | `raw/build-macos-second.log` |
| Build | `bash scripts/build-macos.sh build` | 0 | `raw/build-macos-third.log` |
| Public consumer | `cargo test --locked --release -p agentbrowser-macos-bridge --config 'patch.crates-io.obscura-host-protocol.path="/Volumes/Intel/playground/obscura/m1-profile-navigation-impl-r1-20261003/protocol/browser"'` | 0 | `raw/cargo-test-macos-bridge.log` |

Public consumer outcomes (existing no-Host consumer, official command interface):
`public_consumer.rs` 6/6 pass (`status_without_session_reports_observe`,
`invalid_selection_direction_is_typed`, `typed_select_without_session_is_explicit`,
`unknown_editing_field_is_rejected`, `invalid_editing_binding_is_typed`,
`typed_editing_command_without_session_is_explicit`); bridge unit tests 11/11 pass. Presentation
geometry success is not asserted here: it needs OS/native authority and a paired Host window.

## Artifacts (candidate source tree; version 0.1.0)

| Artifact | SHA-256 |
| --- | --- |
| `apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMac` (Swift executable) | `d6a3e3fd1eb242320939a8fe27a78c1a741b829819bf026fb65132e9dbe5453f` |
| `apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMacBridge` | `f4baf2388720a40205923f3742b5e615e40173bf0b648ddd8a7963ab5909306b` |
| `generated/modules/macos-shell/lib/AgentBrowserMac.app.zip` | `8f7ff83ad6ceab63a55ea83d1ff39f20af63dd97cbcf35aedab3b0e295f927db` |

Evidence: `raw/artifact-hashes.txt`.

## Remaining acceptance (not reachable in this scope)

- Installed app content/chrome/mouse/system IME behavior, and black-box cases BB01/02/03/05/08/09/10
  remain `WAIT` for a fixed app + paired Host window. The source tree must not be launched ad hoc
  because that would prompt permissions.
- Independent architecture review, merge, push, and OTA are not performed here; the root later
  allocates fixed install, native E2E and independent review.
- `git diff --check a31c1d9..worktree` is clean; only the allowed files are modified.

## Resource ledger

- Preserve for root intake: worktree, source diff, `apps/macos/build/AgentBrowserMac.app`,
  `generated/modules/macos-shell/lib/`, and the leased target cache.
- `node_modules/` (ignored) created by `npm ci` in this worktree; disposable.
- No installed app, no user profile, no shared runtime, no extra target/cache/symlink created.
