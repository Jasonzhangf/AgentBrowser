"""Run the real Android account UI against an owned temporary Relay fixture."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = "com.agentbrowser.probe"
TEST_COMPONENT = f"{APP}.test/android.test.InstrumentationTestRunner"
FIXTURE = ROOT / "packages/android-bridge/tests/account-fixture.mjs"

parser = argparse.ArgumentParser()
parser.add_argument("--evidence-dir", type=Path, default=Path(os.environ.get("ACCOUNT_EVIDENCE_DIR", "evidence/account-replay")))
args = parser.parse_args()
serial = os.environ.get("ANDROID_SERIAL")
if not serial:
    raise SystemExit("ANDROID_SERIAL must identify the authorized account-test device")
bind_host = os.environ.get("ACCOUNT_RELAY_BIND_HOST")
advertise_host = os.environ.get("ACCOUNT_RELAY_ADVERTISE_HOST")
if not bind_host or not advertise_host:
    raise SystemExit("ACCOUNT_RELAY_BIND_HOST and ACCOUNT_RELAY_ADVERTISE_HOST are required")
args.evidence_dir.mkdir(parents=True, exist_ok=True)
config_installed = False


def adb(*command, check=True, timeout=60, input_bytes=None):
    result = subprocess.run(
        ["adb", "-s", serial, *command],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"adb {' '.join(command)} failed: {result.stderr.decode(errors='replace')}")
    return result


def run_as(*command, check=True):
    return adb("shell", "run-as", APP, *command, check=check)


def exists_in_app(path):
    return run_as("test", "-e", path, check=False).returncode == 0


def read_fixture(stdout, deadline=30):
    ready, _, _ = select.select([stdout], [], [], deadline)
    if not ready:
        raise TimeoutError("Account Relay fixture startup deadline")
    line = stdout.readline()
    if not line:
        raise RuntimeError("Account Relay fixture exited before ready")
    return json.loads(line)


def start_fixture(fixture_log, extra_args=()):
    loader = ROOT / "services/relay/node_modules/tsx/dist/loader.mjs"
    if not loader.is_file():
        raise RuntimeError("Relay fixture dependencies missing")
    log = fixture_log.open("wb")
    env = os.environ.copy()
    env["ACCOUNT_RELAY_BIND_HOST"] = bind_host
    env["ACCOUNT_RELAY_ADVERTISE_HOST"] = advertise_host
    process = subprocess.Popen(
        ["node", "--import", str(loader), str(FIXTURE), *extra_args],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=log,
        bufsize=0,
    )
    try:
        ready = read_fixture(process.stdout)
    except Exception:
        process.terminate()
        process.wait(timeout=10)
        log.close()
        raise
    return process, log, ready


def stop_fixture(process, log):
    if process.poll() is None:
        process.stdin.write(b'{"command":"shutdown"}\n')
        process.stdin.flush()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("Account Relay fixture shutdown deadline")
    log.close()
    if process.returncode:
        raise RuntimeError(f"Account Relay fixture exit: {process.returncode}")


def install_config(ready):
    global config_installed
    existing = [name for name in ("origin.txt", "ca.der", "wrong-ca.der") if exists_in_app(f"files/relay/{name}")]
    if existing:
        raise RuntimeError(f"ACCOUNT_CONFIG_PRESERVED_EXISTING: {','.join(existing)}")
    run_as("mkdir", "-p", "files/relay")
    config_installed = True
    files = {
        "origin.txt": ready["origin"].encode() + b"\n",
        "ca.der": Path(ready["caDerPath"]).read_bytes(),
        "wrong-ca.der": Path(ready["wrongCaDerPath"]).read_bytes(),
    }
    for name, content in files.items():
        local = args.evidence_dir / f"input-{name}"
        local.write_bytes(content)
        remote = f"/data/local/tmp/agentbrowser-account-{os.getpid()}-{name}"
        adb("push", str(local), remote)
        run_as("cp", remote, f"files/relay/{name}")
        adb("shell", "rm", "-f", remote)


def remove_config():
    global config_installed
    if not config_installed:
        return
    for name in ("origin.txt", "ca.der", "wrong-ca.der"):
        run_as("rm", "-f", f"files/relay/{name}", check=False)
    run_as("rmdir", "files/relay", check=False)
    config_installed = False


def run_instrumentation(scenario, control_url):
    command = [
        "shell", "am", "instrument", "-w", "-r",
        "-e", "accountScenario", scenario,
        "-e", "accountControlUrl", control_url,
        "-e", "class", "com.agentbrowser.probe.AccountDeviceTest",
        TEST_COMPONENT,
    ]
    result = adb(*command, timeout=180, check=False)
    output = result.stdout + result.stderr
    (args.evidence_dir / f"{scenario}-instrument.log").write_bytes(output)
    if result.returncode != 0 or b"OK (1 test)" not in result.stdout:
        raise RuntimeError(f"Account instrumentation failed for {scenario}")


def pull_account_evidence(scenario, names):
    destination = args.evidence_dir / scenario
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        result = adb("exec-out", "run-as", APP, "cat", f"files/account-evidence/{name}", check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Missing account evidence {name} for {scenario}")
        (destination / name).write_bytes(result.stdout)


def run_scenario(scenario, fixture_args, evidence_names):
    process = log = None
    try:
        process, log, ready = start_fixture(args.evidence_dir / f"{scenario}-fixture.log", fixture_args)
        install_config(ready)
        run_instrumentation(scenario, ready["controlUrl"])
        adb("shell", "am", "force-stop", APP)
        pull_account_evidence(scenario, evidence_names)
        result = json.loads((args.evidence_dir / scenario / "result.json").read_text())
        return {"scenario": scenario, "origin": ready["origin"], "hostId": ready["hostId"], "result": result}
    finally:
        try:
            remove_config()
        finally:
            if process is not None:
                stop_fixture(process, log)


model = adb("shell", "getprop", "ro.product.model").stdout.decode().strip()
if model != "PLZ110":
    raise SystemExit(f"Authorized account device model changed: {model}")
installed = {}
for package, relative in (
    (APP, "debug/app-debug.apk"),
    (f"{APP}.test", "androidTest/debug/app-debug-androidTest.apk"),
):
    apk = ROOT / "apps/android/app/build/outputs/apk" / relative
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    adb("install", "-r", str(apk), timeout=120)
    paths = adb("shell", "pm", "path", package).stdout.decode().splitlines()
    if len(paths) != 1 or not paths[0].startswith("package:"):
        raise RuntimeError(f"Expected one installed APK for {package}: {paths}")
    installed_bytes = adb("exec-out", "cat", paths[0][len("package:"):]).stdout
    if hashlib.sha256(installed_bytes).hexdigest() != digest:
        raise RuntimeError(f"Installed APK identity mismatch: {package}")
    installed[package] = digest
(args.evidence_dir / "installed-apks.json").write_text(
    json.dumps({"serial": serial, "model": model, "sha256": installed}, indent=2) + "\n"
)
adb("shell", "am", "force-stop", APP)
summary = {
    "serial": serial,
    "model": model,
    "advertiseHost": advertise_host,
    "installedApkSha256": installed,
    "scenarios": [],
}
try:
    summary["scenarios"].append(run_scenario(
        "directory", (),
        ("result.json", "directory-online.png", "directory-offline.png", "directory-expired.png", "close-error.png"),
    ))
    summary["scenarios"].append(run_scenario(
        "revoke-failure", ("--fail-revoke",),
        ("result.json", "revoke-warning.png"),
    ))
finally:
    remove_config()
    adb("shell", "am", "force-stop", APP, check=False)

(args.evidence_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
