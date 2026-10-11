# Task-19 Mac client wire handshake

Status: **INCOMPLETE — initial profile authorization was the first runtime gate; explicit error handling fixed, reconnect proof pending.**

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

The post-grant native retry did not produce a completed session response before the diagnostic process ended. Endpoint logs show accepted connections and existing media activity, but no durable new Mac session evidence was captured. Therefore the exact first failing gate after the grant is **unknown / reconnect evidence pending**, and no HTTPS/profile/restart claims are made.

## Endpoint identity

Running endpoint PID `67508`:

```text
/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/target/release/obscura-endpoint
```

SHA-256:

```text
2f1f1ec76b18f5ca023fccebb2017db922026d30d047cac2c68d2d3658ac6a03
```

## Scope

Only task6 was used. LAN remained running and untouched. No bundle, transfer code, credential, or identifying cookie value is recorded.
