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

`NativeConnection` exposes open/frame/acknowledge/command/close. Keys load only
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

`scripts/network-replay.py` builds and installs both main and instrumentation
APKs, compares each installed APK SHA-256 with its local artifact, then runs the
current test. Every run uses a new `evidence/network/<run-id>` directory and
passes that identifier into instrumentation. Admission supplies an exclusive
`NETWORK_EVIDENCE_DIR` under its own run directory and checks matching identity.
Failures retain their own logs and cannot reuse another run's PASS or images.
Acceptance exercises real Surface touches, observer denial, takeover, input
focus without automatic fixture focus, busy declaration coalescing, restored
stage dimensions, reconnect state preservation and background resource release.
DOM text proves input state only; visible input text painting needs its own
pixel acceptance and is not claimed by this test.

This remains a direct prepaired WSS integration slice. UDP/Relay selection,
production account enrollment, multiple-client viewport election, complete
rotation/reconnect UX and milestone publication remain separate acceptance work.
No whole-flow PASS is inferred from a compiled library or previous local files.
