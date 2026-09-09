# AB-05 native connection candidate

Owner: `packages/client-connection/`, root Cargo workspace and `scripts/connection*`.
Android native resources, UI, Relay and Obscura protocol remain separate owners.
The existing direct path remains an explicitly prepaired WSS endpoint. This
candidate also adds an explicitly selected WebRTC H.264/DataChannel backend in
the same `Connection` owner. It does not implement automatic candidate
selection, automatic retries, JNI, phone layout election or native displayed-
frame acknowledgement.

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

## WebRTC direct backend

`Connector::connect_webrtc` uses the same generation fence, bounded action pump,
`Connection` API and `DisplayedFrame` input fence as direct WSS. mTLS WSS opens
the Host attachment, carries the typed SDP offer/answer and keeps the binding
alive. After the v3 capability/binding handshake, browser requests and Host
responses use the single `obscura.control.v1` DataChannel; the client never
replays a failed DataChannel mutation over WSS.

The client requires the negotiated UDP ICE pair, receives the Host H.264 RTP
track through `webrtc-rs`, depacketizes Annex B access units with the existing
H.264 depacketizer, and pairs each access unit with the typed
`WebRtcVideoFrame` descriptor by exact RTP timestamp. The descriptor's source
identity, encoder identity, PTS and revisions are preserved; missing or
duplicate descriptors/samples fail a bounded association buffer instead of
guessing a frame. A DataChannel or signaling failure closes the whole
connection and an accepted operation whose response is lost is `OutcomeUnknown`.
The shared `next_video` backend contract is cancellation-safe: the action pump
may insert a status or mutation while media receive is pending without
consuming a partial frame. Connection shutdown explicitly closes the WebRTC
peer and signaling socket; a cancelled setup schedules the same peer close.

`WebRtcConfig::bind_ip` selects the local UDP interface. The default is loopback
for isolated local tests; LAN/Tailscale callers must pass the live interface
address. WebRTC protocol version 3 is a hard capability check; the old v2
descriptor shape is rejected rather than adapted.

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

The direct suite covers framing/continuity, injected response loss/correlation
errors and a real Host/media/endpoint consumer. The WebRTC consumer uses this
library to attach over mTLS, complete SDP/UDP ICE, receive and decode several
RTP H.264 access units, observe the typed descriptor revisions, perform
takeover, navigate, click, Chinese input and scroll over DataChannel, reject an
old displayed frame, release, and reconnect with the Host paused. Its negative
cases must keep stale binding, stale revision and disconnected/unknown outcomes
explicit; WSS input is not a substitute for the DataChannel path.

The generated artifact includes the native rlib and its exact linked acceptance
consumers. Admission executes copied consumers without recompiling them. The
direct consumer covers Observe denial, takeover, stale displayed revision
rejection, click DOM effect, release and reconnect with paused Host.
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
control/media tunnel pair. A Host can instead consume an offer with
`reject_offer(offer, RelayRejectReason::{UnknownPeer, Capacity})`; the method
waits for Relay's matching `tunnel.closed` receipt before reporting success. A
client opens a tunnel with an explicit `hostId`+`sessionId`; an offer that does
not match either identity is rejected.
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

`Connector::connect_relay(...)` is the public client entrypoint. It does not
return a `Connection` until the secure tunnel has received Obscura `Ready`
version 4 for the requested session and a matching `Attach { mode: Observe }`
status. `RelayBackend` retains both the `RelayConnection` and the
`SecureRelayTunnel` for the lifetime of the shared `Connection` pump; dropping
the Relay connection would drop its generation source and fence the secure
channels. Once attached, status, takeover, click, text, scroll and release use
the existing typed action pump, and media uses the existing `decode_video`
path. Relay ticket, peer binding, generation and authentication state never
enter Browser ABI payloads or metadata. A later explicit connect advances the
same `Connector` generation and closes the old Relay connection.

`RelayHostConnection` and `apps/relay-host/` are thin network adapters. The
Host adapter probes the existing Obscura endpoint through typed `Ready` and
`Status`, publishes that session projection, refreshes the complete projection
before its Relay TTL expires, decrypts a secure Relay tunnel, and forwards
control text/media binary frames unchanged to the existing Obscura `/control`
and `/media` endpoints. Relay snapshot revision is owned by the adapter and is
independent of Obscura document revision. It does not read `host.sock`, run
Browser operations, implement navigation, arbitrate Session/control state, or
recreate the Browser ABI. Those remain Obscura endpoint/Host responsibilities.

The Host adapter accepts a typed authorized peer set. The CLI takes one
`--peer-bindings` JSON file; the root is a non-empty array and unknown fields,
empty IDs, duplicate device IDs, empty public keys, and empty certificate pins
are rejected. Each offer selects one binding by its authenticated Relay
`peerDeviceId`; the shared connection owner then verifies the matching inner
TLS certificate pin and signed `TunnelHello`:

