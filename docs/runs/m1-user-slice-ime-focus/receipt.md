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

## Acceptance-environment limitation: intermittent emulator IME wedge

The product path is proven, but three-run determinism was not achieved because
the emulator IME wedges intermittently. Raw focus-transition traces disprove
an app focus thief: failing runs retain `windowFocus=true`,
`currentFocus=RemoteImeView`, `imeIsFocused=true`, and served-view `.F......`.

The same failure reproduced in the standard Settings Search `EditText`:
`mInputShown=false` with a focused served standard EditText and non-null
connection. `adb shell am force-stop com.google.android.inputmethod.latin`
restored `mInputShown=true` and a visible Latin keyboard. The wedge persisted
across a device reboot before the Settings control later passed. This is an
acceptance-environment limitation, not a product focus defect.

The raw `mServedView` flag groups still show the product view is focused:

- Passing run: `RemoteImeView{... VFED..... .F...... 0,0-1,1}` (focused).
- Failing runs retain `RemoteImeView{... VFED..... .F...... 0,0-1,1}` and
  app-side focus, but `mInputShown=false` for the whole bound. The wedge is
  therefore in the emulator IME service/window state, not product focus.

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
| System IME raise | PASS once, then environment FAILs | Recovery-2: `mInputShown=true`, focused `.F......`, `raiseElapsedMs=290`; post-reboot runs 2/3: `mInputShown=false` for ~8 s despite focused `.F......` |
| ASCII through the raised IME | PASS once | Recovery-2: real keyboard taps committed `hello` to the Host field |
| Three-run determinism | NOT ACHIEVED | Post-reboot run 1 failed DocumentsUI admission; runs 2/3 failed the environment IME gate |
| Release / disconnect | PASS in the successful run | `release.json`: `disconnected` / `stopped` |
| Chinese composition | UNKNOWN / separate | no claim |

Authoritative artifacts:

- `ime-focus-20261010T1830/recovery-2/` - complete product-path PASS:
  raiseElapsedMs=290, focused `.F......`, ASCII `hello` in the Host field, and
  clean release/disconnect.
- `ime-focus-determinism-20261010T1830/post-reboot-2/` and `post-reboot-3/` -
  ordinary LAN admission PASS, but strict IME environment FAILs with raw focus
  traces and `mInputShown=false`.
- `ime-focus-determinism-20261010T1830/device-reset/` - reboot/boot state and
  Settings control experiment.

No gate was weakened, skipped, or fabricated. No key-event injection was used
as a substitute for IME input. Chinese composition remains separate and
unclaimed.
