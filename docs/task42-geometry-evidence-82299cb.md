# Task-42 scoped geometry evidence

Candidate commit: `82299cb609fc39dc43af4c43bb8aa93f404adbc4`
Candidate tree: `61c28d1fd76f4990d5d7ad52971a4b2f0631f009`
Worktree was clean before and after the commands. No device, lifecycle, runtime, or broad gate was run.

## Environment

```text
OBSCURA_PROTOCOL_ROOT=/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/protocol/browser
OBSCURA_BIN_DIR=/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/target/release
OBSCURA_ENDPOINT_BIND_IP=192.168.0.3
```

## Commands and results

### Android unit tests

```sh
./apps/android/gradlew -p apps/android :app:testDebugUnitTest --no-daemon
```

Exit code: `0`.

Raw output tail:

```text
> Task :app:testDebugUnitTest UP-TO-DATE
BUILD SUCCESSFUL in 9s
25 actionable tasks: 1 executed, 24 up-to-date
```

Output SHA-256: `2bdb70fd248b01d597c7f6653881f2dc9319865eb1625ee9b9867e77a9eaf4af` (`/tmp/task42-evidence/unit.log`).

### Android instrumentation artifact build

```sh
./apps/android/gradlew -p apps/android :app:assembleDebugAndroidTest --no-daemon
```

Exit code: `0`.

Raw output tail:

```text
> Task :app:assembleDebugAndroidTest UP-TO-DATE
BUILD SUCCESSFUL in 6s
49 actionable tasks: 1 executed, 48 up-to-date
```

Output SHA-256: `e74abac16b29b95e421d5c2206e5f817358d3675d77d1569109e96dbdb8e503c` (`/tmp/task42-evidence/instrument.log`).

Artifact SHA-256:

```text
415c7a12d9d19e5b300d677251847d8aee54911ad813eb4b5fc47196acb68043  apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
ba44770550073bef7ed839fa33a3ea092d5c885e406f8a332579da25c398f2b4  apps/android/app/build/outputs/apk/debug/app-debug.apk
```

### Focused live-geometry contract

```sh
node --test scripts/android-geometry-contract.test.mjs
```

Exit code: `0`.

Raw output:

```text
TAP version 13
# Android live geometry contract: 6 passed
# Subtest: scripts/android-geometry-contract.test.mjs
ok 1 - scripts/android-geometry-contract.test.mjs
1..1
# tests 1
# pass 1
# fail 0
```

Output SHA-256: `3092914335915a320e1f2cefda2987c6a24055fdfd901241c8babbc7d3989b7b` (`/tmp/task42-evidence/focused.log`).

Focused source hashes:

```text
934e0ea8ce54b0fdc42ff5d86c99617128b842cf9502e6e377d01acf76d0e1a5  scripts/android-geometry-contract.test.mjs
696c0f854681422630983fa0f6f90216415a47b59f5c7b51acb5593d8cb3c7bd  apps/android/app/src/androidTest/java/com/agentbrowser/probe/NetworkDeviceTest.java
```
