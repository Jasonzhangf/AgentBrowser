# Mac native client slice

Owner: `apps/macos/` and `scripts/build-macos*.{sh,mjs}`. The shared Cordis
bundle and `packages/client-connection/` remain their existing owners. The
Obscura Browser ABI is consumed from the explicitly selected read-only
`OBSCURA_PROTOCOL_ROOT`; no protocol source is copied into this repository.

## Runtime shape

`AgentBrowserMac` is an AppKit shell. Its right pane loads the existing
`packages/ui-plugins/main.tsx` bundle in `WKWebView`; the WebView receives only
typed JSON snapshots through the synchronous `ProbeNative.request` adapter.
The adapter uses a native `window.prompt` delegate because the existing probe
port is synchronous. It rejects a missing native bridge explicitly.

`AgentBrowserMacBridge` is a child process built from
`apps/macos/connection-bridge/`. It owns pairing-file reads and an instance of
the shared Rust `agentbrowser-connection` kernel. Control commands are bounded
JSON. H.264 Annex B access units use a separate length-framed stdout channel;
the encoded bytes never enter Cordis, the WebView, request payloads or
metadata. The AppKit left pane converts Annex B to length-prefixed NAL units,
decodes with VideoToolbox, paints the returned `CVPixelBuffer`, then sends the
ticketed display acknowledgement. Until that acknowledgement, the Rust
helper does not expose the frame as an input basis.

Host control truth remains the typed `SessionStatus` and `DisplayedFrame` from
`client-connection`. The Mac bridge does not recreate Host state, attach a
second browser, select a route, or retry a mutation. A failed/unknown mutation
is returned as an explicit rejection. `connect` always attaches in Host
observation mode; takeover and release remain Host operations.

## Protocol binding

The browser protocol input for this slice is the clean, read-only Obscura
candidate at `/Volumes/extension/code/AgentBrowser/playground/m1-obscura-combined-build-20260908`.
The binding is reproducible from these Git identities:

| Object | Git identity |
| --- | --- |
| Obscura candidate commit | `fc0bc1fdf9a494d0edaff4068ade15b7446a4271` |
| Obscura candidate tree | `b1bc9c9c92c7bec45e000d2ebae97bd5582cd95a` |
| `protocol/browser` commit | `b0d6eaa72fa713b845c07726a84a486e6be97db6` |
| `protocol/browser` tree | `820b576a81a69d13252d3a1c782fc627e50dcad1` |
| `protocol/browser/Cargo.toml` blob | `81d38b19a6cc9467337f4591a0928ff6a7d49089` |
| `protocol/browser/src/lib.rs` blob | `25c1803c159e1fc9b6d6cd45c152c1293a5c7ee8` |

This binding includes the typed `VideoPacket::EncoderUnavailable` terminal
marker. The Mac media consumer must surface that marker as an explicit encoder
failure and stop the media attempt; `VideoPacket::Unavailable` remains the
recoverable Host capture/page availability marker. The two cases must not be
collapsed into a successful startup or silently retried path.

## Pairing and build

Pairing is a private directory containing `endpoint.txt`, `ca.der`,
`client.der`, and mode-0600 `key.der`. The helper reads it only through the
`AGENTBROWSER_MAC_PAIRING` environment variable. The app's `--pairing-dir`
argument sets that variable for its child. Credentials are not sent to JS or
written into snapshots.

Build the app bundle with the checked-out Browser ABI owner and this worktree's
Cargo target:

```sh
OBSCURA_PROTOCOL_ROOT=/Volumes/extension/code/obscura/playground/m1-obscura-host-candidate-20260910/protocol/browser \
  scripts/build-macos.sh build
```

The script uses `CARGO_BUILD_JOBS=2`, AppKit/WebKit/VideoToolbox from the
installed macOS SDK, and emits `apps/macos/build/AgentBrowserMac.app`. It does
not install a system app or restart a daemon; direct bundle launch is the
module's local entrypoint. For AppSDK consumers, the same build also emits the
complete bundle as `generated/modules/macos-shell/lib/AgentBrowserMac.app.zip`.

```sh
OBSCURA_PROTOCOL_ROOT=/Volumes/extension/code/obscura/playground/m1-obscura-host-candidate-20260910/protocol/browser \
  scripts/build-macos.sh run --pairing-dir /private/path/to/pairing
```

## Loopback verification

Use the existing local `device_fixture` only to supply an isolated Host,
endpoint and ephemeral certificates. Build the fixture in this worktree and
run it on `127.0.0.1` with Obscura binaries already validated by the Obscura
owner:

```sh
OBSCURA_PROTOCOL_ROOT=/Volumes/extension/code/obscura/playground/m1-obscura-host-candidate-20260910/protocol/browser \
  cargo build --release --locked -p agentbrowser-android --example device_fixture \
  --config "patch.crates-io.obscura-host-protocol.path=\"$OBSCURA_PROTOCOL_ROOT\""
OBSCURA_BIN_DIR=/Volumes/extension/code/obscura/playground/m1-obscura-host-candidate-20260910/target/release \
OBSCURA_ENDPOINT_BIND_IP=127.0.0.1 \
  target/release/examples/device_fixture
```

Pass its printed fixture directory to `--pairing-dir`, and close that fixture
through its own `quit` stdin command. Do not use `m1-form/target`, a shared
Host, Android device, or shared Cargo target.

The bridge maps the shared UI `navigate` command directly to
`Connection::navigate`; it does not create a second Host/navigation owner.
Navigation returns the updated typed `SessionStatus`, and input remains
blocked until a frame with the new document revision is displayed and
acknowledged.

The minimum Mac evidence records separately:

1. Swift compile and Rust helper build against the exact protocol path and
   candidate commit.
2. AppKit bundle starts and the existing Cordis UI loads.
3. Loopback `connect` enters observe mode, receives a real Annex B access
   unit, decodes it through VideoToolbox, paints a frame, and acknowledges the
   ticket.
4. After takeover, `navigate` uses `Connection::navigate`, then a typed
   click/text/scroll command uses the newly acknowledged frame's
   document/viewport revisions; stale or unacknowledged input is rejected.
5. Chinese composition is committed as one input operation; composition
   cancellation does not send partial text.
6. Disconnect releases the native connection without stopping the Host, and a
   fresh connect creates a new generation instead of replaying old input.

Formal AppSDK admission uses `scripts/macos-admission.mjs` on a committed owner
candidate. It compiles the pinned module and its connection dependency, copies
the exact zip into a private `/tmp` installation, launches only that extracted
bundle, and records separate install and exact-PID restart receipts. The same
installed entrypoint is driven through AppKit accessibility actions and native
scroll events: connect, receive and display H.264, take over, navigate, click,
enter text, scroll, release, inspect the Host only after release, disconnect,
reconnect, then restart and reconnect again. Screenshots, fixture DOM evidence,
process identities, artifact hashes, and raw command logs remain under the
ignored task-local `evidence/` directory. The acceptance fixture and app
processes are owned by the adapter; cleanup terminates only those exact PIDs.

This is a Mac/client vertical slice. It is not Android+Mac same-session proof,
Relay proof, route-selection proof, final user/global package installation, or
full M1 acceptance. Those remain integration-owner work.
