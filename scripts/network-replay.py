"""Real installed APK + private Host fixture + independent DOM evidence."""
import json
import os
import pathlib
import select
import subprocess
import sys
import time

root = pathlib.Path(__file__).resolve().parent.parent
evidence = root / "evidence"
evidence.mkdir(exist_ok=True)
serial = os.environ["ANDROID_SERIAL"]
protocol = pathlib.Path(os.environ["OBSCURA_PROTOCOL_ROOT"]).resolve(strict=True)
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
        result = subprocess.run(["adb", "-s", serial, "shell", "am", "instrument", "-w", "-r", "-e", "class",
            "com.agentbrowser.probe.NetworkDeviceTest", "com.agentbrowser.probe.test/android.test.InstrumentationTestRunner"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90, check=True)
        (evidence / "network-test.log").write_bytes(result.stdout)
        if b"OK (1 test)" not in result.stdout:
            raise RuntimeError("Network instrumentation failed; see evidence/network-test.log")
        fixture.stdin.write(b"inspect\n"); fixture.stdin.flush()
        response = record(fixture, 10)
        dom = json.loads(response["result"]["value"])
        if dom != {"clicked": 1, "text": "native-network-proof"}:
            raise RuntimeError(f"Host DOM mismatch: {dom}")
        (evidence / "network-dom.json").write_text(json.dumps({"session":ready["session"],"dom":dom},indent=2)+"\n")
        for name in ["result.json", "screen.png", "before.png", "after.png", "reconnected.png"]:
            result = subprocess.run(["adb", "-s", serial, "exec-out", "run-as", "com.agentbrowser.probe", "cat", f"files/network-evidence/{name}"],
                                    stdout=subprocess.PIPE, check=True)
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
