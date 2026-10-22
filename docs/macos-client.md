# Mac native client slice

Owner: `apps/macos/`, `scripts/build-macos*.{sh,mjs}` and
`scripts/mac-user-acceptance.mjs`. The shared Cordis bundle and
`packages/client-connection/` remain their existing owners. The Obscura Browser
ABI is consumed from the explicitly selected read-only `OBSCURA_PROTOCOL_ROOT`;
no protocol source is copied into this repository.

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

## Protocol dependency

The Browser ABI is not vendored in this repository. Use the Obscura fork at
<https://github.com/Jasonzhangf/obscura>. The protocol owner is
`protocol/browser` in that checkout, and the Host binaries come from the same
checkout. Select the revision that the integration owner validated with the
Host and media binaries; do not copy protocol source into this repository.

```sh
OBSCURA_SRC=/absolute/path/to/obscura
# Use an existing checkout, or clone the fork when the path is absent. A Git
# worktree keeps `.git` as a file, so test the checkout, not the directory.
if [ ! -e "$OBSCURA_SRC" ]; then
  git clone https://github.com/Jasonzhangf/obscura.git "$OBSCURA_SRC"
fi
git -C "$OBSCURA_SRC" rev-parse --git-dir >/dev/null
OBSCURA_REV="${OBSCURA_REV:?set OBSCURA_REV to the revision validated by the integration owner}"
git -C "$OBSCURA_SRC" checkout "$OBSCURA_REV"
(cd "$OBSCURA_SRC" && cargo build --release --locked -p obscura-host -p obscura-media)

export OBSCURA_PROTOCOL_ROOT="$OBSCURA_SRC/protocol/browser"
export OBSCURA_BIN_DIR="$OBSCURA_SRC/target/release"
test -f "$OBSCURA_PROTOCOL_ROOT/src/lib.rs"
test -x "$OBSCURA_BIN_DIR/obscura-host"
test -x "$OBSCURA_BIN_DIR/obscura-endpoint"
test -x "$OBSCURA_BIN_DIR/obscura-media"
```

`obscura-endpoint` is produced by the `obscura-host` package. The build and
runtime must use the same checkout and the same `target/release` directory.

The protocol includes the typed `VideoPacket::EncoderUnavailable` terminal
marker. The Mac media consumer must surface that marker as an explicit encoder
failure and stop the media attempt; `VideoPacket::Unavailable` remains the
recoverable Host capture/page availability marker. The two cases must not be
collapsed into a successful startup or silently retried path.

## Pairing and build

Pairing is a private directory containing `endpoint.txt` and a mode-0600
`token`. Those two files are the whole pairing state; the bridge reads nothing
else, and a missing `token` fails as `PAIRING_FILE_MISSING:token`. The token
never enters JS, snapshots, or logs.

The bridge resolves the pairing directory in this order:

1. `AGENTBROWSER_MAC_PAIRING`, when set and non-empty. The app's `--pairing-dir`
   argument sets this variable for its child, so existing harnesses keep the
   same behavior.
2. `$HOME/Library/Application Support/AgentBrowser/mac-pairing`, the default
   store used by an ordinary double-click launch.

The default store is configured when both `endpoint.txt` and `token` are
present. A partial store reports `unconfigured`; the bridge never guesses a
missing file.

### One-time pairing, then a plain launch

Configuration happens once. The human imports one `.abconnection` bundle, and
every later launch connects by itself.

1. Open the app and choose the `连接` menu, then `导入本机配对…`.
2. Select the exported `.abconnection` file in the native open panel.
3. The bridge validates the bundle and writes the store: directory mode `0700`,
   `endpoint.txt` and `token` mode `0600`. Each file is written to a temp file,
   synced, and renamed into place, so no file is ever half written.
4. Close and reopen `AgentBrowserMac.app` with no argument. The app connects to
   the saved Host one time and shows frames.

