"""Run existing Android browser flow with explicit WebRTC transport selection."""
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

root = pathlib.Path(__file__).resolve().parent.parent
serial = os.environ["ANDROID_SERIAL"]
protocol = pathlib.Path(os.environ["OBSCURA_PROTOCOL_ROOT"]).resolve(strict=True)
host_bind_ip = os.environ["OBSCURA_ENDPOINT_BIND_IP"]
device_bind_ip = os.environ["ANDROID_WEBRTC_BIND_IP"]
if not host_bind_ip or not device_bind_ip:
    raise RuntimeError("OBSCURA_ENDPOINT_BIND_IP and ANDROID_WEBRTC_BIND_IP must be live selected interfaces")
android_home = pathlib.Path(os.environ["ANDROID_HOME"]).resolve(strict=True)
adb = pathlib.Path(os.environ.get("ADB", str(android_home / "platform-tools/adb"))).resolve(strict=True)
run_id = uuid.uuid4().hex
evidence = pathlib.Path(os.environ.get("NETWORK_EVIDENCE_DIR", str(root / "evidence" / "webrtc" / run_id))).resolve()
evidence.mkdir(parents=True)

def adb_run(*args, input_bytes=None, check=True):
    return subprocess.run([str(adb), "-s", serial, *args], input=input_bytes, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=check)

def run_as(*args, input_bytes=None, check=True):
    command = shlex.join(["run-as", "com.agentbrowser.probe", *args])
    return adb_run("shell", "-T", command, input_bytes=input_bytes, check=check)

def installed_hash(package):
    paths = [line[8:] for line in adb_run("shell", "pm", "path", package).stdout.decode().splitlines()
             if line.startswith("package:")]
    if len(paths) != 1:
        return None
    return hashlib.sha256(adb_run("exec-out", "cat", paths[0]).stdout).hexdigest()

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

env = os.environ.copy()
env["PATH"] = str(android_home / "platform-tools") + os.pathsep + env.get("PATH", "")
env["OBSCURA_ENABLE_WEBRTC"] = "1"
before = {"com.agentbrowser.probe": installed_hash("com.agentbrowser.probe"),
          "com.agentbrowser.probe.test": installed_hash("com.agentbrowser.probe.test")}
(evidence / "device-before.json").write_text(json.dumps({
    "runId": run_id, "serial": serial, "model": adb_run("shell", "getprop", "ro.product.model").stdout.decode().strip(),
    "sdk": adb_run("shell", "getprop", "ro.build.version.sdk").stdout.decode().strip(),
    "abi": adb_run("shell", "getprop", "ro.product.cpu.abi").stdout.decode().strip(),
    "hostBindIp": host_bind_ip, "deviceBindIp": device_bind_ip, "installedApks": before,
}, indent=2) + "\n")

subprocess.run(["bash", "scripts/android.sh", "assembleDebug", "assembleDebugAndroidTest"], cwd=root, env=env, check=True)
artifacts = {
    "com.agentbrowser.probe": root / "apps/android/app/build/outputs/apk/debug/app-debug.apk",
    "com.agentbrowser.probe.test": root / "apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk",
}
after = {}
for package, artifact in artifacts.items():
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    adb_run("install", "-r", str(artifact))
    if installed_hash(package) != digest:
        raise RuntimeError(f"Installed APK identity mismatch: {package}")
    after[package] = digest
(evidence / "installed-apks.json").write_text(json.dumps({"runId": run_id, "serial": serial, "before": before, "after": after}, indent=2) + "\n")

patch = "patch.crates-io.obscura-host-protocol.path=" + json.dumps(str(protocol))
subprocess.run(["cargo", "build", "--release", "--locked", "-p", "agentbrowser-android", "--example", "device_fixture", "--config", patch], cwd=root, env=env, check=True)
fixture = None
pairing = None
transport_written = False
with (evidence / "host.log").open("wb") as log:
    try:
        fixture = subprocess.Popen([str(root / "target/release/examples/device_fixture")], cwd=root,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=env)
        ready = record(fixture, 15)
        pairing = ready["fixture"]
        subprocess.run([sys.executable, "scripts/device-pairing.py", "install", pairing], cwd=root, env=env, check=True)
        run_as("sh", "-c", "umask 077; cat > files/pairing/transport.json",
               input_bytes=(json.dumps({"transport": "webrtc", "bind_ip": device_bind_ip}) + "\n").encode())
        transport_written = True
        command = [str(adb), "-s", serial, "shell", "am", "instrument", "-w", "-r",
                   "-e", "runId", run_id, "-e", "initialUrlBase64",
                   base64.b64encode(ready["initialUrl"].encode()).decode(), "-e", "class",
                   "com.agentbrowser.probe.WebRtcDeviceTest,com.agentbrowser.probe.NetworkDeviceTest",
                   "com.agentbrowser.probe.test/android.test.InstrumentationTestRunner"]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180, env=env, check=True)
        (evidence / "instrumentation.log").write_bytes(result.stdout)
        if b"OK (2 tests)" not in result.stdout:
            raise RuntimeError(f"WebRTC instrumentation failed; see {evidence / 'instrumentation.log'}")
        for remote, local in [("files/webrtc-evidence/result.json", evidence / "webrtc-result.json"),
                              ("files/network-evidence/result.json", evidence / "network-result.json"),
                              ("files/network-evidence/ime-text.png", evidence / "network-ime-text.png")]:
            local.write_bytes(adb_run("exec-out", "run-as", "com.agentbrowser.probe", "cat", remote).stdout)
        print(f"WebRTC Android replay PASS: evidence={evidence}")
    finally:
        if transport_written:
            run_as("rm", "files/pairing/transport.json", check=False)
        try:
            if pairing:
                subprocess.run([sys.executable, "scripts/device-pairing.py", "remove", pairing], cwd=root, env=env, check=True)
        finally:
            if fixture and fixture.poll() is None:
                fixture.stdin.write(b"quit\n"); fixture.stdin.flush()
                fixture.wait(timeout=15)
            if fixture and fixture.returncode:
                raise RuntimeError(f"Fixture failed: {fixture.returncode}")
