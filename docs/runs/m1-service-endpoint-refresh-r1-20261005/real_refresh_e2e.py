#!/usr/bin/env python3
"""Real-producer E2E for the service-owned endpoint refresh sequence.

Drives only the real binaries the controller recorded (``OBSCURA_HOST_BINARY``
and the ``obscura-endpoint``/``obscura-media`` beside it) through the public
``host-service`` CLI and the public module functions. It never substitutes a
fixture host and never fabricates a receipt, and it stops only the children it
started, by explicit PID, after preserving evidence.

The whole point of this harness is the nine-step refresh sequence in
``repair-design.md`` §4.3: the service must own the endpoint process lifecycle,
allocate the next generation in the existing ``trust/endpoint.json`` locator,
stop the old endpoint, remove the superseded receipt, start the new endpoint,
bounded-wait for the exact generation file, verify it, and only then expose
``authorization_state: "refreshed"``.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[3] / "scripts" / "host-service"
sys.path.insert(0, str(SCRIPT_DIR))

import host_service  # noqa: E402  (public module under test)


MANAGER = SCRIPT_DIR / "host-service"
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


def manager(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(MANAGER), *arguments, "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"manager failed {arguments}: rc={result.returncode}\n{result.stdout}\n{result.stderr}"
        )
    return result


def manager_json(root: Path, *arguments: str) -> dict[str, object]:
    value = json.loads(manager(root, *arguments).stdout)
    if not isinstance(value, dict):
        raise AssertionError(f"manager JSON is not an object: {value!r}")
    return value


def cli_error_code(result: subprocess.CompletedProcess[str]) -> str | None:
    try:
        document = json.loads(result.stderr.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None
    if not isinstance(document, dict):
        return None
    error = document.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def expect_cli_code(label: str, expected: str, root: Path, *arguments: str) -> None:
    result = manager(root, *arguments, check=False)
    code = cli_error_code(result)
    record(label, result.returncode != 0 and code == expected, expected, {"rc": result.returncode, "code": code})


def free_port() -> int:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
        probe.bind(("::1", 0))
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


def expect_code(label: str, expected: str, call) -> None:
    try:
        value = call()
    except host_service.ServiceError as error:
        record(label, error.code == expected, expected, error.code or str(error))
    else:
        record(label, False, expected, f"unexpected success {json.dumps(value)[:160]}")


def file_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def preserve_evidence(destination: Path | None, source: Path, name: str) -> None:
    if destination is None or not source.is_file():
        return
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination / name)


def main() -> int:
    host_binary = require_env("OBSCURA_HOST_BINARY")
    endpoint_binary = Path(os.environ.get("OBSCURA_ENDPOINT_BINARY", str(host_binary.with_name("obscura-endpoint"))))
    media_binary = Path(os.environ.get("OBSCURA_MEDIA_BINARY", str(host_binary.with_name("obscura-media"))))
    protocol_version = int(os.environ.get("OBSCURA_HOST_PROTOCOL_VERSION", "0"))
    if protocol_version != 11:
        raise SystemExit(f"unexpected protocol version: {protocol_version}")
    for name, path in (("endpoint", endpoint_binary), ("media", media_binary)):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise SystemExit(f"{name} binary is not executable: {path}")

    runtime_base = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
    created = subprocess.run(
        ["mktemp", "-d", str(runtime_base / "m1refresh-XXXXXX")],
        check=True,
        capture_output=True,
        text=True,
    )
    base = Path(created.stdout.strip())
    base.chmod(0o700)
    root = base / "root"
    root.mkdir(mode=0o700)
    runtime = root / "rt"
    profiles = root / "profiles"
    port = free_port()
    host_pid = 0
    endpoint_pids: list[int] = []
    evidence_dir = os.environ.get("REFRESH_EVIDENCE_DIR")
    evidence = Path(evidence_dir) if evidence_dir else None
    print(json.dumps({"base": str(base), "port": port, "host_binary": str(host_binary)}), flush=True)

    try:
        # PREREQ: real trust root, enrollment, real Host install/start.
        manager_json(root, "trust", "init", "--endpoint-host", "::1", "--endpoint-port", str(port))
        locator0 = host_service.trust_endpoint(root)
        record(
            "prereq.locator_seed",
            locator0.get("endpoint_generation") == 0
            and locator0.get("endpoint_host") == "::1"
            and locator0.get("endpoint_port") == port,
            {"endpoint_generation": 0, "endpoint_host": "::1", "endpoint_port": port},
            locator0,
        )
        device = manager_json(
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
        device_id = str(device["device_id"])
        manager_json(
            root,
            "install",
            "--binary",
            str(host_binary),
            "--protocol-version",
            str(protocol_version),
            "--runtime-root",
            str(runtime),
            "--profile-root",
            str(profiles),
            "--launchd-root",
            str(root / "launchd"),
        )
        started = manager_json(root, "start", "--ready-timeout", "60")
        host_pid = int(started["pid"])
        socket_dir = Path(str(started["socket_dir"]))
        record("prereq.host_started", host_pid > 0 and socket_dir.is_dir(), "running Host", {"pid": host_pid})

        # CASE 1: the declared service entry owns the refresh sequence.
        refreshed = manager_json(root, "trust", "endpoint-refresh", "--timeout", "30")
        generation = int(refreshed["endpoint_generation"])
        endpoint_pid = int(refreshed["endpoint_pid"])
        endpoint_pids.append(endpoint_pid)
        record(
            "case1.refreshed",
            refreshed["authorization_state"] == "refreshed"
            and generation == 1
            and endpoint_pid > 0
            and refreshed["bound_address"] == f"[::1]:{port}",
            {"authorization_state": "refreshed", "endpoint_generation": 1, "bound_address": f"[::1]:{port}"},
            refreshed,
        )
        record(
            "case1.generation_in_locator",
            host_service.trust_endpoint(root).get("endpoint_generation") == generation,
            generation,
            host_service.trust_endpoint(root).get("endpoint_generation"),
        )

        # CASE 2: the real endpoint wrote the exact generation file.
        receipt_path = runtime / "endpoint-applied" / f"{generation}.json"
        raw_receipt = json.loads(receipt_path.read_bytes())
        registry_bytes = (root / "trust" / "registry.json").read_bytes()
        projection_bytes = (root / "trust" / "authorized-clients.json").read_bytes()
        record(
            "case2.receipt_shape",
            set(raw_receipt)
            == {
                "schema_version",
                "endpoint_generation",
                "endpoint_pid",
                "bound_address",
                "authorization_revision",
                "registry_sha256",
                "projection_sha256",
            }
            and raw_receipt["schema_version"] == 1
            and "status" not in raw_receipt,
            "exact v1 applied-receipt shape, no status field",
            raw_receipt,
        )
        record(
            "case2.receipt_matches_endpoint",
            raw_receipt["endpoint_pid"] == endpoint_pid
            and raw_receipt["endpoint_generation"] == generation
            and raw_receipt["bound_address"] == f"[::1]:{port}"
            and pid_alive(endpoint_pid),
            {"endpoint_pid": endpoint_pid, "endpoint_generation": generation, "bound_address": f"[::1]:{port}"},
            raw_receipt,
        )
        record(
            "case2.receipt_material",
            raw_receipt["registry_sha256"] == host_service.sha256_bytes(registry_bytes)
            and raw_receipt["projection_sha256"] == host_service.sha256_bytes(projection_bytes),
            "receipt digests == committed material",
            {
                "registry_sha256": raw_receipt["registry_sha256"],
                "projection_sha256": raw_receipt["projection_sha256"],
            },
        )
        record("case2.receipt_mode", file_mode(receipt_path) == 0o600, "0600", oct(file_mode(receipt_path)))

        # CASE 3: the declared process record carries the exact PID/argv.
        process_record_path = root / "run" / "endpoint-process.json"
        process_record = json.loads(process_record_path.read_bytes())
        record(
            "case3.process_record",
            process_record["pid"] == endpoint_pid
            and process_record["command"][0] == str(endpoint_binary)
            and str(receipt_path) in process_record["command"]
            and file_mode(process_record_path) == 0o600,
            "declared endpoint process record with the exact argv",
            process_record,
        )

        # CASE 4: status projects the verified receipt, not file publication.
        status = manager_json(root, "trust", "status")
        record(
            "case4.status_refreshed",
            status["authorization_state"] == "refreshed"
            and status["endpoint_pid"] == endpoint_pid
            and status["endpoint_generation"] == generation,
            {"authorization_state": "refreshed", "endpoint_pid": endpoint_pid, "endpoint_generation": generation},
            {
                "authorization_state": status["authorization_state"],
                "endpoint_pid": status["endpoint_pid"],
                "endpoint_generation": status["endpoint_generation"],
            },
        )

        # CASE 5: enrollment alone is not a grant. The explicit authorized grant
        # entry point adds the enrolled target to the committed member set; only
        # this path submits a member set that contains the target. `trust export`
        # consumes the committed membership and never re-grants it.
        admin_socket = socket_dir / "host-admin.sock"
        granted = host_service.admin_membership_set(
            admin_socket, "grant", "default", "local", device_id, ["local", device_id], None
        )
        grant_receipt = granted["membership_receipt"]
        record(
            "case5.membership_grant",
            grant_receipt["receipt_type"] == "export_eligible"
            and grant_receipt["device_id"] == device_id
            and device_id in grant_receipt["members"]
            and "local" in grant_receipt["members"],
            {"receipt_type": "export_eligible", "members": ["local", device_id]},
            grant_receipt,
        )

        # CASE 5: trust export writes a real bundle gated by the real endpoint
        # receipt. The `agentbrowser.connection@1` bundle schema does not carry
        # an `endpoint_receipt` field; the receipt is the export gate, so the
        # bundle is written only because a live endpoint really bound a
        # listener and published the exact generation material.
        bundle_path = base / "device.abconnection"
        exported = manager_json(
            root, "trust", "export", "--device-id", device_id, "--output", str(bundle_path)
        )
        bundle = json.loads(bundle_path.read_bytes())
        verified_receipt = host_service.wait_endpoint_applied(root, generation=generation, timeout_seconds=5.0)
        record(
            "case5.export_bundle",
            exported["state"] == "exported"
            and bundle_path.is_file()
            and bundle["format"] == "agentbrowser.connection"
            and bundle["version"] == 1
            and bundle["transport"] == "wss"
            and bundle["endpoint"] == f"wss://[::1]:{port}/"
            and bundle["device_id"] == device_id
            and isinstance(bundle.get("p12_base64"), str)
            and verified_receipt["endpoint_pid"] == endpoint_pid
            and verified_receipt["endpoint_generation"] == generation,
            {
                "state": "exported",
                "format": "agentbrowser.connection",
                "endpoint": f"wss://[::1]:{port}/",
                "verified_endpoint_receipt": {"endpoint_pid": endpoint_pid, "endpoint_generation": generation},
            },
            {"exported": exported, "endpoint": bundle.get("endpoint"), "endpoint_receipt": verified_receipt},
        )
        record("case5.bundle_mode", file_mode(bundle_path) == 0o600, "0600", oct(file_mode(bundle_path)))
        preserve_evidence(evidence, receipt_path, "endpoint-applied-1.json")
        preserve_evidence(evidence, bundle_path, "device.abconnection")

        # CASE 5b: a guessed, removed, target-omitting, or endpoint-file receipt
        # is refused by the real export boundary. `good_membership` is the
        # committed grant proof from the explicit authorized grant above.
        good_membership = grant_receipt
        refused_path = base / "refused.abconnection"
        unknown_device = "00000000-0000-4000-8000-000000000000"
        expect_code(
            "case5b.guessed_refused",
            "PROFILE_ACCESS_WRITE_FAILED",
            lambda: host_service.export_bundle(
                root,
                device_id,
                refused_path,
                membership_receipt={**good_membership, "device_id": unknown_device},
                endpoint_receipt=verified_receipt,
            ),
        )
        removed = host_service.admin_membership_set(
            admin_socket, "remove", "default", "local", device_id, ["local"], None
        )["membership_receipt"]
        expect_code(
            "case5b.removed_refused",
            "PROFILE_ACCESS_WRITE_FAILED",
            lambda: host_service.export_bundle(
                root, device_id, refused_path, membership_receipt=removed, endpoint_receipt=verified_receipt
            ),
        )
        # The public CLI export must refuse after the removal and must not
        # re-authorize the committed catalog: enrollment alone is not a grant.
        catalog_before_cli = host_service.admin_catalog_info(admin_socket)
        removed_cli = manager(
            root, "trust", "export", "--device-id", device_id, "--output", str(refused_path), check=False
        )
        catalog_after_cli = host_service.admin_catalog_info(admin_socket)
        record(
            "case5b.removed_cli_refused",
            removed_cli.returncode != 0
            and "PROFILE_ACCESS_WRITE_FAILED" in removed_cli.stderr
            and catalog_after_cli == catalog_before_cli
            and not refused_path.exists(),
            {"code": "PROFILE_ACCESS_WRITE_FAILED", "catalog_unchanged": True},
            {
                "rc": removed_cli.returncode,
                "stderr": removed_cli.stderr[-200:],
                "catalog_unchanged": catalog_after_cli == catalog_before_cli,
            },
        )
        expect_code(
            "case5b.target_omitting_refused",
            "PROFILE_ACCESS_WRITE_FAILED",
            lambda: host_service.export_bundle(
                root,
                device_id,
                refused_path,
                membership_receipt={**good_membership, "members": ["local"]},
                endpoint_receipt=verified_receipt,
            ),
        )
        expect_code(
            "case5b.endpoint_file_refused",
            "PROJECTION_MATERIAL_CONFLICT",
            lambda: host_service.export_bundle(
                root, device_id, refused_path, membership_receipt=good_membership, endpoint_receipt=raw_receipt
            ),
        )
        host_service.admin_membership_set(
            admin_socket, "grant", "default", "local", device_id, ["local", device_id], None
        )

        # CASE 6: a real endpoint file with status:ready and no schema_version is refused.
        original_receipt = receipt_path.read_bytes()
        try:
            ready_receipt = {
                "status": "ready",
                "endpoint_pid": endpoint_pid,
                "bound_address": f"[::1]:{port}",
            }
            receipt_path.write_bytes(json.dumps(ready_receipt, separators=(",", ":")).encode("utf-8"))
            os.chmod(receipt_path, 0o600)
            expect_cli_code(
                "case6.status_ready_refused",
                "ENDPOINT_RECEIPT_MALFORMED",
                root,
                "trust",
                "export",
                "--device-id",
                device_id,
                "--output",
                str(base / "refused-status-ready.abconnection"),
            )
        finally:
            receipt_path.write_bytes(original_receipt)
            os.chmod(receipt_path, 0o600)

        # CASE 7: mismatched revision / registry digest / projection digest.
        for label, override in (
            ("case7.revision_conflict", {"authorization_revision": raw_receipt["authorization_revision"] + 1}),
            ("case7.registry_conflict", {"registry_sha256": "0" * 64}),
            ("case7.projection_conflict", {"projection_sha256": "0" * 64}),
        ):
            try:
                receipt_path.write_bytes(
                    json.dumps({**raw_receipt, **override}, separators=(",", ":")).encode("utf-8")
                )
                os.chmod(receipt_path, 0o600)
                expect_cli_code(
                    label,
                    "PROJECTION_MATERIAL_CONFLICT",
                    root,
                    "trust",
                    "export",
                    "--device-id",
                    device_id,
                    "--output",
                    str(base / f"refused-{label}.abconnection"),
                )
            finally:
                receipt_path.write_bytes(original_receipt)
                os.chmod(receipt_path, 0o600)

        # CASE 8: an old generation's receipt never satisfies a new generation.
        expect_code(
            "case8.old_generation_not_accepted",
            "ENDPOINT_NOT_REFRESHED",
            lambda: host_service.wait_endpoint_applied(root, generation=generation + 1, timeout_seconds=0.5),
        )

        # CASE 9: actually cancel a public waiter that is still waiting for the
        # receipt, then prove the cancel released only the waiter. The current
        # generation receipt is hidden so the wait does not resolve early.
        backup_receipt = receipt_path.with_suffix(".json.case9")
        receipt_path.rename(backup_receipt)
        waiter: subprocess.Popen[str] | None = None
        try:
            waiter_driver = (
                "import sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(SCRIPT_DIR)!r})\n"
                "import host_service\n"
                f"root = Path({str(root)!r})\n"
                "lock = host_service.acquire_start_lock(root)\n"
                "try:\n"
                f"    host_service.wait_endpoint_applied(root, generation={generation}, timeout_seconds=120)\n"
                "finally:\n"
                "    host_service.release_start_lock(lock)\n"
            )
            waiter = subprocess.Popen(
                [sys.executable, "-c", waiter_driver],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(1.0)
            waiter_was_waiting = waiter.poll() is None
            os.kill(waiter.pid, signal.SIGINT)
            waiter.wait(timeout=10)
            waiter_exited = waiter.poll() is not None
            record_pid = json.loads(process_record_path.read_bytes())["pid"] if process_record_path.is_file() else None
            socket_dir_path = Path(str(process_record["socket_dir"]))
            lock_holder = host_service.acquire_start_lock(root)
            host_service.release_start_lock(lock_holder)
            record(
                "case9.cancel_keeps_endpoint",
                waiter_was_waiting
                and waiter_exited
                and waiter.returncode != 0
                and pid_alive(endpoint_pid)
                and record_pid == endpoint_pid
                and socket_dir_path.is_dir(),
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
                    "runtime_survived": socket_dir_path.is_dir(),
                    "lock_released": True,
                },
            )
            preserve_evidence(evidence, process_record_path, "endpoint-process-after-cancel.json")
        finally:
            if waiter is not None and waiter.poll() is None:
                waiter.kill()
                waiter.wait(timeout=5)
            if backup_receipt.exists() and not receipt_path.exists():
                backup_receipt.rename(receipt_path)

        # CASE 10: a second refresh stops the old endpoint and advances the generation.
        old_pid = endpoint_pid
        refreshed2 = manager_json(root, "trust", "endpoint-refresh", "--timeout", "30")
        generation2 = int(refreshed2["endpoint_generation"])
        endpoint_pid2 = int(refreshed2["endpoint_pid"])
        endpoint_pids.append(endpoint_pid2)
        record(
            "case10.second_refresh",
            refreshed2["authorization_state"] == "refreshed"
            and generation2 == generation + 1
            and endpoint_pid2 != old_pid
            and not pid_alive(old_pid)
            and pid_alive(endpoint_pid2)
            and not receipt_path.exists(),
            {
                "authorization_state": "refreshed",
                "endpoint_generation": generation + 1,
                "old_pid_dead": True,
                "old_receipt_removed": True,
            },
            {
                "endpoint_generation": generation2,
                "old_pid": old_pid,
                "new_pid": endpoint_pid2,
                "old_pid_alive": pid_alive(old_pid),
                "old_receipt_exists": receipt_path.exists(),
            },
        )
        receipt_path2 = runtime / "endpoint-applied" / f"{generation2}.json"
        record("case10.new_receipt", receipt_path2.is_file(), "new generation receipt", str(receipt_path2))
        preserve_evidence(evidence, receipt_path2, "endpoint-applied-2.json")
        preserve_evidence(evidence, process_record_path, "endpoint-process.json")

        # CASE 11: Host stop also stops the owned endpoint and removes its runtime.
        record("case11.stop", True, "stopped", manager_json(root, "stop")["state"])
        record(
            "case11.endpoint_stopped",
            not pid_alive(endpoint_pid2) and not process_record_path.exists(),
            "endpoint stopped and process record removed",
            {"endpoint_pid": endpoint_pid2, "alive": pid_alive(endpoint_pid2), "record": process_record_path.exists()},
        )

        # CASE 12: a failed refresh exposes not_refreshed with typed partial
        # facts, both as a module error and through the public CLI error JSON.
        try:
            host_service.refresh_endpoint(root, timeout_seconds=1.0)
        except host_service.ServiceError as error:
            facts = error.facts or {}
            record(
                "case12.module_partial_facts",
                error.code == "ENDPOINT_NOT_REFRESHED"
                and facts.get("authorization_state") == "not_refreshed"
                and facts.get("last_step") == "host_required",
                {"code": "ENDPOINT_NOT_REFRESHED", "authorization_state": "not_refreshed", "last_step": "host_required"},
                {"code": error.code, "facts": facts},
            )
        else:
            record("case12.module_partial_facts", False, "typed failure", "unexpected success")
        cli_refresh = manager(root, "trust", "endpoint-refresh", "--timeout", "1", check=False)
        try:
            cli_document = json.loads(cli_refresh.stderr.strip().splitlines()[-1])
            cli_facts = cli_document["error"].get("facts", {})
        except (json.JSONDecodeError, IndexError, KeyError, AttributeError):
            cli_facts = {}
        record(
            "case12.cli_partial_facts",
            cli_refresh.returncode != 0
            and cli_facts.get("authorization_state") == "not_refreshed"
            and cli_facts.get("last_step") == "host_required",
            {"rc_nonzero": True, "authorization_state": "not_refreshed", "last_step": "host_required"},
            {"rc": cli_refresh.returncode, "facts": cli_facts},
        )

        print(
            json.dumps(
                {
                    "generation": generation,
                    "endpoint_pid": endpoint_pid,
                    "generation2": generation2,
                    "endpoint_pid2": endpoint_pid2,
                    "bundle": str(bundle_path),
                    "bundle_endpoint": bundle["endpoint"],
                    "endpoint_receipt": verified_receipt,
                }
            ),
            flush=True,
        )
    finally:
        # Stop only our own children, by explicit PID, after preserving evidence.
        for pid in endpoint_pids:
            if pid_alive(pid):
                stop_pid(pid)
        if host_pid and pid_alive(host_pid):
            stop_pid(host_pid)
        print(json.dumps({"base": str(base), "retained_for_evidence": True}), flush=True)

    failures = [item for item in RESULTS if not item["ok"]]
    if failures:
        print(json.dumps({"failures": failures}, indent=2), flush=True)
        print("REAL_REFRESH_E2E_FAIL", flush=True)
        return 1
    print("REAL_REFRESH_E2E_PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