The import panel is owned by native code. The page may request
`import_connection` or `open_connection_setup`, but native always replaces any
page-supplied path with the human's open-panel selection, so page content can
never choose the imported file. A command with a duplicate top-level key is
refused as `DUPLICATE_COMMAND_FIELD:<key>`, because the page's parser and the
bridge parser would otherwise pick different `op` values.

Import rejects a bundle with a typed error and keeps the previous store:
`BUNDLE_INVALID` (not JSON, or `endpoint`/`token` missing, not a string, empty,
or longer than 4096 bytes), `BUNDLE_PATH_INVALID` (missing `path`, or a path
that is not absolute), `BUNDLE_READ_FAILED` (unreadable path, or not a regular
file), `BUNDLE_TOO_LARGE` (larger than 8192 bytes), `PAIRING_STORE_UNAVAILABLE`
(a store path that exists but is not a directory, a directory that cannot be
created or has no home directory to resolve), and `PAIRING_WRITE_FAILED` (a
failed write or rename). A rejection names the reason only; it never echoes the
token.

Two known limits stay explicit. First, the store is two files, so a process
death between the two renames can leave a new `endpoint.txt` with the old
`token`. That state fails at connect with a Host authentication error, never a
silent success, and one more import repairs it. Second, an interrupted import
can leave a private temp file (`.endpoint.txt.tmp-*` or `.token.tmp-*`, mode
`0600`) in the store; the next import removes stale temps before it writes.

Auto-connect runs at most once per launch and only for a default-store launch:
it needs `connectionSetup.state=configured` and an idle or stopped connection.
An explicit `--pairing-dir` keeps manual control, because it is a test and
developer override; a launch that sets only `AGENTBROWSER_MAC_PAIRING` still
auto-connects, because the bridge sees a configured store. After the first
attempt the human owns every further connect, disconnect, and retry, and a real
connect error stays visible.

The frozen M1 note in section 7.1 of `docs/m1/connection-minimum.md` says an
import performs no local validation and reports one failure code. This bridge
does validate and reports the codes above. The validation was required for this
milestone, so the note is now out of date; updating the frozen M1 document
needs a user decision and is not done here.

Build the app bundle with the selected protocol owner and a writable Swift
module cache:

```sh
ROOT="$(pwd)"
CLANG_MODULE_CACHE_PATH="$ROOT/target/abn-swift-modcache" \
OBSCURA_PROTOCOL_ROOT="$OBSCURA_PROTOCOL_ROOT" \
  scripts/build-macos.sh build
```

`CLANG_MODULE_CACHE_PATH` must point to a writable directory. Without it, Swift
can fail while writing its module cache in a read-only location. The script
uses `CARGO_BUILD_JOBS=2`, AppKit/WebKit/VideoToolbox from the installed macOS
SDK, and emits `apps/macos/build/AgentBrowserMac.app`. It does not install a
system app or restart a daemon; direct bundle launch is the module's local
entrypoint. For AppSDK consumers, the same build also emits the complete bundle
as `generated/modules/macos-shell/lib/AgentBrowserMac.app.zip`.

```sh
CLANG_MODULE_CACHE_PATH="$ROOT/target/abn-swift-modcache" \
OBSCURA_PROTOCOL_ROOT="$OBSCURA_PROTOCOL_ROOT" \
  scripts/build-macos.sh run --pairing-dir "$PAIRING_DIR"
```

## Automated local acceptance

Run this from a real macOS graphical session. The terminal or Codex host that
launches the script needs Accessibility permission for the PID-bound AX
actions. The script also needs Xcode command line tools, Node 22, `cargo`, and
the dependencies from `npm ci`.

```sh
export OBSCURA_SRC=/absolute/path/to/obscura
export OBSCURA_PROTOCOL_ROOT="$OBSCURA_SRC/protocol/browser"
export OBSCURA_BIN_DIR="$OBSCURA_SRC/target/release"
node scripts/mac-user-acceptance.mjs
```

