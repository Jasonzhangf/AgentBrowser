"""Real user path on the live service-lifecycle Host.

Ordinary no-code import, ordinary connect, a real HTTPS document through the
ordinary address bar, remote video, a real touch, and the RAW system IME gate.
The exported bundle carries only the necessary connection configuration.
"""
import sys

# The replay imports one sibling module; keep its bytecode cache out of the tree.
sys.dont_write_bytecode = True

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time

from instrumentation_result import InstrumentationExpectation, parse_instrumentation_result

TEST_CLASS = "com.agentbrowser.probe.NetworkDeviceTest"
RUNNER = "com.agentbrowser.probe.test/android.test.InstrumentationTestRunner"
ADMISSION_METHOD = "testOrdinaryRealPathAdmission"
REAL_PATH_METHOD = os.environ.get("REAL_PATH_METHOD", "testOrdinaryRealHttpsAndRemoteIme")
IME_METHOD = "testOrdinaryRealHttpsAndRemoteIme"
# R2 black-box slices write one JSON receipt each; the IME run keeps its own list.
SLICE_ARTIFACTS = {
    "testBb04GestureSlice": "bb04-gesture.json",
    "testBb10ReconnectSlice": "bb10-reconnect.json",
    "testBb10TransportProbe": "bb10-transport.json",
    "testBb08FocusLayoutSlice": "bb08-focus-layout.json",
    "testBb14ContinuousSlice": "bb14-continuous.json",
    "testRemoteImeReraiseSlice": "ime-reraise.json",
    "testRemoteImeNoEditorSlice": "ime-no-editor.json",
}
SLICE_EXTRA_ARTIFACTS = {
    "testRemoteImeReraiseSlice": ("focus-trace.txt", "dumpsys-input-method.txt",
                                  "dumpsys-input-method-pre-tap.txt"),
}

root = pathlib.Path(__file__).resolve().parent.parent
serial = os.environ["ANDROID_SERIAL"]
launcher = pathlib.Path(os.environ.get("HOST_SERVICE_LAUNCHER", str(root / "scripts/host-service/host-service"))).resolve()
service_root = pathlib.Path(
    os.environ.get("HOST_SERVICE_ROOT", str(pathlib.Path.home() / "Library/Application Support/AgentBrowser/host-service"))
).expanduser()
page_url = os.environ.get("REAL_PATH_URL", "https://www.selenium.dev/selenium/web/web-form.html")
ascii_text = os.environ.get("REAL_PATH_TEXT", "hello")
tap_x = os.environ.get("REAL_PATH_TAP_X", "196")
tap_y = os.environ.get("REAL_PATH_TAP_Y", "107")
endpoint_port = int(os.environ.get("HOST_ENDPOINT_PORT", "51880"))
expected_endpoint = os.environ.get("EXPECTED_ENDPOINT", "")
no_reverse = os.environ.get("NO_ADB_REVERSE", "0") == "1"
# Fresh first-run state: "clear" uses pm clear; "reinstall" uses a real
# uninstall + install, which some production ROMs require because they deny the
# shell CLEAR_APP_USER_DATA permission.
fresh_state_mode = os.environ.get("FRESH_STATE_MODE", "clear")
app_apk = pathlib.Path(os.environ.get(
    "ANDROID_APP_APK", str(root / "apps/android/app/build/outputs/apk/debug/app-debug.apk"))).resolve()
test_apk = pathlib.Path(os.environ.get(
    "ANDROID_TEST_APK", str(root / "apps/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"))).resolve()
# A stalled adb install must fail loudly instead of holding the gate open.
install_timeout = int(os.environ.get("ANDROID_INSTALL_TIMEOUT_SECONDS", "300"))

run_started_at = time.time()
run_id = time.strftime("%Y%m%dT%H%M%S", time.localtime(run_started_at))
evidence = pathlib.Path(
    os.environ.get("REAL_PATH_EVIDENCE_DIR", str(root / "evidence" / "user-slice" / f"real-path-{run_id}"))
).resolve()
evidence.mkdir(parents=True, exist_ok=True)
print(f"Real-path replay evidence: {evidence}", flush=True)


