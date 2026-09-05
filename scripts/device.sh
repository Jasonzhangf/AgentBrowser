#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${ANDROID_SERIAL:?Set ANDROID_SERIAL to the verified target device}"
app=com.agentbrowser.probe
case "${1:-}" in
  install)
    adb -s "$ANDROID_SERIAL" install -r apps/android/app/build/outputs/apk/debug/app-debug.apk
    ;;
  restart)
    adb -s "$ANDROID_SERIAL" shell am force-stop "$app"
    adb -s "$ANDROID_SERIAL" shell am start -W -n "$app/.MainActivity"
    ;;
  replay)
    adb -s "$ANDROID_SERIAL" install -r apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
    mkdir -p evidence
    adb -s "$ANDROID_SERIAL" shell am instrument -w -r -e class com.agentbrowser.probe.ProbeDeviceTest "$app.test/android.test.InstrumentationTestRunner" | tee evidence/device-test.log
    # am instrument can return shell success after a JUnit failure.
    rg -q '^OK \(1 test\)' evidence/device-test.log
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/probe-evidence/result.json > evidence/device-result.json
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/probe-evidence/frame-a.png > evidence/frame-a.png
    adb -s "$ANDROID_SERIAL" exec-out run-as "$app" cat files/probe-evidence/frame-b.png > evidence/frame-b.png
    ;;
  *) echo 'Usage: ANDROID_SERIAL=<verified serial> scripts/device.sh install|restart|replay' >&2; exit 2;;
esac
