# AB-05 native connection candidate

Owner: `packages/client-connection/`, root Cargo workspace and `scripts/connection*`.
Android native resources, UI, Relay and Obscura protocol remain separate owners.
This first slice implements an explicitly prepaired direct WSS endpoint. It does
not implement candidate selection, UDP, automatic retries, JNI,
phone layout election or native displayed-frame acknowledgement.

## Runtime contract

- `Connector` advances generation before every explicit connection attempt.
  Failure of a new attempt also invalidates old sockets. Dropping Connector or
  Connection ends its connections; the independent Host persists.
- `Pairing` supplies endpoint origin, CA DER, client certificate DER and PKCS8
  key. TLS verifies server name and client authentication; no insecure mode.
- Control attach validates Host v4 and Observe identity. Media uses the single
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
npm --prefix services/relay ci
python3 scripts/connection.py test
python3 scripts/connection.py build
node scripts/connection-admission.mjs
```

Six tests cover framing/continuity, injected response loss/correlation errors and
a real Host/media/endpoint consumer: Observe denial, takeover, stale displayed
revision rejection, click DOM effect, release and reconnect with paused Host.
The generated artifact includes the native rlib and its exact linked acceptance
consumer; admission executes that copied consumer without recompiling it.
The admission adapter compiles only `client-connection` through AppSDK's
`compile-module` entrypoint. It does not build downstream Android during this
module's review admission. Project contracts and the complete pre-review evidence
gate still run; Android retains its declared dependency and separate admission.
It is a development candidate, not a portable distributable Rust SDK.
Library deployment requires neither installing a service nor restarting one.
Tests start only their own temporary Host/endpoint and clean their fixtures.

Android local-file decoding acceptance is a separate candidate. Network frames
displayed by 15T, mobile input, resize/rotation and complete reconnect UX remain
whole-flow integration work before baseline, milestone and memory rebuild.

## Relay v2 client and Host adapter

`relay` is an additional transport adapter within the same native connection
owner. The existing direct `Connector`, media framing and Obscura Browser ABI
remain unchanged. Relay v1 is explicitly rejected; there is no v1/v2 fallback.
Relay v2 semantics are owned by `protocol/relay`; private Rust wire types
validate that protocol, not a second browser protocol.

`RelayClient` logs in, registers an Ed25519 device, reads the account-scoped
directory and revokes its token. `RelayConnector` supports both client and Host
control roles. A Host registers a Host object, publishes a complete
session-bound `HostSnapshot`, receives side `1` offers and accepts the same
control/media tunnel pair. A client opens a tunnel with an explicit
`hostId`+`sessionId`; an offer that does not match either identity is rejected.
The generation source is retained by each connection, so a connection made from
a temporary connector still owns its fencing lifetime; a newer generation,
revocation or connection drop closes old channels.

Relay v2 uses separate authorized control/media WSS tunnels and no Relay
transport certificate or peer public key in the offer. `peerDeviceId` is only a
claim that must match the local `RelayPeerBinding`; it is not TOFU. Every
control/media channel is then wrapped in inner rustls mutual TLS. The client
pins the Host certificate and the Host pins the client certificate to the
locally stored binding. After TLS, both sides exchange a signed `TunnelHello`
whose transcript binds the TLS exporter, role, channel, tunnel ID, Host ID,
session ID, local/peer device IDs and both certificate fingerprints. The Relay
only sees outer TLS and inner TLS ciphertext; it never verifies or interprets
the Browser ABI.

`RelayHostConnection` and `apps/relay-host/` are thin network adapters. The
Host adapter probes the existing Obscura endpoint through typed `Ready` and
`Status`, publishes that session projection, refreshes the complete projection
before its Relay TTL expires, decrypts a secure Relay tunnel, and forwards
control text/media binary frames unchanged to the existing Obscura `/control`
and `/media` endpoints. Relay snapshot revision is owned by the adapter and is
independent of Obscura document revision. It does not read `host.sock`, run
Browser operations, implement navigation, arbitrate Session/control state, or
recreate the Browser ABI. Those remain Obscura endpoint/Host responsibilities.

HTTPS and outer WSS require the configured CA, reject plaintext/redirects, and
expose bounded connection and request failures. No automatic retry, silent
downgrade, or fallback is introduced. Wrong peer/device keys, same-CA but
unpinned certificates, role/channel/tunnel/session mismatches, replayed or
expired offers, and mixed control/media tickets fail explicitly.

The connection build stages `relay-acceptance` beside `connection-acceptance`.
Admission executes both exact compiled consumers: the existing real Host path
and a real HTTPS/WSS Relay fixture covering wrong CA, account isolation,
directory, Host publication, side-1 offers, separate binary channels, inner
mTLS/TunnelHello, token revocation and generation fencing.
Relay service sources, protocol and dependency lock are hashed as test inputs.
The fixture requires the local Node/tsx runtime; these development consumers
are not portable deployment artifacts. Client platform login UI, direct-path
selection and full installed product replay remain integration work before
complete M1 network acceptance. The Host adapter CLI is a thin bridge, not
proof that an installed client has completed the end-to-end Browser flow.

The explicit real adapter replay is `apps/relay-host/tests/relay-host-replay.rs`
and is ignored by ordinary Cargo runs because it starts external binaries. Run
it only with validated Obscura binaries:

```sh
OBSCURA_PROTOCOL_ROOT=/path/to/obscura/protocol/browser
cargo build -p agentbrowser-android --example device_fixture \
  --config "patch.crates-io.obscura-host-protocol.path=\"$OBSCURA_PROTOCOL_ROOT\""
OBSCURA_BIN_DIR=/path/to/obscura/target/release \
  cargo test -p agentbrowser-relay-host --test relay-host-replay \
  --config "patch.crates-io.obscura-host-protocol.path=\"$OBSCURA_PROTOCOL_ROOT\"" \
  -- --ignored --nocapture --test-threads=1
```

The replay starts the real Relay fixture, Host/endpoint/media fixture, Relay
Host adapter and secure client. It proves the `Ready → Attach → media` order,
Attach response, takeover, click receipt and a later media sequence. The
endpoint's remote policy rejects `Evaluate`; this replay therefore does not
claim DOM inspection or pixel-difference proof, nor does it prove installed
product delivery.