The entry builds the fixture and app, starts the fixture on `127.0.0.1`, launches
the app with its printed `--pairing-dir`, binds AX to that exact app PID, opens
the menu, presses `连接`, waits for `connectionState=connected` and
`renderedFrames>0`, presses `接管`, waits for `controlMode=control` and
`inputReady=true`, checks `visibleContent=native-video`, presses `返回观察`, and
compares a fixture `inspect` with the pre-connect baseline. It then closes the
app and fixture PIDs and writes
`evidence/mac-user-acceptance/<run>/receipt.json`.

`PASS_AUTOMATED` means only the automated path passed. Native click, keyboard,
scroll and Chinese IME are listed in the receipt's `manual` section with status
`PENDING_HUMAN`; they are not PASS. A failed gate records the first failing gate,
its raw evidence, and a nonzero exit code. `node scripts/mac-user-acceptance.mjs
--help` lists the optional receipt, timeout and observation-hold arguments.

`--persisted-pairing` runs the one-time configuration path instead of the
`--pairing-dir` path:

```sh
node scripts/mac-user-acceptance.mjs --persisted-pairing \
  --receipt-dir evidence/mac-persisted-pairing
```

This mode creates a throwaway HOME under `target/persisted-home-<run>`, imports
the fixture bundle into that HOME's default store through the bridge
`import_connection` command, and checks the store modes `0700`/`0600`. It then
launches the app with no pairing argument at all. The gate
`PERSISTED_AUTO_CONNECT` passes only when that launch reaches
`connectionState=connected` with `renderedFrames>0` without any connect press.
The run removes the throwaway HOME and records the result in
`cleanup.scratch_home`. The real user store in the developer home is never
touched.

The script captures both app stdout and stderr because this revision writes
`MAC_NATIVE_SEND`/`MAC_NATIVE_RECV` diagnostics to stderr. Do not bind AX by the
app name: another `AgentBrowserMac` instance may already be running. The script
binds to the PID it spawned.

Cleanup counts as part of the result. The receipt's `cleanup` section records the
app PID, the fixture PID, the fixture's own Host and endpoint child PIDs, and any
survivor. The run reports `FAIL` and exits nonzero when a child stays alive or
when a forced fixture exit leaves the child set unverified. It does not delete the
fixture directory in that case. `node scripts/mac-user-acceptance.mjs --self-test`
runs the cleanup decision cases without starting a Host or an app; it exits
nonzero when a non-graceful exit would be reported as a clean cleanup.

## Manual user path

The automated entry does not synthesize mouse or keyboard events. Use the
following path when you need to verify native input yourself. Run the dependency
setup above first so `OBSCURA_PROTOCOL_ROOT` and `OBSCURA_BIN_DIR` are set.

1. Build the fixture:

   ```sh
   cargo build --release --locked -p agentbrowser-android --example device_fixture \
     --config "patch.crates-io.obscura-host-protocol.path=\"$OBSCURA_PROTOCOL_ROOT\""
   ```

2. Start the isolated Host and keep the terminal open:

   ```sh
   OBSCURA_BIN_DIR="$OBSCURA_BIN_DIR" \
   OBSCURA_ENDPOINT_BIND_IP=127.0.0.1 \
     target/release/examples/device_fixture
   ```

   The first stdout line is JSON. Copy its `fixture` directory; it contains
   `endpoint.txt` and the mode-0600 `token`.

3. Launch the app by executable path, not by application name:

   ```sh
   apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMac \
     --pairing-dir "$FIXTURE_DIR"
   ```

   To verify the persisted path instead, import `$FIXTURE_DIR/pairing.abconnection`
   once through `连接` → `导入本机配对…`, then launch the app with no argument.
   The app connects by itself and you can continue at step 5.

4. In the app, press `打开菜单`, then `连接`. The expected status is
   `connected`, the native video area shows the fixture page, and the diagnostic
   stream reports growing `renderedFrames`.

5. Press `接管`. The expected status is `controlMode=control`,
   `inputReady=true`, and `visibleContent=native-video`.

