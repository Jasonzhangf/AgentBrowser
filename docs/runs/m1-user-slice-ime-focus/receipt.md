# Task-13 system IME focus receipt

Run: `evidence/user-slice/ime-focus-20261010T1800/final-pass/`.
The LAN Host lifecycle was not started, stopped, or refreshed by this run.

## Environment

- `show_ime_with_hard_keyboard`: previous `0`, changed to `1`; this is an
  environment configuration step, not a product result.
- `default_input_method`: Latin IME selected:
  `com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME`.
- AVD `ab_m1_n3`: `hw.keyboard=no`, `hw.keyboard.lid=yes`.
- Enabled IMEs: Latin IME and Google TTS.

## Product change

`RemoteImeView.showInputWhenReady()` now uses `showSoftInput(this, 0)`.
`SHOW_IMPLICIT` was a product defect because it allowed the IME to ignore the
show request after a real remote-field tap. The change is committed in
`ed63ae8`. The latest bounded-poll test code is in `74d3692`.

## LAN gates

| Gate | Result | Evidence |
| --- | --- | --- |
| Clean first-run state | PASS | `pm-clear.txt`, empty pairing listing |
| Ordinary LAN admission | PASS | `admission.json`: `wss://192.168.0.3:51882/` |
| No reverse route for LAN port | PASS | `adb-reverse.json`: no `tcp:51882` mapping |
| Real HTTPS navigation | PASS | `real-path.json` from the prior complete stage: selenium web form, `https` |
| Remote video and touch focus | PASS | current instrumentation reached the IME gate after video and Host editor focus |
| System IME focus | **FAIL** | bounded raw samples in `dumpsys-input-method.txt`: `mInputShown=false` through ~8 seconds; `mCurId=...LatinIME`, served view and connection are `RemoteImeView` |
| ASCII through raised IME | NOT REACHED | strict IME gate stopped before keyboard taps |
| Release/disconnect and Host close | NOT REACHED | strict first-failure behavior |
| Chinese composition | UNKNOWN / separate | no claim |

The exact first failing gate is bounded real system IME visibility. The failure
is not a parser artifact or a single immediate sample: the current-run raw
trace samples `mInputShown` every approximately 250 ms for the bounded interval.
The selected IME and RemoteImeView connection are active, but Android does not
set `mInputShown=true`. No test-only key injection or local-widget substitution
was used.
