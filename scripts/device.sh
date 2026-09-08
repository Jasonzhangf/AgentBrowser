#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${ANDROID_SERIAL:?Set ANDROID_SERIAL to the verified target device}"
app=com.agentbrowser.probe

prepare_device() {
  local power_state policy_state window_state

  echo "Android replay preflight: waking display and dismissing keyguard"
  adb -s "$ANDROID_SERIAL" shell input keyevent KEYCODE_WAKEUP
  adb -s "$ANDROID_SERIAL" shell wm dismiss-keyguard
  adb -s "$ANDROID_SERIAL" shell cmd statusbar collapse

  for _ in {1..40}; do
    power_state=$(adb -s "$ANDROID_SERIAL" shell dumpsys power)
    policy_state=$(adb -s "$ANDROID_SERIAL" shell dumpsys window policy)
    if printf '%s\n' "$power_state" | rg '^[[:space:]]*mWakefulness=Awake$' >/dev/null \
        && printf '%s\n' "$policy_state" | rg '^[[:space:]]+showing=false$' >/dev/null \
        && printf '%s\n' "$policy_state" | rg '^[[:space:]]+inputRestricted=false$' >/dev/null \
        && printf '%s\n' "$policy_state" | rg '^[[:space:]]+screenState=SCREEN_STATE_ON$' >/dev/null \
        && printf '%s\n' "$policy_state" | rg '^[[:space:]]+interactiveState=INTERACTIVE_STATE_AWAKE$' >/dev/null; then
      break
    fi
    sleep 0.1
  done

  if ! printf '%s\n' "$power_state" | rg '^[[:space:]]*mWakefulness=Awake$' >/dev/null; then
    echo "Android replay preflight failed: display is not awake (mWakefulness=Awake not observed)" >&2
    return 1
  fi
  if ! printf '%s\n' "$policy_state" | rg '^[[:space:]]+showing=false$' >/dev/null \
      || ! printf '%s\n' "$policy_state" | rg '^[[:space:]]+inputRestricted=false$' >/dev/null \
      || ! printf '%s\n' "$policy_state" | rg '^[[:space:]]+screenState=SCREEN_STATE_ON$' >/dev/null \
      || ! printf '%s\n' "$policy_state" | rg '^[[:space:]]+interactiveState=INTERACTIVE_STATE_AWAKE$' >/dev/null; then
    echo "Android replay preflight failed: keyguard or display policy is still blocking input" >&2
    return 1
  fi

  adb -s "$ANDROID_SERIAL" shell am start -W -n "$app/.MainActivity"
  for _ in {1..40}; do
    window_state=$(adb -s "$ANDROID_SERIAL" shell dumpsys window)
    if printf '%s\n' "$window_state" | rg 'mCurrentFocus=.*com\.agentbrowser\.probe/com\.agentbrowser\.probe\.MainActivity' >/dev/null \
        && printf '%s\n' "$window_state" | rg 'mFocusedApp=.*com\.agentbrowser\.probe/\.MainActivity' >/dev/null; then
      echo "Android replay preflight PASS: display awake, keyguard clear, probe Activity focused"
      return 0
    fi
    sleep 0.1
  done

  echo "Android replay preflight failed: probe Activity is not the focused window" >&2
  return 1
}

case "${1:-}" in
  install)
    adb -s "$ANDROID_SERIAL" install -r apps/android/app/build/outputs/apk/debug/app-debug.apk
    ;;
  restart)
    adb -s "$ANDROID_SERIAL" shell am force-stop "$app"
    adb -s "$ANDROID_SERIAL" shell am start -W -n "$app/.MainActivity"
    ;;
  prepare)
    prepare_device
    ;;
  replay)
    adb -s "$ANDROID_SERIAL" install -r apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
    prepare_device
    mkdir -p evidence
    adb -s "$ANDROID_SERIAL" shell am instrument -w -r -e class com.agentbrowser.probe.ProbeDeviceTest,com.agentbrowser.probe.AnnexBDeviceTest "$app.test/android.test.InstrumentationTestRunner" | tee evidence/device-test.log
    # am instrument can return shell success after a JUnit failure.
    rg -q '^OK \(2 tests\)' evidence/device-test.log
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/probe-evidence/result.json > evidence/device-result.json
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/probe-evidence/frame-a.png > evidence/frame-a.png
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/probe-evidence/frame-b.png > evidence/frame-b.png
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/annexb-evidence/result.json > evidence/annexb-result.json
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/annexb-evidence/before.png > evidence/annexb-before.png
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/annexb-evidence/after.png > evidence/annexb-after.png
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/annexb-evidence/resized-coded.png > evidence/annexb-coded.png
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/annexb-evidence/resized-visible.png > evidence/annexb-visible.png
    python3 scripts/network-replay.py
    ;;
  account-replay)
    python3 scripts/account-replay.py
    ;;
  *) echo 'Usage: ANDROID_SERIAL=<verified serial> scripts/device.sh install|restart|prepare|replay|account-replay' >&2; exit 2;;
esac
