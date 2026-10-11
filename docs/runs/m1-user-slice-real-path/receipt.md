# Task-12 real user path receipt

Run: `real-path-20261010T173018`. Host lifecycle was owned by
`mac-client-owner` and was not started, stopped, or refreshed by this run.
The endpoint was the task-6 loopback service reached through `adb reverse`, not
LAN or Tailscale.

## Gate results

| Gate | Result | Evidence |
| --- | --- | --- |
| Exported bundle/device copy identity | PASS | `bundle.json`: device SHA equals exported SHA `e855a9c0dba043b626ef7620daea17744d05210438f075004478ba8c951ddfb8` |
| Ordinary DocumentsUI import/admission | PASS | `instrument-testOrdinaryRealPathAdmission.txt`, `admission.json`: panel `configured`, active generation changed |
| Ordinary connect | PASS | `real-path.json`: connection reached connected state |
| Real HTTPS navigation through address bar | PASS | `real-path.json`: final URL `https://www.selenium.dev/selenium/web/web-form.html`, scheme `https`, document revision `1 -> 2` |
| Remote video | PASS | `real-path.json`: rendered frames `3 -> 4`, displayed ticket `41` |
| Real touch on remote input | PASS | `real-path.json`: Host editor target `node-46`, kind `input`, editable `true` |
| Real system IME focus | **FAIL** | Immediate raw `dumpsys input_method`: `mInputShown=false`, `mServedInputConnection=null`; raw served-view line is the app `MainActivity` decor, not the remote IME view. |
| ASCII through raised system IME reaches remote page | **FAIL / NOT REACHED** | No system IME window/package was raised, so no test-only injection or local-widget substitute was used. |
| Release/disconnect | NOT REACHED after IME failure | Instrumentation stopped at the first failing IME gate. |
| Chinese composition | UNKNOWN / separate gate | This run makes no Chinese-composition claim. |

The first failing gate is real system IME focus. This is a genuine failure, not
an environment-blocked success: the immediate dump has `mInputShown=false` and
`mServedInputConnection=null`. The accompanying logcat focus trace is retained
for diagnosis. The remote page focus itself was proven before that failure by
the Host-published editable context.

## Ordinary-path and secret hygiene

The admission test uses the real launcher, real DocumentsUI picker, protected
`Transfer code` field, and real `IMPORT`. It does not call `startImportForTest`
and does not stage the bundle into app-private storage. The transfer code was
captured in-process in the same shell as the instrumentation invocation, passed
only as a transient argument, then removed from memory and the private bundle
directory. The device copy was removed after the run. No bundle, protected-field
UI dump, or transfer code is committed.

The exact force-added evidence list is recorded in the task commit. Raw IME
state and logcat are included because the IME gate failed and requires the
unfiltered focus path. The endpoint is explicitly `wss://127.0.0.1:51880/` via
reverse mapping; this is not LAN/Tailscale evidence.