def adb(*args, **kwargs):
    return subprocess.run(["adb", "-s", serial, *args], check=True, capture_output=True, **kwargs)


def shell(command):
    return adb("shell", command).stdout.decode("utf-8", "replace")


def pull(name):
    """Copy one app-private evidence file out; a missing file is not an error."""
    result = subprocess.run(
        ["adb", "-s", serial, "exec-out", "run-as", "com.agentbrowser.probe", "cat", f"files/network-evidence/{name}"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    if result.returncode or not result.stdout or result.stdout.startswith(b"cat: "):
        return None
    (evidence / name).write_bytes(result.stdout)
    return result.stdout


def instrumentation(method, extra, expected_entries):
    command = ["adb", "-s", serial, "shell", "am", "instrument", "-w", "-r", "-e", "class", f"{TEST_CLASS}#{method}"]
    for name, value in extra:
        command += ["-e", name, value]
    command.append(RUNNER)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240, check=False)
    (evidence / f"instrument-{method}.txt").write_bytes(result.stdout)
    expectation = InstrumentationExpectation(
        test_class=TEST_CLASS, test_count=1, summary="OK (1 test)",
        instrumentation_code=-1, test_entries=((TEST_CLASS, method),),
    )
    parsed = parse_instrumentation_result(result.stdout, expectation)
    return parsed


gates = {}


def gate(name, passed, detail):
    gates[name] = {"result": "PASS" if passed else "FAIL", "detail": detail}
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}", flush=True)
    return passed


