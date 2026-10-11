# R2 Android black-box receipt

Source of truth: `docs/m1/blackbox-acceptance.md`, case row BB11 Profile
(line 25 in the authoritative contract). The contract requires visible UI
profile selection, a real site login, detach/reconnect, and a formal Host
service restart, with the same account visibly logged in afterward. Cookie
files or counts are not evidence.

## Scope and environment

- Device: `emulator-5554`, serialized Android flow.
- Dedicated Host root: `$HOME/Library/Application Support/AgentBrowser/host-service-lan`.
- Lifecycle entrypoint: `m1-user-slice-host-integration-20261010/scripts/host-service/host-service`.
- Host was started through that entrypoint for this attempt. The lifecycle
  `stop` command returned an unsupported-state/config-key error and status
  still reports `state=running`; no direct PID kill was used. No task6 Host or
  user `weibo` profile was touched.
- No credentials, CAPTCHA, private bridge, DOM value write, URL-label
  assertion, cookie-count assertion, or `adb input text` was used.

## Ordered result

| Case | Result | First failing gate / evidence |
| --- | --- | --- |
| **BB11 Profile** | **NOT_EXECUTED** | Android capability projection reports `profiles=false`; `NetworkSession.java:233` states no Android native profile owner exists. The required visible profile create/select/login path is absent. |
| BB10 dual-end reconnect | NOT_EXECUTED | Serialized order requires BB11 first; no dual-end trial was started. |
| BB04 gestures | NOT_EXECUTED | Serialized order requires BB11 first; no gesture trial was started. |
| BB08 focus/layout | NOT_EXECUTED | Serialized order requires BB11 first; no keyboard-dependent trial was started. |
| BB14 continuous use | NOT_EXECUTED | Serialized order requires BB11 first; no continuous-use trial was started. |

BB11 is not marked PASS or BLOCKED: the first gate is a confirmed product
capability absence, so the correct contract status is NOT_EXECUTED. Human login
or CAPTCHA was not reached and no credential was requested or read.

## Evidence

- `evidence/r2-android/BB11/<run-id>/host-start.txt`: lifecycle start output.
- `evidence/r2-android/BB11/<run-id>/host-status-running.json`: live dedicated
  Host status after start.
- `evidence/r2-android/BB11/<run-id>/android-capabilities.txt`: visible
  capability readback showing `profiles=false`.
- `evidence/r2-android/BB11/<run-id>/host-stop.txt`: lifecycle stop receipt.
- `evidence/r2-android/BB11/<run-id>/host-status-stopped.json`: final stopped
  state.

## Product/environment split

This R2 result is independent of the earlier emulator IME limitation. BB11
fails before any keyboard or login step because the Android product has no
profile owner. The earlier R1 IME work proved the product IME path once and
identified an emulator-wide intermittent IME wedge; it does not satisfy BB11.

No R2 case is claimed PASS. Chinese composition remains separate and
unclaimed.
