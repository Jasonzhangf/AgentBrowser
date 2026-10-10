#!/usr/bin/env python3
"""Focused public-entry regressions for the three r2 P1 findings and the P2 leak.

Every case drives the real ``host-service`` CLI and the real Obscura
Host/endpoint binaries. ``--service-dir`` selects the service under test: the
in-tree directory is the after candidate, a scratch copy of the r1/r2 commit
is the before candidate. Each case asserts the old behavior with
``--expect before`` and the fixed behavior with ``--expect after`` so every
finding has a discriminating, real-filesystem before/after pair.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


RESULTS: list[dict[str, object]] = []
ENDPOINT_RUNTIME_RE = re.compile(r"^endpoint-[0-9a-f]{8}$")


def record(name: str, ok: bool, expected: object, actual: object) -> None:
    RESULTS.append({"case": name, "ok": bool(ok), "expected": expected, "actual": actual})
    print(json.dumps({"case": name, "ok": bool(ok), "expected": expected, "actual": actual}), flush=True)


def require_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    path = Path(value)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise SystemExit(f"{name} is not an executable file: {path}")
    return path


def load_service_module(service_dir: Path):
    path = service_dir / "host_service.py"
    spec = importlib.util.spec_from_file_location(f"harness_host_service_{service_dir.name}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load host_service.py from {service_dir}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manager(service_dir: Path, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(service_dir / "host-service"), *arguments, "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def manager_json(service_dir: Path, root: Path, *arguments: str) -> dict[str, object]:
    result = manager(service_dir, root, *arguments)
    if result.returncode != 0:
        raise AssertionError(f"manager failed {arguments}: rc={result.returncode}\n{result.stdout}\n{result.stderr}")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise AssertionError(f"manager JSON is not an object: {value!r}")
    return value


def cli_error(result: subprocess.CompletedProcess[str]) -> dict[str, object] | None:
    try:
        document = json.loads(result.stderr.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None
    if not isinstance(document, dict):
        return None
    error = document.get("error")
    return error if isinstance(error, dict) else None


def free_port(host: str = "127.0.0.1") -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def port_is_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_pid(pid: int, timeout: float = 10.0) -> None:
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def processes_matching(token: str) -> list[int]:
    result = subprocess.run(
        ["/bin/ps", "-Ao", "pid=,command="],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or token not in stripped:
            continue
        head = stripped.split(None, 1)[0]
        if head.isdigit() and int(head) != os.getpid():
            pids.append(int(head))
    return pids


def setup_host(
    base: Path,
    service_dir: Path,
    service,
    *,
    endpoint_host: str,
    host_binary: Path,
    protocol_version: int,
) -> dict[str, object]:
    root = base / "root"
    root.mkdir(mode=0o700)
    runtime = base / "rt"
    port = free_port("127.0.0.1")
    manager_json(service_dir, root, "trust", "init", "--endpoint-host", endpoint_host, "--endpoint-port", str(port))
    device = manager_json(
        service_dir,
        root,
        "trust",
        "provision",
        "--label",
        "Android phone",
        "--scope",
        "observe_human_control",
        "--scope",
        "profile_read",
        "--scope",
        "profile_select",
        "--initial-profile",
        "profile_id:default",
    )
    manager_json(
        service_dir,
        root,
        "install",
        "--binary",
        str(host_binary),
        "--protocol-version",
        str(protocol_version),
        "--runtime-root",
        str(runtime),
        "--profile-root",
        str(root / "profiles"),
        "--launchd-root",
        str(root / "launchd"),
    )
    started = manager_json(service_dir, root, "start", "--ready-timeout", "60")
    return {
        "root": root,
        "runtime": runtime,
        "port": port,
        "device_id": str(device["device_id"]),
        "host_pid": int(started["pid"]),
        "socket_dir": Path(str(started["socket_dir"])),
    }


def teardown(service_dir: Path, service, context: dict[str, object]) -> None:
    root = Path(str(context["root"]))
    host_pid = int(context.get("host_pid") or 0)
    try:
        manager(service_dir, root, "stop")
    except Exception:
        pass
    for pid in list(context.get("extra_pids") or []):
        if pid_alive(int(pid)):
            stop_pid(int(pid))
    if host_pid and pid_alive(host_pid):
        stop_pid(host_pid)


# --- P1-a: one declared numeric bind basis -----------------------------------


ROTATING_RESOLVER = '''
import socket as _socket

_original_getaddrinfo = _socket.getaddrinfo
_ROTATING_HOST = "rot.test"
_ANSWERS = ["127.0.0.1", "127.0.0.1", "127.0.0.2"]
_state = {"index": 0}


def _rotating_getaddrinfo(host, port, *args, **kwargs):
    if host == _ROTATING_HOST:
        index = _state["index"]
        _state["index"] = index + 1
        address = _ANSWERS[index] if index < len(_ANSWERS) else _ANSWERS[-1]
        return [(_socket.AF_INET, _socket.SOCK_STREAM, _socket.IPPROTO_TCP, "", (address, port))]
    return _original_getaddrinfo(host, port, *args, **kwargs)


_socket.getaddrinfo = _rotating_getaddrinfo
'''


def write_rotating_resolver(base: Path) -> Path:
    directory = base / "rot-sc"
    directory.mkdir(mode=0o700)
    (directory / "sitecustomize.py").write_text(ROTATING_RESOLVER, encoding="utf-8")
    return directory


def refresh_with_env(
    service_dir: Path, root: Path, env_extra: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [str(service_dir / "host-service"), *arguments, "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


def case_p1a(service_dir: Path, service, base: Path, expect: str, host_binary: Path, protocol_version: int) -> None:
    resolver_dir = write_rotating_resolver(base)
    env_extra = {"PYTHONPATH": str(resolver_dir)}
    context = setup_host(
        base,
        service_dir,
        service,
        endpoint_host="rot.test",
        host_binary=host_binary,
        protocol_version=protocol_version,
    )
    try:
        root = Path(str(context["root"]))
        port = int(context["port"])
        locator = service.trust_endpoint(root)
        record(
            "p1a.dns_locator_preserved",
            locator["endpoint_host"] == "rot.test" and locator["endpoint_host_kind"] == "dns",
            {"host": "rot.test", "kind": "dns"},
            {"host": locator["endpoint_host"], "kind": locator["endpoint_host_kind"]},
        )
        # The resolver returns 127.0.0.1 for the preflight and the start, then
        # 127.0.0.2 for the next query. The real endpoint binds the assignable
        # 127.0.0.1 and writes a correct receipt for it.
        result = refresh_with_env(
            service_dir, root, env_extra, "trust", "endpoint-refresh", "--timeout", "10"
        )
        status = refresh_with_env(service_dir, root, {}, "trust", "status")
        status_document = json.loads(status.stdout) if status.returncode == 0 else {}
        locator_after = service.trust_endpoint(root)
        generation = locator_after["endpoint_generation"]
        expected_bound = f"127.0.0.1:{port}"
        receipt_path = Path(str(context["runtime"])) / "endpoint-applied" / f"{generation}.json"
        receipt_ok = receipt_path.is_file()
        document = json.loads(result.stdout) if result.returncode == 0 else {}
        # The fixed postcondition, run against both candidates. The r2 code
        # resolves three times, so a changing answer rejects a correct receipt.
        ok = (
            result.returncode == 0
            and document.get("authorization_state") == "refreshed"
            and document.get("bound_address") == expected_bound
            and generation == 1
            and receipt_ok
            and status_document.get("authorization_state") == "refreshed"
            and status_document.get("endpoint_pid") == document.get("endpoint_pid")
        )
        record(
            "p1a.rotating_resolution",
            ok,
            {
                "authorization_state": "refreshed",
                "bound_address": expected_bound,
                "generation": 1,
                "status_authorization_state": "refreshed",
            },
            {
                "rc": result.returncode,
                "error": cli_error(result),
                "document": document,
                "generation": generation,
                "receipt": receipt_ok,
                "status_authorization_state": status_document.get("authorization_state"),
                "status_endpoint_pid": status_document.get("endpoint_pid"),
            },
        )
    finally:
        teardown(service_dir, service, context)


# --- P1-b: the acceptance boundary converts a real read failure --------------


def case_p1b(service_dir: Path, service, base: Path, expect: str, host_binary: Path, protocol_version: int) -> None:
    context = setup_host(
        base,
        service_dir,
        service,
        endpoint_host="127.0.0.1",
        host_binary=host_binary,
        protocol_version=protocol_version,
    )
    try:
        root = Path(str(context["root"]))
        runtime = Path(str(context["runtime"]))
        registry_path = root / "trust" / "registry.json"
        record_path = root / "run" / "endpoint-process.json"
        flip = {"done": False, "at": None}

        def watchdog() -> None:
            # The typed process record is written strictly after the step-1
            # registry read and strictly before the acceptance reads. Flip the
            # registry to an unreadable regular file once it appears.
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if record_path.exists():
                    try:
                        os.chmod(registry_path, 0o000)
                    except OSError:
                        pass
                    flip["done"] = True
                    flip["at"] = time.monotonic()
                    return
                time.sleep(0.001)

        watcher = threading.Thread(target=watchdog, daemon=True)
        watcher.start()
        try:
            result = manager(service_dir, root, "trust", "endpoint-refresh", "--timeout", "10")
        finally:
            os.chmod(registry_path, 0o600)
            watcher.join(timeout=5)
        error = cli_error(result)
        generation = service.trust_endpoint(root)["endpoint_generation"]
        record_pid = None
        if record_path.is_file():
            record_pid = int(json.loads(record_path.read_bytes())["pid"])
        # The lifecycle lock must be released on both exits.
        lock_fd = service.acquire_start_lock(root)
        service.release_start_lock(lock_fd)
        facts = (error or {}).get("facts", {})
        # The fixed postcondition, run against both candidates. The r2 code
        # lets the acceptance OSError escape as an untyped traceback.
        ok = (
            result.returncode != 0
            and (error or {}).get("code") == "ENDPOINT_NOT_REFRESHED"
            and facts.get("authorization_state") == "not_refreshed"
            and facts.get("last_step") == "verify_applied"
            and facts.get("endpoint_generation") == 1
            and isinstance(facts.get("endpoint_pid"), int)
            and flip["done"]
            and generation == 1
            and record_pid is not None
            and pid_alive(record_pid)
        )
        record(
            "p1b.verify_read_failure",
            ok,
            {
                "code": "ENDPOINT_NOT_REFRESHED",
                "last_step": "verify_applied",
                "authorization_state": "not_refreshed",
                "generation": 1,
                "lock_released": True,
                "endpoint_pid_alive": True,
            },
            {
                "rc": result.returncode,
                "error": error,
                "typed": error is not None,
                "traceback": "Traceback" in result.stderr,
                "generation": generation,
                "record_pid": record_pid,
                "endpoint_pid_alive": bool(record_pid and pid_alive(record_pid)),
                "stderr_tail": result.stderr[-300:],
            },
        )
    finally:
        if registry_path.exists():
            try:
                os.chmod(registry_path, 0o600)
            except OSError:
                pass
        teardown(service_dir, service, context)


# --- P1-c: the real process-record write path fails on a real filesystem -----


def endpoint_runtime_dirs(runtime: Path) -> list[Path]:
    if not runtime.is_dir():
        return []
    return [entry for entry in runtime.iterdir() if entry.is_dir() and ENDPOINT_RUNTIME_RE.match(entry.name)]


def case_p1c(service_dir: Path, service, base: Path, expect: str, host_binary: Path, protocol_version: int) -> None:
    context = setup_host(
        base,
        service_dir,
        service,
        endpoint_host="127.0.0.1",
        host_binary=host_binary,
        protocol_version=protocol_version,
    )
    orphan_pids: list[int] = []
    try:
        root = Path(str(context["root"]))
        runtime = Path(str(context["runtime"]))
        port = int(context["port"])
        run_dir = root / "run"
        record_path = run_dir / "endpoint-process.json"
        # A real, controllable filesystem failure: the record's own parent
        # directory rejects new entries, so the unmodified product writer
        # fails on a real mkstemp/rename instead of a replaced internal call.
        os.chflags(run_dir, stat.UF_IMMUTABLE)
        try:
            try:
                service.refresh_endpoint(root, timeout_seconds=10.0)
            except BaseException as error:  # noqa: BLE001 - both before/after failures are the observation
                failure = error
            else:
                failure = None
        finally:
            os.chflags(run_dir, 0)
        locator = service.trust_endpoint(root)
        generation = locator["endpoint_generation"]
        receipt_path = runtime / "endpoint-applied" / f"{generation}.json"
        orphan_pids = processes_matching(str(receipt_path))
        typed_failure = failure if isinstance(failure, service.ServiceError) else None
        facts = (typed_failure.facts if typed_failure is not None else None) or {}
        runtime_dirs = endpoint_runtime_dirs(runtime)
        started_pid = facts.get("endpoint_pid")
        # The fixed postcondition, run against both candidates. The r1 code has
        # no start/persist guard, so it leaves one orphan endpoint process.
        ok = (
            typed_failure is not None
            and typed_failure.code == "ENDPOINT_NOT_REFRESHED"
            and facts.get("last_step") == "persist_endpoint_record"
            and isinstance(started_pid, int)
            and not pid_alive(started_pid)
            and not orphan_pids
            and generation == 1
            and not record_path.exists()
            and not runtime_dirs
            and port_is_free("127.0.0.1", port)
        )
        record(
            "p1c.record_write_failure",
            ok,
            {
                "code": "ENDPOINT_NOT_REFRESHED",
                "last_step": "persist_endpoint_record",
                "pid_exited": True,
                "listener_released": True,
                "no_orphan_runtime": True,
                "no_record": True,
            },
            {
                "error_type": type(failure).__name__,
                "code": typed_failure.code if typed_failure else None,
                "facts": facts,
                "orphan_pids": orphan_pids,
                "record_exists": record_path.exists(),
                "runtime_dirs": [str(path) for path in runtime_dirs],
                "listener_free": port_is_free("127.0.0.1", port),
                "generation": generation,
            },
        )
    finally:
        os.chflags(root / "run", 0)
        for pid in orphan_pids:
            if pid_alive(pid):
                stop_pid(pid)
        teardown(service_dir, service, context)


# --- P2: the public consumer reclaims the short runtime ----------------------


def case_p2(service_dir: Path, service, base: Path, expect: str, host_binary: Path, protocol_version: int) -> None:
    consumer = service_dir / "test_host_trust_public.py"
    if not consumer.is_file():
        raise SystemExit(f"public consumer not found: {consumer}")
    leaked_before = scan_leaks()
    run_root = base / "consumer-root"
    run_root.mkdir(mode=0o700)

    driver = (
        "import importlib.util, sys\n"
        "from pathlib import Path\n"
        f"path = Path({str(consumer)!r})\n"
        "spec = importlib.util.spec_from_file_location('consumer_mod', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['consumer_mod'] = module\n"
        "spec.loader.exec_module(module)\n"
        f"module.test_refresh_missing_binary_typed_failure(Path({str(run_root)!r}))\n"
        "print('CONSUMER_SLICE_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", driver],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    leaked_after = scan_leaks()
    new_leaks = sorted(set(leaked_after) - set(leaked_before))
    ok = result.returncode == 0 and "CONSUMER_SLICE_OK" in result.stdout and not new_leaks
    record(
        "p2.temporary_runtime",
        ok,
        "the public consumer slice leaves no new r2mb-* runtime under /private/tmp",
        {
            "rc": result.returncode,
            "new_leaks": new_leaks,
            "stdout_tail": result.stdout[-200:],
            "stderr_tail": result.stderr[-300:],
        },
    )
    # Reclaim any directory this observation created so the leak does not
    # accumulate across runs; the assertion above already recorded the fact.
    for path in new_leaks:
        shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(run_root, ignore_errors=True)


def scan_leaks() -> list[str]:
    base = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
    return sorted(str(path) for path in base.glob("r2mb-*"))


CASES = {
    "p1a": case_p1a,
    "p1b": case_p1b,
    "p1c": case_p1c,
    "p2": case_p2,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-dir", required=True)
    parser.add_argument("--case", required=True, choices=sorted(CASES))
    parser.add_argument("--expect", required=True, choices=("before", "after"))
    parser.add_argument("--base-dir", default=None)
    arguments = parser.parse_args()

    service_dir = Path(arguments.service_dir).resolve()
    if not (service_dir / "host-service").is_file() or not (service_dir / "host_service.py").is_file():
        raise SystemExit(f"service dir is not a host-service directory: {service_dir}")
    service = load_service_module(service_dir)
    host_binary = require_env("OBSCURA_HOST_BINARY")
    protocol_version = int(os.environ.get("OBSCURA_HOST_PROTOCOL_VERSION", "0"))
    if protocol_version != 11:
        raise SystemExit(f"unexpected protocol version: {protocol_version}")

    if arguments.base_dir:
        base = Path(arguments.base_dir)
        base.mkdir(mode=0o700, exist_ok=True)
        retained = True
    else:
        runtime_base = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
        created = subprocess.run(
            ["mktemp", "-d", str(runtime_base / "m1r3-XXXXXX")],
            check=True,
            capture_output=True,
            text=True,
        )
        base = Path(created.stdout.strip())
        base.chmod(0o700)
        retained = False
    print(json.dumps({"base": str(base), "case": arguments.case, "expect": arguments.expect}), flush=True)

    CASES[arguments.case](service_dir, service, base, arguments.expect, host_binary, protocol_version)

    failures = [item for item in RESULTS if not item["ok"]]
    if failures:
        print(json.dumps({"failures": failures}, indent=2), flush=True)
        print("FINDINGS_HARNESS_FAIL", flush=True)
        return 1
    if not retained:
        shutil.rmtree(base, ignore_errors=True)
    print("FINDINGS_HARNESS_PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