def install_apk(path, label):
    """Install one APK under a bounded wait; a stalled install fails explicitly."""
    try:
        result = subprocess.run(
            ["adb", "-s", serial, "install", "-r", "-t", str(path)],
            capture_output=True, text=True, timeout=install_timeout,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(f"{label} install exceeded {install_timeout}s: {path}")
    if result.returncode != 0 or "Success" not in result.stdout:
        raise SystemExit(f"{label} install failed: {result.stdout.strip()} {result.stderr.strip()}")
    return result.stdout.strip()


def installed_base_apk_sha256(package):
    """Hash the base.apk the device actually installed, not the local file."""
    listing = shell(f"pm path {package}").strip()
    paths = [line[len("package:"):] for line in listing.splitlines()
             if line.startswith("package:") and line.endswith("base.apk")]
    if not paths:
        raise SystemExit(f"no installed base.apk path for {package}: {listing!r}")
    words = shell(f"sha256sum {paths[0]}").strip().split()
    if not words:
        raise SystemExit(f"cannot hash the installed base.apk for {package}: {paths[0]}")
    return paths[0], words[0]


private = pathlib.Path(tempfile.mkdtemp(prefix="real-path-"))
os.chmod(private, 0o700)
code = None
created_reverse = False
try:
    # LAN acceptance must begin with the real empty first-run pairing state.
    if expected_endpoint:
        # Drop the app as the IME's served client first: clearing its data while
        # the system IME still holds the connection desyncs the IME service.
        adb("shell", "am", "force-stop", "com.agentbrowser.probe")
        if fresh_state_mode == "reinstall":
            # Some production ROMs deny shell CLEAR_APP_USER_DATA. Record the raw
            # denial, then reach the empty first-run state with a real uninstall
            # + install, which also matches BB01's new-install precondition. The
            # uninstall is idempotent: an absent package is the state we want.
            denied = subprocess.run(
                ["adb", "-s", serial, "shell", "pm", "clear", "com.agentbrowser.probe"],
                capture_output=True, text=True, check=False,
            )
            removed = subprocess.run(
                ["adb", "-s", serial, "shell", "pm", "uninstall", "com.agentbrowser.probe"],
                capture_output=True, text=True, check=False,
            ).stdout.strip()
            still = shell("pm list packages | grep -x 'package:com.agentbrowser.probe' || true").strip()
            if still:
                raise SystemExit(f"fresh-state uninstall left the app installed: {removed!r}")
            install_apk(app_apk, "app")
            install_apk(test_apk, "androidTest")
            installed_path, installed_sha = installed_base_apk_sha256("com.agentbrowser.probe")
            frozen_sha = hashlib.sha256(app_apk.read_bytes()).hexdigest()
            (evidence / "pm-clear.txt").write_text(
                "fresh state mode=reinstall\n"
                f"adb shell pm clear rc={denied.returncode}\n"
                f"stdout={denied.stdout.strip()}\nstderr={denied.stderr.strip()}\n"
                f"uninstall={removed!r}\napp apk={app_apk}\ntest apk={test_apk}\n")
            (evidence / "installed-apk.json").write_text(json.dumps({
                "frozenApk": str(app_apk), "frozenSha256": frozen_sha,
                "installedPath": installed_path, "installedSha256": installed_sha,
                "matches": installed_sha == frozen_sha,
            }, indent=2) + "\n")
            if installed_sha != frozen_sha:
                raise SystemExit(
                    f"installed base.apk hash {installed_sha} does not match the frozen APK {frozen_sha}")
        else:
            clear = adb("shell", "pm", "clear", "com.agentbrowser.probe").stdout.decode("utf-8", "replace")
            (evidence / "pm-clear.txt").write_text(
                "fresh state mode=clear\nadb shell pm clear com.agentbrowser.probe\n" + clear)
            if "Success" not in clear:
                raise SystemExit("pm clear did not report Success")
        empty = shell("find /data/data/com.agentbrowser.probe/files/pairing -maxdepth 2 -print 2>/dev/null || true")
        (evidence / "empty-pairing-listing.txt").write_text(empty)
        if empty.strip():
            raise SystemExit("app pairing directory is not empty after the fresh-state step")

        # Environment precondition, not a product result: the system IME can
        # wedge after repeated clear cycles, and the wedge is cross-app (a
        # standard Settings search field cannot raise it either). Reset the IME
        # process and record the state before and after.
        ime_id = shell("settings get secure default_input_method").strip()
        ime_package = ime_id.split("/")[0]
        before = shell("dumpsys input_method | grep -E 'mInputShown=|mIsInputViewShown'")
        adb("shell", "am", "force-stop", ime_package)
        adb("shell", "input", "keyevent", "4")
        time.sleep(2)
        after = shell("dumpsys input_method | grep -E 'mInputShown=|mIsInputViewShown'")
        selected_after = shell("settings get secure default_input_method").strip()
        (evidence / "ime-environment-reset.txt").write_text(
            f"ime={ime_id}\n-- before reset --\n{before}\n"
            f"adb shell am force-stop {ime_package}\n-- after reset --\n{after}\n"
            f"selected after reset={selected_after}\n")
        # The reset must not change the selected IME; the raise itself is proven
        # by the run's own gate.
        if selected_after != ime_id:
            raise SystemExit("the IME environment reset changed the selected input method")

    # Loopback runs use adb reverse. LAN runs must prove the selected port has no
    # reverse mapping, so their route remains a real LAN route.
    reverse_before = adb("reverse", "--list").stdout.decode("utf-8", "replace").strip().splitlines()
    port_mapping = [line for line in reverse_before if f"tcp:{endpoint_port}" in line]
    if no_reverse and port_mapping:
        raise SystemExit(f"LAN run refuses existing adb reverse mapping for tcp:{endpoint_port}")
    if not no_reverse:
        adb("reverse", f"tcp:{endpoint_port}", f"tcp:{endpoint_port}")
        created_reverse = True
    reverse_after = adb("reverse", "--list").stdout.decode("utf-8", "replace").strip().splitlines()
    (evidence / "adb-reverse.json").write_text(json.dumps(
        {"runId": run_id, "serial": serial, "port": endpoint_port,
         "noReverse": no_reverse, "before": reverse_before, "after": reverse_after}, indent=2) + "\n")

    # Step 1: export the pairing bundle. It carries only the necessary
    # connection configuration: endpoint, device id and the paired client
    # certificate/key material, written as a no-code PKCS#12.
    exported = subprocess.run(
        [str(launcher), "trust", "export", "--root", str(service_root),
         "--output", str(private / "pairing.abconnection")],
        capture_output=True, text=True, check=True,
    )
    try:
        json.loads(exported.stdout)
    except ValueError:
        raise SystemExit("trust export did not return JSON")
    bundle = private / "pairing.abconnection"
    bundle_sha256 = hashlib.sha256(bundle.read_bytes()).hexdigest()

    # Step 2: push the bundle and prove the device copy is byte-identical.
    adb("push", str(bundle), "/sdcard/Download/pairing.abconnection")
    device_sha256 = shell(f"sha256sum /sdcard/Download/pairing.abconnection").split()[0]
    (evidence / "bundle.json").write_text(json.dumps({
        "runId": run_id, "serial": serial, "serviceRoot": str(service_root),
        "endpointPort": endpoint_port, "bundleSha256": bundle_sha256, "deviceSha256": device_sha256,
        "deviceCopyMatches": device_sha256 == bundle_sha256,
    }, indent=2) + "\n")
    if not gate("bundle-identity", device_sha256 == bundle_sha256,
                f"device sha256 {device_sha256} vs exported {bundle_sha256}"):
        raise SystemExit(2)

    # Step 3: the ordinary DocumentsUI import of the no-code bundle.
    admission = instrumentation(ADMISSION_METHOD, [], ((TEST_CLASS, ADMISSION_METHOD),))
    admission_evidence = pull("admission.json")
    if admission_evidence is None:
        admission_evidence = b"{}"
    admission_json = json.loads(admission_evidence)
    admitted_endpoint = str(admission_json.get("endpoint") or "")
    # The frozen minimum contract publishes exactly endpoint.txt and token.
    materials_ok = bool(admission_json.get("endpointPublished")) and bool(admission_json.get("tokenPublished"))
    admission_ok = (admission.ok and materials_ok
                    and (not expected_endpoint or expected_endpoint in admitted_endpoint))
    gate("ordinary-admission", admission_ok,
         f"panel={admission_json.get('panelState')} endpoint={admitted_endpoint} expected={expected_endpoint or None} "
         f"endpointPublished={admission_json.get('endpointPublished')} "
         f"tokenPublished={admission_json.get('tokenPublished')} parser={admission.status}")
    if not admission_ok:
        raise SystemExit("ordinary admission did not prove the expected endpoint; stopping before connect/browse")

    # Steps 4-9: connect, real HTTPS document, video, real touch, system IME, release.
    real_path = instrumentation(REAL_PATH_METHOD, [
        ("pageUrl", page_url), ("asciiText", ascii_text), ("tapX", tap_x), ("tapY", tap_y),
    ], ((TEST_CLASS, REAL_PATH_METHOD),))
    if REAL_PATH_METHOD == IME_METHOD:
        for name in ["real-path.json", "dumpsys-input-method.txt", "dumpsys-input-method-pre-tap.txt",
                     "dumpsys-input-method-after-typing.txt", "focus-trace.txt",
                     "logcat-focus.txt", "ime-state.json", "ime-key-touches.txt", "ime-keyboard.txt",
                     "real-path-before-tap.png", "real-path-after-tap.png", "release.json"]:
            pull(name)
        real_path_json = {}
        current_artifacts = [evidence / name for name in
            ["real-path.json", "dumpsys-input-method.txt", "ime-state.json", "logcat-focus.txt"]]
        if not all(path.is_file() and path.stat().st_mtime >= run_started_at for path in current_artifacts):
            raise SystemExit("current-run IME/navigation artifacts are missing or stale")
        real_path_json = json.loads((evidence / "real-path.json").read_text())

        final_scheme = real_path_json.get("finalScheme")
        gate("real-https-navigation", final_scheme == "https",
             f"finalUrl={real_path_json.get('finalUrl')} scheme={final_scheme} "
             f"documentRevision={real_path_json.get('documentRevisionBefore')}->{real_path_json.get('documentRevisionAfter')}")
        gate("remote-video", (real_path_json.get("renderedFramesAfter") or 0) >= 2,
             f"renderedFrames={real_path_json.get('renderedFramesBefore')}->{real_path_json.get('renderedFramesAfter')} "
             f"ticket={real_path_json.get('displayedTicket')}")
        gate("real-touch-focus", bool(real_path_json.get("editorEditable")),
             f"target={real_path_json.get('editorTargetId')} kind={real_path_json.get('editorKind')} "
             f"frameChanged={real_path_json.get('frameChangedAfterTap')}")
        ime_shown = str(real_path_json.get("imeInputShown")) == "true"
        served = real_path_json.get("imeServedInputConnection")
        served_view = str(real_path_json.get("imeServedView"))
        gate("system-ime-focus", ime_shown and served is not None and "com.agentbrowser.probe" in served_view,
             f"mInputShown={real_path_json.get('imeInputShown')} mServedInputConnection={served} "
             f"mServedView={served_view} mCurMethodId={real_path_json.get('imeCurMethodId')}")
        host_text = str(real_path_json.get("hostFieldText") or "")
        gate("ascii-lands-in-remote-page", ascii_text in host_text,
             f"ascii={ascii_text!r} hostFieldText={host_text!r} typingNote={real_path_json.get('typingNote')}")
        gate("release-and-disconnect", real_path.ok, f"parser={real_path.status}")
    else:
        receipt_name = SLICE_ARTIFACTS.get(REAL_PATH_METHOD)
        if receipt_name is None:
            raise SystemExit(f"no slice receipt is declared for {REAL_PATH_METHOD}")
        pull(receipt_name)
        for extra in SLICE_EXTRA_ARTIFACTS.get(REAL_PATH_METHOD, ()):
            pull(extra)
        receipt = evidence / receipt_name
        if not receipt.is_file() or receipt.stat().st_mtime < run_started_at:
            raise SystemExit(f"current-run slice receipt is missing or stale: {receipt_name}")
        real_path_json = json.loads(receipt.read_text())
        gate(f"{REAL_PATH_METHOD}-assertions", real_path.ok,
             f"parser={real_path.status} receipt={receipt_name} keys={sorted(real_path_json)}")

    (evidence / "run.json").write_text(json.dumps({
        "runId": run_id, "serial": serial, "pageUrl": page_url, "asciiText": ascii_text,
        "tapX": tap_x, "tapY": tap_y, "bundleSha256": bundle_sha256,
        "admissionParser": admission.status, "realPathParser": real_path.status,
        "realPath": real_path_json, "gates": gates,
    }, indent=2) + "\n")
    failed = [name for name, value in gates.items() if value["result"] != "PASS"]
    print(json.dumps({"failed": failed, "gates": gates}, indent=2), flush=True)
    raise SystemExit(1 if failed else 0)
finally:
    # The code and its bundle are transient: drop them before anything else.
    code = None
    shutil.rmtree(private, ignore_errors=True)
    subprocess.run(["adb", "-s", serial, "shell", "rm", "-f", "/sdcard/Download/pairing.abconnection"],
                   check=False, capture_output=True)
    # Remove only a reverse mapping this run created; a LAN run creates none.
    if created_reverse:
        removed = subprocess.run(
            ["adb", "-s", serial, "reverse", "--remove", f"tcp:{endpoint_port}"],
            capture_output=True, text=True, check=False,
        )
        remaining = subprocess.run(
            ["adb", "-s", serial, "reverse", "--list"], capture_output=True, text=True, check=False,
        ).stdout
        (evidence / "adb-reverse-cleanup.json").write_text(json.dumps({
            "runId": run_id, "port": endpoint_port, "createdByRun": True,
            "removeStdout": removed.stdout.strip(), "removeStderr": removed.stderr.strip(),
            "listingAfter": remaining.strip().splitlines(),
        }, indent=2) + "\n")
