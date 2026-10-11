# Task-13 system IME focus receipt

Scope: LAN over the local network (`wss://192.168.0.3:51882/`), one Android
emulator, the fresh `host-service-lan` root. This run started, stopped, or
refreshed no Host.

## Environment configuration (not a product result)

| Item | Value | Evidence |
| --- | --- | --- |
| `show_ime_with_hard_keyboard` | previous `0`, changed to `1` | `ime-focus-20261010T1733/show_ime_with_hard_keyboard.before.txt` / `.after.txt` |
| AVD hardware keyboard | `hw.keyboard=no`, `hw.keyboard.lid=yes` | `ime-focus-20261010T1733/avd-hardware-keyboard.txt` |
| `default_input_method` | `com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME` | `ime-focus-20261010T1740/default_input_method.before.txt` |
| Enabled IMEs | Latin IME and Google TTS | `ime-focus-20261010T1740/ime-list.txt` |
| Pre-app window focus | launcher (`NexusLauncherActivity`), not app focus | `ime-focus-20261010T1740/window-focus.before.txt` |

The selected IME was already Latin, so the environment gap was not a missing
IME selection. The setting change made `onCreateInputConnection()` run, and it
is recorded as configuration, not as the fix.

## Product defects found and fixed

1. `RemoteImeView.showInputWhenReady()` used
   `InputMethodManager.SHOW_IMPLICIT`. A real remote-field tap is an explicit
   request, and `SHOW_IMPLICIT` let the IME ignore it. Now `showSoftInput(this, 0)`.
2. `InputMethodManager.showSoftInput` alone is unreliable on API 30+. The
   supported path is the window-owned `WindowInsetsController.show(Type.ime())`.
   The request now runs on the main thread only while the view has window focus,
   with `restartInput` kept on the identity change in `sync()`.
3. A single fire-and-forget request was not a reliable contract, so the show
   path retries on a bounded schedule and stops as soon as the real window
   insets report the IME visible.

Commits in the Android worktree: `ed63ae8`, `2af8511`, `288c0de`.

## Remaining product defect: view focus is lost

The bridge view keeps the IME connection but loses Android *view* focus, and an
unfocused served view refuses the raise. Raw `mServedView` flag groups separate
the two outcomes:

- Passing run: `RemoteImeView{... VFED..... .F...... 0,0-1,1}` (focused).
- Failing runs: `RemoteImeView{... VFED..... ........ 0,0-1,1}` (served, not
  focused), with `mInputShown=false` for the whole bound.

The naive repair (re-claim focus from the retry loop) re-entered
`onCreateInputConnection`, re-armed the retry loop, and produced an IME restart
storm that crashed the app process. That attempt is reverted in `4996d74`; the
current tree holds the bounded retry without the restart cycle.

## Gates

| Gate | Result | Evidence |
| --- | --- | --- |
| Clean first-run state | PASS | `pm-clear.txt` `Success`, empty pairing listing |
| No reverse route for the LAN port | PASS | `adb-reverse.json`: no `tcp:51882` mapping |
| Ordinary LAN admission | PASS | `admission.json`: `wss://192.168.0.3:51882/` |
| Real HTTPS navigation | PASS | `real-path.json`: `https://www.selenium.dev/selenium/web/web-form.html`, scheme `https` |
| Remote video | PASS | `real-path.json`: rendered frames 3 -> 5, ticket 71 |
| Real touch focus | PASS | `real-path.json`: Host target `node-46`, kind `input` |
| Causal pre-tap hidden precondition | PASS | asserted in the run before the tap; the raw pre-tap line is now pulled as `dumpsys-input-method-pre-tap.txt` (the `insets-pass` run predates that pull, so its assertion survives in `instrument-testOrdinaryRealHttpsAndRemoteIme.txt`) |
| System IME raise | PASS once, then intermittent FAIL | `ime-state.json`: `mInputShown=true`, `raiseElapsedMs=654`; later bounded traces never raise |
| ASCII through the raised IME | NOT REACHED | the strict raise gate stopped the run before any key tap |
| Release / disconnect | PASS in the successful run | `release.json`: `disconnected` / `stopped` |
| Chinese composition | UNKNOWN / separate | no claim |

Authoritative artifacts:

- `ime-focus-20261010T1810/insets-pass/` - the one run with a causal IME raise
  (`raiseElapsedMs=654`) plus clean release/disconnect.
- `ime-focus-20261010T1800/final-pass/dumpsys-input-method.txt` and
  `ime-focus-20261010T1850/ascii-retry/dumpsys-input-method.txt` - bounded raw
  sample traces where `mInputShown` stays `false` and the keyboard window is
  absent for the whole bound.

No gate was weakened, skipped, or fabricated. No key-event injection was used
as a substitute for IME input. The ASCII gate stays unclaimed.
