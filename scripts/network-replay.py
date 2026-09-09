"""Real installed APK + private Host fixture + independent DOM evidence."""
import json
import base64
import hashlib
import os
import pathlib
import re
import select
import subprocess
import sys
import time
import uuid

# Keep this bound to the two public test methods in NetworkDeviceTest. A test
# count change must update this gate before replay can pass.
NETWORK_TEST_COUNT = 2


def instrumentation_passed(output: bytes, expected_test_count: int) -> bool:
    """Accept only a complete, successful result for the fixed test class."""
    if not isinstance(output, bytes) or expected_test_count < 1:
        return False
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return False

    summary_candidates = [(index, line) for index, line in enumerate(lines) if line.startswith("OK (")]
    if len(summary_candidates) != 1:
        return False
    summary_index, summary_line = summary_candidates[0]
    summary = re.fullmatch(r"OK \(([1-9][0-9]*) (test|tests)\)", summary_line)
    if summary is None:
        return False
    count_text = summary.group(1)
    noun = summary.group(2)
    if count_text != str(expected_test_count) or noun != ("test" if expected_test_count == 1 else "tests"):
        return False

    code_candidates = [
        (index, line) for index, line in enumerate(lines) if line.startswith("INSTRUMENTATION_CODE:")
    ]
    if len(code_candidates) != 1:
        return False
    code_index, code_line = code_candidates[0]
    code = re.fullmatch(r"INSTRUMENTATION_CODE: (-?(?:0|[1-9][0-9]*))", code_line)
    return code is not None and code.group(1) == "-1" and code_index > summary_index


root = pathlib.Path(__file__).resolve().parent.parent
run_id = uuid.uuid4().hex
evidence = pathlib.Path(os.environ.get("NETWORK_EVIDENCE_DIR", str(root / "evidence" / "network" / run_id))).resolve()
evidence.mkdir(parents=True)
print(f"Network replay evidence: {evidence}", flush=True)
serial = os.environ["ANDROID_SERIAL"]
protocol = pathlib.Path(os.environ["OBSCURA_PROTOCOL_ROOT"]).resolve(strict=True)
subprocess.run(["bash", "scripts/android.sh", "assembleDebug", "assembleDebugAndroidTest"], cwd=root, check=True)
installed = {}
for package, relative in [
    ("com.agentbrowser.probe", "debug/app-debug.apk"),
    ("com.agentbrowser.probe.test", "androidTest/debug/app-debug-androidTest.apk"),
]:
    apk = root / "apps/android/app/build/outputs/apk" / relative
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    subprocess.run(["adb", "-s", serial, "install", "-r", str(apk)], check=True)
    paths = subprocess.check_output(["adb", "-s", serial, "shell", "pm", "path", package], text=True).splitlines()
    if len(paths) != 1 or not paths[0].startswith("package:"):
        raise RuntimeError(f"Expected one installed APK for {package}: {paths}")
    device_apk = subprocess.check_output(["adb", "-s", serial, "exec-out", "cat", paths[0][8:]])
    if hashlib.sha256(device_apk).hexdigest() != digest:
        raise RuntimeError(f"Installed APK identity mismatch: {package}")
    installed[package] = digest
(evidence / "installed-apks.json").write_text(json.dumps({"runId": run_id, "serial": serial, "sha256": installed}, indent=2)+"\n")
patch = "patch.crates-io.obscura-host-protocol.path=" + json.dumps(str(protocol))
subprocess.run(["cargo", "build", "--release", "--locked", "-p", "agentbrowser-android", "--example", "device_fixture", "--config", patch], cwd=root, check=True)

def record(process, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], max(0, deadline-time.monotonic()))
        if not ready:
            break
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("Fixture ended before evidence")
        return json.loads(line)
    raise TimeoutError("Fixture response deadline")

with (evidence / "network-host.log").open("wb") as log:
    fixture = subprocess.Popen([str(root / "target/release/examples/device_fixture")], cwd=root,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
    pairing = None
    try:
        ready = record(fixture, 15)
        pairing = ready["fixture"]
        subprocess.run([sys.executable, "scripts/device-pairing.py", "install", pairing], cwd=root, check=True)
        subprocess.run(["bash", "scripts/device.sh", "prepare"], cwd=root, check=True)
        result = subprocess.run(["adb", "-s", serial, "shell", "am", "instrument", "-w", "-r", "-e", "runId", run_id, "-e", "class",
            "com.agentbrowser.probe.NetworkDeviceTest", "-e", "initialUrlBase64", base64.b64encode(ready["initialUrl"].encode()).decode(),
            "com.agentbrowser.probe.test/android.test.InstrumentationTestRunner"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90, check=True)
        (evidence / "network-test.log").write_bytes(result.stdout)
        if not instrumentation_passed(result.stdout, NETWORK_TEST_COUNT):
            raise RuntimeError(f"Network instrumentation failed; see {evidence / 'network-test.log'}")
        instrumentation = json.loads(subprocess.check_output(
            ["adb", "-s", serial, "exec-out", "run-as", "com.agentbrowser.probe", "cat", "files/network-evidence/result.json"]
        ))
        required = ["addressNavigation", "keyboardVisible", "compositionStarted", "compositionSendDisabled", "compositionCancelled",
                    "compositionCommitted", "unfinishedCompositionDropped", "disconnectCompositionCancelled",
                    "imeTextEvidence", "imeTextPainted"]
        if not all(instrumentation.get(flag) is True for flag in required):
            raise RuntimeError(f"IME composition evidence incomplete: {instrumentation}")
        fixture.stdin.write(b"inspect\n"); fixture.stdin.flush()
        response = record(fixture, 10)
        dom = json.loads(response["result"]["value"])
        if (dom.get("clicked") != 1 or dom.get("text") != "native-network-proof中文"
                or dom.get("scrollY") != 0 or dom.get("maxScroll", 0) < 240):
            raise RuntimeError(f"Host DOM mismatch: {dom}")
        (evidence / "network-dom.json").write_text(json.dumps({"session":ready["session"],"dom":dom},indent=2)+"\n")
        for name in ["result.json", "screen.png", "before.png", "after.png", "reconnected.png", "text-before.png", "text-after.png", "ime-text.png", "scrolled.png", "scroll-restored.png", "landscape.png", "portrait-restored.png"]:
            result = subprocess.run(["adb", "-s", serial, "exec-out", "run-as", "com.agentbrowser.probe", "cat", f"files/network-evidence/{name}"],
                                    stdout=subprocess.PIPE, check=True)
            if name == "result.json" and json.loads(result.stdout).get("runId") != run_id:
                raise RuntimeError("Instrumentation evidence is not from this replay")
            (evidence / f"network-{name}").write_bytes(result.stdout)
        print("Network device + independent Host DOM replay PASS")
    finally:
        try:
            if pairing:
                subprocess.run([sys.executable, "scripts/device-pairing.py", "remove", pairing], cwd=root, check=True)
        finally:
            if fixture.poll() is None:
                fixture.stdin.write(b"quit\n"); fixture.stdin.flush()
                try:
                    fixture.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    fixture.terminate()
                    fixture.wait(timeout=5)
                    raise RuntimeError("Fixture shutdown deadline exceeded")
            if fixture.returncode:
                raise RuntimeError(f"Fixture failed: {fixture.returncode}")
