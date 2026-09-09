"""Run existing Android browser flow with explicit WebRTC transport selection."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import select
import shlex
import subprocess
import sys
import time
import uuid

from instrumentation_result import (
    EXPECTED_INSTRUMENTATION_CODE,
    InstrumentationExpectation,
    InstrumentationResult,
    parse_instrumentation_result,
)


ROOT = pathlib.Path(__file__).resolve().parent.parent
WEBRTC_TEST_ENTRIES = (
    ("com.agentbrowser.probe.WebRtcDeviceTest", "testTypedWebRtcReachesNativeMedia"),
    ("com.agentbrowser.probe.NetworkDeviceTest", "testImeCompositionCancel"),
    ("com.agentbrowser.probe.NetworkDeviceTest", "testRealNetworkControlAndFrames"),
)
WEBRTC_EXPECTATION = InstrumentationExpectation(
    test_class=WEBRTC_TEST_ENTRIES[0][0],
    test_count=len(WEBRTC_TEST_ENTRIES),
    summary=f"OK ({len(WEBRTC_TEST_ENTRIES)} tests)",
    instrumentation_code=EXPECTED_INSTRUMENTATION_CODE,
    test_entries=WEBRTC_TEST_ENTRIES,
)
WEBRTC_INSTRUMENTATION_CLASSES = ",".join(dict.fromkeys(test_class for test_class, _ in WEBRTC_TEST_ENTRIES))


def validate_instrumentation_output(output: bytes) -> InstrumentationResult:
    return parse_instrumentation_result(output, expected=WEBRTC_EXPECTATION)


def adb_run(adb, serial, *args, input_bytes=None, check=True):
    return subprocess.run([str(adb), "-s", serial, *args], input=input_bytes, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=check)


def run_as(adb, serial, *args, input_bytes=None, check=True):
    command = shlex.join(["run-as", "com.agentbrowser.probe", *args])
    return adb_run(adb, serial, "shell", "-T", command, input_bytes=input_bytes, check=check)


def installed_hash(adb, serial, package):
    paths = [line[8:] for line in adb_run(adb, serial, "shell", "pm", "path", package).stdout.decode().splitlines()
             if line.startswith("package:")]
    if len(paths) != 1:
        return None
    return hashlib.sha256(adb_run(adb, serial, "exec-out", "cat", paths[0]).stdout).hexdigest()

def record(process, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
        if not ready:
            break
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("Fixture ended before evidence")
        return json.loads(line)
    raise TimeoutError("Fixture response deadline")

def main() -> int:
    serial = os.environ["ANDROID_SERIAL"]
    protocol = pathlib.Path(os.environ["OBSCURA_PROTOCOL_ROOT"]).resolve(strict=True)
    host_bind_ip = os.environ["OBSCURA_ENDPOINT_BIND_IP"]
    device_bind_ip = os.environ["ANDROID_WEBRTC_BIND_IP"]
    if not host_bind_ip or not device_bind_ip:
        raise RuntimeError("OBSCURA_ENDPOINT_BIND_IP and ANDROID_WEBRTC_BIND_IP must be live selected interfaces")
    android_home = pathlib.Path(os.environ["ANDROID_HOME"]).resolve(strict=True)
    adb = pathlib.Path(os.environ.get("ADB", str(android_home / "platform-tools/adb"))).resolve(strict=True)
    run_id = uuid.uuid4().hex
    evidence = pathlib.Path(os.environ.get("NETWORK_EVIDENCE_DIR", str(ROOT / "evidence" / "webrtc" / run_id))).resolve()
    evidence.mkdir(parents=True)

    env = os.environ.copy()
    env["PATH"] = str(android_home / "platform-tools") + os.pathsep + env.get("PATH", "")
    env["OBSCURA_ENABLE_WEBRTC"] = "1"
    before = {"com.agentbrowser.probe": installed_hash(adb, serial, "com.agentbrowser.probe"),
              "com.agentbrowser.probe.test": installed_hash(adb, serial, "com.agentbrowser.probe.test")}
    (evidence / "device-before.json").write_text(json.dumps({
        "runId": run_id, "serial": serial, "model": adb_run(adb, serial, "shell", "getprop", "ro.product.model").stdout.decode().strip(),
        "sdk": adb_run(adb, serial, "shell", "getprop", "ro.build.version.sdk").stdout.decode().strip(),
        "abi": adb_run(adb, serial, "shell", "getprop", "ro.product.cpu.abi").stdout.decode().strip(),
        "hostBindIp": host_bind_ip, "deviceBindIp": device_bind_ip, "installedApks": before,
    }, indent=2) + "\n")

    subprocess.run(["bash", "scripts/android.sh", "assembleDebug", "assembleDebugAndroidTest"], cwd=ROOT, env=env, check=True)
    artifacts = {
        "com.agentbrowser.probe": ROOT / "apps/android/app/build/outputs/apk/debug/app-debug.apk",
        "com.agentbrowser.probe.test": ROOT / "apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk",
    }
    after = {}
    for package, artifact in artifacts.items():
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        adb_run(adb, serial, "install", "-r", str(artifact))
        if installed_hash(adb, serial, package) != digest:
            raise RuntimeError(f"Installed APK identity mismatch: {package}")
        after[package] = digest
    (evidence / "installed-apks.json").write_text(json.dumps({"runId": run_id, "serial": serial, "before": before, "after": after}, indent=2) + "\n")

    patch = "patch.crates-io.obscura-host-protocol.path=" + json.dumps(str(protocol))
    subprocess.run(["cargo", "build", "--release", "--locked", "-p", "agentbrowser-android", "--example", "device_fixture", "--config", patch], cwd=ROOT, env=env, check=True)
    fixture = None
    pairing = None
    transport_written = False
    with (evidence / "host.log").open("wb") as log:
        try:
            fixture = subprocess.Popen([str(ROOT / "target/release/examples/device_fixture")], cwd=ROOT,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=env)
            ready = record(fixture, 15)
            pairing = ready["fixture"]
            subprocess.run([sys.executable, "scripts/device-pairing.py", "install", pairing], cwd=ROOT, env=env, check=True)
            run_as(adb, serial, "sh", "-c", "umask 077; cat > files/pairing/transport.json",
                   input_bytes=(json.dumps({"transport": "webrtc", "bind_ip": device_bind_ip}) + "\n").encode())
            transport_written = True
            command = [str(adb), "-s", serial, "shell", "am", "instrument", "-w", "-r",
                       "-e", "runId", run_id, "-e", "initialUrlBase64",
                       base64.b64encode(ready["initialUrl"].encode()).decode(), "-e", "class",
                       WEBRTC_INSTRUMENTATION_CLASSES,
                       "com.agentbrowser.probe.test/android.test.InstrumentationTestRunner"]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, env=env, check=False)
            (evidence / "instrumentation.log").write_bytes(result.stdout)
            instrumentation = validate_instrumentation_output(result.stdout)
            (evidence / "instrumentation-validation.json").write_text(
                json.dumps(instrumentation.to_dict(), indent=2) + "\n"
            )
            if result.returncode != 0:
                raise RuntimeError(f"WebRTC instrumentation command failed with exit {result.returncode}; see {evidence / 'instrumentation.log'}")
            if not instrumentation.ok:
                error = instrumentation.error
                detail = f"{error.code}: {error.message}" if error else "unknown instrumentation validation failure"
                raise RuntimeError(f"WebRTC instrumentation failed ({detail}); see {evidence / 'instrumentation.log'}")
            for remote, local in [("files/webrtc-evidence/result.json", evidence / "webrtc-result.json"),
                                  ("files/network-evidence/result.json", evidence / "network-result.json"),
                                  ("files/network-evidence/ime-text.png", evidence / "network-ime-text.png")]:
                local.write_bytes(adb_run(adb, serial, "exec-out", "run-as", "com.agentbrowser.probe", "cat", remote).stdout)
            print(f"WebRTC Android replay PASS: evidence={evidence}")
        finally:
            if transport_written:
                run_as(adb, serial, "rm", "files/pairing/transport.json", check=False)
            try:
                if pairing:
                    subprocess.run([sys.executable, "scripts/device-pairing.py", "remove", pairing], cwd=ROOT, env=env, check=True)
            finally:
                if fixture and fixture.poll() is None:
                    fixture.stdin.write(b"quit\n"); fixture.stdin.flush()
                    fixture.wait(timeout=15)
                if fixture and fixture.returncode:
                    raise RuntimeError(f"Fixture failed: {fixture.returncode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
