#!/usr/bin/env python3
"""Real subprocess acceptance for the AgentBrowser Host service entrypoint."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


SCRIPT = Path(__file__).with_name("host_service.py")


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


def host_request(socket_path: Path, command: dict[str, object]) -> tuple[dict[str, object], str]:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(10)
    client.connect(str(socket_path))
    try:
        ready = json.loads(client.makefile("rb").readline().decode("utf-8"))
        if ready.get("type") != "ready" or ready.get("version") != 4:
            raise AssertionError(f"unexpected Host ready message: {ready!r}")
        client.sendall((json.dumps({"id": 1, "command": command}) + "\n").encode("utf-8"))
        response = json.loads(client.makefile("rb").readline().decode("utf-8"))
        return response, str(ready["session_id"])
    finally:
        client.close()


def assert_error(result: subprocess.CompletedProcess[str], fragment: str) -> None:
    if result.returncode == 0 or fragment not in result.stderr:
        raise AssertionError(f"expected error {fragment!r}: rc={result.returncode}, stderr={result.stderr!r}")


def register_service_cleanup(root: Path, runtime: Path, *extra_files: Path) -> None:
    def cleanup() -> None:
        result = run_manager("stop", "--root", str(root))
        if result.returncode == 0:
            shutil.rmtree(runtime, ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)
            for path in extra_files:
                path.unlink(missing_ok=True)

    atexit.register(cleanup)


def acceptance(binary: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="agentbrowser-host-service-") as temporary:
        base = Path(temporary)
        runtime_base = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
        root = runtime_base / f"abhs-service-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        runtime = runtime_base / f"abhs-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        register_service_cleanup(root, runtime)
        launchd = base / "LaunchAgents"

        missing = base / "missing"
        missing_result = run_manager(
            "install", "--root", str(base / "missing-service"), "--binary", str(missing)
        )
        assert_error(missing_result, "host binary is not executable")

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
        )
        assert_error(shared_result, "directory must already be private")
        if private_mode(shared_runtime) != 0o755:
            raise AssertionError("existing runtime directory permissions were changed")

        spaced_result = run_manager(
            "install",
            "--root",
            str(base / "spaced-service"),
            "--binary",
            str(binary),
            "--runtime-root",
            str(base / "runtime with space"),
        )
        assert_error(spaced_result, "runtime root cannot contain whitespace")

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
        )
        assert installed["state"] == "installed"
        config_path = root / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config["profile_mode"] != "in_memory" or config["protocol_version"] != 4:
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

        config["profile_mode"] = "disk"
        config_path.write_text(json.dumps(config, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)
        assert_error(run_manager("start", "--root", str(root)), "profile_mode must be in_memory")
        config["profile_mode"] = "in_memory"
        config_path.write_text(json.dumps(config, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(config_path, 0o600)

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

        response, client_session = host_request(socket_dir / "host.sock", {"type": "attach", "mode": "observe"})
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
    arguments.add_argument("--binary", required=True, type=Path)
    args = arguments.parse_args()
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        raise SystemExit(f"real executable required: {args.binary}")
    acceptance(args.binary.resolve())
    print("HOST_SERVICE_REAL_SUBPROCESS_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
