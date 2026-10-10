"""Real installed APK + private Host fixture + independent DOM evidence."""
import sys

# The replay imports one sibling module; keep its bytecode cache out of the tree.
sys.dont_write_bytecode = True

import json
import base64
import hashlib
import os
import pathlib
import select
import subprocess
import time
import urllib.parse
import uuid

from instrumentation_result import EXPECTED_TEST_CLASS, parse_instrumentation_result

# The replay collects exactly these two entries. The argument-dependent tests
# that need a protected transfer code are deliberately not collected, so the
# parser's fixed two-test contract still describes this run.
REPLAY_METHODS = ("testImeCompositionCancel", "testRealNetworkControlAndFrames")
REPLAY_SELECTOR = ",".join(f"{EXPECTED_TEST_CLASS}#{method}" for method in REPLAY_METHODS)


root = pathlib.Path(__file__).resolve().parent.parent
run_id = uuid.uuid4().hex
evidence = pathlib.Path(os.environ.get("NETWORK_EVIDENCE_DIR", str(root / "evidence" / "network" / run_id))).resolve()
evidence.mkdir(parents=True)
print(f"Network replay evidence: {evidence}", flush=True)
serial = os.environ["ANDROID_SERIAL"]
protocol = pathlib.Path(os.environ["OBSCURA_PROTOCOL_ROOT"]).resolve(strict=True)


def adb_reverse_list():
    return subprocess.run(["adb", "-s", serial, "reverse", "--list"], check=True,
                          capture_output=True, text=True).stdout


def reverse_rows(listing, port):
    """Exact reverse rows for one device-side port on the selected device."""
    device = f"tcp:{port}"
    return [
        line.strip()
        for line in listing.splitlines()
        if len(line.split()) == 3
        # `adb -s <serial> reverse --list` scopes the listing to this device, but
        # recent adb servers print a host-local alias such as `host-19` instead of
        # the remote serial. The command scope is the ownership proof.
        and (line.split()[0] == serial or line.split()[0].startswith("host-"))
        and line.split()[1] == device
        and line.split()[2] == device
    ]


