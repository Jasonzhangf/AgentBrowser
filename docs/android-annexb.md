# Native Annex B input slice

Owner: AB-04 Android platform media, `work/android-annexb`. Starts from remote
main `eed395c` plus the reviewed Android baseline `aef3858` (carried as `204c157`).
Existing local MP4 diagnostic remains independent. This slice implements no
authentication, routing, BrowserSession protocol, Host or remote transport.

## Local input and lifecycle

`MainActivity.submitAccessUnit(AccessUnit)` is a main-thread native entry;
returns `CompletableFuture<AnnexBDecoder.Receipt>` after OnFrameRendered.
It never accepts bytes through JavaScript, JSON, base64 or the Cordis event bus.
The Activity arbitrates its single Surface between MP4 and Annex B; selecting
one while the other owns the Surface fails explicitly. Existing Cordis stop and
plugin unload stop the selected media owner. Surface loss, background and close
invalidate pending callbacks and asynchronously release the codec.

`AccessUnit` owns an immutable copy of at most 1 MiB, positive decoder generation,
nonnegative PTS, even coded dimensions 2..4096, visible dimensions inside coded
dimensions with at most one padding pixel per axis. Each AU must carry Annex B
SPS/PPS/IDR. These parameters are a local decode port, not a copy of the remote
VideoPacket contract. The transport owner must explicitly handle the native
1 MiB ceiling if its own maximum is larger; no truncation or hidden drop.

`AnnexBDecoder` admits one pending AU. Backpressure is an explicit exception.
Within a generation geometry stays fixed and PTS increases; geometry changes
require a larger generation and codec reconstruction. After release the caller
must use a larger generation. Delayed callbacks check a private lifecycle token,
generation, codec instance and PTS before updating state. Errors release resources
and complete the receipt exceptionally; release failure never claims released.

Codec output crop describes SPS dimensions separately from internal macroblock
padding (15T reports 160x128 with crop-bottom=119 for a 160x120 stream). The
decoder checks this crop against coded dimensions. A clipped native view scales
the visible rectangle and excludes the encoder's even-size padding at right and
bottom. Media bytes remain in native memory and MediaCodec buffers.

## Verification and fixture origin

Run existing `npm run verify:local`, Android `testDebugUnitTest assembleDebug
assembleDebugAndroidTest`, then `scripts/device.sh install|restart|replay` with
explicit verified `ANDROID_SERIAL`. Replay now runs both the original MP4 and
Annex B instrumentation flows. `scripts/validate-android.mjs` binds both flows
to one committed candidate, exact built/installed APK and AppSDK admission.
No new governance or memory system is introduced.

Test APK alone contains three real Host-produced H.264 fixtures, read-only copies
from `/tmp/obscura-h264-host-proof/`. Before/after: 160x120; resized: coded392x846,
visible391x845. Constrained Baseline, YUV420, BT.709. SHA-256:

- before: `c938ee51e31aab6615fe1a8ba175b8cd900418d894053c8f87460e8af655e675`
- after: `d5e4d2f51a31880acdc1c961f16342cfbc044d9a288218009779a562b7fbfaa2`
- resized: `9285bb3062d9608c54af3667cb6e985ee854926903d463a45eadca32eb343f91`

Instrumentation checks raw Surface pixels plus a composed UiAutomation screenshot,
nonblack page content and native child clipping bounds (the sample's white padding
cannot be distinguished from its white page by pixel inequality). It proves
changing native pixels and visible geometry excluding coded padding,
dimension reconstruction, stale-generation rejection, corrupt codec contents,
declared/bitstream size mismatch, byte/dimension limits, Surface loss and actual
Activity background release. Images and receipts live in task-local `evidence/`.
This is real Host fixture decoding on 15T, not a network-stream acceptance claim.
