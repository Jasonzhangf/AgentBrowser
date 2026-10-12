# Mac client connection gate (task-19 history, task-21 closure)

Status: **CLOSED — PASS on the ordinary Mac client connection gate.**
The gate now runs through a fail-loud harness, the app exposes a black-box
connection status surface, the client reaches `connected` against the task6
lifecycle Host, the app and the Host agree on one session id, and the client
reconnects to that same session after a restart.

The Mac half of BB10 is proven (Mac attaches to, and observes, the Host session).
The Android half ("the phone sees the same session") is owned by the Android
slice and was not run here.

## Harness (task-21 requirement 1)

Entrypoint: `scripts/mac-connect-gate.mjs`.

The previous Mac-gate attempt was an ad-hoc shell chain. Its trailing `&`
backgrounded the whole `cd`/variable chain, so the real gate never executed and
the writes landed on `/mac.pid`, `/ax-before.txt`, `/mac-gate.txt` and the
read-only filesystem. That run produced a receipt for work it never did.

The committed harness removes that class of failure:

- No shell pipeline. Each step is a checked child process, and a step records a
  receipt only after its own success is verified.
- The first run of the harness exposed a second, identical failure mode inside
  it: the AX reader used `kAXWebAreaRole`, which does not exist in the Swift
  bindings. The compiler error was silently mapped to "no status surface", which
  would have been reported as a product failure. The reader now raises a hard
  `AX_READER` failure on any non-zero exit and dumps the AX tree, so a broken
  probe can never masquerade as a product verdict.
- A run ends with `result: pass` or with the first failing gate, the raw detail,
  and `exit 1`. It never writes a pass receipt after a skipped step.

## Observability (task-21 requirement 2)

The ordinary app already exposed the surface added by commit `1160488`
(`feat: expose Mac connection status surface`), and it is readable by an
ordinary AX observer without a private bridge:

```text
AXStaticText title= desc= value=Connection: idle | Transport: wss | Path: direct
```

The harness reads exactly this node. `evidence/r2-mac-client/20261012T001311/ax-before.txt`
records the pre-connect value; `steps.json` records the same string.

## Timestamped series (task-21 requirement 3)

Run `evidence/r2-mac-client/20261012T001311`:

| Time (UTC) | Surface |
| --- | --- |
| 00:13:2x | `Connection: idle \| Transport: wss \| Path: direct` |
| after AX `打开菜单` then `连接` | `Connection: connected \| Transport: wss \| Path: direct` |

The connected value is stable for the whole observation window
(`status-series.json`), and `app.stderr` shows `"renderedFrames":124` with zero
media errors.

## First failing gate and its fix (task-21 requirement 4)

The first harness run that actually executed (`20261012T000202`) failed at the
connect gate with this exact user-visible line:

```text
Connection: error | Transport: wss | Path: direct | Error: Host media unavailable: Frames require a render-enabled Host
```

The raw client trace for that run shows the connection itself succeeded:

```text
MAC_NATIVE_SEND op=connect raw={"op":"connect"}
TASK19_RAW_CONTROL_FRAME text={"type":"ready","version":11,"session_id":"094c0a65-2212-4b84-a6f5-174164e5eb20"}
TASK19_RAW_CONTROL_FRAME text={"type":"result","id":1,"value":{...,"attachment_id":27,"mode":"observe","attachments":1,...}}
```

So the TLS/WebSocket admission, the protocol-11 Ready frame, and the observe
attachment all worked. The unique owner of the failure was the deployed Host
binary: it had been built without the optional `render` feature, and
`crates/obscura-host/src/worker.rs:350` returns that exact string from the
`#[cfg(not(feature = "render"))]` arm of `capture()`.

`docs/host-service.md:541-547` requires a render-enabled Host
(`cargo build --release -p obscura-host --features render`). The fix is a
deployment fix, not a source change: the task6 service was re-bound through the
official lifecycle entrypoint to the render-enabled Host built from the frozen
Obscura candidate commit `35b58be5cde26582ce6a6281157a88719faf4217`.

```sh
scripts/host-service/host-service install --root "$ROOT" \
  --binary /private/tmp/obscura-host-render-target/release/obscura-host \
  --protocol-version 11 --runtime-root /tmp/abn-task6-runtime \
  --profile-root /private/tmp/abn-task6-profile \
  --service-label com.agentbrowser.task6 --profile-id default --allow-private-network
scripts/host-service/host-service start --root "$ROOT"
```

The endpoint and media binaries are derived from the configured Host binary's
directory (`host_service.py:2905-2918`), so the whole task6 media chain moved to
the render-enabled build together. No AB source, no `sdk.lock`, and no
protocol-version assertion was changed or bypassed.

| Binary | Before (non-render) | After (render-enabled) |
| --- | --- | --- |
| `obscura-host` | `7cc7a2e346c6ccf738edd18e7bdb28d1fd34d274170952e92f9c959b985d3853` | `ff04b2489cbb88bf07c4b95b639e60ef2eb734298198ddfebc449b39dd4e801e` |
| `obscura-endpoint` | shared target release | `831e9d18f1ab30ecaed54aec38c0209e6eb43d30371f41f54e6aa0d196d00bbb` |
| `obscura-media` | shared target release | `cec73e3990e71381e9148a4f5552be0cfac5737e0197bb41f82eb9b1524281d2` |