```json
[
  {
    "relay_device_id": "registered-device-id",
    "auth_public_key": "64 hex characters",
    "certificate_sha256": "64 hex characters"
  }
]
```

Relay Host keeps one global heartbeat/control supervisor and admits at most
eight independent forwarding sessions. An individual offer's authentication,
handshake, or endpoint failure ends only that session and retains its typed
diagnostic; Relay control or heartbeat failure ends the adapter after awaiting
heartbeat and session cleanup. Obscura remains the sole owner of attachment,
Session, operation, and control arbitration.

HTTPS and outer WSS require the configured CA, reject plaintext/redirects, and
expose bounded connection and request failures. No automatic retry, silent
downgrade, or fallback is introduced. Wrong peer/device keys, same-CA but
unpinned certificates, role/channel/tunnel/session mismatches, replayed or
expired offers, and mixed control/media tickets fail explicitly. Host-side
`tunnel.reject` accepts only `UNKNOWN_PEER` or `CAPACITY` while the offer is
pending and owned by that authenticated Host. Relay closes only that tunnel
and returns `HOST_REJECTED_UNKNOWN_PEER` or `HOST_REJECTED_CAPACITY` to the
requester as a typed 409 result; unknown, expired, foreign-Host, and already
established offers remain explicit errors.

The connection build stages `relay-acceptance` beside
`relay-connection-acceptance` and `connection-acceptance`.
`relay-acceptance` is the low-level Relay service/tunnel consumer; it is not
public `Connection` proof. `relay-connection-acceptance` is compiled from
`packages/client-connection/tests/relay_connection.rs` and runs the public
`Connector::connect_relay` entrypoint against a real Relay fixture, real
`agentbrowser-relay-host`, and real Obscura endpoint. It covers directory
publication, wrong session and peer binding rejection, `Ready → Observe
Attach → media`, H.264 delivery, observe/takeover/input/release, and
generation-fenced reconnect. The native Relay test also proves Host rejection
receipts for both reasons, requester typed failures, pending/active fencing,
foreign-Host rejection, and a later isolated tunnel. Admission executes both
exact compiled Relay
consumers, with the relay-host binary and Obscura binaries supplied as hashed
external inputs.
generation-fenced reconnect. The native Relay test also proves Host rejection
receipts for both reasons, requester typed failures, pending/active fencing,
foreign-Host rejection, and a later isolated tunnel. Admission executes the
exact compiled consumers for the real Host path, public Relay `Connection`
path, and the HTTPS/WSS Relay fixture, with the relay-host binary and Obscura
binaries supplied as hashed external inputs.
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

The explicit public client replay is ignored for the same reason and requires
the same Node/tsx fixture plus a separately built relay-host adapter:

```sh
export OBSCURA_PROTOCOL_ROOT=/path/to/obscura/protocol/browser
export OBSCURA_BIN_DIR=/path/to/obscura/target/release
export AGENTBROWSER_RELAY_HOST_BIN="$PWD/target/debug/agentbrowser-relay-host"
npm --prefix services/relay ci
CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR="$PWD/target" \
  cargo build -p agentbrowser-android --example device_fixture \
  --config "patch.crates-io.obscura-host-protocol.path=\"$OBSCURA_PROTOCOL_ROOT\""
CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR="$PWD/target" \
  cargo build -p agentbrowser-relay-host \
  --config "patch.crates-io.obscura-host-protocol.path=\"$OBSCURA_PROTOCOL_ROOT\""
CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR="$PWD/target" \
  cargo test -p agentbrowser-connection --test relay_connection \
  --config "patch.crates-io.obscura-host-protocol.path=\"$OBSCURA_PROTOCOL_ROOT\"" \
  -- --ignored --nocapture --test-threads=1
```

This replay proves the public Relay `Connection` path only for the supplied
local binaries and fixture. It does not prove an installed client, platform
login UI, production Relay, or the separate relay-host module's own delivery
gates.

The isolation case registers two distinct Relay devices with independent
device identities and inner client certificates. It offers an unauthorized
peer while the first tunnel is active, then admits the second authorized peer
without interrupting the first. Both tunnels receive media concurrently; an
operation from the observing second attachment receives the typed
`CONTROL_REQUIRED` Host error while the first attachment owns control; after
the second tunnel closes, the first still performs an operation and receives a
new media sequence. An unauthorized offer is rejected through
`RelayHostConnection::reject_offer` with `RelayRejectReason::UnknownPeer`;
capacity admission uses `RelayRejectReason::Capacity`. The Host waits for the
matching `tunnel.closed` receipt before continuing, and the requester receives
the typed `HOST_REJECTED_UNKNOWN_PEER` or `HOST_REJECTED_CAPACITY` 409 error. A
rejected offer cannot tear down an established tunnel; rejection or receipt
failure is reported as a Relay control error and terminates the Host supervisor
explicitly.
