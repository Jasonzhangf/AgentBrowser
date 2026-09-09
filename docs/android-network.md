# Android direct network integration

Current repair owner: `playground/network-replay-integrity`, based on
`origin/main` (`eed395c`) with the retained Android network candidates imported.
Main and other owner worktrees remain unchanged.

AB-04 owns `packages/android-bridge`, Java native lifecycle and Android packaging.
AB-05 owns TLS, WSS, browser protocol import, generation and input transport.
AB-02/03 own Cordis UI and read-only projection. JNI never copies the Browser ABI.
AppSDK binds Android deployment to the connection module dependency; integration
must rerun its actual APK build/install/restart/device path before review.

## Native boundary

`NativeConnection` exposes explicit WSS `open`, WebRTC `openWebRtc`,
frame/acknowledge/command/close. Keys load only
from the app-private pairing directory. No credentials or media bytes pass
through WebView. JNI returns a bounded byte array directly to MediaCodec.
The shared connection emits latest-only complete AU; Android admits one decode
at a time, acknowledges only after OnFrameRendered, then requests the next AU.
The native acknowledgement captures Host document/viewport revisions for input.
An unknown, stale or missing acknowledgement is an explicit failure.

Java serializes native calls on its background executor and fences callbacks by
native lifecycle identity. Decoder generation advances on connection/geometry
change and release. UI supplies its observed control epoch. Host alone grants
takeover and completes atomic operations. Closing Android resources closes its
connection, preserving the independent Host document.

Correlated Host command errors cross JNI as `HostCommandException` with the
original code and message. Android reports the failed operation, refreshes Host
status, and retains the connection; it never retries the rejected operation.
Transport/protocol failures and unknown outcomes retain the fatal path. A failed
status refresh after a rejection also ends the connection explicitly.

The phone declares the measured page stage in CSS pixels, excluding local
chrome and system bars. Only a change to stage bounds declares a new size;
decoded-frame layout does not redeclare it. `NetworkSession` retains the latest
requested declaration through connection setup and an outstanding command,
then submits it when the serial command owner is available. Host v4 owns the
shared viewport election and committed revision. Input stays unavailable while
a local declaration or Host viewport change is pending.

## Build and real acceptance

Requires Rust `aarch64-linux-android`, NDK `29.0.14206865`, Android SDK 36/JDK17+,
`OBSCURA_PROTOCOL_ROOT` pointing to the explicit protocol owner, validated
`OBSCURA_BIN_DIR`, and refreshed `ANDROID_SERIAL` / reachable Host bind IP.
Gradle invokes `scripts/build-native.py`; missing dependencies fail explicitly.
The native library uses NDK linking with 16KiB ELF segment alignment.

The `device_fixture` Rust example starts a private, real Host and paired endpoint
with an initial 391x845 page; the attached phone then declares its measured
stage. A red button becomes green and increments `window.clicked`;
an input field records text. Fixture `inspect` reads DOM independently through
the local Agent after human control is released. It issues ephemeral credentials,
never touches system trust, and owns its child processes and temporary directory.
`scripts/device-pairing.py` installs only into an absent private pairing directory
and removes it only when its owner marker matches the fixture.

Fixture `status` projects the Host's typed `SessionStatus` without an Agent
operation. It remains available during human takeover to compare Session,
attachment count, control and shared viewport revisions; it neither evaluates
page JavaScript nor resumes Agent control. The regression
`python3 packages/android-bridge/tests/fixture-status.py /path/to/device_fixture`
uses the real Host, takes human control on a second connection, and verifies
that repeated fixture status reads leave control, revisions and the Agent
operation sequence unchanged. Set `OBSCURA_BIN_DIR` and
`OBSCURA_ENDPOINT_BIND_IP=127.0.0.1`. This proves only the read-only fixture
entrypoint; real Android and Mac display alignment still requires both clients.

`scripts/network-replay.py` builds and installs both main and instrumentation
APKs, compares each installed APK SHA-256 with its local artifact, then runs the
current test. Every run uses a new `evidence/network/<run-id>` directory and
passes that identifier into instrumentation. Admission supplies an exclusive
`NETWORK_EVIDENCE_DIR` under its own run directory and checks matching identity.
Failures retain their own logs and cannot reuse another run's PASS or images.
Acceptance exercises real Surface touches, observer denial, takeover, input
focus without automatic fixture focus, busy declaration coalescing, restored
stage dimensions, reconnect state preservation and background resource release.
DOM text proves input state only. The test separately requires dark text glyphs
to appear inside the native input Surface after typing, excluding its border
and focus outline; before/after images are retained. The old Host fails this
check even though its DOM value changes. A passing run must prove both.

JNI carries document and viewport revisions from the actual media source into
the immutable frame. Java exposes those revisions only after native display
and acknowledgement, and allows input only when they match the Host status.
The device test observes this invariant during waits, including viewport changes.

## Explicit Android WebRTC selection

`NetworkSession` reads optional app-private `files/pairing/transport.json`.
Missing file means the explicit WSS adapter for regression. WebRTC requires the
following typed configuration:

```json
{"transport":"webrtc","bind_ip":"192.0.2.10"}
```

`bind_ip` must be a literal, non-unspecified, non-multicast IP selected by the
platform. The native boundary passes it to
`Connector::connect_webrtc_with_config`; a malformed address, failed signaling,
failed UDP ICE, failed DataChannel capability/binding, or missing H.264 media
is an error. There is no WSS fallback after WebRTC selection, and signaling
success does not mark media ready.

The app keeps the existing MediaCodec, displayed-frame acknowledgement and
operation fences for both adapters. The independent source/JNI smoke is:
`python3 packages/android-bridge/tests/run-webrtc-smoke.py --library-dir ...
--fixture-bin ... --bind-ip ...`. It covers invalid configuration, a real
WebRTC status/media boundary, release, and stale-handle fencing; it does not
claim installed-device or 15T evidence until that entrypoint is run with the
approved resource window.

The network test also submits a stale takeover epoch and verifies that the
reported rejection leaves the same Session connected with continuing frames,
before exercising a valid takeover. `packages/android-bridge/tests/run-smoke.py`
is the narrower real JVM/JNI/TLS regression; pass explicit `--library-dir` and
`--fixture-bin`, with `JAVA_HOME`, `OBSCURA_BIN_DIR` and
`OBSCURA_ENDPOINT_BIND_IP` set. It checks the structured rejection and a
subsequent status read using a private Host fixture; it does not prove Android
UI or native display behavior.

Android touch routing uses the platform movement threshold to distinguish a tap
from a swipe. One completed swipe submits one atomic scroll; no operation is
submitted while the finger moves. A cancelled or multi-pointer gesture is
discarded, as is a gesture whose connection, control epoch, document or viewport
changes before release. Inertial scrolling and multi-touch zoom are not yet
implemented. Acceptance checks actual lower-page pixels, restoration of the
same button, an independent Host scroll offset history, cancellation and a
viewport change between press and release.

WSS remains an explicit prepaired regression adapter. UDP/Relay selection,
production account enrollment, multiple-client viewport election, complete
reconnect UX and milestone publication remain separate acceptance work.
No whole-flow PASS is inferred from a compiled library or previous local files.

Android handles orientation and screen-size configuration changes in the
existing Activity. The same WebView and network connection remain alive while
the native stage is measured again and the Host negotiates its shared viewport.
Rotation cancels any unfinished local gesture. Real-device acceptance rotates
landscape then portrait and requires unchanged Session, document and control,
retained visible input and matching stage/source dimensions. Keyboard insets
and multi-device election still require separate verification.
