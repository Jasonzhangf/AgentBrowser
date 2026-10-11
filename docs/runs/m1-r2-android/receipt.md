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
| **BB04 re-run after formal restart** | **FAIL** | video precondition `renderedFrames>=2 && inputReady` never satisfied (`bb04-run3`) | Android/Host media-after-restart path |
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

## Environment note after the formal restart

After the formal Host `restart`, the client still connected but never reached
`renderedFrames>=2 && inputReady`. The same precondition then also failed for
the previously passing BB04 slice (`bb04-run3`), with only endpoint generation 5
(PID 9406) listening on `192.168.0.3:51882`. So the post-restart frame stall is
not BB10-specific. `bb10-run6` additionally hit the known one-time
`androidx.test.uiautomator.StaleObjectException` during ordinary DocumentsUI
admission; the next run passed admission, so that is an environment flake.

## Product/environment split

- Proven product behaviour: BB04 real drag scroll, no phantom tap, blank-space
  edit termination, release termination, `inputReady` recovery.
- Confirmed product gap: no recovery after a real network interrupt/restore
  (BB10 Gate B), first failing gate above.
- Environment: the UiAutomator stale-object flake, and the post-restart frame
  stall that also blocks the previously passing BB04 slice.
- Not claimed: BB08, BB14, BB11, Chinese composition.
