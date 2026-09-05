# Android direct network integration

Owner worktree: `playground/android-network`. Imports Android candidates
`204c157`/`19d7567` and shared connection candidate `1869833` from latest
`origin/main` (`eed395c`). Main and retained owner candidates remain unchanged.

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

## Build and real acceptance

Requires Rust `aarch64-linux-android`, NDK `29.0.14206865`, Android SDK 36/JDK17+,
`OBSCURA_PROTOCOL_ROOT` pointing to the explicit protocol owner, validated
`OBSCURA_BIN_DIR`, and refreshed `ANDROID_SERIAL` / reachable Host bind IP.
Gradle invokes `scripts/build-native.py`; missing dependencies fail explicitly.
The native library uses NDK linking with 16KiB ELF segment alignment.

The `device_fixture` Rust example starts a private, real Host and paired endpoint
with a 391x845 page. A red button becomes green and increments `window.clicked`;
an input field records text. Fixture `inspect` reads DOM independently through
the local Agent after human control is released. It issues ephemeral credentials,
never touches system trust, and owns its child processes and temporary directory.
`scripts/device-pairing.py` installs only into an absent private pairing directory
and removes it only when its owner marker matches the fixture.

This remains a direct prepaired WSS integration slice. UDP/Relay selection,
production account enrollment, automatic phone viewport election, complete
rotation/reconnect UX and milestone publication remain separate acceptance work.
No whole-flow PASS is inferred from a compiled library or previous local files.
