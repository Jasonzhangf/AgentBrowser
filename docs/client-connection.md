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

## Relay v1 client adapter

`relay` is an additional transport adapter within the same native connection
owner. The existing direct `Connector`, media framing and Obscura Browser ABI
remain unchanged. Relay v1 semantics are owned by `protocol/relay`; private
Rust wire types validate that protocol, not a second browser protocol.

`RelayClient` logs in, registers an Ed25519 device, reads the account-scoped
directory and revokes its token. Credentials and signing keys remain in native
memory. `RelayConnector` fences previous connection generations and opens
separate authorized control/media WSS tunnels. HTTPS and WSS require the
configured CA, reject plaintext/redirects, and expose bounded connection and
request failures. No automatic retry or downgrade is introduced. Relay TLS
protects the connection to Relay; this adapter does not claim endpoint-to-
endpoint encryption or interpret browser operations and video bytes.

The connection build stages `relay-acceptance` beside `connection-acceptance`.
Admission executes both exact compiled consumers: the existing real Host path
and a real HTTPS/WSS Relay fixture covering wrong CA, account isolation,
directory, separate binary channels, token revocation and generation fencing.
Relay service sources, protocol and dependency lock are hashed as test inputs.
The fixture requires the local Node/tsx runtime; these development consumers
are not portable deployment artifacts. Client platform login UI, Host-side
Relay publication, Browser ABI tunnel binding and device path selection remain
integration work before complete M1 network acceptance.
