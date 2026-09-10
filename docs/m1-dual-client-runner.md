# M1 Android + Mac combined replay

`scripts/m1-dual-client-runner.py` composes the existing Android network
instrumentation entrypoint and the existing Mac native bridge against one
owned `device_fixture` process. The fixture is started once, prints one
pairing directory and Session identity, and remains the Host and Session
authority for the whole run. The runner does not implement a second Browser
ABI, transport, Host, or client lifecycle.

The runner is a local direct path check. It requires the fixture endpoint to be
`wss://127.0.0.1:<port>` or `wss://[::1]:<port>` and rejects a relay or another
bind address. Relay, Tailscale, LAN, installed product admission, and complete
M1 acceptance are separate evidence layers.

## Invocation

Inspect the output contract without starting a process:

```sh
python3 scripts/m1-dual-client-runner.py --print-schema
```

Validate an invocation and create task-local evidence without touching the
fixture, Android device, or Mac bridge:

```sh
python3 scripts/m1-dual-client-runner.py \
  --dry-run \
  --run-id m1-dual-dry-run \
  --evidence-dir /tmp/agentbrowser-m1-dual/m1-dual-dry-run
```

`--print-schema` and `--dry-run` are mutually exclusive. A dry run exits zero
with `result=dry_run`; it does not report a client or Host pass.

For a real local replay, provide the exact executable artifacts and a live ADB
serial. The Mac bridge reads the fixture pairing directory, while Android gets
the same fixture credentials through `scripts/device-pairing.py`; Android also
receives the fixture's exact `initialUrl` and the same `run_id`.

```sh
JAVA_HOME=/path/to/jdk17-or-newer \
OBSCURA_PROTOCOL_ROOT=/path/to/obscura/protocol/browser \
python3 scripts/m1-dual-client-runner.py \
  --fixture-bin /path/to/device_fixture \
  --obscura-bin-dir /path/to/obscura/target/release \
  --mac-bridge-bin /path/to/AgentBrowserMacBridge \
  --android-serial "$ANDROID_SERIAL" \
  --agent-commit <AgentBrowser-commit> \
  --agent-tree <AgentBrowser-tree> \
  --obscura-commit <Obscura-commit> \
  --obscura-tree <Obscura-tree> \
  --run-id m1-dual-$(date +%Y%m%dT%H%M%SZ) \
  --evidence-dir /tmp/agentbrowser-m1-dual/<run-id>
```

The candidate must be a clean non-`main`/non-`master` worktree. The optional
commit and tree arguments bind the run to the caller's selected AgentBrowser
and Obscura identities; when supplied, the AgentBrowser values are checked
against `HEAD` and `HEAD^{tree}`. The Obscura values are recorded with the
binary identities and must be verified by the Obscura build/admission owner.
The runner never treats an old evidence directory or an old artifact hash as
current proof.

