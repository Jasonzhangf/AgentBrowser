# R2 Android black-box receipt

Contract source: `docs/m1/blackbox-acceptance.md`, rows BB04, BB08, BB10,
BB11, BB14. Host endpoint was refreshed from the integration worktree before
attempts: `authorization_state=refreshed`, `bound_address=192.168.0.3:51882`,
`endpoint_pid=71033`.

## Results

| Case | Result | First failing gate | Owner |
|---|---|---|---|
| BB11 Profile | BLOCKED | Android correctly reports `profiles=false`; real profile creation/login is Mac task-21 | Mac client |
| BB04 Gestures | FAIL | Existing real-path harness has no BB04 contract assertions for nested region, pinch, long-press selection, Host click counter, or lease termination; no valid BB04 run can be claimed | Android harness |
| BB08 Focus/layout | FAIL | Existing harness has no two-field/blank-space/rotation/second-client contract flow; IME reset alone cannot prove the row | Android/Mac harness |
| BB10 Android half | FAIL | No close/reopen plus network interrupt/restore action/assertion sequence exists in the owned runner; old-generation and detach persistence gates were not exercised | Android/Mac integration |
| BB14 Continuous use | FAIL | No continuous real browsing/search/form/quality/detach/reconnect sequence or freeze/reconnect bound exists in the owned runner | Android harness |

These are FAIL, not PASS or UNKNOWN: the first failing gate is missing required
black-box action/assertion coverage in the owned harness. No DOM writes, private
bridge, URL-label assertion, cookie count, mock login, or `adb input text` was
used. BB11 remains blocked on Mac task-21 and does not receive an Android
profile substitute. Chinese composition remains separate and unclaimed.

## Lifecycle evidence

The LAN Host was already running, so `start` correctly reported already
running. The required integration-worktree refresh succeeded with endpoint PID
71033. Stop was completed through the integration-worktree lifecycle entrypoint;
see `evidence/r2-android/BB11/r2-bb11-20261010/host-stop-final.json` and
`host-status-stopped-final.json`.
