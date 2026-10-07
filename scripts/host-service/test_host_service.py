#!/usr/bin/env python3
"""Real subprocess acceptance for the AgentBrowser Host service entrypoint."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import socket
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


SCRIPT = Path(__file__).with_name("host_service.py")
U32_MAX = (1 << 32) - 1


def protocol_version_argument(value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("protocol version must be a positive u32") from error
    if not 1 <= parsed <= U32_MAX:
        raise argparse.ArgumentTypeError("protocol version must be a positive u32")
    return parsed


def run_manager(*arguments: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"manager failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def manager_json(*arguments: str) -> dict[str, object]:
    result = run_manager(*arguments, check=True)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"manager did not return JSON: {result.stdout!r}") from error
    if not isinstance(value, dict):
        raise AssertionError(f"manager JSON is not an object: {value!r}")
    return value


def private_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def fixture_host(
    base: Path,
    *,
    actual_version: int,
    flushes: str = "success",
    receipt: Path | None = None,
    name: str = "fixture-host",
) -> Path:
    host = base / name
    body = base / f"{name}.py"
    if receipt is None:
        receipt = base / f"{name}-receipt.json"
    script = f'''#!/usr/bin/env python3
import json
import os
import pathlib
import signal
import socket
import selectors
import sys
import threading

if sys.argv[1:2] == ["--help"]:
    print("Local persistent browser Session")
    raise SystemExit(0)
if len(sys.argv) < 3 or sys.argv[1] != "--socket-dir":
    raise SystemExit(2)

socket_dir = pathlib.Path(sys.argv[2])
socket_dir.mkdir(mode=0o700, exist_ok=True)
argv = sys.argv[3:]
profile_root = None
if argv and argv[0] == "--profile-root":
    if len(argv) != 2:
        raise SystemExit(3)
    profile_root = argv[1]
    argv = []
if argv not in ([], ["--allow-private-network"]):
    raise SystemExit(4)

state = {{"ready": 0, "flushes": {flushes!r}, "count": 0, "profile_root": profile_root}}
state_path = socket_dir / "state.json"
receipt_path = pathlib.Path({str(receipt)!r})
if receipt_path.exists():
    previous = json.loads(receipt_path.read_text())
    state["count"] = previous.get("count", 0)
state_path.write_text(json.dumps(state))
receipt_path.write_text(json.dumps(state))
os.chmod(socket_dir, 0o700)
listeners = []
for name in ("host.sock", "frames.sock"):
    path = socket_dir / name
    if path.exists():
        path.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(8)
    os.chmod(path, 0o600)
    listeners.append(listener)

def handle(client):
    session = "session-" + str(os.getpid())
    client.sendall((json.dumps({{"type": "ready", "version": {actual_version}, "session_id": session}}) + "\\n").encode())
    reader = client.makefile("rwb")
    line = reader.readline()
    if not line:
        return
    request = json.loads(line)
    if request.get("id") != 1 or request.get("command") != {{"type": "flush_profiles"}}:
        response = {{"type": "error", "id": request.get("id"), "code": "INVALID_REQUEST", "message": "unexpected fixture command"}}
    else:
        state["count"] += 1
        state_path.write_text(json.dumps(state))
        receipt_path.write_text(json.dumps(state))
        if state["flushes"] == "success":
            response = {{"type": "result", "id": 1, "value": {{"flushed": True, "profiles": 1}}}}
        elif state["flushes"] == "busy":
            response = {{"type": "error", "id": 1, "code": "MAINTENANCE_BUSY", "message": "busy"}}
        elif state["flushes"] == "unknown":
            return
        else:
            response = {{"type": "error", "id": 1, "code": "PROFILE_STORAGE_FAILED", "message": "store failed"}}
        client.sendall((json.dumps(response) + "\\n").encode())

def stop(_signum, _frame):
    for listener in listeners:
        listener.close()
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
selector = selectors.DefaultSelector()
for listener in listeners:
    selector.register(listener, selectors.EVENT_READ)
while True:
    for key, _ in selector.select(timeout=10):
        client, _ = key.fileobj.accept()
        thread = threading.Thread(target=handle, args=(client,), daemon=True)
        thread.start()
'''
    body.write_text(script, encoding="utf-8")
    # Compile a thin launcher so that `ps` reports this exact path as argv[0].
    # The manager's ownership check compares the recorded binary path against
    # the real argv, so a shell wrapper that execs an interpreter would be
    # (correctly) rejected as a foreign PID. This shim forwards signals to the
    # Python body, so it behaves like a real Host process for lifecycle tests.
    shim = base / f"{name}.c"
    shim.write_text(
        "#include <errno.h>\n"
        "#include <signal.h>\n"
        "#include <stdio.h>\n"
        "#include <stdlib.h>\n"
        "#include <sys/wait.h>\n"
        "#include <unistd.h>\n"
        "\n"
        "static pid_t child = -1;\n"
        "static void forward(int sig) { if (child > 0) kill(child, sig); }\n"
        "\n"
        "int main(int argc, char **argv) {\n"
        f"    const char *python = {json.dumps(str(sys.executable))};\n"
        f"    const char *body = {json.dumps(str(body))};\n"
        "    char **args = calloc((size_t)argc + 2, sizeof(char *));\n"
        "    if (args == NULL) { return 2; }\n"
        "    args[0] = (char *)python;\n"
        "    args[1] = (char *)body;\n"
        "    for (int i = 1; i < argc; i++) { args[i + 1] = argv[i]; }\n"
        "    args[argc + 1] = NULL;\n"
        "    pid_t pid = fork();\n"
        "    if (pid < 0) { perror(\"fork\"); return 2; }\n"
        "    if (pid == 0) { execv(python, args); perror(\"execv\"); _exit(127); }\n"
        "    child = pid;\n"
        "    signal(SIGTERM, forward);\n"
        "    signal(SIGINT, forward);\n"
        "    int status = 0;\n"
        "    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) { }\n"
        "    if (WIFEXITED(status)) { return WEXITSTATUS(status); }\n"
        "    if (WIFSIGNALED(status)) { signal(WTERMSIG(status), SIG_DFL); raise(WTERMSIG(status)); }\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    compile_result = subprocess.run(
        ["cc", "-O2", "-o", str(host), str(shim)],
        check=False,
        capture_output=True,
        text=True,
    )
    if compile_result.returncode != 0:
        raise AssertionError(f"cannot compile fixture host: {compile_result.stderr}")
    os.chmod(host, 0o755)
    return host


def host_request(
    socket_path: Path,
    command: dict[str, object],
    expected_version: int,
) -> tuple[dict[str, object], str]:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(10)
    client.connect(str(socket_path))
    try:
        ready = json.loads(client.makefile("rb").readline().decode("utf-8"))
        if ready.get("type") != "ready" or ready.get("version") != expected_version:
            raise AssertionError(f"unexpected Host ready message: {ready!r}")
        client.sendall((json.dumps({"id": 1, "command": command}) + "\n").encode("utf-8"))
        response = json.loads(client.makefile("rb").readline().decode("utf-8"))
        return response, str(ready["session_id"])
    finally:
        client.close()


def host_flush_request(
    socket_path: Path,
    expected_version: int,
) -> dict[str, object]:
    response, _ = host_request(socket_path, {"type": "flush_profiles"}, expected_version)
    return response


def assert_error(result: subprocess.CompletedProcess[str], fragment: str) -> None:
    if result.returncode == 0 or fragment not in result.stderr:
        raise AssertionError(f"expected error {fragment!r}: rc={result.returncode}, stderr={result.stderr!r}")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_owned_fixture_pid(pid: int, timeout: float = 10.0) -> None:
    if not pid_alive(pid):
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if pid_alive(pid):
        raise AssertionError(f"owned fixture Host pid did not stop: {pid}")


def wait_for_spawned_child(
    state_path: Path,
    launch_mode: str,
    launcher: subprocess.Popen[str],
    timeout: float = 20.0,
) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if launcher.poll() is not None:
            raise AssertionError(
                f"launchd manager exited before recording a {launch_mode} child: "
                f"rc={launcher.returncode}, stderr={launcher.stderr.read()!r}"
            )
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
            pid = state.get("pid")
            if state.get("launch_mode") == launch_mode and isinstance(pid, int) and pid > 0:
                return pid
        time.sleep(0.05)
    raise AssertionError(f"owned Host child pid was not recorded in {state_path}")


def launchd_maintenance_contract(
    *,
    base: Path,
    runtime_base: Path,
    short: str,
    protocol_version: int,
    flushes: str,
) -> None:
    """Exercise launchd shutdown through the typed ProfileFlush receipt.

    The launchd manager owns the Host child directly, so a plain signal to the
    manager must confirm the flush before terminating that child. Rejected and
    unknown receipts must leave the Host running and the state in place.
    """
    mode_token = {"success": "ok", "busy": "busy", "failed": "fail", "unknown": "unk"}[flushes]
    token = short.split("-")[-1]
    root = runtime_base / f"abhl-{mode_token}-{token}"
    runtime = runtime_base / f"abhlr-{mode_token}-{token}"
    receipt = base / f"receipt-launchd-{flushes}.json"
    fixture = fixture_host(
        base,
        actual_version=protocol_version,
        flushes=flushes,
        receipt=receipt,
        name=f"fixture-host-launchd-{flushes}",
    )
    register_service_cleanup(root, runtime)
    manager_json(
        "install",
        "--root",
        str(root),
        "--binary",
        str(fixture),
        "--runtime-root",
        str(runtime),
        "--profile-root",
        str(base / f"profiles-launchd-{flushes}"),
        "--protocol-version",
        str(protocol_version),
    )
    profile_root = (base / f"profiles-launchd-{flushes}").resolve()
    launcher = subprocess.Popen(
        [sys.executable, str(SCRIPT), "launchd", "--root", str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    state_path = root / "run" / "state.json"
    child_pid = wait_for_spawned_child(state_path, "launchd", launcher)
    service_state = json.loads(state_path.read_text(encoding="utf-8"))
    host_state = json.loads(
        (Path(str(service_state["socket_dir"])) / "state.json").read_text(encoding="utf-8")
    )
    if host_state["profile_root"] != str(profile_root):
        raise AssertionError(f"launchd Host argv lost the explicit profile root: {host_state!r}")
    launcher_pid = launcher.pid
    try:
        before = json.loads(receipt.read_text(encoding="utf-8"))
        if before["count"] != 0:
            raise AssertionError("launchd manager flushed before it was asked to stop")
        os.kill(launcher_pid, signal.SIGTERM)
        try:
            _stdout, stderr = launcher.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            launcher.kill()
            _stdout, stderr = launcher.communicate(timeout=5)
        returncode = launcher.returncode
        child_alive_after = pid_alive(child_pid)
        after = json.loads(receipt.read_text(encoding="utf-8"))
        if after["count"] != 1:
            raise AssertionError(
                f"launchd shutdown did not confirm a flush exactly once: {after!r}"
            )
        if flushes == "success":
            if returncode != 0:
                raise AssertionError(
                    "confirmed launchd flush did not complete the manager shutdown: "
                    f"rc={returncode}, stderr={stderr!r}"
                )
            if state_path.exists():
                raise AssertionError("confirmed launchd flush left service state behind")
            stopped = run_manager("stop", "--root", str(root))
            if stopped.returncode != 0:
                raise AssertionError(f"stop did not accept the launchd cleanup: {stopped!r}")
            if list(runtime.iterdir()):
                raise AssertionError("launchd runtime directory was not cleaned")
        else:
            if flushes == "unknown":
                if not child_alive_after:
                    raise AssertionError("unknown launchd flush result terminated the Host child")
                if not state_path.exists():
                    raise AssertionError("unknown launchd flush result removed service state")
                if "cannot confirm profile flush" not in stderr:
                    raise AssertionError(
                        f"unknown launchd flush did not report the typed uncertainty: {stderr!r}"
                    )
            elif returncode == 0:
                raise AssertionError(
                    f"launchd manager reported a saved stop without a {flushes} receipt: "
                    f"stderr={stderr!r}"
                )
            if flushes == "busy" and "MAINTENANCE_BUSY" not in stderr:
                raise AssertionError(f"launchd flush rejection lost its typed error: {stderr!r}")
            if flushes == "failed" and "PROFILE_STORAGE_FAILED" not in stderr:
                raise AssertionError(f"launchd flush rejection lost its typed error: {stderr!r}")
            if not child_alive_after:
                raise AssertionError("rejected launchd flush still terminated the Host child")
            if not state_path.exists():
                raise AssertionError("rejected launchd flush removed service state")
            stop_refused = run_manager("stop", "--root", str(root))
            assert_error(stop_refused, "launchd-managed Host must be stopped through launchctl")
    finally:
        if launcher.poll() is None:
            launcher.kill()
        try:
            launcher.communicate(timeout=5)
        except (subprocess.TimeoutExpired, ValueError):
            pass
        stop_owned_fixture_pid(child_pid)
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(runtime, ignore_errors=True)


def register_service_cleanup(root: Path, runtime: Path, *extra_files: Path) -> None:
    def cleanup() -> None:
        result = run_manager("stop", "--root", str(root))
        if result.returncode == 0:
            shutil.rmtree(runtime, ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)
            for path in extra_files:
                path.unlink(missing_ok=True)

    atexit.register(cleanup)


def terminal_launchd_rejection_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="agentbrowser-host-service-dev-") as temporary:
        base = Path(temporary)
        runtime_base = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
        token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        root = runtime_base / f"abhs-rj-{token}"
        runtime = runtime_base / f"abhs-rj-rt-{token}"
        label = f"com.agentbrowser.dev-reject.{token}"
        fixture = fixture_host(base, actual_version=2)
        manager_json(
            "install",
            "--root",
            str(root),
            "--binary",
            str(fixture),
            "--runtime-root",
            str(runtime),
            "--launchd-root",
            str(base / "LaunchAgents"),
            "--service-label",
            label,
            "--protocol-version",
            "1",
        )
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "launchd", "--root", str(root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(timeout=15)
        if process.returncode == 0 or "Host protocol version is 2, expected 1" not in stderr:
            raise AssertionError(
                f"terminal launchd rejection was not explicit: rc={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
            )
        terminal = run_manager("status", "--root", str(root))
        terminal_state = json.loads(terminal.stdout)
        if terminal.returncode == 0 or terminal_state["state"] != "ready_rejected":
            raise AssertionError(f"status did not expose terminal rejection: {terminal!r}")
        if terminal_state["protocol_version"] != 1 or "Host protocol version is 2, expected 1" not in terminal_state["ready_error"]:
            raise AssertionError(f"terminal rejection lost configured-version evidence: {terminal_state!r}")
        state_path = root / "run" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("launch_mode") != "launchd_rejected" or state.get("service_label") != label:
            raise AssertionError(f"terminal launchd state is not owned evidence: {state!r}")
        if list(runtime.iterdir()):
            raise AssertionError("terminal launchd rejection left an owned socket runtime behind")
        stopped = manager_json("stop", "--root", str(root))
        if stopped["state"] != "stopped" or state_path.exists():
            raise AssertionError(f"terminal launchd evidence was not cleaned explicitly: {stopped!r}")
        shutil.rmtree(root)
        shutil.rmtree(runtime)

        legacy_root = runtime_base / f"abhs-rj-legacy-{token}"
        legacy_runtime = runtime_base / f"abhs-rj-legacy-rt-{token}"
        legacy_state = {
            "state_version": 1,
            "service_label": f"com.agentbrowser.dev-legacy.{token}",
            "pid": os.getpid(),
            "host_binary": str(fixture),
            "binary_sha256": "0" * 64,
            "protocol_version": 1,
            "profile_id": "default",
            "profile_mode": "in_memory",
            "runtime_root": str(legacy_runtime),
            "socket_dir": str(legacy_runtime / "host-00000000000000000000000000000000"),
            "session_id": "legacy-session",
            "config_id": "legacy-config",
            "config_sha256": "0" * 64,
            "launch_mode": "subprocess",
            "started_at_unix": 1,
        }
        legacy_run = legacy_root / "run"
        legacy_run.mkdir(parents=True, mode=0o700)
        legacy_state_path = legacy_run / "state.json"
        legacy_state_path.write_text(json.dumps(legacy_state, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(legacy_state_path, 0o600)
        if run_manager("status", "--root", str(legacy_root)).returncode != 1:
            raise AssertionError("legacy state without ready_error was not loadable")
        shutil.rmtree(legacy_root)


def persistent_maintenance_contract(protocol_version: int) -> None:
    def stop_fixture_pid(pid: int) -> None:
        if not pid_alive(pid):
            return
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
            deadline = time.monotonic() + 5
            while pid_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.05)
        if pid_alive(pid):
            raise AssertionError(f"own fixture Host pid did not stop: {pid}")

    def cleanup_owned_fixture_tree() -> None:
        for prefix in ("", "restart-"):
            for kind in ("success", "busy", "failed", "unknown", "foreign"):
                shutil.rmtree(runtime_base / f"abhp-{prefix}{kind}-{short}", ignore_errors=True)
                shutil.rmtree(runtime_base / f"abhp-rt-{prefix}{kind}-{short}", ignore_errors=True)
        for kind in ("success", "busy", "failed", "unknown"):
            shutil.rmtree(runtime_base / f"abhr-{kind}-{short}", ignore_errors=True)

    with tempfile.TemporaryDirectory(prefix="agentbrowser-host-service-persistent-") as temporary:
        base = Path(temporary)
        runtime_base = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
        short = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        active_pids: list[int] = []
        try:
            for flush_mode in ("success", "busy", "failed"):
                root = runtime_base / f"abhp-{flush_mode}-{short}"
                runtime = runtime_base / f"abhp-rt-{flush_mode}-{short}"
                profile_root = base / f"profiles-{flush_mode}"
                receipt = base / f"receipt-{flush_mode}.json"
                fixture = fixture_host(
                    base,
                    actual_version=protocol_version,
                    flushes=flush_mode,
                    receipt=receipt,
                )
                install = manager_json(
                    "install",
                    "--root",
                    str(root),
                    "--binary",
                    str(fixture),
                    "--runtime-root",
                    str(runtime),
                    "--profile-root",
                    str(profile_root),
                    "--protocol-version",
                    str(protocol_version),
                )
                canonical_profile_root = str(profile_root.resolve())
                if install["profile_mode"] != "persistent" or install["profile_root"] != canonical_profile_root:
                    raise AssertionError(f"install did not project explicit profile root: {install!r}")
                started = manager_json("start", "--root", str(root), "--ready-timeout", "10")
                if started["profile_mode"] != "persistent" or started["profile_root"] != canonical_profile_root:
                    raise AssertionError(f"start did not project persistent mode: {started!r}")
                state = json.loads((root / "run" / "state.json").read_text(encoding="utf-8"))
                active_pids.append(int(state["pid"]))
                host_state = json.loads((Path(str(state["socket_dir"])) / "state.json").read_text(encoding="utf-8"))
                if host_state["profile_root"] != canonical_profile_root:
                    raise AssertionError(f"Host argv did not carry explicit profile root: {host_state!r}")
                receipt_state = json.loads(receipt.read_text(encoding="utf-8"))
                if receipt_state["count"] != 0:
                    raise AssertionError("flush ran before stop")
                result = run_manager("stop", "--root", str(root))
                if flush_mode == "success":
                    if result.returncode != 0:
                        raise AssertionError(f"successful typed flush did not stop the Host: {result!r}")
                else:
                    assert_error(result, "profile flush failed" if flush_mode == "failed" else "MAINTENANCE_BUSY")
                    status = manager_json("status", "--root", str(root))
                    if status["state"] not in {"running", "foreign_pid"}:
                        raise AssertionError("failed maintenance was reported as saved/stopped")
                receipt_state = json.loads(receipt.read_text(encoding="utf-8"))
                if receipt_state["count"] != 1:
                    raise AssertionError("maintenance request did not reach the Host exactly once")
                if flush_mode == "success":
                    shutil.rmtree(root)
                    shutil.rmtree(runtime)
        finally:
            for pid in active_pids:
                stop_fixture_pid(pid)
            cleanup_owned_fixture_tree()

        active_pids = []
        try:
            for flush_mode in ("success", "busy", "failed", "unknown"):
                root = runtime_base / f"abhp-restart-{flush_mode}-{short}"
                runtime = runtime_base / f"abhr-{flush_mode}-{short}"
                profile_root = base / f"profiles-restart-{flush_mode}"
                receipt = base / f"receipt-restart-{flush_mode}.json"
                fixture = fixture_host(
                    base,
                    actual_version=protocol_version,
                    flushes=flush_mode,
                    receipt=receipt,
                    name=f"fixture-host-restart-{flush_mode}",
                )
                manager_json(
                    "install",
                    "--root",
                    str(root),
                    "--binary",
                    str(fixture),
                    "--runtime-root",
                    str(runtime),
                    "--profile-root",
                    str(profile_root),
                    "--protocol-version",
                    str(protocol_version),
                )
                started = manager_json("start", "--root", str(root), "--ready-timeout", "10")
                first_pid = int(started["pid"])
                first_session = str(started["session_id"])
                active_pids.append(first_pid)
                result = run_manager("restart", "--root", str(root), "--ready-timeout", "10")
                receipt_state = json.loads(receipt.read_text(encoding="utf-8"))
                if receipt_state["count"] != 1:
                    raise AssertionError("restart maintenance request did not reach the Host exactly once")
                if flush_mode == "success":
                    if result.returncode != 0:
                        raise AssertionError(f"successful restart maintenance failed: {result!r}")
                    restarted = json.loads(result.stdout)
                    active_pids.append(int(restarted["pid"]))
                    after_restart = json.loads(receipt.read_text(encoding="utf-8"))
                    if after_restart["count"] != 1:
                        raise AssertionError("successful restart did not preserve the flush receipt")
                    if (
                        restarted["profile_mode"] != "persistent"
                        or restarted["profile_root"] != str(profile_root.resolve())
                        or int(restarted["pid"]) == first_pid
                        or str(restarted["session_id"]) == first_session
                    ):
                        raise AssertionError(
                            f"restart did not preserve the explicit root and create a new session: {restarted!r}"
                        )
                    manager_json("stop", "--root", str(root))
                else:
                    expected = {
                        "busy": "MAINTENANCE_BUSY",
                        "failed": "PROFILE_STORAGE_FAILED",
                        "unknown": "cannot confirm profile flush",
                    }[flush_mode]
                    assert_error(result, expected)
                    status = manager_json("status", "--root", str(root))
                    if (
                        status["state"] != "running"
                        or int(status["pid"]) != first_pid
                        or str(status["session_id"]) != first_session
                    ):
                        raise AssertionError("rejected restart changed or replaced the running Host")
        finally:
            for pid in active_pids:
                stop_fixture_pid(pid)
            cleanup_owned_fixture_tree()

        active_pids = []
        try:
            unknown_root = runtime_base / f"abhp-unknown-{short}"
            unknown_runtime = runtime_base / f"abhp-rt-unknown-{short}"
            unknown_profile = base / "profiles-unknown"
            unknown_fixture = fixture_host(base, actual_version=protocol_version, flushes="unknown")
            manager_json(
                "install",
                "--root",
                str(unknown_root),
                "--binary",
                str(unknown_fixture),
                "--runtime-root",
                str(unknown_runtime),
                "--profile-root",
                str(unknown_profile),
                "--protocol-version",
                str(protocol_version),
            )
            manager_json("start", "--root", str(unknown_root), "--ready-timeout", "10")
            unknown_state = json.loads(
                (unknown_root / "run" / "state.json").read_text(encoding="utf-8")
            )
            active_pids.append(int(unknown_state["pid"]))
            unknown = run_manager("stop", "--root", str(unknown_root))
            assert_error(unknown, "cannot confirm profile flush")
            if manager_json("status", "--root", str(unknown_root))["state"] not in {"starting", "running"}:
                raise AssertionError("unknown flush changed the tracked Host state")
        finally:
            for pid in active_pids:
                stop_fixture_pid(pid)
            cleanup_owned_fixture_tree()

        for flush_mode in ("success", "busy", "failed", "unknown"):
            launchd_maintenance_contract(
                base=base,
                runtime_base=runtime_base,
                short=short,
                protocol_version=protocol_version,
                flushes=flush_mode,
            )

        active_pids = []
        try:
            foreign_root = runtime_base / f"abhp-foreign-{short}"
            foreign_runtime = runtime_base / f"abhp-rt-foreign-{short}"
            foreign_profile = base / "profiles-foreign"
            foreign_fixture = fixture_host(base, actual_version=protocol_version)
            manager_json(
                "install",
                "--root",
                str(foreign_root),
                "--binary",
                str(foreign_fixture),
                "--runtime-root",
                str(foreign_runtime),
                "--profile-root",
                str(foreign_profile),
                "--protocol-version",
                str(protocol_version),
            )
            manager_json("start", "--root", str(foreign_root), "--ready-timeout", "10")
            foreign_state_path = foreign_root / "run" / "state.json"
            foreign_state = json.loads(foreign_state_path.read_text(encoding="utf-8"))
            foreign_pid = int(foreign_state["pid"])
            active_pids.append(foreign_pid)
            foreign_state["pid"] = os.getpid()
            foreign_state_path.write_text(json.dumps(foreign_state, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(foreign_state_path, 0o600)
            foreign = run_manager("stop", "--root", str(foreign_root))
            assert_error(foreign, "recorded PID is owned by another process")
            foreign_state["pid"] = None
            foreign_state_path.write_text(json.dumps(foreign_state, sort_keys=True) + "\n", encoding="utf-8")
            os.chmod(foreign_state_path, 0o600)
        finally:
            for pid in active_pids:
                stop_fixture_pid(pid)
            cleanup_owned_fixture_tree()


def config_version_compatibility_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="agentbrowser-host-service-config-") as temporary:
        base = Path(temporary)
        runtime_base = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
        short = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        root = runtime_base / f"abhs-cfg-{short}"
        runtime = runtime_base / f"abhs-cfg-rt-{short}"
        fixture = fixture_host(base, actual_version=11, name="fixture-host-config")
        register_service_cleanup(root, runtime)
        manager_json(
            "install",
            "--root",
            str(root),
            "--binary",
            str(fixture),
            "--runtime-root",
            str(runtime),
            "--protocol-version",
            "11",
        )
        config_path = root / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config["config_version"] != 2 or config["profile_root"] is not None:
            raise AssertionError(f"install did not bind config v2 explicitly: {config!r}")

        missing_root = dict(config)
        del missing_root["profile_root"]
        config_path.write_text(json.dumps(missing_root, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        assert_error(run_manager("status", "--root", str(root)), "service config is missing keys: profile_root")
        config_path.write_text(json.dumps(config, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)

        empty_root = runtime_base / f"abhs-cfg-empty-{short}"
        register_service_cleanup(empty_root, runtime_base / f"abhs-cfg-empty-rt-{short}")
        assert_error(
            run_manager(
                "install",
                "--root",
                str(empty_root),
                "--binary",
                str(fixture),
                "--runtime-root",
                str(runtime_base / f"abhs-cfg-empty-rt-{short}"),
                "--profile-root",
                "",
                "--protocol-version",
                "11",
            ),
            "profile root must be an absolute path",
        )

        legacy = {
            key: value
            for key, value in config.items()
            if key != "config_version"
            and key != "profile_root"
        }
        legacy["config_version"] = 1
        legacy["profile_mode"] = "in_memory"
        config_path.write_text(json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        status = manager_json("status", "--root", str(root))
        if status["profile_mode"] != "in_memory" or status["profile_root"] is not None:
            raise AssertionError(f"legacy config was not projected as in-memory: {status!r}")
        started = manager_json("start", "--root", str(root), "--ready-timeout", "10")
        if started["profile_mode"] != "in_memory" or started["profile_root"] is not None:
            raise AssertionError(f"legacy config start changed profile mode: {started!r}")
        stopped = manager_json("stop", "--root", str(root))
        if stopped["state"] != "stopped":
            raise AssertionError(f"legacy config stop did not complete: {stopped!r}")

        mutated = dict(legacy)
        mutated["profile_root"] = str(base / "never-created")
        config_path.write_text(json.dumps(mutated, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        assert_error(run_manager("status", "--root", str(root)), "unsupported config keys")

        persisted = dict(legacy)
        persisted["config_version"] = 2
        persisted["profile_mode"] = "persistent"
        persisted["profile_root"] = str((base / "persisted").resolve())
        config_path.write_text(json.dumps(persisted, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        persisted_status = manager_json("status", "--root", str(root))
        if (
            persisted_status["profile_mode"] != "persistent"
            or persisted_status["profile_root"] != persisted["profile_root"]
        ):
            raise AssertionError(f"v2 persistent config was not projected: {persisted_status!r}")

        preserve_root = runtime_base / f"abhs-cfg-preserve-{short}"
        preserve_runtime = runtime_base / f"abhs-cfg-preserve-rt-{short}"
        preserve_profile = base / "preserved-profile-root"
        preserve_profile.mkdir(mode=0o700)
        sentinel = preserve_profile / "unknown-profile-data"
        sentinel.write_text("preserve-me\n", encoding="utf-8")
        register_service_cleanup(preserve_root, preserve_runtime)
        manager_json(
            "install",
            "--root",
            str(preserve_root),
            "--binary",
            str(fixture),
            "--runtime-root",
            str(preserve_runtime),
            "--profile-root",
            str(preserve_profile),
            "--protocol-version",
            "11",
        )
        manager_json(
            "install",
            "--root",
            str(preserve_root),
            "--binary",
            str(fixture),
            "--runtime-root",
            str(preserve_runtime),
            "--protocol-version",
            "11",
        )
        preserved_config = json.loads(
            (preserve_root / "config.json").read_text(encoding="utf-8")
        )
        if (
            preserved_config["profile_mode"] != "persistent"
            or preserved_config["profile_root"] != str(preserve_profile.resolve())
        ):
            raise AssertionError(
                f"reinstall silently dropped the existing profile root: {preserved_config!r}"
            )
        if sentinel.read_text(encoding="utf-8") != "preserve-me\n":
            raise AssertionError("reinstall modified an existing unknown profile root")


def acceptance(binary: Path, protocol_version: int) -> None:
    with tempfile.TemporaryDirectory(prefix="agentbrowser-host-service-") as temporary:
        base = Path(temporary)
        runtime_base = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
        root = runtime_base / f"abhs-service-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        runtime = runtime_base / f"abhs-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        register_service_cleanup(root, runtime)
        launchd = base / "LaunchAgents"

        missing = base / "missing"
        missing_result = run_manager(
            "install",
            "--root",
            str(base / "missing-service"),
            "--binary",
            str(missing),
            "--protocol-version",
            str(protocol_version),
        )
        assert_error(missing_result, "host binary is not executable")

        missing_protocol = run_manager(
            "install",
            "--root",
            str(base / "missing-protocol-service"),
            "--binary",
            str(binary),
        )
        assert_error(missing_protocol, "--protocol-version")
        for bad_protocol in ("0", str(U32_MAX + 1), "true"):
            bad_result = run_manager(
                "install",
                "--root",
                str(base / f"bad-protocol-{bad_protocol}"),
                "--binary",
                str(binary),
                "--protocol-version",
                bad_protocol,
            )
            assert_error(bad_result, "protocol version must be a positive u32")

        shared_runtime = base / "shared-runtime"
        shared_runtime.mkdir()
        os.chmod(shared_runtime, 0o755)
        shared_result = run_manager(
            "install",
            "--root",
            str(base / "shared-service"),
            "--binary",
            str(binary),
            "--runtime-root",
            str(shared_runtime),
            "--protocol-version",
            str(protocol_version),
        )
        assert_error(shared_result, "directory must already be private")
        if private_mode(shared_runtime) != 0o755:
            raise AssertionError("existing runtime directory permissions were changed")

        # Both the service root and the runtime root carry a space, matching
        # the documented default shape ("Application Support"). The names stay
        # short so the Host's own session socket still fits the Unix bind bound.
        spaced_token = uuid.uuid4().hex[:6]
        spaced_root = runtime_base / f"abhs-sv c-{spaced_token}"
        spaced_runtime = runtime_base / f"abhs-r t-{spaced_token}"
        os.makedirs(spaced_root, mode=0o700, exist_ok=True)
        os.makedirs(spaced_root / "run", mode=0o700, exist_ok=True)
        os.makedirs(spaced_root / "logs", mode=0o700, exist_ok=True)
        os.makedirs(spaced_runtime, mode=0o700, exist_ok=True)
        spaced_result = run_manager(
            "install",
            "--root",
            str(spaced_root),
            "--binary",
            str(binary),
            "--runtime-root",
            str(spaced_runtime),
            "--protocol-version",
            str(protocol_version),
        )
        if spaced_result.returncode != 0:
            raise AssertionError(f"spaced runtime install failed: {spaced_result.stderr}")

        spaced_started = manager_json(
            "start",
            "--root",
            str(spaced_root),
            "--ready-timeout",
            "60",
        )
        if spaced_started["state"] != "running":
            raise AssertionError(f"spaced service root did not start: {spaced_started!r}")
        spaced_status = manager_json("status", "--root", str(spaced_root))
        if spaced_status["state"] != "running" or spaced_status.get("config_identity_match") is not True:
            raise AssertionError(f"spaced service root lost process identity: {spaced_status!r}")
        spaced_restarted = manager_json(
            "restart",
            "--root",
            str(spaced_root),
            "--ready-timeout",
            "60",
        )
        if spaced_restarted["state"] != "running" or spaced_restarted["pid"] == spaced_started["pid"]:
            raise AssertionError(f"spaced service root restart reused the Host pid: {spaced_restarted!r}")
        spaced_stopped = manager_json("stop", "--root", str(spaced_root))
        if spaced_stopped["state"] != "stopped":
            raise AssertionError(f"spaced service root did not stop: {spaced_stopped!r}")
        if spaced_runtime.exists() and any(spaced_runtime.iterdir()):
            raise AssertionError("spaced service root left an owned runtime behind")
        shutil.rmtree(spaced_runtime, ignore_errors=True)
        shutil.rmtree(spaced_root, ignore_errors=True)

        installed = manager_json(
            "install",
            "--root",
            str(root),
            "--binary",
            str(binary),
            "--runtime-root",
            str(runtime),
            "--launchd-root",
            str(launchd),
            "--service-label",
            "com.agentbrowser.test-host",
            "--profile-id",
            "test-profile",
            "--profile-root",
            str(base / "installed-profiles"),
            "--protocol-version",
            str(protocol_version),
        )
        assert installed["state"] == "installed"
        config_path = root / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if (
            config["profile_mode"] != "persistent"
            or config["profile_root"] != str((base / "installed-profiles").resolve())
            or config["protocol_version"] != protocol_version
        ):
            raise AssertionError(f"invalid installed identity: {config!r}")
        if private_mode(root) != 0o700 or private_mode(root / "run") != 0o700 or private_mode(runtime) != 0o700:
            raise AssertionError("service directories are not private")
        if private_mode(config_path) != 0o600 or private_mode(root / "logs" / "host.log") != 0o600:
            raise AssertionError("service files are not private")
        plist = Path(str(installed["launchd_plist"]))
        if private_mode(plist) != 0o600:
            raise AssertionError("launchd plist is not private")
        plist_text = plist.read_text(encoding="utf-8")
        for forbidden in ("token", "password", "secret"):
            if forbidden in plist_text.lower():
                raise AssertionError("launchd plist contains credential-like text")

        malformed_state = root / "run" / "state.json"
        malformed_state.write_text(json.dumps({"pid": os.getpid()}) + "\n", encoding="utf-8")
        os.chmod(malformed_state, 0o600)
        malformed_stop = run_manager("stop", "--root", str(root))
        assert_error(malformed_stop, "service state is missing keys")
        malformed_state.unlink()

        config["profile_mode"] = "persistent"
        config["profile_root"] = None
        config_path.write_text(json.dumps(config, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        assert_error(run_manager("start", "--root", str(root)), "persistent profile_mode requires profile_root")

        config["profile_mode"] = "in_memory"
        config["profile_root"] = str((base / "installed-profiles").resolve())
        config_path.write_text(json.dumps(config, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        assert_error(run_manager("start", "--root", str(root)), "profile_root requires profile_mode persistent")

        config["profile_mode"] = "persistent"
        config["profile_root"] = str((base / "installed-profiles").resolve())
        config_path.write_text(json.dumps(config, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)

        config["protocol_version"] = True
        config_path.write_text(json.dumps(config, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        assert_error(
            run_manager("start", "--root", str(root)),
            "host protocol version must be a positive u32",
        )
        config["protocol_version"] = protocol_version
        config_path.write_text(json.dumps(config, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)

        mismatch_version = protocol_version - 1 if protocol_version > 1 else protocol_version + 1
        mismatch_root = runtime_base / f"abhs-mismatch-service-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        mismatch_runtime = runtime_base / f"abhs-m-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        register_service_cleanup(mismatch_root, mismatch_runtime)
        manager_json(
            "install",
            "--root",
            str(mismatch_root),
            "--binary",
            str(binary),
            "--runtime-root",
            str(mismatch_runtime),
            "--protocol-version",
            str(mismatch_version),
        )
        mismatch_start = run_manager(
            "start", "--root", str(mismatch_root), "--ready-timeout", "5"
        )
        assert_error(
            mismatch_start,
            f"Host protocol version is {protocol_version}, expected {mismatch_version}",
        )
        if (mismatch_root / "run" / "state.json").exists():
            raise AssertionError("version mismatch left service state behind")
        if list(mismatch_runtime.iterdir()):
            raise AssertionError("version mismatch left an owned socket runtime behind")

        failing_binary = base / "failing-host"
        failing_binary.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "if [ \"${1:-}\" = \"--help\" ]; then\n"
            "  echo 'Local persistent browser Session'\n"
            "  exit 0\n"
            "fi\n"
            "test \"${1:-}\" = \"--socket-dir\"\n"
            "mkdir \"$2\"\n"
            "rmdir \"$2\"\n"
            "touch \"$2\"\n"
            "exec /bin/sleep 30\n",
            encoding="utf-8",
        )
        os.chmod(failing_binary, 0o755)
        failure_root = runtime_base / f"abhs-failure-service-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        failure_runtime = runtime_base / f"abhs-f-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        register_service_cleanup(failure_root, failure_runtime)
        manager_json(
            "install",
            "--root",
            str(failure_root),
            "--binary",
            str(failing_binary),
            "--runtime-root",
            str(failure_runtime),
            "--protocol-version",
            str(protocol_version),
        )
        failed_start = run_manager(
            "start", "--root", str(failure_root), "--ready-timeout", "1"
        )
        assert_error(failed_start, "refusing to remove non-directory runtime path")
        failure_state_path = failure_root / "run" / "state.json"
        if not failure_state_path.is_file():
            raise AssertionError("failed start did not retain recovery state")
        failure_state = json.loads(failure_state_path.read_text(encoding="utf-8"))
        failure_socket = Path(str(failure_state["socket_dir"]))
        if not failure_socket.is_file():
            raise AssertionError("failed start did not retain tampered runtime")
        failed_stop = run_manager("stop", "--root", str(failure_root))
        assert_error(failed_stop, "refusing to remove non-directory runtime path")
        failure_socket.unlink()
        manager_json("stop", "--root", str(failure_root))
        if failure_state_path.exists():
            raise AssertionError("recovered failed-start state was not removed")

        concurrent_root = runtime_base / f"abhs-concurrent-service-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        concurrent_runtime = runtime_base / f"abhs-c-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        register_service_cleanup(concurrent_root, concurrent_runtime)
        manager_json(
            "install",
            "--root",
            str(concurrent_root),
            "--binary",
            str(binary),
            "--runtime-root",
            str(concurrent_runtime),
            "--protocol-version",
            str(protocol_version),
        )
        start_command = [
            sys.executable,
            str(SCRIPT),
            "start",
            "--root",
            str(concurrent_root),
            "--ready-timeout",
            "60",
        ]
        concurrent_processes = [
            subprocess.Popen(start_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
            subprocess.Popen(start_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
        ]
        try:
            concurrent_results = [process.communicate(timeout=90) for process in concurrent_processes]
            successful = [result for process, result in zip(concurrent_processes, concurrent_results) if process.returncode == 0]
            failed = [result for process, result in zip(concurrent_processes, concurrent_results) if process.returncode != 0]
            if len(successful) != 1 or len(failed) != 1:
                raise AssertionError(f"concurrent starts were not exclusive: {concurrent_results!r}")
            if "another lifecycle mutation is in progress" not in failed[0][1]:
                raise AssertionError(f"concurrent loser did not report lock ownership: {failed[0]!r}")
            concurrent_status = manager_json("status", "--root", str(concurrent_root))
            if concurrent_status["state"] != "running":
                raise AssertionError(f"concurrent winner did not remain tracked: {concurrent_status!r}")
        finally:
            run_manager("stop", "--root", str(concurrent_root))

        restart_binary = runtime_base / f"abhs-restart-binary-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        shutil.copy2(binary, restart_binary)
        os.chmod(restart_binary, 0o755)
        restart_root = runtime_base / f"abhs-restart-service-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        restart_runtime = runtime_base / f"abhs-r-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        register_service_cleanup(restart_root, restart_runtime, restart_binary)
        manager_json(
            "install",
            "--root",
            str(restart_root),
            "--binary",
            str(restart_binary),
            "--runtime-root",
            str(restart_runtime),
            "--protocol-version",
            str(protocol_version),
        )
        restart_started = manager_json("start", "--root", str(restart_root), "--ready-timeout", "60")
        restart_pid = restart_started["pid"]
        restart_session = restart_started["session_id"]
        with restart_binary.open("ab") as output:
            output.write(b"tampered")
        restart_result = run_manager("restart", "--root", str(restart_root), "--ready-timeout", "1")
        assert_error(restart_result, "host binary changed")
        restart_status = manager_json("status", "--root", str(restart_root))
        if (
            restart_status["state"] != "running"
            or restart_status["pid"] != restart_pid
            or restart_status["session_id"] != restart_session
        ):
            raise AssertionError("restart preflight stopped a healthy Host after binary drift")
        manager_json("stop", "--root", str(restart_root))

        started = manager_json("start", "--root", str(root), "--ready-timeout", "60")
        if started["state"] != "running" or not isinstance(started["pid"], int):
            raise AssertionError(f"invalid start result: {started!r}")
        first_session = str(started["session_id"])
        socket_dir = Path(str(started["socket_dir"]))
        if private_mode(socket_dir) != 0o700:
            raise AssertionError("Host socket directory is not private")
        if private_mode(socket_dir / "host.sock") != 0o600 or private_mode(socket_dir / "frames.sock") != 0o600:
            raise AssertionError("Host sockets are not private")

        state_path = root / "run" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["launch_mode"] = "launchd"
        state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(state_path, 0o600)
        launchd_stop = run_manager("stop", "--root", str(root))
        assert_error(launchd_stop, "launchd-managed Host must be stopped through launchctl")
        if manager_json("status", "--root", str(root))["state"] != "running":
            raise AssertionError("launchd-managed Host was stopped by subprocess command")
        state["launch_mode"] = "subprocess"
        state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(state_path, 0o600)

        duplicate = run_manager("start", "--root", str(root))
        assert_error(duplicate, "Host already running")

        response, client_session = host_request(
            socket_dir / "host.sock",
            {"type": "attach", "mode": "observe"},
            protocol_version,
        )
        if response.get("type") != "result" or client_session != first_session:
            raise AssertionError(f"client attach did not reach real Host: {response!r}")
        after_client_exit = manager_json("status", "--root", str(root))
        if after_client_exit["state"] != "running" or after_client_exit["session_id"] != first_session:
            raise AssertionError("client exit incorrectly stopped or replaced Host")

        stopped = manager_json("stop", "--root", str(root))
        if stopped["state"] != "stopped" or socket_dir.exists():
            raise AssertionError("normal stop did not remove owned Host runtime")
        stopped_status = manager_json("status", "--root", str(root))
        if stopped_status["state"] != "stopped":
            raise AssertionError(f"status after stop: {stopped_status!r}")

        restarted = manager_json("restart", "--root", str(root), "--ready-timeout", "60")
        if restarted["state"] != "running" or restarted["session_id"] == first_session:
            raise AssertionError("restart did not create a new Host Session identity")
        manager_json("stop", "--root", str(root))


def main() -> int:
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--binary", type=Path)
    arguments.add_argument("--protocol-version", type=protocol_version_argument)
    args = arguments.parse_args()
    if args.binary is None and args.protocol_version is None:
        terminal_launchd_rejection_contract()
        persistent_maintenance_contract(11)
        config_version_compatibility_contract()
        print("HOST_SERVICE_DEV_FIXTURE_PASS")
        return 0
    if args.binary is None or args.protocol_version is None:
        raise SystemExit("--binary and --protocol-version must be supplied together")
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        raise SystemExit(f"real executable required: {args.binary}")
    acceptance(args.binary.resolve(), args.protocol_version)
    print("HOST_SERVICE_REAL_SUBPROCESS_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