The Android build requires `JAVA_HOME` for JDK 17 or newer and
`OBSCURA_PROTOCOL_ROOT` for the explicit Obscura `protocol/browser` owner.
Those variables are inherited by Gradle and `scripts/build-native.py`; a
missing value fails the Android build before instrumentation. The runner uses
the fixture's loopback WSS port to establish an `adb reverse tcp:<port>
tcp:<port>` mapping for the selected serial, verifies the mapping with
`adb reverse --list`, records it in `adb-reverse.json`, and removes only that
mapping during cleanup. This keeps the fixture's exact loopback endpoint and
initial URL unchanged for the Android client.

The real entrypoint builds and installs both Android APKs unless
`--skip-android-build` is supplied. That option only reuses APK files already
present in this candidate's declared build output paths and requires
`--android-build-provenance` pointing to a receipt whose AgentBrowser commit,
tree, and APK SHA-256 values match the current candidate; it does not skip
installation or installed-file SHA-256 verification. The selected `adb` can be
provided with `ADB`. `ANDROID_SERIAL` is required for a real run and may not
contain whitespace. `OBSCURA_ENDPOINT_BIND_IP` defaults to `127.0.0.1`, and
the runner rejects non-loopback values.

The fixture owns `/tmp/an-*` pairing material and removes it through its own
`quit` command. The runner installs and removes Android pairing only through
`scripts/device-pairing.py`, preserving the fixture owner marker. Evidence is
written under `/tmp/agentbrowser-m1-dual/<run-id>` by default. An evidence path
inside the candidate worktree is accepted only when Git already ignores it.

## Stage order and client behavior

The stages are recorded in `evidence.json` in this order:

1. `preflight` records the candidate, selected binaries, Android identity, and
   loopback constraint.
2. `fixture_start` starts one fixture, validates its ready record and pairing
   endpoint, and records Host status.
3. `android_install` builds and installs the main and instrumentation APKs,
   establishes and verifies the selected serial's ADB reverse mapping,
   compares installed bytes with each local artifact, and installs the same
   fixture pairing.
4. `mac_connect` starts the Mac bridge with that pairing, connects in Host
   observation mode, drains framed media, and sends transport acknowledgements
   so the bridge can advance. This runner has no AppKit or VideoToolbox display
   observer, so this stage cannot prove a native frame was displayed.
5. `android_replay` runs `NetworkDeviceTest` with the fixture URL and run ID.
   While it runs, the runner keeps polling the Mac bridge and Host status so
   neither output pipe becomes a hidden backpressure point.
6. `mac_control` observes the Host epoch, requests takeover, then releases it
   through typed bridge commands.
7. `mac_reconnect` disconnects and reconnects the Mac bridge, requires a new
   acknowledged frame, and leaves the bridge stopped before cleanup.
8. `correlation` builds side-specific and cross-side claims.
9. `cleanup` removes the selected pairing and this run's ADB reverse mapping,
   terminates only the owned bridge and fixture, and records whether the
   fixture root disappeared.

If a stage fails, dependent stages are skipped. The first failure keeps its
code, message, owner, and one executable next action. Cleanup still runs when
the real runner has started resources. A cleanup failure changes the final
result to `failed` without replacing an earlier failure.

## Evidence contract

The top-level `evidence.json` has the schema identifier
`agentbrowser.m1.dual-client-runner/v1` and separates these sources:

| Source | What it owns in this runner |
| --- | --- |
| `fixture` / `sides.host` | fixture artifact, endpoint, Session, attachment count, Host viewport, document and viewport revisions, and control epoch from typed `SessionStatus` |
| `android-result.json` / `sides.android` | the instrumentation `runId`, Session, measured CSS and source dimensions, and the flags emitted by `NetworkDeviceTest` for navigation, rotation, composition, disconnect, and reconnect |
| Mac bridge / `sides.mac` | bridge snapshots, framed media headers, generation, Session, coded and visible dimensions, revisions, transport ACK tickets, control receipts, disconnect, and reconnect; native display is a separate AppKit evidence source |

Every side field is either an evidence-bearing object with `status=known` and
its source list, or an explicit `status=unknown` object with a reason. The
runner normalizes the Host's integer-valued JSON float dimensions without
rounding non-integer values. It never fills a missing source field from another
side.

`cross_side` compares only fields emitted by all three sides:

- `session_id` proves one Session only when Host, Android, and a Mac frame agree;
- `viewport` compares the committed Host dimensions with Android CSS and Mac
  visible dimensions;
- `source_dimensions` compares encoded/source dimensions when all three sides
  export them;
- `viewport_revision`, `document_revision`, and `control_epoch` require all
  three typed values;
- `frame_ack` requires each side to export native display acknowledgement
  evidence. The Mac bridge ACK emitted by this runner is recorded separately as
  `frame_transport_ack`; it only advances the bridge's framed transport.

`compare_field()` returns `proved` only when every side is known and equal. A
missing side returns `unknown`; unequal known values return `failed`. A required
claim composed from multiple fields is `failed` if any dependency failed,
`proved` when every dependency is explicitly true or proved, and otherwise
`unknown`. Rotation and disconnect/reconnect remain required claims; missing
Mac orientation identity or Android boolean output keeps them unknown.

The runner currently preserves limitations in the existing clients. Android's
`NetworkDeviceTest` result does not export attachment IDs, revisions, control
epochs, frame ACK tickets, or per-operation receipts, so those claims remain
`unknown` even when the instrumentation assertions pass. The Mac bridge has no
rotation identity, so its rotation side claim remains `unknown`. These values
keep the combined result at `partial` until the owning client emits the missing
typed evidence. A `partial` result is not a full M1 pass.

## Evidence boundaries

The runner can prove that the selected fixture, binaries, installed APK bytes,
Mac framed bridge, Android instrumentation, and local cleanup participated in
one run. It cannot promote any of those layers into another layer:

- a build or installed APK hash does not prove the deployed UI entrypoint;
- an Android emulator or device result does not prove a 15T, Tailscale, or
  public-network result;
- a fixture status or signal does not prove real media display or operation
  completion;
- the combined runner's `ack_frame` response and `renderedFrames` counter do not
  prove that Annex-B bytes reached VideoToolbox or an AppKit surface;
- a Mac frame ACK does not prove Android rendered the same frame;
- a source, configuration, or health record does not prove integration, review,
  merge, push, or release.

Keep `evidence.json`, `android-result.json`, `correlation.json`, child logs,
installed APK hashes, and any external build/admission receipts together when
reviewing a run. Their candidate, tree, artifact, environment, and `run_id`
bindings must match before the evidence is reused.
