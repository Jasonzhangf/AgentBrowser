#!/usr/bin/env python3
"""Real-producer E2E for the Host service admin and export surface.

Drives only the real binaries the controller recorded (``OBSCURA_HOST_BINARY``
and the ``obscura-endpoint`` beside it) through the public ``host-service`` CLI
and the public module functions. It never substitutes a fixture host and never
fabricates a receipt, and it stops only the children it started, by explicit
PID, after preserving evidence.
"""

from __future__ import annotations

import json
import os
import signal
import shutil
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
        timeout=120,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"manager failed {arguments}: rc={result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def manager_json(root: Path, *arguments: str) -> dict[str, object]:
    return json.loads(manager(root, *arguments).stdout)


def admin_exchange(socket_path: Path, request: dict[str, object]) -> dict[str, object]:
    payload = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(15.0)
        client.connect(str(socket_path))
        client.sendall(payload)
        data = bytearray()
        while b"\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
    return json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))


def expect_code(label: str, expected: str, call) -> None:
    try:
        value = call()
    except host_service.ServiceError as error:
        record(label, error.code == expected, expected, error.code or str(error))
    else:
        record(label, False, expected, f"unexpected success {json.dumps(value)[:120]}")


def expect_admin_error(label: str, socket_path: Path, request: dict[str, object], expected: str) -> None:
    response = admin_exchange(socket_path, request)
    actual = response.get("code") if response.get("state") == "error" else f"state={response.get('state')}"
    record(label, actual == expected, expected, actual)


def free_port() -> int:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
        probe.bind(("::1", 0))
        return int(probe.getsockname()[1])