6. Verify native input in the video area:
   - Click the red `touch` button. It should turn lime.
   - Click the text field and type ASCII text. The field should show the text.
   - Drag down, then scroll back to the top. The page should move and return.
   - Compose Chinese text with the system IME, commit it, then test cancellation.
     The committed text should arrive once; cancellation should send no partial
     text.

7. Press `返回观察`. The Host should return to observation mode; the Agent can
   continue.

8. In the fixture terminal, send `inspect`. It prints JSON with `clicked`,
   `text`, `scrollY`, `maxScroll`, `scrollTrace` and `editorEvents`. If the app
   still holds human control, `inspect` returns `CONTROL_REQUIRED`; press
   `返回观察` first. Before any manual input the page reports `clicked=0`,
   `text=""`, `scrollY=0`, and `maxScroll=0`; after step 6 the same command
   reports the input results.

9. Press `断开` in the app. The expected status is `stopped`; the Host page is
   retained by the Host.

10. Quit the app with `退出 AgentBrowser` or `Command-Q`, then send `quit` to the
    fixture terminal. The fixture closes its endpoint and Host and removes its
    temporary directory.

Do not use a shared Host, Android device, `m1-form/target`, or another worker's
Cargo target for this loopback path. Do not claim native input from the
automated receipt.

The bridge maps the shared UI `navigate` command directly to
`Connection::navigate`; it does not create a second Host/navigation owner.
Navigation returns the updated typed `SessionStatus`, and input remains
blocked until a frame with the new document revision is displayed and
acknowledged.

The minimum Mac evidence records these levels separately:

1. Swift compile and Rust helper build against the selected protocol path.
2. AppKit bundle starts and the existing Cordis UI loads.
3. Loopback `connect` enters observe mode, receives a real Annex B access
   unit, decodes it through VideoToolbox, paints a frame, and reports
   `renderedFrames>0`.
4. Takeover reports `controlMode=control`, `inputReady=true`, and
   `visibleContent=native-video`.
5. Return to observation, then a fixture `inspect` matches the pre-connect
   baseline. This proves the automated path did not modify the page.
6. Native click, text, scroll and Chinese IME are still `PENDING_HUMAN` until a
   person runs the manual path. A pending item is not a PASS.
7. Disconnect is a manual step in this path. The automated receipt does not
   claim it; a fresh connect must create a new generation instead of replaying
   old input.

The installed replay entry is `scripts/macos-admission.mjs` on a committed owner
candidate. It compiles the module and its connection dependency, copies the
compiled zip into a private `/tmp` installation, launches only that extracted
bundle, and records separate install and PID restart receipts. Its scope is
AppKit accessibility `打开菜单` → `连接`, Host session status, and restart;
media/codec, takeover, navigation, remote input, disconnect and reconnect are
outside that entry.

`scripts/mac-user-acceptance.mjs` is the local fixture entry. It covers build,
visible connect, a rendered frame, takeover, release, and the independent DOM
unchanged check. It does not cover native input. `scripts/mac-connect-gate.mjs`
is the lifecycle entry for connect, session status, restart and wrong-token
rejection. These are separate receipts, not one whole-product PASS.

This is a Mac/client vertical slice. It is not Android+Mac same-session proof,
Relay proof, route-selection proof, final user/global package installation, or
full M1 acceptance. Those remain integration-owner work.

The regression gate for this module is the real bridge consumer test,
`cargo test -p agentbrowser-macos-bridge`. The Mac connect gate
`scripts/mac-connect-gate.mjs` is the lifecycle entry that proves connect,
session status, restart and the wrong-token rejection on the installed app.

## Narrow replay scope

The installed replay entry verifies only the ordinary AX menu-open and connect action, Host session status, and restart receipt. It does not claim media or codec coverage; `h264_display` is explicitly skipped. It also explicitly skips takeover, navigation, remote input, disconnect, and reconnect. Those paths require separate real-entry receipts.