def write_network_path_evidence(session_id, instrumentation):
    """Persist the Android-owned path projection without inventing receipts."""
    frame_ack = instrumentation.get("frameAck") if isinstance(instrumentation, dict) else None
    if not isinstance(frame_ack, dict):
        frame_ack = {}
    rendered_frames = frame_ack.get("renderedFrames")
    displayed = frame_ack.get("displayed") is True
    has_positive_frames = isinstance(rendered_frames, int) and not isinstance(rendered_frames, bool) and rendered_frames > 0
    media_proved = displayed and has_positive_frames
    media = {
        "status": "proved" if media_proved else "unknown",
        "decoded_frames": rendered_frames if has_positive_frames else 0,
        "displayed": displayed if has_positive_frames else False,
        "evidence": ["network-result.json", "network-screen.png"],
    }
    if not media_proved:
        media["reason"] = "Android replay did not provide a positive displayed frame acknowledgement"
    record = {
        "schema": "agentbrowser.network-path.evidence.v1",
        "path_id": "local-direct",
        "network_path": "local",
        "transport": "WSS",
        "session_id": session_id,
        "run_id": run_id,
        "signaling": {
            "status": "proved",
            "evidence": ["network-test.log", "network-host.log"],
        },
        "media": media,
        "operation_evidence": {
            "status": "unknown",
            "evidence": [],
            "reason": "Existing instrumentation operationReceipts omit operation IDs and generations; no operation receipt is projected",
        },
        "operation_receipts": [],
        "source": {
            "root": str(evidence),
            "files": ["network-result.json", "network-test.log", "network-screen.png"],
            "run_id": run_id,
            "producer": "scripts/network-replay.py",
        },
        "result": "UNPROVEN",
    }
    (evidence / "network-path-evidence.json").write_text(json.dumps(record, indent=2) + "\n")


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
# Each run must produce its own app evidence: a stale artifact from an earlier run
# would otherwise be pulled when this run fails before writing it.
subprocess.run(["adb", "-s", serial, "shell", "run-as", "com.agentbrowser.probe",
                "rm", "-rf", "files/network-evidence"], check=True)
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
    reverse_port = None
    passed = False
    try:
        ready = record(fixture, 15)
        pairing = ready["fixture"]
        bundle = pathlib.Path(ready["bundle"]).resolve(strict=True)
        bundle_sha256 = hashlib.sha256(bundle.read_bytes()).hexdigest()
        # The fixture binds loopback on this host, so the device reaches it only
        # through its own loopback while adb owns the exact reverse mapping.
        endpoint = urllib.parse.urlsplit(ready["endpoint"])
        if endpoint.port is None or not 1 <= endpoint.port <= 65535:
            raise RuntimeError(f"Fixture endpoint has no usable TCP port: {ready['endpoint']}")
        reverse_port = endpoint.port
        if reverse_rows(adb_reverse_list(), reverse_port):
            raise RuntimeError(f"Device-side tcp:{reverse_port} already has reverse mappings")
        subprocess.run(["adb", "-s", serial, "reverse", f"tcp:{reverse_port}", f"tcp:{reverse_port}"], check=True)
        reverse_mapping = reverse_rows(adb_reverse_list(), reverse_port)
        if len(reverse_mapping) != 1:
            raise RuntimeError(f"adb reverse tcp:{reverse_port} was not confirmed: {reverse_mapping}")
        (evidence / "adb-reverse.json").write_text(json.dumps({
            "runId": run_id, "serial": serial, "port": reverse_port, "mapping": reverse_mapping[0],
        }, indent=2) + "\n")
        (evidence / "fixture-pairing.json").write_text(json.dumps({
            "runId": run_id,
            "fixture": pairing,
            "deviceId": ready["deviceId"],
            "endpoint": ready["endpoint"],
            "bundleSha256": bundle_sha256,
            "session": ready["session"],
        }, indent=2) + "\n")
        subprocess.run([sys.executable, "scripts/device-pairing.py", "install", pairing], cwd=root, check=True)
        subprocess.run(["bash", "scripts/device.sh", "prepare"], cwd=root, check=True)
        result = subprocess.run(["adb", "-s", serial, "shell", "am", "instrument", "-w", "-r", "-e", "runId", run_id, "-e", "class",
            REPLAY_SELECTOR, "-e", "bundleSha256", bundle_sha256,
            "-e", "initialUrlBase64", base64.b64encode(ready["initialUrl"].encode()).decode(),
            "com.agentbrowser.probe.test/android.test.InstrumentationTestRunner"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90, check=True)
        (evidence / "network-test.log").write_bytes(result.stdout)
        if not parse_instrumentation_result(result.stdout).ok:
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
        if "error" in response:
            raise RuntimeError(f"Independent Host DOM inspection was refused at the end state: {response['error']}")
        dom = json.loads(response["result"]["value"])
        # The commit inserts the committed text exactly once at the caret, so the
        # independent Host check asserts exact-once insertion plus the preserved
        # surrounding text, not a fixed insertion offset.
        text = dom.get("text") or ""
        trace = dom.get("scrollTrace") or []
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in trace):
            raise RuntimeError(f"Host scroll trace is not an integer series: {trace!r}")
        if (dom.get("clicked") != 1 or text.count("中文") != 1
                or text.replace("中文", "") != "native-network-proof"
                or dom.get("scrollY") != 0 or dom.get("maxScroll", 0) < 240):
            raise RuntimeError(f"Host DOM mismatch: {dom}")
        # The Host page records every scroll offset it observes, so the drag is a
        # real positive delta from the pre-drag 0 and the reverse drag restores 0.
        if max(trace, default=0) <= 0:
            raise RuntimeError(f"Host scroll trace does not show a real drag delta: {trace!r}")
        (evidence / "network-scroll-trace.json").write_text(json.dumps({
            "session": ready["session"], "scrollTrace": trace,
            "maxScroll": dom.get("maxScroll"), "finalScrollY": dom.get("scrollY"),
            "restoredToZero": bool(trace) and trace[-1] == 0,
        }, indent=2) + "\n")
        (evidence / "network-dom.json").write_text(json.dumps({"session":ready["session"],"dom":dom},indent=2)+"\n")
        for name in ["result.json", "screen.png", "before.png", "after.png", "reconnected.png", "text-before.png", "text-after.png", "ime-text.png", "ime-trace.txt", "scrolled.png", "scroll-restored.png", "landscape.png", "portrait-restored.png"]:
            result = subprocess.run(["adb", "-s", serial, "exec-out", "run-as", "com.agentbrowser.probe", "cat", f"files/network-evidence/{name}"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
            if result.returncode or not result.stdout or result.stdout.startswith(b"cat: "):
                continue
            if name == "result.json" and json.loads(result.stdout).get("runId") != run_id:
                raise RuntimeError("Instrumentation evidence is not from this replay")
            (evidence / f"network-{name}").write_bytes(result.stdout)
        write_network_path_evidence(str(ready["session"]), instrumentation)
        passed = True
    finally:
        # Preserve app evidence even when instrumentation fails. This runs before fixture
        # teardown, so diagnostic traces and screenshots remain available for diagnosis.
        for name in ["result.json", "ime-trace.txt", "chrome-touch.txt", "rotation-visual.txt", "cancel-before.png", "cancel-after.png", "landscape.png", "portrait-restored.png", "text-before-composition.png", "text-after-cancel.png", "ime-text.png", "scrolled.png", "scroll-restored.png"]:
            pulled = subprocess.run(["adb", "-s", serial, "exec-out", "run-as", "com.agentbrowser.probe", "cat", f"files/network-evidence/{name}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if pulled.returncode == 0 and pulled.stdout and not pulled.stdout.startswith(b"cat: "):
                (evidence / f"network-{name}").write_bytes(pulled.stdout)
        # The ordered IME trace is a required evidence artifact for the composition
        # gates. The device log always carries it, so record it separately from the
        # app file and never let a missing app file drop the evidence.
        logs = subprocess.run(["adb", "-s", serial, "logcat", "-d", "-t", "5000"], stdout=subprocess.PIPE, check=False).stdout
        lines = b"\n".join(line for line in logs.splitlines() if b"IME_DIAGNOSTIC_TRACE" in line)
        if lines:
            (evidence / "network-ime-trace.logcat.txt").write_bytes(lines + b"\n")
            if not (evidence / "network-ime-trace.txt").exists():
                (evidence / "network-ime-trace.txt").write_bytes(lines + b"\n")
        if not passed and fixture.poll() is None:
            try:
                fixture.stdin.write(b"inspect\n"); fixture.stdin.flush()
                inspect = record(fixture, 10)
                (evidence / "network-dom-failing.json").write_text(json.dumps(inspect, indent=2)+"\n")
            except Exception as failure:
                (evidence / "network-dom-failing-error.txt").write_text(str(failure)+"\n")
        try:
            # device_fixture's quit performs CloseSession and waits for Closed before it exits.
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
        finally:
            if reverse_port is not None:
                subprocess.run(["adb", "-s", serial, "reverse", "--remove", f"tcp:{reverse_port}"], check=True)
                if reverse_rows(adb_reverse_list(), reverse_port):
                    raise RuntimeError(f"adb reverse tcp:{reverse_port} survived cleanup")
            if pairing:
                subprocess.run([sys.executable, "scripts/device-pairing.py", "remove", pairing], cwd=root, check=True)

# The ordered IME trace is required evidence for the composition gates, so a run
# without it is not accepted as a pass.
trace = evidence / "network-ime-trace.txt"
if not trace.is_file() or trace.stat().st_size == 0:
    raise RuntimeError(f"Required IME trace artifact is missing from {evidence}")
if passed:
    print("Network device + independent Host DOM replay PASS")