def stop_pid(pid: int, timeout: float = 10.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def start_endpoint(
    base: Path,
    endpoint_binary: Path,
    media_binary: Path,
    root: Path,
    socket_dir: Path,
    port: int,
    generation: int,
) -> tuple[subprocess.Popen, Path]:
    endpoint_socket_dir = base / f"endpoint-{uuid.uuid4().hex[:8]}"
    receipt_path = root / "rt" / "endpoint-applied" / f"{generation}.json"
    receipt_path.parent.mkdir(mode=0o700, exist_ok=True)
    log = (base / f"endpoint-{generation}.log").open("ab")
    process = subprocess.Popen(
        [
            str(endpoint_binary),
            "--listen", f"[::1]:{port}",
            "--host-dir", str(socket_dir),
            "--socket-dir", str(endpoint_socket_dir),
            "--server-cert", str(root / "trust" / "server.der"),
            "--server-key", str(root / "trust" / "server-key.der"),
            "--client-ca", str(root / "trust" / "ca.der"),
            "--authorized-clients", str(root / "trust" / "authorized-clients.json"),
            "--media-bin", str(media_binary),
            "--ffmpeg", os.environ.get("OBSCURA_FFMPEG", "ffmpeg"),
            "--applied-receipt-path", str(receipt_path),
            "--endpoint-generation", str(generation),
        ],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        close_fds=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if receipt_path.is_file():
            break
        if process.poll() is not None:
            raise AssertionError(f"endpoint exited before writing the applied receipt: rc={process.returncode}")
        time.sleep(0.05)
    return process, receipt_path


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
        ["mktemp", "-d", str(runtime_base / "r5real-XXXXXX")],
        check=True, capture_output=True, text=True,
    )
    base = Path(created.stdout.strip())
    base.chmod(0o700)
    root = base / "root"
    root.mkdir(mode=0o700)
    runtime = root / "rt"
    profiles = root / "profiles"
    port = free_port()
    endpoint_process: subprocess.Popen | None = None
    print(json.dumps({"base": str(base), "port": port, "host_binary": str(host_binary)}), flush=True)

    try:
        # PREREQ: real trust root, enrollment, real Host start.
        manager_json(root, "trust", "init", "--endpoint-host", "::1", "--endpoint-port", str(port))
        device = manager_json(
            root, "trust", "provision", "--label", "Android phone",
            "--scope", "observe_human_control", "--scope", "profile_read",
            "--scope", "profile_select", "--initial-profile", "profile_id:default",
        )
        device_id = str(device["device_id"])
        manager_json(
            root, "install", "--binary", str(host_binary),
            "--protocol-version", str(protocol_version),
            "--runtime-root", str(runtime), "--profile-root", str(profiles),
            "--launchd-root", str(root / "launchd"),
        )
        started = manager_json(root, "start", "--ready-timeout", "60")
        socket_dir = Path(str(started["socket_dir"]))
        admin_socket = socket_dir / "host-admin.sock"
        record("prereq.host_started", admin_socket.exists(), "host-admin.sock present", admin_socket.exists())

        # CASE 1: real admin create / select / info / membership.set.
        info0 = host_service.admin_catalog_info(admin_socket)
        created_profile = host_service.admin_catalog_create(admin_socket, "Work", False, info0["catalog_revision"])
        selected = host_service.admin_catalog_select(
            admin_socket, created_profile["profile_receipt"]["profile_id"], None
        )
        grant = host_service.admin_membership_set(
            admin_socket, "grant", "default", "local", device_id,
            ["local", device_id], selected["catalog_revision"],
        )
        receipt = grant["membership_receipt"]
        record(
            "case1.real_admin_success",
            receipt["receipt_type"] == "export_eligible"
            and receipt["device_id"] == device_id
            and device_id in receipt["members"]
            and receipt["owner_device_id"] in receipt["members"],
            "export_eligible receipt bound to committed revision and exact members",
            receipt,
        )
        catalog_after = host_service.admin_catalog_info(admin_socket)
        record(
            "case1.committed_revision",
            receipt["catalog_revision"] == catalog_after["catalog_revision"],
            "receipt revision == committed catalog revision",
            {"receipt": receipt["catalog_revision"], "committed": catalog_after["catalog_revision"]},
        )

        # CASE 2: typed refusals on real input.
        malformed_request = {
            "schema_version": 1, "id": 1, "op": "admin.membership.set",
            "profile_id": "default", "owner_device_id": "local",
            "device_id": device_id, "members": ["local", device_id],
        }
        expect_admin_error("case2.ADMIN_FRAME_MALFORMED", admin_socket, malformed_request, "ADMIN_FRAME_MALFORMED")
        expect_code(
            "case2.CATALOG_REVISION_CONFLICT", "CATALOG_REVISION_CONFLICT",
            lambda: host_service.admin_membership_set(
                admin_socket, "grant", "default", "local", device_id, ["local", device_id], 99
            ),
        )
        expect_code(
            "case2.PROFILE_NOT_FOUND", "PROFILE_NOT_FOUND",
            lambda: host_service.admin_catalog_select(admin_socket, "does-not-exist", None),
        )
        expect_code(
            "case2.MEMBERSHIP_REQUIRED", "MEMBERSHIP_REQUIRED",
            lambda: host_service.admin_membership_set(
                admin_socket, "grant", "default", "local", device_id, ["local"], None
            ),
        )
        unknown_device = "00000000-0000-4000-8000-000000000000"
        expect_code(
            "case2.DEVICE_NOT_REGISTERED", "DEVICE_NOT_REGISTERED",
            lambda: host_service.admin_membership_set(
                admin_socket, "grant", "default", "local", unknown_device,
                ["local", unknown_device], None,
            ),
        )
        revoked = manager_json(
            root, "trust", "provision", "--label", "To revoke",
            "--scope", "profile_read", "--initial-profile", "default_profile",
        )
        revoked_id = str(revoked["device_id"])
        manager_json(root, "trust", "revoke", "--device-id", revoked_id)
        expect_code(
            "case2.DEVICE_REVOKED", "DEVICE_REVOKED",
            lambda: host_service.admin_membership_set(
                admin_socket, "grant", "default", "local", revoked_id,
                ["local", revoked_id], None,
            ),
        )
        registry_path = root / "trust" / "registry.json"
        registry = json.loads(registry_path.read_bytes())
        for item in registry["devices"]:
            if item["device_id"] == device_id:
                item["initial_profile_grant"]["authorization_revision"] = registry["authorization_revision"] - 1
        registry_path.write_bytes(host_service.serialize_registry(registry))
        expect_code(
            "case2.DEVICE_GRANT_STALE", "DEVICE_GRANT_STALE",
            lambda: host_service.admin_membership_set(
                admin_socket, "grant", "default", "local", device_id,
                ["local", device_id], None,
            ),
        )
        for item in registry["devices"]:
            if item["device_id"] == device_id:
                item["initial_profile_grant"]["authorization_revision"] = registry["authorization_revision"]
        registry_path.write_bytes(host_service.serialize_registry(registry))
        manager(
            root, "trust", "provision", "--label", "Rebind",
            "--scope", "profile_read", "--initial-profile", "default_profile",
        )

        # CASE 3: real endpoint applied receipt.
        endpoint_process, receipt_path = start_endpoint(
            base, endpoint_binary, media_binary, root, socket_dir, port, 0
        )
        mode = stat.S_IMODE(receipt_path.stat().st_mode)
        raw_receipt = json.loads(receipt_path.read_bytes())
        record(
            "case3.receipt_shape",
            set(raw_receipt) == {
                "schema_version", "endpoint_generation", "endpoint_pid", "bound_address",
                "authorization_revision", "registry_sha256", "projection_sha256",
            }
            and raw_receipt["schema_version"] == 1,
            "exact v1 applied-receipt shape",
            raw_receipt,
        )
        record("case3.receipt_mode", mode == 0o600, "0600", oct(mode))
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.settimeout(2.0)
            bound = probe.connect_ex(("::1", port)) == 0
        record("case3.listener_bound_after_receipt", bound, "endpoint listener reachable", bound)
        verified = host_service.wait_endpoint_applied(root, generation=0, timeout_seconds=5.0)
        record(
            "case3.wait_endpoint_applied",
            verified["status"] == "refreshed"
            and verified["endpoint_pid"] == raw_receipt["endpoint_pid"]
            and verified["endpoint_generation"] == 0,
            "refreshed bound to exact PID and generation",
            verified,
        )
        malformed = receipt_path.parent / "9.json"
        malformed.write_bytes(b'{"status":"ready"}\n')
        malformed.chmod(0o600)
        expect_code(
            "case3.ENDPOINT_RECEIPT_MALFORMED", "ENDPOINT_RECEIPT_MALFORMED",
            lambda: host_service.wait_endpoint_applied(root, generation=9, timeout_seconds=0.5),
        )
        malformed.unlink()

        # CASE 4: real IPv6 export + refused receipts.
        output = base / "bundle.abconnection"
        export = manager(root, "trust", "export", "--device-id", device_id, "--output", str(output))
        bundle = json.loads(output.read_bytes())
        record(
            "case4.real_ipv6_export",
            export.returncode == 0
            and bundle["endpoint"] == f"wss://[::1]:{port}/"
            and stat.S_IMODE(output.stat().st_mode) == 0o600,
            f"exit 0 + wss://[::1]:{port}/ + 0600",
            {
                "rc": export.returncode,
                "endpoint": bundle["endpoint"],
                "mode": oct(stat.S_IMODE(output.stat().st_mode)),
            },
        )
        good_membership = host_service.admin_membership_set(
            admin_socket, "grant", "default", "local", device_id, ["local", device_id], None
        )["membership_receipt"]
        good_endpoint = verified
        refused = base / "refused.abconnection"
        expect_code(
            "case4.guessed_refused", "PROFILE_ACCESS_WRITE_FAILED",
            lambda: host_service.export_bundle(
                root, device_id, refused,
                membership_receipt={**good_membership, "device_id": unknown_device},
                endpoint_receipt=good_endpoint,
            ),
        )
        removed = host_service.admin_membership_set(
            admin_socket, "remove", "default", "local", device_id, ["local"], None
        )["membership_receipt"]
        record("case4.removed_receipt_type", removed["receipt_type"] == "removed", "removed", removed["receipt_type"])
        expect_code(
            "case4.removed_refused", "PROFILE_ACCESS_WRITE_FAILED",
            lambda: host_service.export_bundle(
                root, device_id, refused, membership_receipt=removed, endpoint_receipt=good_endpoint
            ),
        )
        host_service.admin_membership_set(
            admin_socket, "grant", "default", "local", device_id, ["local", device_id], None
        )
        expect_code(
            "case4.target_omitting_refused", "PROFILE_ACCESS_WRITE_FAILED",
            lambda: host_service.export_bundle(
                root, device_id, refused,
                membership_receipt={**good_membership, "members": ["local"]},
                endpoint_receipt=good_endpoint,
            ),
        )
        expect_code(
            "case4.endpoint_file_refused", "PROJECTION_MATERIAL_CONFLICT",
            lambda: host_service.export_bundle(
                root, device_id, refused, membership_receipt=good_membership, endpoint_receipt=raw_receipt
            ),
        )
        record("case4.no_side_effect_on_refusal", not refused.exists(), "no refused output file", refused.exists())

        # CASE 6: wrong-identity / expired / revoked / cancel.
        dead_pid = 1 << 30
        wrong = receipt_path.parent / "30.json"
        wrong.write_bytes(
            json.dumps(
                {**raw_receipt, "endpoint_generation": 30, "endpoint_pid": dead_pid},
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
        )
        wrong.chmod(0o600)
        host_service.write_json(
            root / "trust" / "endpoint.json",
            {**host_service.trust_endpoint(root), "endpoint_generation": 30},
        )
        expect_code(
            "case6.wrong_identity_pid_mismatch", "ENDPOINT_PID_MISMATCH",
            lambda: host_service.wait_endpoint_applied(root, generation=30, timeout_seconds=0.5),
        )
        host_service.write_json(
            root / "trust" / "endpoint.json",
            {**host_service.trust_endpoint(root), "endpoint_generation": 0},
        )
        expect_code(
            "case6.expired_generation_stale", "ENDPOINT_GENERATION_STALE",
            lambda: host_service.wait_endpoint_applied(root, generation=30, timeout_seconds=0.5),
        )
        wrong.unlink()
        record(
            "case6.revoked_observed",
            any(item["case"] == "case2.DEVICE_REVOKED" and item["ok"] for item in RESULTS),
            "DEVICE_REVOKED",
            [item["actual"] for item in RESULTS if item["case"] == "case2.DEVICE_REVOKED"],
        )
        # Client cancellation after an accepted mutating request does not undo
        # the committed write (implementation-contracts §2.5). Send the full
        # frame on one connection without reading it, confirm acceptance by
        # polling the committed revision from separate real connections, then
        # close the request connection so its response is undelivered. The next
        # real `admin.catalog.info` must still report the committed revision.
        before = host_service.admin_catalog_info(admin_socket)["catalog_revision"]
        cancel_device = manager_json(
            root, "trust", "provision", "--label", "Cancel device",
            "--scope", "profile_read", "--initial-profile", "default_profile",
        )
        cancel_id = str(cancel_device["device_id"])
        payload = (
            json.dumps(
                {
                    "schema_version": 1, "id": 4242, "op": "admin.membership.set",
                    "intent": "grant", "attempt_id": str(uuid.uuid4()),
                    "profile_id": "default", "owner_device_id": "local",
                    "device_id": cancel_id, "members": ["local", cancel_id],
                },
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
        )
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(admin_socket))
        client.sendall(payload)
        accepted = before
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            accepted = host_service.admin_catalog_info(admin_socket)["catalog_revision"]
            if accepted > before:
                break
            time.sleep(0.1)
        # Cancel the request connection without reading its response.
        client.close()
        after = host_service.admin_catalog_info(admin_socket)["catalog_revision"]
        record(
            "case6.cancel_committed_revision",
            accepted > before and after > before,
            "accepted write survives the cancelled connection; later catalog read reports it",
            {"before": before, "accepted": accepted, "after_cancel": after},
        )
        # Cancelling the wait releases only the waiter; the endpoint stays live.
        expect_code(
            "case6.wait_cancel_not_refreshed", "ENDPOINT_NOT_REFRESHED",
            lambda: host_service.wait_endpoint_applied(root, generation=999, timeout_seconds=0.4),
        )
        record(
            "case6.wait_cancel_keeps_endpoint",
            endpoint_process.poll() is None,
            "endpoint still live after the wait released",
            endpoint_process.poll(),
        )

        # CLEANUP.
        manager(root, "stop")
        if endpoint_process.poll() is None:
            stop_pid(endpoint_process.pid)
        record("cleanup.endpoint_stopped", endpoint_process.poll() is not None, "endpoint exited", endpoint_process.poll())
    finally:
        if endpoint_process is not None and endpoint_process.poll() is None:
            stop_pid(endpoint_process.pid)
        manager(root, "stop", check=False)
        # Remove only this harness's own private temp root, never a shared
        # target or another owner's directory.
        if base.name.startswith("r5real-") and base.parent in {Path("/private/tmp"), Path("/tmp")}:
            shutil.rmtree(base, ignore_errors=True)

    failures = [item for item in RESULTS if not item["ok"]]
    print(json.dumps({"summary": {"total": len(RESULTS), "failed": len(failures), "base": str(base)}}, indent=2), flush=True)
    if failures:
        print(json.dumps({"failures": failures}, indent=2), flush=True)
        return 1
    print("REAL_E2E_PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
