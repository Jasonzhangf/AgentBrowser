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
  *) echo 'Usage: ANDROID_SERIAL=<verified serial> scripts/device.sh install|restart|replay' >&2; exit 2;;
esac
