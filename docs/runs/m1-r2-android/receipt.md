# R2 Android black-box receipt

Source of truth: `docs/m1/blackbox-acceptance.md`; case rows BB04, BB08, BB10,
BB11, and BB14. This receipt records only real black-box evidence. Existing
instrumentation or protocol tests are not substituted for the contract cases.

## Scope and lifecycle

- Device: `emulator-5554`, serialized flow.
- Dedicated Host root: `$HOME/Library/Application Support/AgentBrowser/host-service-lan`.
- Lifecycle entrypoint was run only from the integration worktree:
  `/Volumes/Intel/playground/agentbrowser/m1-user-slice-host-integration-20261010/scripts/host-service/host-service`.
- Start reported `state=running`; endpoint refresh reported
  `authorization_state=refreshed`, `bound_address=192.168.0.3:51882`, and
  non-null `endpoint_pid=67081`.
- Final lifecycle stop returned `pid=78018, service_label=com.agentbrowser.lan,
  state=stopped`; final status reported `state=stopped`.
- The earlier Android-worktree stop failure was an operator error caused by a
  stale lifecycle copy. No task6 Host or user `weibo` profile was touched.

## Ordered case results

| Case row | Result | First failing gate / owner |
| --- | --- | --- |
| **BB11 Profile** | **BLOCKED** | Mac client profile create/select and real login are owned by task-21 / mac-client-owner. Android correctly reports `profiles=false`; no Android profile owner was added. |
| **BB04 Gestures** | **UNKNOWN** | No dedicated real-user BB04 runner is present in this Android worktree. Existing lower-level gesture tests do not prove the contract's continuous scroll, nested region, drag, pinch, long-press selection, release termination, and no-phantom-tap row. Owner: Android black-box harness/integration. |
| **BB08 Focus/layout** | **UNKNOWN** | No dedicated real-user BB08 runner is present. The required two-field switch, blank tap, keyboard dismissal, rotation, JS counters/form/scroll preservation, and both-side layout comparison were not all executed. Owner: Android black-box harness/integration; device IME reset remains required before keyboard steps. |
| **BB10 Android half** | **UNKNOWN** | No dedicated Android dual-end reconnect runner or Mac peer was available in this worktree. Client close/reopen, network interrupt/restore, same document/state, duplicate prevention, old-generation fencing, and detach persistence were not all executed. Owner: Mac/Android integration. |
| **BB14 Continuous use** | **UNKNOWN** | No dedicated continuous-use runner was available. A single existing replay cannot prove the contract's sustained browsing/search/form/drawer/detach/reconnect duration. Owner: Android black-box harness/integration. |

BB11 is blocked on the Mac client dependency. The remaining cases are recorded
UNKNOWN, not PASS: their complete contract rows were not executed through the
real Android/Mac user path. No DOM writes, private bridge, URL-label assertion,
`adb input text`, cookie/count assertion, mock login, or fabricated credential
was used.

## Raw lifecycle evidence

- `evidence/r2-android/BB11/r2-bb11-20261010/host-start.txt`
- `evidence/r2-android/BB11/r2-bb11-20261010/host-status-running.json`
- `evidence/r2-android/BB11/r2-bb11-20261010/host-stop-final.json`
- `evidence/r2-android/BB11/r2-bb11-20261010/host-status-stopped-final.json`

Chinese composition remains UNKNOWN/separate and unclaimed.
