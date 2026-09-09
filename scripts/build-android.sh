#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/android.sh assembleDebug
mkdir -p generated/modules/android-probe/lib
cp apps/android/app/build/outputs/apk/debug/app-debug.apk generated/modules/android-probe/lib/app-debug.apk