## Session proof (task-21 requirement 5)

Run `evidence/r2-mac-client/20261012T001311` passes every gate:

| Gate | Result | Raw evidence |
| --- | --- | --- |
| Harness executes every step | PASS | `steps.json`; no skipped step |
| AX status surface visible | PASS | `Connection: idle \| Transport: wss \| Path: direct` |
| Visible UI connect | PASS | AX `打开菜单`, then AX `连接` |
| Connected state | PASS | `Connection: connected \| Transport: wss \| Path: direct` |
| Host-side session | PASS | lifecycle `status` session_id `45a7a43a-96cb-4659-86c9-3026dfd222b7` |
| App observes the same session | PASS | app trace sessionId equals the Host session id (`session-identity.json`) |
| Reconnect | PASS | restarted app (pid 8406 -> new pid) reattached to the same session and reached `connected` |
| Host refusal surfaces its real code | PASS | `negative-authorization.json` |

## Host refusal code (98d7b8f behaviour)

An already-enrolled device with no profile membership (`diagnostic-unused`,
`fc3ea254-e663-4bbe-8b40-50713e70d729`) was driven through the same visible UI.
The raw Host frame:

```json
{"type":"error","id":1,"code":"REMOTE_PROFILE_SCOPE_REQUIRED","message":"Remote principal lacks observe_human_control"}
```

Surfaced to the user as:

```text
Connection: error | Transport: wss | Path: direct | Error: Protocol: Host REMOTE_PROFILE_SCOPE_REQUIRED: Remote principal lacks observe_human_control
```

The real code and message reach the user, and the old generic
`Expected Host protocol version 11` message does not appear. The test used only
already-enrolled material and did not change the trust registry; its temporary
pairing copy was deleted at the end of the run.

## Findings left for the owner (not changed here)

1. `packages/client-connection/src/transport.rs:577-579` reports the Host refusal
   as `Failure::Protocol(format!("Host {code}: {message}"))`. The content is
   right, but the user-visible text keeps the `Protocol: ` prefix. The typed
   `Failure::Host { code, message }` variant would surface `Host CODE: message`
   directly. This is a display classification at the handshake; it does not
   change the outcome. It was not changed here because it would move the frozen
   `client-connection` candidate.
2. `packages/client-connection/src/transport.rs:681-690` still carries the
   task-19 `TASK19_RAW_CONTROL_FRAME` `eprintln!` diagnostics, which print every
   control frame to stderr in the product path. They should be removed with the
   module owner.

## Limitations

- The Android half of BB10 was not run. "The Mac observes the same session the
  phone sees" is proven only as: the Mac attaches in observe mode to the Host
  session and both the app trace and the Host report the same session id.
- The gate covers connect, session identity, and reconnect. It does not claim
  media quality, codec behaviour, takeover, navigation, or remote input.
- The render-enabled Host is deployed from `/private/tmp/obscura-host-render-target`,
  the same owner-specific target directory the project documents for render Hosts.

## Final resource state

| Service | Stop result | Verification |
| --- | --- | --- |
| `com.agentbrowser.task6` | PASS, stopped | `evidence/r2-mac-client/20261012T001311/stop.json`, exit 0, state `stopped`; status `state: stopped`, `endpoint_pid: null` |
| `com.agentbrowser.lan` | not touched | owned by the Android slice; no command was issued against that root |

Every app process started by this work was terminated by explicit PID. The
negative-path pairing copy was removed. No bundle, transfer code, credential, or
identifying cookie value is recorded.

## History — task-19

Status at task-19: **CLOSED — valid client error-reporting fix; ordinary Mac
connection remains UNKNOWN/FAIL.**

Raw first frame captured by temporary instrumentation in
`packages/client-connection/src/transport.rs:675-690`:

```json
{"type":"error","id":1,"code":"REMOTE_INITIAL_PROFILE_ACCESS_DENIED","message":"Profile default does not grant this device"}
```

Both sides declare protocol version 11. The endpoint handled the TLS/WebSocket
request and delegated remote admission to the Host. The unique defect was client
handling: the handshake matcher treated every non-Ready response, including
`Response::Error`, as a protocol-version mismatch. Fix `98d7b8f` preserves the
Ready version assertion and maps `Response::Error` to `Host {code}: {message}`.
The Mac snapshot fix remains in `552c53e`.

The task6 catalog was updated through the official service command to grant
device `e22b76b6-31ac-487b-9953-13e0d78daec8` membership in profile `default`
(catalog revision 32, receipt
`evidence/user-slice/mac-client-connect-20261010T1820/task6-membership-grant.json`).

The post-grant run did not produce a session, the endpoint logged repeated
`endpoint connection ended: tls handshake eof`, and the AX tree exposed no
connection/status nodes. That observability gap was closed by `1160488`; the
connection gap was closed by the render-enabled Host deployment recorded above.
