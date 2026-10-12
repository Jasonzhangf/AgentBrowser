# R2 Android black-box receipt (bounded first pass)

Contract source: `docs/m1/blackbox-acceptance.md`, rows BB04, BB08, BB10, BB11,
BB14. The pass ran the real user path through the ordinary launcher, ordinary
connection UI, real video touch, and the real system IME reset. No DOM value
write, private bridge, mock, URL-label assertion, cookie count, or
`adb input text` was used. The transfer code stayed transient in-process.

## Lifecycle

- Dedicated root: `$HOME/Library/Application Support/AgentBrowser/host-service-lan`.
- Entrypoint used only from the integration worktree
  `m1-user-slice-host-integration-20261010/scripts/host-service/host-service`.
- `trust endpoint-refresh` returned `authorization_state=refreshed`,
  `bound_address=192.168.0.3:51882`, non-null `endpoint_pid`.
- A formal `restart` later returned `pid=9146`, `state=running`; refresh then
  returned `endpoint_generation=5`, `endpoint_pid=9406`.
- Stop through the same entrypoint returned a real stop receipt.

## Per-case results

| Case | Result | First failing gate | Owner |
|---|---|---|---|
| **BB04 gestures** | **PASS (bounded slice)** | none in `bb04-run2` | Android gesture path |
| **BB04 re-run after formal restart** | **FAIL** | client gets `Host SESSION_MEDIA_FINALIZE` at connect; `renderedFrames` stays 0 (`probe-2`) | Host session media finalize path (obscura) |
| **BB10 reconnect** | **FAIL** | network interrupt/restore recovery: client did not return to `connected && inputReady` within 45 s (`bb10-run2`) | Android/Mac reconnect path |
| **BB08 focus/layout** | **NOT COVERED in this pass** | only the blank-space part was exercised (inside BB04); two-field switch, keyboard dismissal, rotation, second client not run | Android/Mac harness |
| **BB14 continuous use** | **NOT COVERED in this pass** | no continuous session was run | Android harness |
| **BB11 profile** | **BLOCKED** | Android correctly reports `profiles=false`; real profile create/select/login is Mac task-21 | Mac client |

## BB04 raw assertion lines (`evidence/r2-android/BB04/bb04-run2/bb04-gesture.json`)

```json
{"tapEditable": true, "tapTarget": "node-46", "blankTapEditable": false,
 "dragOverTargetEditable": false,
 "dragGestureState": "gestureOperationId=null gestureSessionId=null openOperationId=null gestureOperationId=null gestureEventSequence=0 gestureEpoch=3 hostLease=null hostRunning=false",
 "dragLeaseFencesAgents": false, "tapAfterDragEditable": true,
 "renderedFrames": 28, "forwardDragChangedCells": 303,
 "reverseDragChangedCells": 0,
 "scrollDragGestureState": "gestureOperationId=null gestureSessionId=null openOperationId=null gestureOperationId=null gestureEventSequence=0 gestureEpoch=3 hostLease=null hostRunning=false",
 "scrollLeaseFencesAgents": false, "inputReadyAfterDrag": "true"}
```

Covered: a real tap focuses the remote editable target (`node-46`); a blank-space
tap terminates the old edit; a real drag released over the target does **not**
click it while the same point still clicks afterwards; a real drag changes the
presented remote content by 303 of 1024 sampled cells and the reverse drag
restores it exactly (0 changed cells); release leaves no open Host gesture lease
and `inputReady` recovers.

Not covered in BB04: the Host DOM `scrollY`/`maxScroll` read. The Android
protocol-11 Host projection publishes no scroll field
(`hostScrollProjectionAvailable: false`), so the scroll assertion uses the
Host-encoded video surface instead. Owner: Host protocol / Android projection.

## BB10 raw lines

`evidence/r2-android/BB10/bb10-run2/instrument-testBb10ReconnectSlice.txt`:

```text
INSTRUMENTATION_STATUS: stack=junit.framework.AssertionFailedError: The client must recover after the network is restored
```

Gate A (real client close and reopen) passed before that line: after
`activity.finish()` and relaunch, reconnect returned the same Host session and
the same document URL, so detach did not delete the session. Gate B failed: with
the LAN route really interrupted (`cmd connectivity airplane-mode enable`,
verified separately as `ping 192.168.0.3` failing) and then restored, the client
did not return to `connected && inputReady` within 45 s. Gate C (stale-generation
fencing) was not reached.

