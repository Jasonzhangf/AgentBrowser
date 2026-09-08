# Local direct replay

`replay.py` is a bounded evidence runner for the local path described by the
architecture contract:

```text
Host <-> endpoint       Unix sockets: <fixture-root>/host/host.sock and
                         <fixture-root>/endpoint/encoded.sock
Agent <-> endpoint      loopback WSS: wss://127.0.0.1:<port> (or [::1])
Mac UI <-> native       stdin/stdout process pipe (bridge mode), or an
                         externally driven AppKit surface (appkit mode)
```

The runner consumes real executables. It does not compile or implement the
Browser ABI, start a Relay, copy protocol types, write AppSDK records, or turn
configuration and signaling into a product pass. A non-loopback or non-WSS
fixture endpoint fails with `RELAY_ENDPOINT_REJECTED`.

## Commands

`build-fixture.sh` is the build entrypoint for the real Rust fixture. It runs
the exact workspace command `cargo build --example device_fixture -p
agentbrowser-android`, writes compiler output to stderr, and prints only the
resulting executable path to stdout. Set `LOCAL_DIRECT_MANIFEST_PATH` or
`LOCAL_DIRECT_TARGET_DIR` when the Android bridge workspace uses a non-default
location. A missing workspace, source file, or output executable is an explicit
failure; the wrapper never creates a substitute fixture.

Build the fixture and pass its path to the runner:

```sh
fixture_bin=$(scripts/local-direct/build-fixture.sh)
python3 scripts/local-direct/replay.py \
  --fixture-bin "$fixture_bin" \
  --obscura-bin-dir /path/to/obscura/target/release \
  --agent-bin /path/to/AgentBrowserMacBridge
```

The `origin/main` design baseline currently has no Cargo workspace or Android
bridge package, so this wrapper reports that missing manifest until the
Android bridge package owner supplies those inputs. That failure is retained
as build evidence rather than treated as a fixture pass.

Bridge mode drives the framed `AgentBrowserMacBridge` process and checks the
direct connection, an acknowledged Annex B frame, click/input/scroll response
snapshots, Host fixture state, disconnect, reconnect, and page-state
retention:

```sh
python3 scripts/local-direct/replay.py \
  --mode bridge \
  --fixture-bin /path/to/device_fixture \
  --obscura-bin-dir /path/to/obscura/target/release \
  --agent-bin /path/to/AgentBrowserMacBridge
```

The fixture must emit one JSON ready record on stdout with non-empty
`fixture`, `endpoint`, and `session` fields. The current fixture shape also
provides `endpoint.txt`, `ca.der`, `client.der`, and `key.der` under the root;
the runner checks that `endpoint.txt` exactly matches the ready endpoint and
that the private key is not group/world readable. The fixture's `status`,
`inspect`, and `quit` commands are used only through its stdin/stdout pipe.

AppKit mode launches `AgentBrowserMac` and a separate UI driver. The driver
receives `AGENTBROWSER_LOCAL_DIRECT_CONTEXT`,
`AGENTBROWSER_LOCAL_DIRECT_EVIDENCE_DIR`, `AGENTBROWSER_MAC_PAIRING`,
`AGENTBROWSER_LOCAL_DIRECT_ENDPOINT`, and
`AGENTBROWSER_LOCAL_DIRECT_AGENT_PID`. It must emit one JSON object per line.
The minimum event stream is:

```json
{"event":"ui_ready","surface":"appkit"}
{"event":"connected","endpoint":"wss://127.0.0.1:12345","transport":"WSS","network_path":"local"}
{"event":"video_displayed","displayed":true,"frames":1}
{"event":"operation_receipt","operation":"click","operation_id":"op-click","outcome":"applied"}
{"event":"operation_receipt","operation":"input_text","operation_id":"op-text","outcome":"completed"}
{"event":"operation_receipt","operation":"scroll","operation_id":"op-scroll","outcome":"applied"}
{"event":"inspect","clicked":1,"text":"你好，Mac 输入","scrollY":560,"maxScroll":560}
{"event":"disconnected"}
{"event":"reconnected","endpoint":"wss://127.0.0.1:12345","transport":"WSS","network_path":"local"}
{"event":"done"}
```

`connected` and `reconnected` must identify the exact endpoint supplied by
the fixture. An operation receipt needs `operation`, `operation_id`, and an
`outcome` of `applied`, `completed`, or `success`. The inspect event must
contain the changed click count, expected text, and positive scroll extent.
Extra driver arguments are passed with repeatable `--ui-driver-arg` options;
the agent and fixture have corresponding `--agent-arg` and `--fixture-arg`
options. AppKit mode remains incomplete unless the real UI driver emits all
required events.

## Evidence and exit status

Each run writes a single `evidence.json` and a driver context file to a unique
temporary directory by default:

```text
${TMPDIR:-/tmp}/agentbrowser-local-direct/<run-id>/
```

Use `--evidence-dir` to select another temporary or Git-ignored directory.
The runner refuses an unignored directory inside the candidate worktree. The
evidence includes candidate commit/tree/worktree, executable hashes and PIDs,
fixture identity, daemon observations, all three path descriptions, ordered
stages, first failure with owner and next action, raw child output, proven
claims, unknown claims, and cleanup/root state. Saved PIDs are checked against
their executable command before a targeted SIGTERM; SIGKILL is attempted only
after the same identity check. The fixture's `quit` command is attempted first.

Exit `0` means the selected replay completed. Bridge mode reports
`result=bridge_transport_pass` and explicitly leaves the AppKit surface as
unknown; it is not an AppKit UI admission. AppKit mode reports `result=pass`
only after the complete event stream succeeds. Missing entrypoints, relay
paths, malformed framing, missing receipts, changed page state, and cleanup
failures are non-zero and preserve the first failure in `evidence.json`.
