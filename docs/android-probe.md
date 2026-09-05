# Android / Cordis / H.264 local probe

Scope: one installed diagnostic app, `com.agentbrowser.probe`. This is the
client compatibility slice of P2. It does not attach a BrowserSession, send an
operation, establish a network transport, or implement an Obscura Host.

## Source ownership and actual edges

The subsequent native Annex B input, local limits and distinct test fixtures are
documented in [android-annexb.md](android-annexb.md); MP4 remains diagnostic only.

| Existing design owner | Implemented path | Edge / resource |
| --- | --- | --- |
| AB-01 ui-kernel | `packages/ui-kernel/kernel.ts` | Cordis root owns injected provider and built-in plugin lifetime |
| AB-02 ui-plugins | `packages/ui-plugins/` | React panel and subscription; play/stop/bad sample and plugin unload/reload controls |
| AB-03 client-domain | `packages/client-domain/probe.ts` | Closed local command and snapshot types; validates native responses |
| AB-04 platform-host | `apps/android/` | Activity, private asset-only WebView, Surface and serial MediaCodec worker |

The AppSDK build module `android-probe` packages this slice into one APK.
Logical ownership above remains distinct; there is only one runtime delivery
artifact. Build support lives in `scripts/`, `package.json` and lock files.
Forbidden paths: `services/relay/`, `protocol/relay/`, other worktrees and the
Obscura/zterm repositories. No shared root truth is edited outside this worktree.

Native `MediaProbe` owns media state, cancellation, generation and resource
release. JS receives a read-only snapshot. The Cordis event bus carries no
media. The UI plugin stops native playback and observes release before its
explicit diagnostic unload; React unmount alone removes UI subscriptions.
Activity stop or Surface loss cancels playback; Activity destruction shuts
down the worker. A new play is rejected while resources remain owned.

Assets are loaded through a closed HTTPS origin allowlist. Network permission
is absent; WebView navigation, file/content access, network loads and frames
are blocked. Native commands accept two built-in sample IDs, never URLs or
paths. Unknown fields and unsupported commands are rejected without changing
an active stream. This is a trusted built-in plugin bridge, not a plugin sandbox.

MediaExtractor reads a bounded built-in H.264 MP4 fixture and feeds MediaCodec
input buffers directly. Output goes to Surface. Video never becomes JS strings
or base64. The 12-second 360×640/30fps sample was generated from FFmpeg testsrc2
raw pixels with h264_videotoolbox, `-allow_sw 0`, 600k bitrate, yuv420p and
`-movflags +faststart`; no PNG encoding precedes H.264. The broken fixture is
deliberately invalid and must fail through the extractor boundary.

## Reproducible commands

Prerequisites: Node 22, JDK 17 or newer, Android SDK platform 36, network access
for locked npm/Gradle dependencies, and an explicitly selected authorized ADB
device. Set `JAVA_HOME`, `ANDROID_HOME` and `ANDROID_SERIAL` in the invocation
environment. On this Mac JDK is bundled in Android Studio.

```sh
npm ci
scripts/setup/enable-local-protection.sh
npm run verify:local
bash scripts/android.sh testDebugUnitTest assembleDebug assembleDebugAndroidTest
npm run install:android
bash scripts/device.sh restart
npm run replay:android
```

`replay` requires the real installed APK and test APK. It activates shipped
Cordis UI buttons, waits for native OnFrameRendered callbacks, captures two
native Surface PixelCopy images, asserts changing nonblack pixels, then checks
stop, invalid stream, UI plugin unload/reload and Activity-stop release.
ADB installation alone is not the acceptance signal. Evidence is retained in
the ignored task-local `evidence/` directory and the app's private files.

For a committed candidate, `node scripts/validate-android.mjs` executes those
checks in causal order, compares the installed APK bytes with the built APK,
then writes project-owned lifecycle/evidence records and runs AppSDK review
admission. It derives commit/tree/artifact/device identity; it accepts no
hand-entered hashes or verdicts. Existing admission records are retained and
never overwritten. Records and logs remain task-local; review is a subsequent
read-only AGY operation. The official `appsdk pin-lock` owns compiler and SDK
lock generation; compiled manifests are never authored by this adapter.

Network disconnection is not an implemented path in this local probe.
The negative lifecycle boundary is Surface/Activity loss. AB-05 will later
provide authenticated bounded access units and codec configuration to a native
decoder input adapter, preserving native byte ownership and generation checks.
WebRTC depacketization and WSS framing remain separate transport adapters;
the MP4 fixture reader must not become a network framing protocol.

## Evidence boundaries

AppSDK draft verification, Git protection, source tests, APK build, device
install, dynamic display, review and remote integration are separate results.
This task retains the owner worktree for review; no main merge, push, freeze,
immutable publication or cleanup is included.
