# AB-05 direct connection candidate

Owner: `packages/client-connection/`, root Cargo workspace and `scripts/connection*`.
Android native resources, UI, Relay and Obscura protocol remain separate owners.
This first slice implements an explicitly prepaired direct WSS endpoint. It does
not implement candidate selection, UDP, relay routing, automatic retries, JNI,
phone layout election or native displayed-frame acknowledgement.

## Runtime contract

- `Connector` advances generation before every explicit connection attempt.
  Failure of a new attempt also invalidates old sockets. Dropping Connector or
  Connection ends its connections; the independent Host persists.
- `Pairing` supplies endpoint origin, CA DER, client certificate DER and PKCS8
  key. TLS verifies server name and client authentication; no insecure mode.
- Control attach validates Host v3 and Observe identity. Media uses the single
  use token returned by that control handshake. Connected means both handshakes
  completed, not that a native decoder has displayed a frame.
- One actor serializes control, one media future consumes bounded messages.
  Work queue capacity is one; media watch retains only the latest complete AU.
  Any transport/protocol failure ends both sockets. Accepted mutations with a
  missing or uncorrelated response produce `OutcomeUnknown`, never retry.
- Host errors remain typed errors. Observe/takeover/release state comes from
  Host. Input sequence comes from current Host status; document/viewport
  revisions come from the actually displayed frame, epoch from observed control.
  The platform must only create DisplayedFrame after native frame acknowledgement.
- Framing rejects headers over 4095 bytes, AU over 4MiB, inconsistent raw/coded
  dimensions, revision regression, session mismatch and encoder replacement.
  Native Android currently limits AU to 1MiB; the future bridge must explicitly
  reject larger packets, never truncate. Codec content is validated by decoder.

## Reproduce

Set `OBSCURA_PROTOCOL_ROOT` to the Obscura owner's `protocol/browser` directory
and `OBSCURA_BIN_DIR` to its validated release binaries (Host, endpoint, media).
The protocol is an explicit Cargo development patch, not a published dependency.
No protocol definitions are copied here. Freeze must replace this mutable local
binding with an actual immutable source/version; no milestone is admitted yet.

```sh
python3 scripts/connection.py test
python3 scripts/connection.py build
node scripts/connection-admission.mjs
```

Six tests cover framing/continuity, injected response loss/correlation errors and
a real Host/media/endpoint consumer: Observe denial, takeover, stale displayed
revision rejection, click DOM effect, release and reconnect with paused Host.
The generated artifact includes the native rlib and its exact linked acceptance
consumer; admission executes that copied consumer without recompiling it.
It is a development candidate, not a portable distributable Rust SDK.
Library deployment requires neither installing a service nor restarting one.
Tests start only their own temporary Host/endpoint and clean their fixtures.

Android local-file decoding acceptance is a separate candidate. Network frames
displayed by 15T, mobile input, resize/rotation and complete reconnect UX remain
whole-flow integration work before baseline, milestone and memory rebuild.