Later BB10 attempts (`bb10-run5`, `bb10-run7`, `bb10-run8`) failed earlier, at
`controlMode==='control'` and then at `renderedFrames>=2 && inputReady`:

```text
INSTRUMENTATION_STATUS: stack=junit.framework.AssertionFailedError: Condition: document.querySelector('[data-control-mode]').dataset.controlMode==='control'
INSTRUMENTATION_STATUS: stack=junit.framework.AssertionFailedError: Condition: JSON.parse(ProbeNative.request('{"op":"status"}')).renderedFrames>=2 && JSON.parse(ProbeNative.request('{"op":"status"}')).inputReady
```

## Diagnosis of the first failure: Host session media finalize

The isolation probe `testBb10TransportProbe` records the app projection plus
endpoint reachability every second. Raw samples in
`evidence/r2-android/BB10/probe-2/bb10-transport.json`:

```json
{"phase": "connect", "index": 2, "elapsedMs": 2160, "connectionState": "error",
 "renderedFrames": 0, "inputReady": false,
 "error": "java.lang.IllegalStateException: Host SESSION_MEDIA_FINALIZE"}
```

The same sample series in `probe-1` surfaced the transport-level form of the
same failure:

```json
{"phase": "connect", "index": 5, "elapsedMs": 5234, "connectionState": "error",
 "renderedFrames": 1, "inputReady": false,
 "error": "java.lang.IllegalStateException: Transport: IO error: peer closed connection without sending TLS close_notify"}
```

The Host/endpoint side of the same window
(`$HOME/Library/Application Support/AgentBrowser/host-service-lan/logs/endpoint.log`)
shows the media acquire and finalize failures:

```text
session media acquire: 93a4a3ff-c727-4077-8bb4-232589d37f90 child spawned, supervisor started
Error: H.264 encoder exceeded five second deadline

Caused by:
    deadline has elapsed
session media finalize terminal: Encoder stream ended or invalid header
Error: ENDPOINT_SESSION_FINALIZE_FAILED: Encoder stream ended or invalid header
```

Excluded causes, each checked directly:

- Authorization: `trust status` reports `authorization_state=refreshed`,
  `active_clients=1`, and the device certificate
  `16cf5f6b...` listed active with scopes `observe_human_control`,
  `profile_read`, `profile_create`, `profile_select`.
- Routing: exactly one endpoint listens on `192.168.0.3:51882`
  (`lsof -nP -iTCP:51882 -sTCP:LISTEN` -> PID 78494), and no `adb reverse`
  mapping exists for the port.
- Host runtime: `/tmp/abn-lan-runtime/host-15b71ef7/` contains `frames.sock`,
  `host.sock`, `host-admin.sock`, `remote-admission.sock`, and the session
  socket.
- Encoder toolchain: `--ffmpeg ffmpeg` resolves inside the endpoint's own PATH
  (`/opt/homebrew/bin/ffmpeg`), and the `obscura-media` binary exists.

So the first failure is the Host session media finalize/encoder path, not the
Android reconnect logic and not authorization. Owner: obscura Host media path
(`crates/obscura-host/src/endpoint`, `crates/obscura-media`) in the
`m1-media-encoder-ownership-fix-r8-20261009` worktree, which is outside this
Android worktree. Gate B could not be isolated further because the client never
reaches `inputReady` on this endpoint, so a Gate B rerun would only re-observe
this first failure.

`bb10-run6` additionally hit the known one-time
`androidx.test.uiautomator.StaleObjectException` during ordinary DocumentsUI
admission; the next run passed admission, so that is an environment flake.

## Product/environment split

- Proven product behaviour: BB04 real drag scroll, no phantom tap, blank-space
  edit termination, release termination, `inputReady` recovery.
- Confirmed product gap: no recovery after a real network interrupt/restore
  (BB10 Gate B), first failing gate above.
- Blocking first failure: Host `SESSION_MEDIA_FINALIZE` /
  `H.264 encoder exceeded five second deadline`, which also blocks the
  previously passing BB04 slice and any further BB run on this endpoint.
- Environment: the UiAutomator stale-object flake.
- Not claimed: BB08, BB14, BB11, Chinese composition.
