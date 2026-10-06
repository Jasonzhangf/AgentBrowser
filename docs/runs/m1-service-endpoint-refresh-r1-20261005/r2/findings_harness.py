#!/usr/bin/env python3
"""Focused public-entry regressions for the five r1 review findings.

Every case drives the real ``host-service`` CLI and the real Obscura
Host/endpoint binaries. ``--service-dir`` selects the service under test: the
in-tree directory is the after candidate, a scratch copy of the r1 commit is
the before candidate. The same case asserts the old behavior with
``--expect before`` and the fixed behavior with ``--expect after`` so each
finding has a discriminating before/after pair.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path


RESULTS: list[dict[str, object]] = []


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
    spec = importlib.util.spec_from_file_location("harness_host_service", path)
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


def free_port(host: str = "::1") -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


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
        if head.isdigit():
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
    runtime = root / "rt"
    port = free_port(endpoint_host if ":" in endpoint_host else "127.0.0.1")
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


def catalog_profile(service, admin_socket: Path, profile_id: str) -> dict[str, object]:
    catalog = service.admin_catalog_info(admin_socket)
    profile = next((entry for entry in catalog["profiles"] if entry["profile_id"] == profile_id), None)
    if profile is None:
        raise AssertionError(f"profile {profile_id} not found")
    return {"revision": catalog["catalog_revision"], "members": list(profile["members"]), "owner": profile["owner_device_id"]}


def finding_f1(service_dir: Path, service, base: Path, expect: str, host_binary: Path, protocol_version: int) -> None:
    context = setup_host(
        base, service_dir, service, endpoint_host="::1", host_binary=host_binary, protocol_version=protocol_version
    )
    try:
        root = Path(str(context["root"]))
        socket_dir = Path(str(context["socket_dir"]))
        device_id = str(context["device_id"])
        admin_socket = socket_dir / "host-admin.sock"
        refresh = manager_json(service_dir, root, "trust", "endpoint-refresh", "--timeout", "30")
        record("f1.endpoint_refreshed", refresh["authorization_state"] == "refreshed", "refreshed", refresh["authorization_state"])

        # Explicit authorized initial grant entry point: only here does the
        # member set that contains the target get submitted.
        service.admin_membership_set(
            admin_socket, "grant", "default", "local", device_id, ["local", device_id], None
        )
        # Public explicit removal.
        service.admin_membership_set(admin_socket, "remove", "default", "local", device_id, ["local"], None)
        before = catalog_profile(service, admin_socket, "default")
        output = base / "after-remove.abconnection"
        result = manager(service_dir, root, "trust", "export", "--device-id", device_id, "--output", str(output))
        after = catalog_profile(service, admin_socket, "default")
        if expect == "before":
            ok = (
                result.returncode == 0
                and output.is_file()
                and device_id in after["members"]
                and after["revision"] != before["revision"]
            )
            record(
                "f1.export_after_remove",
                ok,
                "old caller re-grants: export succeeds and catalog members/revision change",
                {"rc": result.returncode, "before": before, "after": after, "bundle": output.is_file()},
            )
        else:
            error = cli_error(result)
            ok = (
                result.returncode != 0
                and error is not None
                and error.get("code") == "PROFILE_ACCESS_WRITE_FAILED"
                and not output.exists()
                and after == before
            )
            record(
                "f1.export_after_remove",
                ok,
                "refused PROFILE_ACCESS_WRITE_FAILED with catalog members/revision unchanged",
                {"rc": result.returncode, "error": error, "before": before, "after": after, "bundle": output.exists()},
            )

        # Public grant that omits the target must be denied before mutation.
        remove_now = ["local"] if device_id not in after["members"] else ["local"]
        try:
            service.admin_membership_set(admin_socket, "remove", "default", "local", device_id, remove_now, None)
        except service.ServiceError:
            pass
        denied_before = catalog_profile(service, admin_socket, "default")
        grant_code = None
        try:
            service.admin_membership_set(
                admin_socket, "grant", "default", "local", device_id, ["local"], None
            )
        except service.ServiceError as error:
            grant_code = error.code
        denied_after = catalog_profile(service, admin_socket, "default")
        record(
            "f1.target_omitting_grant_denied",
            grant_code == "MEMBERSHIP_REQUIRED" and denied_after == denied_before,
            {"code": "MEMBERSHIP_REQUIRED", "catalog_unchanged": True},
            {"code": grant_code, "before": denied_before, "after": denied_after},
        )
        output2 = base / "after-omit.abconnection"
        result2 = manager(service_dir, root, "trust", "export", "--device-id", device_id, "--output", str(output2))
        final = catalog_profile(service, admin_socket, "default")
        if expect == "before":
            ok = (
                result2.returncode == 0
                and output2.is_file()
                and device_id in final["members"]
                and final["revision"] != denied_after["revision"]
            )
            record(
                "f1.export_after_target_omitting_grant",
                ok,
                "old caller re-grants: export succeeds and catalog members/revision change",
                {"rc": result2.returncode, "before": denied_after, "after": final, "bundle": output2.is_file()},
            )
        else:
            error2 = cli_error(result2)
            ok = (
                result2.returncode != 0
                and error2 is not None
                and error2.get("code") == "PROFILE_ACCESS_WRITE_FAILED"
                and not output2.exists()
                and final == denied_after
            )
            record(
                "f1.export_after_target_omitting_grant",
                ok,
                "refused PROFILE_ACCESS_WRITE_FAILED with catalog members/revision unchanged",
                {"rc": result2.returncode, "error": error2, "before": denied_after, "after": final, "bundle": output2.exists()},
            )
    finally:
        teardown(service_dir, service, context)


def finding_f2(service_dir: Path, service, base: Path, expect: str, host_binary: Path, protocol_version: int) -> None:
    context = setup_host(
        base, service_dir, service, endpoint_host="localhost", host_binary=host_binary, protocol_version=protocol_version
    )
    try:
        root = Path(str(context["root"]))
        runtime = Path(str(context["runtime"]))
        port = int(context["port"])
        locator = service.trust_endpoint(root)
        url = service.endpoint_ws_url(
            str(locator["endpoint_host"]), str(locator["endpoint_host_kind"]), int(locator["endpoint_port"])
        )
        record("f2.dns_url_preserved", url == f"wss://localhost:{port}/", f"wss://localhost:{port}/", url)
        result = manager(service_dir, root, "trust", "endpoint-refresh", "--timeout", "5")
        locator_after = service.trust_endpoint(root)
        generation = locator_after["endpoint_generation"]
        if expect == "before":
            endpoint_log = (root / "logs" / "endpoint.log").read_text(errors="replace")
            ok = (
                result.returncode != 0
                and generation == 1
                and "invalid socket address syntax" in endpoint_log
                and not (runtime / "endpoint-applied" / "1.json").exists()
            )
            record(
                "f2.dns_locator_refresh",
                ok,
                "old projection fails with real endpoint exit 2 invalid socket address syntax",
                {"rc": result.returncode, "generation": generation, "endpoint_log_tail": endpoint_log[-400:]},
            )
        else:
            document = json.loads(result.stdout) if result.returncode == 0 else {}
            bind_host, bind_port = service.endpoint_bind_address(locator_after)
            expected_address = f"[{bind_host}]:{bind_port}" if ":" in bind_host else f"{bind_host}:{bind_port}"
            ok = (
                result.returncode == 0
                and document.get("authorization_state") == "refreshed"
                and generation == 1
                and document.get("bound_address") == expected_address
                and (runtime / "endpoint-applied" / "1.json").is_file()
            )
            record(
                "f2.dns_locator_refresh",
                ok,
                {"authorization_state": "refreshed", "generation": 1, "bound_address": expected_address},
                {"rc": result.returncode, "document": document, "generation": generation},
            )
    finally:
        teardown(service_dir, service, context)

    # Rejected listen address: a valid IP that is not locally assignable must
    # fail before the generation advances and before the old endpoint stops.
    # Keep the rejected-listen root on a short path so the real Host's session
    # socket stays inside the macOS Unix-socket bound.
    rejected = Path(tempfile.mkdtemp(prefix="r2fj-", dir="/private/tmp"))
    rejected.chmod(0o700)
    context = setup_host(
        rejected,
        service_dir,
        service,
        endpoint_host="::1",
        host_binary=host_binary,
        protocol_version=protocol_version,
    )
    old_pid = 0
    try:
        root = Path(str(context["root"]))
        first = manager_json(service_dir, root, "trust", "endpoint-refresh", "--timeout", "30")
        old_pid = int(first["endpoint_pid"])
        locator = service.trust_endpoint(root)
        locator["endpoint_host"] = "203.0.113.7"
        locator["endpoint_host_kind"] = "ip"
        service.write_json(root / "trust" / "endpoint.json", locator)
        result = manager(service_dir, root, "trust", "endpoint-refresh", "--timeout", "3")
        locator_after = service.trust_endpoint(root)
        generation = locator_after["endpoint_generation"]
        error = cli_error(result)
        if expect == "before":
            ok = result.returncode != 0 and generation == 2 and not pid_alive(old_pid)
            record(
                "f2.rejected_listen_before_side_effect",
                ok,
                "old behavior advances generation and stops the old endpoint before the bind fails",
                {"rc": result.returncode, "generation": generation, "old_pid_alive": pid_alive(old_pid)},
            )
        else:
            facts = (error or {}).get("facts", {})
            ok = (
                result.returncode != 0
                and generation == 1
                and pid_alive(old_pid)
                and (error or {}).get("code") == "ENDPOINT_BIND_MISMATCH"
                and facts.get("last_step") == "resolve_bind_address"
                and facts.get("authorization_state") == "not_refreshed"
            )
            record(
                "f2.rejected_listen_before_side_effect",
                ok,
                {
                    "generation": 1,
                    "old_pid_alive": True,
                    "code": "ENDPOINT_BIND_MISMATCH",
                    "last_step": "resolve_bind_address",
                },
                {"rc": result.returncode, "generation": generation, "old_pid_alive": pid_alive(old_pid), "error": error},
            )
    finally:
        context["extra_pids"] = [old_pid]
        teardown(service_dir, service, context)


def finding_f3(service_dir: Path, service, base: Path, expect: str, host_binary: Path, protocol_version: int) -> None:
    context = setup_host(
        base, service_dir, service, endpoint_host="::1", host_binary=host_binary, protocol_version=protocol_version
    )
    orphan_pids: list[int] = []
    try:
        root = Path(str(context["root"]))
        runtime = Path(str(context["runtime"]))
        record_path = root / "run" / "endpoint-process.json"
        original_write_json = service.write_json

        def failing_write_json(path: Path, value: dict[str, object]) -> None:
            if path.name == service.ENDPOINT_PROCESS_RECORD_NAME:
                raise OSError(28, "No space left on device")
            return original_write_json(path, value)

        # Drive the public refresh entry with one injected persistence failure
        # on the process-record write only. Everything else (projection,
        # generation, old-endpoint stop, real endpoint Popen) stays real.
        service.write_json = failing_write_json
        try:
            service.refresh_endpoint(root, timeout_seconds=5.0)
        except BaseException as error:  # noqa: BLE001 - both before/after failures are the observation
            failure = error
        else:
            failure = None
        finally:
            service.write_json = original_write_json
        locator = service.trust_endpoint(root)
        generation = locator["endpoint_generation"]
        receipt_path = runtime / "endpoint-applied" / f"{generation}.json"
        orphan_pids = processes_matching(str(receipt_path))
        typed_failure = failure if isinstance(failure, service.ServiceError) else None
        facts = (typed_failure.facts if typed_failure is not None else None) or {}
        if expect == "before":
            ok = (
                isinstance(failure, OSError)
                and len(orphan_pids) == 1
                and not record_path.exists()
            )
            record(
                "f3.record_persistence_failure",
                ok,
                "old behavior leaves one orphan endpoint process with no usable record",
                {
                    "error_type": type(failure).__name__,
                    "orphan_pids": orphan_pids,
                    "record_exists": record_path.exists(),
                },
            )
        else:
            started_pid = facts.get("endpoint_pid")
            ok = (
                typed_failure is not None
                and typed_failure.code == "ENDPOINT_NOT_REFRESHED"
                and facts.get("last_step") == "persist_endpoint_record"
                and isinstance(started_pid, int)
                and not pid_alive(started_pid)
                and not orphan_pids
                and generation == 1
            )
            record(
                "f3.record_persistence_failure",
                ok,
                "typed failure reclaims the child: no listening endpoint and no orphan process",
                {
                    "code": typed_failure.code if typed_failure else None,
                    "facts": facts,
                    "orphan_pids": orphan_pids,
                    "generation": generation,
                },
            )
    finally:
        for pid in orphan_pids:
            if pid_alive(pid):
                stop_pid(pid)
        teardown(service_dir, service, context)


def finding_f4(service_dir: Path, service, base: Path, expect: str, host_binary: Path, protocol_version: int) -> None:
    private_bin = base / "private-bin"
    private_bin.mkdir(mode=0o700)
    private_host = private_bin / "obscura-host"
    shutil.copyfile(host_binary, private_host)
    private_host.chmod(0o755)
    context = setup_host(
        base, service_dir, service, endpoint_host="::1", host_binary=private_host, protocol_version=protocol_version
    )
    try:
        root = Path(str(context["root"]))
        result = manager(service_dir, root, "trust", "endpoint-refresh", "--timeout", "3")
        generation = service.trust_endpoint(root)["endpoint_generation"]
        error = cli_error(result)
        if expect == "before":
            ok = (
                result.returncode != 0
                and error is None
                and "endpoint binary is not executable" in result.stderr
                and generation == 1
            )
            record(
                "f4.missing_sibling_binary",
                ok,
                "old behavior returns an untyped text error after the generation advanced",
                {"rc": result.returncode, "typed": error is not None, "generation": generation, "stderr": result.stderr[-400:]},
            )
        else:
            facts = (error or {}).get("facts", {})
            ok = (
                result.returncode != 0
                and (error or {}).get("code") == "ENDPOINT_NOT_REFRESHED"
                and facts.get("authorization_state") == "not_refreshed"
                and facts.get("last_step") == "start_endpoint"
                and facts.get("endpoint_generation") == 1
                and facts.get("endpoint_pid") is None
                and generation == 1
            )
            record(
                "f4.missing_sibling_binary",
                ok,
                {
                    "code": "ENDPOINT_NOT_REFRESHED",
                    "last_step": "start_endpoint",
                    "authorization_state": "not_refreshed",
                    "generation": 1,
                },
                {"rc": result.returncode, "error": error, "generation": generation},
            )
    finally:
        teardown(service_dir, service, context)


def finding_f5(service_dir: Path, service, base: Path, expect: str, host_binary: Path, protocol_version: int) -> None:
    context = setup_host(
        base, service_dir, service, endpoint_host="::1", host_binary=host_binary, protocol_version=protocol_version
    )
    try:
        root = Path(str(context["root"]))
        runtime = Path(str(context["runtime"]))
        refresh = manager_json(service_dir, root, "trust", "endpoint-refresh", "--timeout", "30")
        generation = int(refresh["endpoint_generation"])
        endpoint_pid = int(refresh["endpoint_pid"])
        record_path = root / "run" / "endpoint-process.json"
        process_record = json.loads(record_path.read_bytes())
        if expect == "before":
            try:
                service.wait_endpoint_applied(root, generation=generation + 1, timeout_seconds=0.5)
            except service.ServiceError:
                pass
            ok = (
                pid_alive(endpoint_pid)
                and record_path.is_file()
                and json.loads(record_path.read_bytes())["pid"] == endpoint_pid
            )
            record(
                "f5.cancel_keeps_endpoint",
                ok,
                "old CASE 9 proves only timeout survival: no cancel was performed",
                {"cancel_observed": False, "endpoint_pid_alive": pid_alive(endpoint_pid), "record_pid": process_record["pid"]},
            )
            return

        receipt_path = runtime / "endpoint-applied" / f"{generation}.json"
        backup = runtime / "endpoint-applied" / f"{generation}.json.waiting"
        receipt_path.rename(backup)
        waiter: subprocess.Popen[str] | None = None
        try:
            driver = textwrap.dedent(
                f"""
                import sys
                from pathlib import Path
                sys.path.insert(0, {str(service_dir)!r})
                import host_service
                root = Path({str(root)!r})
                lock = host_service.acquire_start_lock(root)
                try:
                    host_service.wait_endpoint_applied(root, generation={generation}, timeout_seconds=120)
                finally:
                    host_service.release_start_lock(lock)
                """
            )
            waiter = subprocess.Popen(
                [sys.executable, "-c", driver], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            time.sleep(1.0)
            waiting = waiter.poll() is None
            # Cancel the public wait: SIGINT becomes KeyboardInterrupt inside
            # the waiter, so its finally block releases the lifecycle lock
            # while the endpoint process keeps running.
            os.kill(waiter.pid, signal.SIGINT)
            waiter.wait(timeout=10)
            waiter_exited = waiter.poll() is not None
            record_pid = json.loads(record_path.read_bytes())["pid"] if record_path.is_file() else None
            socket_dir = Path(str(process_record["socket_dir"]))
            lock_fd = service.acquire_start_lock(root)
            service.release_start_lock(lock_fd)
            ok = (
                waiting
                and waiter_exited
                and waiter.returncode != 0
                and pid_alive(endpoint_pid)
                and record_pid == endpoint_pid
                and socket_dir.is_dir()
            )
            record(
                "f5.cancel_keeps_endpoint",
                ok,
                {
                    "cancel_observed": True,
                    "waiter_exited": True,
                    "endpoint_pid_alive": True,
                    "record_pid": endpoint_pid,
                    "runtime_survived": True,
                    "lock_released": True,
                },
                {
                    "cancel_observed": True,
                    "waiter_exited": waiter_exited,
                    "waiter_returncode": waiter.returncode,
                    "endpoint_pid_alive": pid_alive(endpoint_pid),
                    "record_pid": record_pid,
                    "runtime_survived": socket_dir.is_dir(),
                    "lock_released": True,
                },
            )
        finally:
            if waiter is not None and waiter.poll() is None:
                waiter.kill()
                waiter.wait(timeout=5)
            if backup.exists() and not receipt_path.exists():
                backup.rename(receipt_path)
    finally:
        teardown(service_dir, service, context)


FINDINGS = {
    "f1": finding_f1,
    "f2": finding_f2,
    "f3": finding_f3,
    "f4": finding_f4,
    "f5": finding_f5,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-dir", required=True)
    parser.add_argument("--finding", required=True, choices=sorted(FINDINGS))
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
            ["mktemp", "-d", str(runtime_base / "m1refresh-r2-XXXXXX")],
            check=True,
            capture_output=True,
            text=True,
        )
        base = Path(created.stdout.strip())
        base.chmod(0o700)
        retained = False
    print(json.dumps({"base": str(base), "finding": arguments.finding, "expect": arguments.expect}), flush=True)

    FINDINGS[arguments.finding](service_dir, service, base, arguments.expect, host_binary, protocol_version)

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
