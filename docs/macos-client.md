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

## Pairing and build

Pairing is a private directory containing `endpoint.txt`, `ca.der`,
`client.der`, and mode-0600 `key.der`. The helper reads it only through the
`AGENTBROWSER_MAC_PAIRING` environment variable. The app's `--pairing-dir`
argument sets that variable for its child. Credentials are not sent to JS or
written into snapshots.

Build the app bundle with the checked-out Browser ABI owner and this worktree's
Cargo target:

```sh
OBSCURA_PROTOCOL_ROOT=/Volumes/extension/code/AgentBrowser/playground/obscura-fork/playground/m1-form/protocol/browser \
  scripts/build-macos.sh build
```

The script uses `CARGO_BUILD_JOBS=2`, AppKit/WebKit/VideoToolbox from the
installed macOS SDK, and emits `apps/macos/build/AgentBrowserMac.app`. It does
not install a system app or restart a daemon; direct bundle launch is the
module's local entrypoint.

```sh
OBSCURA_PROTOCOL_ROOT=/Volumes/extension/code/AgentBrowser/playground/obscura-fork/playground/m1-form/protocol/browser \
  scripts/build-macos.sh run --pairing-dir /private/path/to/pairing
```

## Loopback verification

Use the existing local `device_fixture` only to supply an isolated Host,
endpoint and ephemeral certificates. Run it on `127.0.0.1` with binaries
already validated by the Obscura owner, pass its printed fixture directory to
`--pairing-dir`, and close that fixture through its own `quit` stdin command.
Do not use a shared Host, Android device, or shared Cargo target.

The minimum Mac evidence records separately:

1. Swift compile and Rust helper build against the exact protocol path and
   candidate commit.
2. AppKit bundle starts and the existing Cordis UI loads.
3. Loopback `connect` enters observe mode, receives a real Annex B access
   unit, decodes it through VideoToolbox, paints a frame, and acknowledges the
   ticket.
4. After takeover, a typed click/text/scroll command uses the acknowledged
   frame's document/viewport revisions; stale or unacknowledged input is
   rejected.
5. Disconnect releases the native connection without stopping the Host.

This is a Mac/client vertical slice. It is not Android+Mac same-session proof,
Relay proof, route-selection proof, package installation proof, or full M1
acceptance. Those remain integration-owner work.
