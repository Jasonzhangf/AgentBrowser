# Task-19 Mac client wire handshake

Status: **CLOSED — valid client error-reporting fix; ordinary Mac connection remains UNKNOWN/FAIL.**

## Raw first frame

Temporary instrumentation in `packages/client-connection/src/transport.rs:675-690` captured this first server `/control` WebSocket frame:

```json
{"type":"error","id":1,"code":"REMOTE_INITIAL_PROFILE_ACCESS_DENIED","message":"Profile default does not grant this device"}
```

The frame is valid Host control JSON. The previous generic client error (`Protocol: Expected Host protocol version 11`) was misleading: the Host did not send a mismatched Ready version. It sent an authorization error.

## Owner and fix

Both client and Host declare protocol version 11. The endpoint handled the TLS/WebSocket request and delegated remote admission to the Host. The unique defect was client handling: the handshake matcher treated every non-Ready response, including `Response::Error`, as a protocol-version mismatch. The minimal fix preserves the Ready version assertion and maps `Response::Error` to `Host {code}: {message}`.

The Mac snapshot fix remains in commit `552c53e`; explicit Host error handling is in commit `98d7b8f`.

## Authorization retry

The task6 lifecycle catalog was updated through the official service command to grant device `e22b76b6-31ac-487b-9953-13e0d78daec8` membership in profile `default`. Receipt: `evidence/user-slice/mac-client-connect-20261010T1820/task6-membership-grant.json`, catalog revision `32`.

The post-grant ordinary-app run did not produce a session. Endpoint logs show repeated `endpoint connection ended: tls handshake eof` and no identifiable new Mac session. The app's AX tree exposed no connection/status nodes during the 24-second observation, so the in-app state could not be observed. This is an observability gap: the ordinary app exposes no usable status surface for this path. The first failing gate is therefore **UNKNOWN/FAIL, not PASS**. No HTTPS/profile/restart claims are made.

## Gate table

| Gate | Result | Raw evidence |
| --- | --- | --- |
| Pairing source and credential load | PASS | `98d7b8f`; pairing files loaded from `AGENTBROWSER_MAC_PAIRING` |
| UI `连接` to native `connect` mapping | PASS | `main.tsx:542`; `main.swift:2320-2336`; bridge `main.rs:500-515` |
| Protocol version agreement | PASS | Both sides declare `HOST_PROTOCOL_VERSION=11` |
| Host error surfaced accurately | PASS | Raw `REMOTE_INITIAL_PROFILE_ACCESS_DENIED` frame; fix `98d7b8f` |
| Default-profile grant | PASS | lifecycle admin receipt, catalog revision 32 |
| Ordinary Mac client session after grant | **UNKNOWN/FAIL — first failing gate** | `mac-client-connect-20261010T1845/ordinary-app-poll.txt`; endpoint TLS EOF lines; no identifiable new session |
| In-app connection observability | **FAIL (product quality)** | AX tree exposed no connection/status nodes during the 24-second run |
| Real HTTPS page | NOT REACHED | blocked by first failing gate |
| Durable cookie/localStorage state | NOT REACHED | blocked by first failing gate |
| Default profile isolation | NOT REACHED | blocked by first failing gate |
| Formal Host restart | NOT RUN | blocked by first failing gate |
| Reconnect and state readback | NOT REACHED | blocked by first failing gate |

## Endpoint identity

Running endpoint PID `67508`:

```text
/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/target/release/obscura-endpoint
```

SHA-256:

```text
2f1f1ec76b18f5ca023fccebb2017db922026d30d047cac2c68d2d3658ac6a03
```

## Final resource state

Both lifecycle instances were stopped through the project entrypoint after evidence capture.

| Service | Stop result | Verification |
| --- | --- | --- |
| `com.agentbrowser.lan` | PASS, stopped | `evidence/user-slice/mac-client-connect-20261010T1845/lan-stop/stop.json`; status state `stopped`, pid/endpoint_pid null; `/tmp/abn-lan-runtime/state.json` absent; launchd job absent |
| `com.agentbrowser.task6` | PASS, stopped | `evidence/user-slice/mac-client-connect-20261010T1845/task6-stop/stop.json`; status state `stopped`, pid/endpoint_pid null; `/tmp/abn-task6-runtime/state.json` absent; launchd job absent |

Only task6 was used for Mac-client work. LAN evidence remained unchanged until the final stop instruction. No bundle, transfer code, credential, or identifying cookie value is recorded.
