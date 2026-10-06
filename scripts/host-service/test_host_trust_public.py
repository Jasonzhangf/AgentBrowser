#!/usr/bin/env python3
"""Public consumer acceptance for the Host service local trust authority.

The test drives only the public CLI (`host-service trust ...`) and the public
module functions, asserts external filesystem side effects, and verifies every
certificate and the PKCS#12 container with the real installed OpenSSL binary.
It never inspects private calls, never fabricates catalog/endpoint receipts, and
never prints private key material or the one-time transfer code.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import host_service  # noqa: E402  (public module under test)


MANAGER = SCRIPT_DIR / "host-service"
EMPTY_VECTOR = (
    b'{"schema_version":2,"authorization_revision":0,'
    b'"registry_sha256":"0000000000000000000000000000000000000000000000000000000000000000",'
    b'"clients":[]}\n'
)
EMPTY_VECTOR_SHA256 = "543c99d61e58f6051e5024a233b1fe084afecad4829f515f279908d0fccdc73a"
NONEMPTY_VECTOR = (
    b'{"schema_version":2,"authorization_revision":7,'
    b'"registry_sha256":"1111111111111111111111111111111111111111111111111111111111111111",'
    b'"clients":[{"device_id":"a1b2c3d4-0000-4000-8000-000000000001",'
    b'"client_certificate_sha256":"2222222222222222222222222222222222222222222222222222222222222222",'
    b'"scopes":["observe_human_control","profile_read","profile_select"],'
    b'"initial_profile_grant":{"selection":{"profile_id":"p3"},"authorization_revision":7}}]}\n'
)
NONEMPTY_VECTOR_SHA256 = "ab48af7fd6b7360bb8424643981b54033fee67c8cd4bf591bd2252069b7e98c5"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def find_openssl() -> str:
    candidate = Path("/opt/homebrew/bin/openssl")
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    found = shutil.which("openssl")
    if not found:
        raise AssertionError("a real openssl binary is required for trust verification")
    return found


OPENSSL = find_openssl()


def openssl(
    *arguments: str,
    check_result: bool = True,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [OPENSSL, *arguments],
        check=False,
        capture_output=True,
        input=stdin,
        text=True,
        timeout=60,
    )
    if check_result and result.returncode != 0:
        raise AssertionError(
            f"openssl failed: {arguments}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def manager(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(MANAGER), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )


def manager_json(*arguments: str) -> dict[str, object]:
    result = manager(*arguments)
    check(result.returncode == 0, f"manager failed ({result.returncode}): {arguments}\n{result.stderr}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"manager did not return JSON: {result.stdout!r}") from error
    check(isinstance(value, dict), f"manager JSON is not an object: {value!r}")
    return value


def manager_error(*arguments: str) -> str:
    result = manager(*arguments)
    check(result.returncode != 0, f"manager unexpectedly succeeded: {arguments}")
    try:
        value = json.loads(result.stderr)
    except json.JSONDecodeError as error:
        raise AssertionError(f"manager error was not typed JSON: {result.stderr!r}") from error
    return str(value["error"]["code"])


def start_manager(*arguments: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [str(MANAGER), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def admin_error_frame(request_id: object, code: str, message: str = "typed denial") -> bytes:
    # The real Host serializes a flat error object, not a nested `error` field.
    return (
        json.dumps(
            {
                "schema_version": 1,
                "id": request_id,
                "state": "error",
                "code": code,
                "message": message,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def admin_ok_frame(request_id: object, **fields: object) -> bytes:
    document = {"schema_version": 1, "id": request_id, "state": "ok", **fields}
    return (json.dumps(document, separators=(",", ":")) + "\n").encode("utf-8")


def read_request_id(request_bytes: bytes) -> object:
    return json.loads(request_bytes.split(b"\n", 1)[0].decode("utf-8")).get("id")


class AdminOwner:
    """One-shot task-owned local admin socket used for public refusal checks."""

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.received: list[bytes] = []
        self.connected = 0
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(socket_path))
        self._server.listen(1)
        self._server.settimeout(5.0)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            connection, _ = self._server.accept()
        except OSError:
            return
        self.connected += 1
        with connection:
            data = bytearray()
            while b"\n" not in data:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
            self.received.append(bytes(data))
            response = self._response(bytes(data))
            if response is not None:
                try:
                    connection.sendall(response)
                except OSError:
                    pass

    def _response(self, request_bytes: bytes) -> bytes | None:
        return None

    def join(self) -> None:
        self._thread.join(timeout=10.0)
        self._server.close()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


class CannedAdminOwner(AdminOwner):
    def __init__(self, socket_path: Path, response: bytes | None) -> None:
        self._canned = response
        super().__init__(socket_path)

    def _response(self, request_bytes: bytes) -> bytes | None:
        return self._canned


class RespondingAdminOwner(AdminOwner):
    def __init__(self, socket_path: Path, builder) -> None:
        self._builder = builder
        super().__init__(socket_path)

    def _response(self, request_bytes: bytes) -> bytes | None:
        return self._builder(read_request_id(request_bytes))


def short_socket_dir(prefix: str = "ab-adm-") -> Path:
    base = "/private/tmp" if Path("/private/tmp").is_dir() else "/tmp"
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    directory.chmod(0o700)
    return directory


def build_service_fixture(base: Path, *, endpoint_host: str = "100.64.0.1") -> tuple[Path, Path]:
    """Create a valid declared service root + runtime root for receipt checks."""
    root = base / "service-root"
    root.mkdir(mode=0o700)
    (root / "run").mkdir(mode=0o700)
    (root / "logs").mkdir(mode=0o700)
    runtime = base / "runtime"
    runtime.mkdir(mode=0o700)
    binary = Path(sys.executable).resolve()
    config = {
        "config_version": 2,
        "config_id": "r4-source-fixture",
        "service_label": "com.agentbrowser.obscura-host",
        "host_binary": str(binary),
        "binary_sha256": sha256_file(binary),
        "protocol_version": 11,
        "profile_id": "default",
        "profile_mode": "in_memory",
        "profile_root": None,
        "allow_private_network": False,
        "runtime_root": str(runtime),
    }
    host_service.write_json(root / "config.json", config)
    manager_json(
        "trust", "init", "--root", str(root),
        "--endpoint-host", endpoint_host, "--endpoint-port", "8443",
    )
    return root, runtime


def endpoint_receipt_fields(root: Path, *, generation: int = 3, pid: int | None = None) -> dict[str, object]:
    registry_bytes = (root / "trust" / "registry.json").read_bytes()
    projection_bytes = (root / "trust" / "authorized-clients.json").read_bytes()
    registry = json.loads(registry_bytes)
    return {
        "schema_version": 1,
        "endpoint_generation": generation,
        "endpoint_pid": os.getpid() if pid is None else pid,
        "bound_address": "100.64.0.1:8443",
        "authorization_revision": registry["authorization_revision"],
        "registry_sha256": hashlib.sha256(registry_bytes).hexdigest(),
        "projection_sha256": hashlib.sha256(projection_bytes).hexdigest(),
    }


def write_endpoint_receipt(runtime: Path, generation: int, fields: dict[str, object]) -> Path:
    directory = runtime / "endpoint-applied"
    if not directory.exists():
        directory.mkdir(mode=0o700)
    path = directory / f"{generation}.json"
    host_service.write_bytes(path, (json.dumps(fields, separators=(",", ":")) + "\n").encode("utf-8"), 0o600)
    return path


def set_endpoint_generation(root: Path, generation: int) -> None:
    """Move the single declared generation in the existing endpoint locator."""
    locator = host_service.trust_endpoint(root)
    locator["endpoint_generation"] = generation
    host_service.write_json(root / "trust" / "endpoint.json", locator)


def assert_export_refused(
    root: Path,
    device_id: str,
    output: Path,
    expected_code: str,
    *,
    membership_receipt: dict[str, object] | None = None,
    endpoint_receipt: dict[str, object] | None = None,
) -> None:
    trust = root / "trust"
    registry_path = trust / "registry.json"
    projection_path = trust / "authorized-clients.json"
    record_path = trust / "devices" / f"{device_id}.json"
    registry_hash_before = sha256_file(registry_path)
    projection_hash_before = sha256_file(projection_path)
    record_hash_before = sha256_file(record_path) if record_path.is_file() else None
    revision_before = json.loads(registry_path.read_bytes())["authorization_revision"]
    count_before = len(json.loads(registry_path.read_bytes())["devices"])
    try:
        host_service.export_bundle(
            root,
            device_id,
            output,
            membership_receipt=membership_receipt,
            endpoint_receipt=endpoint_receipt,
        )
        raise AssertionError(f"export_bundle unexpectedly succeeded for {expected_code}")
    except host_service.ServiceError as error:
        check(error.code == expected_code, f"expected {expected_code}, got {error.code}: {error}")
    check(not output.exists(), f"refused export left an output file: {output}")
    check(sha256_file(registry_path) == registry_hash_before, "refused export mutated the registry")
    check(sha256_file(projection_path) == projection_hash_before, "refused export mutated the projection")
    if record_path.is_file():
        check(sha256_file(record_path) == record_hash_before, "refused export mutated the device record")
    registry_now = json.loads(registry_path.read_bytes())
    check(registry_now["authorization_revision"] == revision_before, "refused export advanced the revision")
    check(len(registry_now["devices"]) == count_before, "refused export changed the device count")


MODULE_ENROLL_SOURCE = r"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[4])
import host_service
root = Path(sys.argv[1])
flag = Path(sys.argv[2])
label = sys.argv[3]
deadline = time.time() + 30
while not flag.exists() and time.time() < deadline:
    time.sleep(0.01)
if not flag.exists():
    print(json.dumps({"state": "barrier_timeout"}), flush=True)
    raise SystemExit(9)
try:
    result = host_service.enroll_device(
        root,
        label=label,
        scopes=["profile_read"],
        selection="default_profile",
    )
except host_service.ServiceError as error:
    print(json.dumps({"state": "error", "code": error.code, "message": str(error)}), flush=True)
    raise SystemExit(2)
print(json.dumps(result), flush=True)
"""


def test_public_mixed_mutation_no_lost_success(root: Path) -> None:
    """Concurrent public module enrolls and CLI revokes must not lose effects.

    Every public mutation boundary shares one lock, so each caller serializes
    on the complete registry read/mutate/commit/projection transaction. A
    successful receipt must therefore survive every other concurrent success.
    """
    concurrent_root = root / "concurrent-root"
    concurrent_root.mkdir(mode=0o700)
    manager_json(
        "trust", "init", "--root", str(concurrent_root),
        "--endpoint-host", "100.64.0.1", "--endpoint-port", "8443",
    )
    seed = manager_json(
        "trust", "provision", "--root", str(concurrent_root), "--label", "Seed",
        "--scope", "profile_read", "--initial-profile", "default_profile",
    )
    revoke_targets = [
        manager_json(
            "trust", "provision", "--root", str(concurrent_root), "--label", f"Revoke {index}",
            "--scope", "profile_read", "--initial-profile", "default_profile",
        )
        for index in range(3)
    ]
    revision_before = 4
    registry_path = concurrent_root / "trust" / "registry.json"
    check(
        json.loads(registry_path.read_bytes())["authorization_revision"] == revision_before,
        "seed provisioning did not reach the expected revision",
    )

    flag = concurrent_root / "go"
    enroll_processes = [
        subprocess.Popen(
            [
                sys.executable, "-c", MODULE_ENROLL_SOURCE,
                str(concurrent_root), str(flag), f"Enroll {index}", str(SCRIPT_DIR),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(6)
    ]
    revoke_processes = [
        start_manager(
            "trust", "revoke", "--root", str(concurrent_root),
            "--device-id", target["device_id"],
        )
        for target in revoke_targets
    ]
    flag.write_text("go")

    successful_enrolls: list[dict[str, object]] = []
    for process in enroll_processes:
        out, err = process.communicate(timeout=180)
        check(process.returncode == 0, f"module enroll failed rc={process.returncode}\n{out}\n{err}")
        receipt = json.loads(out)
        check(receipt.get("state") == "enrolled", f"module enroll state: {receipt}")
        successful_enrolls.append(receipt)

    successful_revokes: list[dict[str, object]] = []
    for process in revoke_processes:
        out, err = process.communicate(timeout=180)
        check(process.returncode == 0, f"CLI revoke failed rc={process.returncode}\n{out}\n{err}")
        receipt = json.loads(out)
        check(receipt.get("state") == "revoked", f"CLI revoke state: {receipt}")
        successful_revokes.append(receipt)

    successes = len(successful_enrolls) + len(successful_revokes)
    registry_bytes = registry_path.read_bytes()
    registry = json.loads(registry_bytes)
    devices = {item["device_id"]: item for item in registry["devices"]}
    for receipt in successful_enrolls:
        check(receipt["device_id"] in devices, "successful module enroll is not committed")
        check(devices[receipt["device_id"]]["status"] == "active", "successful enroll is not active")
    for receipt in successful_revokes:
        check(
            devices[receipt["device_id"]]["status"] == "revoked",
            "successful CLI revocation did not survive",
        )
    check(devices[seed["device_id"]]["status"] == "active", "unrelated seed device changed")
    check(
        registry["authorization_revision"] == revision_before + successes,
        f"revision {registry['authorization_revision']} != {revision_before} + {successes} successes",
    )
    for device in registry["devices"]:
        if device["status"] == "active":
            check(
                device["initial_profile_grant"]["authorization_revision"] == registry["authorization_revision"],
                f"active grant for {device['device_id']} is not current",
            )
    expected, registry_sha = host_service.refresh_projection(concurrent_root, registry, registry_bytes)
    check(
        (concurrent_root / "trust" / "authorized-clients.json").read_bytes() == expected,
        "published projection does not match the committed registry",
    )
    check(sha256_file(registry_path) == registry_sha, "registry digest drifted")
    record_files = sorted((concurrent_root / "trust" / "devices").glob("*.json"))
    check(
        len(record_files) == len(registry["devices"]),
        "device records and committed registry diverged",
    )


def test_status_owner_and_export_denials(root: Path) -> None:
    """Status has one owner; export denials are verified from the public API.

    Every receipt here is deliberately malformed, mismatched, or stale. A
    refusal must never create output, a transfer code, a usable receipt, or any
    registry/revision/key/profile side effect. Authenticated successful export
    needs the real catalog/endpoint product and stays NOT_RUN.
    """
    denial_root = root / "denial-root"
    denial_root.mkdir(mode=0o700)
    manager_json(
        "trust", "init", "--root", str(denial_root),
        "--endpoint-host", "100.64.0.1", "--endpoint-port", "8443",
    )
    device = manager_json(
        "trust", "provision", "--root", str(denial_root), "--label", "Denial device",
        "--scope", "profile_read", "--initial-profile", "profile_id:work",
    )
    device_id = device["device_id"]
    trust = denial_root / "trust"
    registry_path = trust / "registry.json"
    projection_path = trust / "authorized-clients.json"
    output = denial_root / "refused.abconnection"

    cli_status = manager_json("trust", "status", "--root", str(denial_root))
    module_status = host_service.trust_status(denial_root)
    for status in (cli_status, module_status):
        check(status["authorization_state"] == "not_refreshed", f"status claimed {status['authorization_state']}")
        check(status["endpoint_pid"] is None, "status invented an endpoint PID")
        check(status["projection_state"] == "current", f"published projection not current: {status['projection_state']}")
    check(cli_status["authorization_state"] == module_status["authorization_state"], "CLI/module status diverged")
    check(cli_status["projection_sha256"] == module_status["projection_sha256"], "CLI/module projection digest diverged")

    projection_bytes = projection_path.read_bytes()
    registry_bytes = registry_path.read_bytes()
    registry_sha = host_service.sha256_bytes(registry_bytes)
    projection_sha = host_service.sha256_bytes(projection_bytes)
    revision = json.loads(registry_bytes)["authorization_revision"]
    valid_membership = {
        "receipt_type": "export_eligible",
        "device_id": device_id,
        "catalog_revision": 1,
        "profile_id": "work",
        "owner_device_id": "local",
        "members": ["local", device_id],
    }
    valid_endpoint = {
        "status": "refreshed",
        "endpoint_pid": 424242,
        "authorization_revision": revision,
        "registry_sha256": registry_sha,
        "projection_sha256": projection_sha,
    }

    # Malformed and mismatched membership receipts are refused.
    assert_export_refused(
        denial_root, device_id, output, "PROFILE_ACCESS_WRITE_FAILED",
        membership_receipt={"not": "a receipt"},
    )
    # A committed removal, a missing target, and a missing owner are not grants.
    assert_export_refused(
        denial_root, device_id, output, "PROFILE_ACCESS_WRITE_FAILED",
        membership_receipt={**valid_membership, "receipt_type": "removed", "members": ["local"]},
    )
    assert_export_refused(
        denial_root, device_id, output, "PROFILE_ACCESS_WRITE_FAILED",
        membership_receipt={**valid_membership, "members": ["local"]},
    )
    assert_export_refused(
        denial_root, device_id, output, "PROFILE_ACCESS_WRITE_FAILED",
        membership_receipt={**valid_membership, "members": [device_id]},
    )
    assert_export_refused(
        denial_root, device_id, output, "PROFILE_ACCESS_WRITE_FAILED",
        membership_receipt={**valid_membership, "device_id": "00000000-0000-4000-8000-000000000000"},
    )
    assert_export_refused(
        denial_root, device_id, output, "PROFILE_ACCESS_WRITE_FAILED",
        membership_receipt={**valid_membership, "profile_id": "other"},
    )
    # A missing catalog receipt is an explicit dependency wait, not success.
    assert_export_refused(
        denial_root, device_id, output, "PROFILE_CATALOG_UNAVAILABLE",
        membership_receipt=None,
    )
    # A missing or mismatched endpoint applied receipt is refused.
    assert_export_refused(
        denial_root, device_id, output, "ENDPOINT_REFRESH_REQUIRED",
        membership_receipt=valid_membership,
    )
    assert_export_refused(
        denial_root, device_id, output, "PROJECTION_MATERIAL_CONFLICT",
        membership_receipt=valid_membership,
        endpoint_receipt={**valid_endpoint, "endpoint_pid": -1},
    )
    assert_export_refused(
        denial_root, device_id, output, "PROJECTION_MATERIAL_CONFLICT",
        membership_receipt=valid_membership,
        endpoint_receipt={**valid_endpoint, "authorization_revision": revision + 1},
    )
    assert_export_refused(
        denial_root, device_id, output, "PROJECTION_MATERIAL_CONFLICT",
        membership_receipt=valid_membership,
        endpoint_receipt={**valid_endpoint, "registry_sha256": "0" * 64},
    )
    assert_export_refused(
        denial_root, device_id, output, "PROJECTION_MATERIAL_CONFLICT",
        membership_receipt=valid_membership,
        endpoint_receipt={**valid_endpoint, "projection_sha256": "0" * 64},
    )

    # Corrupted published projection material is refused and left untouched.
    projection_path.write_bytes(projection_bytes[: len(projection_bytes) - 10])
    try:
        corrupt_status = manager_json("trust", "status", "--root", str(denial_root))
        check(corrupt_status["projection_state"] == "stale", f"corrupt projection state: {corrupt_status}")
        check(corrupt_status["authorization_state"] == "not_refreshed", "corrupt projection claimed refreshed")
        assert_export_refused(
            denial_root, device_id, output, "PROJECTION_MATERIAL_CONFLICT",
            membership_receipt=valid_membership,
            endpoint_receipt=valid_endpoint,
        )
    finally:
        projection_path.write_bytes(projection_bytes)

    # A registry whose active grant is no longer current is refused.
    stale_registry = json.loads(registry_bytes)
    for item in stale_registry["devices"]:
        if item["device_id"] == device_id:
            item["initial_profile_grant"]["authorization_revision"] = revision - 1
    registry_path.write_bytes(host_service.serialize_registry(stale_registry))
    try:
        assert_export_refused(
            denial_root, device_id, output, "BUNDLE_NOT_READY",
            membership_receipt=valid_membership,
            endpoint_receipt=valid_endpoint,
        )
    finally:
        registry_path.write_bytes(registry_bytes)

    # A newer registry mutation makes the captured endpoint receipt stale.
    manager_json(
        "trust", "provision", "--root", str(denial_root), "--label", "Later device",
        "--scope", "profile_read", "--initial-profile", "default_profile",
    )
    assert_export_refused(
        denial_root, device_id, output, "PROJECTION_MATERIAL_CONFLICT",
        membership_receipt=valid_membership,
        endpoint_receipt=valid_endpoint,
    )

    # The CLI export path has no receipt input and refuses at the catalog wait.
    code = manager_error(
        "trust", "export", "--root", str(denial_root),
        "--device-id", device_id, "--output", str(output),
    )
    check(code == "PROFILE_CATALOG_UNAVAILABLE", f"CLI export code was {code}")
    check(not output.exists(), "CLI export left an output file")


def test_ipv6_url_san(root: Path) -> None:
    ipv6_root = root / "ipv6-root"
    ipv6_root.mkdir(mode=0o700)
    manager_json(
        "trust", "init", "--root", str(ipv6_root),
        "--endpoint-host", "fd7a:115c:a1e0::1", "--endpoint-port", "8443",
    )
    ca_pem = ipv6_root / "ca.pem"
    server_pem = ipv6_root / "server.pem"
    der_to_pem(ipv6_root / "trust" / "ca.der", ca_pem)
    der_to_pem(ipv6_root / "trust" / "server.der", server_pem)
    openssl("verify", "-CAfile", str(ca_pem), str(server_pem))
    san = openssl("x509", "-in", str(server_pem), "-noout", "-ext", "subjectAltName").stdout
    san_values = [
        line.split("IP Address:", 1)[1].strip()
        for line in san.splitlines()
        if "IP Address:" in line
    ]
    check(
        [ipaddress.ip_address(value) for value in san_values] == [ipaddress.ip_address("fd7a:115c:a1e0::1")],
        f"IPv6 server SAN mismatch: {san!r}",
    )

    url = host_service.endpoint_ws_url("fd7a:115c:a1e0::1", "ip", 8443)
    parsed = urllib.parse.urlsplit(url)
    check(parsed.scheme == "wss", f"IPv6 URL scheme: {parsed.scheme!r}")
    check(parsed.hostname == "fd7a:115c:a1e0::1", f"IPv6 URL hostname: {parsed.hostname!r}")
    check(parsed.port == 8443, f"IPv6 URL port: {parsed.port!r}")
    check(parsed.username is None and parsed.password is None, "IPv6 URL contains userinfo")
    check(host_service.endpoint_ws_url("100.64.0.1", "ip", 8443).startswith("wss://100.64.0.1:8443/"), "IPv4 URL drifted")
    check(host_service.endpoint_ws_url("host.example", "dns", 8443).startswith("wss://host.example:8443/"), "DNS URL drifted")


def expect_service_error(call) -> str:
    try:
        call()
    except host_service.ServiceError as error:
        return error.code or ""
    raise AssertionError("expected a typed ServiceError")


def test_admin_client_public_refusals(root: Path) -> None:
    """The typed local-admin client refuses malformed/EOF/denied frames.

    The one-shot owners below are task-owned transport harnesses. They return
    deliberately invalid, denied, or absent frames only; they never produce a
    valid grant, applied receipt, or usable bundle.
    """
    sockets = short_socket_dir()
    try:
        # Missing owner socket: declared catalog-unavailable, no side effect.
        missing = sockets / "absent.sock"
        code = expect_service_error(lambda: host_service.admin_catalog_info(missing))
        check(code == "CATALOG_UNAVAILABLE", f"missing socket code was {code}")

        # Invalid intent is rejected before any connection is opened.
        guard_path = sockets / "guard.sock"
        guard = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        guard.bind(str(guard_path))
        guard.listen(1)
        guard.settimeout(0.3)
        code = expect_service_error(
            lambda: host_service.admin_membership_set(
                guard_path, "banana", "p1", "local", "device", ["local", "device"], None
            )
        )
        check(code == "ADMIN_FRAME_MALFORMED", f"invalid intent code was {code}")
        try:
            guard.accept()
            raise AssertionError("client connected despite a rejected intent")
        except socket.timeout:
            pass
        finally:
            guard.close()
            guard_path.unlink()

        # Strict envelope/frame refusals at the public client boundary.
        malformed_cases = {
            "wrong-schema": admin_ok_frame(
                1, catalog_revision=0, default_profile_id=None, profiles=[]
            ).replace(b'"schema_version":1', b'"schema_version":2'),
            "unknown-field": admin_ok_frame(
                1, catalog_revision=0, default_profile_id=None, profiles=[], extra=1
            ),
            "missing-field": admin_ok_frame(1, catalog_revision=0, default_profile_id=None),
            "not-json": b"this is not json\n",
            "trailing-bytes": admin_ok_frame(
                1, catalog_revision=0, default_profile_id=None, profiles=[]
            )
            + b"extra\n",
            "malformed-error": b'{"schema_version":1,"id":1,"state":"error","error":{"code":"X"}}\n',
            "oversized": b'{"x":"' + b"a" * 70000 + b'"}\n',
        }
        for label, response in malformed_cases.items():
            socket_path = sockets / f"{label}.sock"
            owner = CannedAdminOwner(socket_path, response)
            try:
                code = expect_service_error(lambda p=socket_path: host_service.admin_catalog_info(p))
                check(code == "ADMIN_FRAME_MALFORMED", f"{label} code was {code}")
            finally:
                owner.join()

        # A clean EOF before any response is a transport failure, not success.
        eof_path = sockets / "eof.sock"
        eof_owner = CannedAdminOwner(eof_path, None)
        try:
            code = expect_service_error(lambda: host_service.admin_catalog_info(eof_path))
            check(code == "CATALOG_UNAVAILABLE", f"clean EOF code was {code}")
        finally:
            eof_owner.join()

        # A partial response frame at EOF is a malformed frame.
        partial_path = sockets / "partial.sock"
        partial_owner = CannedAdminOwner(partial_path, b'{"schema_version":1,"id":1')
        try:
            code = expect_service_error(lambda: host_service.admin_catalog_info(partial_path))
            check(code == "ADMIN_FRAME_MALFORMED", f"partial frame code was {code}")
        finally:
            partial_owner.join()

        # A typed denial from the owner is propagated with its exact code.
        denied_path = sockets / "denied.sock"
        denied_owner = RespondingAdminOwner(
            denied_path, lambda request_id: admin_error_frame(request_id, "MEMBERSHIP_REQUIRED")
        )
        try:
            code = expect_service_error(
                lambda: host_service.admin_membership_set(
                    denied_path, "grant", "p1", "local", "device", ["local"], None
                )
            )
            check(code == "MEMBERSHIP_REQUIRED", f"typed denial code was {code}")
        finally:
            denied_owner.join()

        # A valid info response is parsed into the declared typed projection.
        info_path = sockets / "info.sock"
        info_owner = RespondingAdminOwner(
            info_path,
            lambda request_id: admin_ok_frame(
                request_id,
                catalog_revision=1,
                default_profile_id="p1",
                profiles=[
                    {
                        "profile_id": "p1",
                        "display_name": "Work",
                        "default": True,
                        "owner_device_id": "local",
                        "members": ["local", "device"],
                    }
                ],
            ),
        )
        try:
            info = host_service.admin_catalog_info(info_path)
            check(info["catalog_revision"] == 1, f"info revision: {info}")
            check(info["default_profile_id"] == "p1", f"info default: {info}")
            check(info["profiles"][0]["members"] == ["local", "device"], f"info members: {info}")
        finally:
            info_owner.join()

        # A valid export_eligible grant frame is parsed and returned verbatim.
        grant_path = sockets / "grant.sock"
        grant_owner = RespondingAdminOwner(
            grant_path,
            lambda request_id: admin_ok_frame(
                request_id,
                catalog_revision=5,
                membership_receipt={
                    "receipt_type": "export_eligible",
                    "catalog_revision": 5,
                    "profile_id": "p1",
                    "device_id": "device",
                    "owner_device_id": "local",
                    "members": ["local", "device"],
                },
            ),
        )
        try:
            grant = host_service.admin_membership_set(
                grant_path, "grant", "p1", "local", "device", ["local", "device"], 4
            )
            check(grant["membership_receipt"]["receipt_type"] == "export_eligible", f"grant: {grant}")
            check(grant["membership_receipt"]["members"] == ["local", "device"], f"grant members: {grant}")
        finally:
            grant_owner.join()

        # A grant frame that omits a required receipt field is malformed.
        bad_grant_path = sockets / "bad-grant.sock"
        bad_grant_owner = RespondingAdminOwner(
            bad_grant_path,
            lambda request_id: admin_ok_frame(
                request_id,
                catalog_revision=5,
                membership_receipt={
                    "receipt_type": "export_eligible",
                    "catalog_revision": 5,
                    "profile_id": "p1",
                    "device_id": "device",
                    "members": ["device"],
                },
            ),
        )
        try:
            code = expect_service_error(
                lambda: host_service.admin_membership_set(
                    bad_grant_path, "grant", "p1", "local", "device", ["local", "device"], 4
                )
            )
            check(code == "ADMIN_FRAME_MALFORMED", f"owner-omitting grant code was {code}")
        finally:
            bad_grant_owner.join()
    finally:
        shutil.rmtree(sockets, ignore_errors=True)


def test_wait_endpoint_applied_public(root: Path) -> None:
    """The service verifies the exact v1 endpoint receipt and refuses the rest."""
    base = root / "endpoint-consumer"
    base.mkdir(mode=0o700)
    service_root, runtime = build_service_fixture(base)
    # `trust init` seeds the service-owned applied-receipt generation in the
    # existing endpoint locator. There is no second endpoint store, and the
    # consumer's generation comes only from here (design §3.1).
    check(
        host_service.trust_endpoint(service_root).get("endpoint_generation") == 0,
        "trust init did not seed the endpoint generation",
    )
    registry_path = service_root / "trust" / "registry.json"
    projection_path = service_root / "trust" / "authorized-clients.json"
    registry_before = sha256_file(registry_path)
    projection_before = sha256_file(projection_path)

    # The wired public consumer reads that declared generation and accepts a
    # matching real receipt; this is the path `command_trust_export` drives.
    write_endpoint_receipt(runtime, 0, endpoint_receipt_fields(service_root, generation=0))
    wired = host_service._export_endpoint_receipt(service_root)
    check(wired["status"] == "refreshed", f"wired export receipt: {wired}")
    check(wired["endpoint_generation"] == 0, f"wired export generation: {wired}")

    set_endpoint_generation(service_root, 3)
    fields = endpoint_receipt_fields(service_root)
    write_endpoint_receipt(runtime, 3, fields)
    refreshed = host_service.wait_endpoint_applied(service_root, generation=3, timeout_seconds=1.0)
    check(refreshed["status"] == "refreshed", f"refresh status: {refreshed}")
    check(refreshed["endpoint_pid"] == os.getpid(), f"refresh pid: {refreshed}")
    check(refreshed["endpoint_generation"] == 3, f"refresh generation: {refreshed}")
    check(refreshed["bound_address"] == "100.64.0.1:8443", f"refresh address: {refreshed}")
    check(refreshed["authorization_revision"] == fields["authorization_revision"], "refresh revision drifted")
    check(refreshed["registry_sha256"] == fields["registry_sha256"], "refresh registry digest drifted")
    check(refreshed["projection_sha256"] == fields["projection_sha256"], "refresh projection digest drifted")

    def refused(generation: int, expected: str) -> None:
        set_endpoint_generation(service_root, generation)
        code = expect_service_error(
            lambda: host_service.wait_endpoint_applied(service_root, generation=generation, timeout_seconds=0.3)
        )
        check(code == expected, f"generation {generation} code was {code}, expected {expected}")

    # The v1 applied receipt has no status field and no unknown fields.
    write_endpoint_receipt(runtime, 4, {**endpoint_receipt_fields(service_root, generation=4), "status": "ready"})
    refused(4, "ENDPOINT_RECEIPT_MALFORMED")
    missing = endpoint_receipt_fields(service_root, generation=5)
    del missing["schema_version"]
    write_endpoint_receipt(runtime, 5, missing)
    refused(5, "ENDPOINT_RECEIPT_MALFORMED")
    # The endpoint's legacy `ready()` initializer: `status: "ready"` and no
    # `schema_version` at all. It is never the applied receipt.
    ready_initializer = {
        "status": "ready",
        "endpoint_generation": 21,
        "endpoint_pid": os.getpid(),
        "bound_address": "100.64.0.1:8443",
        "authorization_revision": 0,
        "registry_sha256": "0" * 64,
        "projection_sha256": "0" * 64,
    }
    write_endpoint_receipt(runtime, 21, ready_initializer)
    refused(21, "ENDPOINT_RECEIPT_MALFORMED")
    write_endpoint_receipt(runtime, 6, {**endpoint_receipt_fields(service_root, generation=6), "schema_version": 2})
    refused(6, "ENDPOINT_RECEIPT_MALFORMED")
    write_endpoint_receipt(runtime, 7, {**endpoint_receipt_fields(service_root, generation=7), "extra": 1})
    refused(7, "ENDPOINT_RECEIPT_MALFORMED")

    # A generation mismatch, a dead PID, and a wrong bind address are explicit.
    write_endpoint_receipt(runtime, 8, endpoint_receipt_fields(service_root, generation=9))
    refused(8, "ENDPOINT_GENERATION_STALE")
    write_endpoint_receipt(runtime, 10, endpoint_receipt_fields(service_root, generation=10, pid=1 << 30))
    refused(10, "ENDPOINT_PID_MISMATCH")
    write_endpoint_receipt(
        runtime, 11, {**endpoint_receipt_fields(service_root, generation=11), "bound_address": "100.64.0.9:8443"}
    )
    refused(11, "ENDPOINT_BIND_MISMATCH")

    # A locator that declares a different generation than the request is stale,
    # even when the receipt bytes match the request (single-generation truth).
    write_endpoint_receipt(runtime, 20, endpoint_receipt_fields(service_root, generation=20))
    set_endpoint_generation(service_root, 0)
    refused_without_sync = expect_service_error(
        lambda: host_service.wait_endpoint_applied(service_root, generation=20, timeout_seconds=0.3)
    )
    check(refused_without_sync == "ENDPOINT_GENERATION_STALE", f"locator mismatch code: {refused_without_sync}")
    set_endpoint_generation(service_root, 20)

    # Revision or material drift is a projection conflict, never a refresh.
    revision = int(endpoint_receipt_fields(service_root)["authorization_revision"])
    write_endpoint_receipt(
        runtime, 12, {**endpoint_receipt_fields(service_root, generation=12), "authorization_revision": revision + 1}
    )
    refused(12, "PROJECTION_MATERIAL_CONFLICT")
    write_endpoint_receipt(
        runtime, 13, {**endpoint_receipt_fields(service_root, generation=13), "registry_sha256": "0" * 64}
    )
    refused(13, "PROJECTION_MATERIAL_CONFLICT")
    write_endpoint_receipt(
        runtime, 14, {**endpoint_receipt_fields(service_root, generation=14), "projection_sha256": "0" * 64}
    )
    refused(14, "PROJECTION_MATERIAL_CONFLICT")

    # A stale published projection refuses even with matching receipt digests.
    projection_bytes = projection_path.read_bytes()
    projection_path.write_bytes(projection_bytes[: len(projection_bytes) - 10])
    try:
        refused(3, "PROJECTION_MATERIAL_CONFLICT")
    finally:
        projection_path.write_bytes(projection_bytes)

    # A missing generation at timeout is an explicit not-refreshed result.
    refused(999, "ENDPOINT_NOT_REFRESHED")

    check(sha256_file(registry_path) == registry_before, "endpoint wait mutated the registry")
    check(sha256_file(projection_path) == projection_before, "endpoint wait mutated the projection")


def der_to_pem(der_path: Path, pem_path: Path) -> None:
    openssl("x509", "-inform", "DER", "-in", str(der_path), "-out", str(pem_path))


def test_endpoint_bind_basis_public(root: Path) -> None:
    """The public DNS locator maps to one numeric bind basis for start+verify."""
    dns_root = root / "bind-basis"
    dns_root.mkdir(mode=0o700)
    manager_json(
        "trust", "init", "--root", str(dns_root),
        "--endpoint-host", "localhost", "--endpoint-port", "8443",
    )
    locator = host_service.trust_endpoint(dns_root)
    check(locator["endpoint_host"] == "localhost", "DNS locator host drifted")
    check(locator["endpoint_host_kind"] == "dns", "DNS locator kind drifted")
    numeric_host, numeric_port = host_service.endpoint_bind_address(locator)
    check(ipaddress.ip_address(numeric_host), "bind basis is not numeric")
    check(numeric_port == 8443, f"bind basis port drifted: {numeric_port}")
    listen = host_service.endpoint_listen_address(locator)
    check("localhost" not in listen, f"raw DNS leaked into --listen: {listen}")
    # The applied-receipt verifier compares against the same numeric basis.
    check(
        host_service._bound_address_matches(f"[{numeric_host}]:8443", locator)
        if ":" in numeric_host
        else host_service._bound_address_matches(f"{numeric_host}:8443", locator),
        "bound-address verifier did not use the shared bind basis",
    )
    check(
        not host_service._bound_address_matches("localhost:8443", locator),
        "bound-address verifier accepted the public DNS spelling as a bind",
    )
    # The public WSS URL and SAN semantics keep the DNS locator.
    url = host_service.endpoint_ws_url(
        str(locator["endpoint_host"]), str(locator["endpoint_host_kind"]), int(locator["endpoint_port"])
    )
    check(url == "wss://localhost:8443/", f"DNS URL drifted: {url}")


def test_refresh_missing_binary_typed_failure(root: Path) -> None:
    """A refresh that fails after a side effect returns typed partial facts.

    A private Host copy with no sibling ``obscura-endpoint`` binary drives the
    real public ``refresh_endpoint`` entry: the generation advances, the old
    receipt is cleared, and the missing binary must surface as
    ``ENDPOINT_NOT_REFRESHED`` with ``authorization_state: not_refreshed``,
    ``last_step: start_endpoint`` and the advanced generation.
    """
    base = root / "missing-binary"
    base.mkdir(mode=0o700)
    service_root, _runtime = build_service_fixture(base, endpoint_host="127.0.0.1")
    private_bin = base / "bin"
    private_bin.mkdir(mode=0o700)
    private_host = (private_bin / "obscura-host").resolve()
    shutil.copyfile(Path(sys.executable).resolve(), private_host)
    private_host.chmod(0o755)
    config = host_service.load_json(service_root / "config.json", "service config")
    config["host_binary"] = str(private_host)
    config["binary_sha256"] = sha256_file(private_host)
    # Keep the runtime root on a short path so the real Host session socket
    # stays inside the Unix-socket bound.
    short_runtime = Path(tempfile.mkdtemp(prefix="r2mb-", dir="/private/tmp"))
    short_runtime.chmod(0o700)
    config["runtime_root"] = str(short_runtime)
    host_service.write_json(service_root / "config.json", config)
    # A running Host socket directory is required to pass the precondition.
    runtime_root = host_service.runtime_root_from(str(config["runtime_root"]))
    socket_dir = host_service.make_socket_dir(runtime_root)
    socket_dir.mkdir(mode=0o700)
    started_at = int(time.time())
    state = host_service.state_for(
        config, "0" * 64, os.getpid(), socket_dir, "subprocess", "fixture", None
    )
    state["started_at_unix"] = started_at
    host_service.write_json(service_root / "run" / "state.json", state)
    try:
        host_service.refresh_endpoint(service_root, timeout_seconds=1.0)
    except host_service.ServiceError as error:
        check(error.code == "ENDPOINT_NOT_REFRESHED", f"missing-binary code was {error.code}")
        facts = error.facts or {}
        check(facts.get("authorization_state") == "not_refreshed", f"missing-binary facts: {facts}")
        check(facts.get("last_step") == "start_endpoint", f"missing-binary step: {facts}")
        check(facts.get("endpoint_generation") == 1, f"missing-binary generation: {facts}")
    else:
        raise AssertionError("refresh with no sibling endpoint binary must refuse")
    check(
        host_service.trust_endpoint(service_root)["endpoint_generation"] == 1,
        "missing-binary refresh did not record the advanced generation",
    )


def test_refresh_sequence_public(root: Path) -> None:
    """The declared service entry owns the sequence; failures carry typed facts.

    This is source-only: it proves the declared public entry exists, reads its
    generation only from the existing locator, and exposes ``not_refreshed``
    with typed partial facts when the sequence cannot run. The real
    nine-step sequence against the real endpoint/Host binaries is proven by the
    run harness `docs/runs/m1-service-endpoint-refresh-r1-20261005/`.
    """
    base = root / "refresh-sequence"
    base.mkdir(mode=0o700)
    service_root, _runtime = build_service_fixture(base)
    # `trust init` seeds the single service-owned generation stamp.
    check(
        host_service.trust_endpoint(service_root).get("endpoint_generation") == 0,
        "trust init did not seed the refresh generation",
    )
    # Without a running Host the declared entry refuses explicitly, and the
    # refusal carries the typed partial facts the design requires.
    try:
        host_service.refresh_endpoint(service_root, timeout_seconds=1.0)
    except host_service.ServiceError as error:
        check(error.code == "ENDPOINT_NOT_REFRESHED", f"refresh refusal code was {error.code}")
        facts = error.facts or {}
        check(facts.get("authorization_state") == "not_refreshed", f"refresh facts state: {facts}")
        check(facts.get("last_step") == "host_required", f"refresh facts step: {facts}")
        check(facts.get("error") == "ENDPOINT_NOT_REFRESHED", f"refresh facts error: {facts}")
        check(facts.get("endpoint_pid") is None, f"refresh facts invented a PID: {facts}")
    else:
        raise AssertionError("refresh without a running Host must refuse")
    # CLI/machine emission surfaces the same typed partial facts on stderr.
    result = subprocess.run(
        [str(MANAGER), "trust", "endpoint-refresh", "--root", str(service_root), "--timeout", "1"],
        check=False,
        capture_output=True,
        text=True,
    )
    check(result.returncode != 0, "CLI refresh without a running Host returned success")
    document = json.loads(result.stderr.strip().splitlines()[-1])
    check(document["error"]["code"] == "ENDPOINT_NOT_REFRESHED", f"CLI refresh error: {document}")
    check(
        document["error"].get("facts", {}).get("last_step") == "host_required",
        f"CLI refresh facts: {document}",
    )


def check_vectors() -> None:
    empty = host_service.projection_content("0" * 64, 0, [])
    check(empty == EMPTY_VECTOR, "declared empty projection vector bytes drifted")
    check(len(empty) == 146, f"empty projection vector length is {len(empty)}")
    check(hashlib.sha256(empty).hexdigest() == EMPTY_VECTOR_SHA256, "empty vector digest drifted")
    client = {
        "device_id": "a1b2c3d4-0000-4000-8000-000000000001",
        "client_certificate_sha256": "2" * 64,
        "scopes": ["observe_human_control", "profile_read", "profile_select"],
        "initial_profile_grant": {"selection": {"profile_id": "p3"}, "authorization_revision": 7},
    }
    nonempty = host_service.projection_content("1" * 64, 7, [client])
    check(nonempty == NONEMPTY_VECTOR, "declared nonempty projection vector bytes drifted")
    check(len(nonempty) == 445, f"nonempty projection vector length is {len(nonempty)}")
    check(
        hashlib.sha256(nonempty).hexdigest() == NONEMPTY_VECTOR_SHA256,
        "nonempty vector digest drifted",
    )
    check(b"projection_sha256" not in empty, "empty projection embeds a self-hash field")
    check(b"projection_sha256" not in nonempty, "nonempty projection embeds a self-hash field")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ab-host-trust-test-"))
    root.chmod(0o700)
    try:
        run(root)
    finally:
        shutil.rmtree(root, ignore_errors=False)
    print("HOST_TRUST_PUBLIC_CONSUMER_OK")
    return 0


def run(root: Path) -> None:
    check_vectors()

    trust = root / "trust"
    registry_path = trust / "registry.json"
    projection_path = trust / "authorized-clients.json"

    # 1. Real private trust root initialize once.
    initialized = manager_json(
        "trust", "init", "--root", str(root),
        "--endpoint-host", "100.64.0.1", "--endpoint-port", "8443",
    )
    check(initialized["state"] == "initialized", f"unexpected init state: {initialized}")
    check(initialized["authorization_revision"] == 0, "install must create revision 0")
    ca_der = trust / "ca.der"
    server_der = trust / "server.der"
    for required in ("ca.der", "ca-key.der", "server.der", "server-key.der", "registry.json", "authorized-clients.json"):
        check((trust / required).is_file(), f"trust init did not create {required}")
    check(private_mode(trust) == 0o700, "trust directory is not 0700")
    check(private_mode(trust / "ca-key.der") == 0o600, "CA key is not 0600")
    check(private_mode(trust / "server-key.der") == 0o600, "server key is not 0600")
    check(private_mode(registry_path) == 0o600, "registry is not 0600")
    check(private_mode(projection_path) == 0o600, "projection is not 0600")
    check(private_mode(ca_der) == 0o644, "CA cert is not 0644")
    check(private_mode(server_der) == 0o644, "server cert is not 0644")

    # 2. Real crypto verification of CA and server identity.
    ca_pem = root / "ca.pem"
    server_pem = root / "server.pem"
    der_to_pem(ca_der, ca_pem)
    der_to_pem(server_der, server_pem)
    ca_text = openssl("x509", "-in", str(ca_pem), "-noout", "-text").stdout
    check("CA:TRUE" in ca_text, "CA certificate is not a CA")
    openssl("verify", "-CAfile", str(ca_pem), str(server_pem))
    san = openssl("x509", "-in", str(server_pem), "-noout", "-ext", "subjectAltName").stdout
    check("IP Address:100.64.0.1" in san, f"server SAN mismatch: {san!r}")
    eku = openssl("x509", "-in", str(server_pem), "-noout", "-ext", "extendedKeyUsage").stdout
    check("TLS Web Server Authentication" in eku, f"server EKU mismatch: {eku!r}")

    # 3. Reinitialize is refused and preserves the identity/material.
    ca_before = ca_der.read_bytes()
    server_before = server_der.read_bytes()
    code = manager_error(
        "trust", "init", "--root", str(root),
        "--endpoint-host", "100.64.0.1", "--endpoint-port", "8443",
    )
    check(code == "TRUST_ALREADY_INITIALIZED", f"reinit code was {code}")
    check(ca_der.read_bytes() == ca_before, "reinit mutated the CA identity")
    check(server_der.read_bytes() == server_before, "reinit mutated the server identity")

    # 4. Empty deny-all projection: exact bytes, external digest, no self-hash.
    projection_bytes = projection_path.read_bytes()
    check(b"projection_sha256" not in projection_bytes, "projection embeds a self-hash field")
    check(
        sha256_file(projection_path) == initialized["projection_sha256"],
        "install projection digest does not match the published bytes",
    )
    check(
        sha256_file(registry_path) == initialized["registry_sha256"],
        "install registry digest does not match the committed bytes",
    )
    projection = json.loads(projection_bytes)
    check(projection["clients"] == [], "empty install projection is not deny-all")
    check(projection["registry_sha256"] == sha256_file(registry_path), "projection registry digest mismatch")

    # 5. Enroll A then B: A's scopes/selection/cert/scope_revision are preserved
    #    while the grant freshness is rebound to the current global revision.
    device_a = manager_json(
        "trust", "provision", "--root", str(root), "--label", "Android phone",
        "--scope", "observe_human_control", "--scope", "profile_read", "--scope", "profile_select",
        "--initial-profile", "profile_id:work",
    )
    check(device_a["state"] == "enrolled", f"A enroll state: {device_a}")
    check(device_a["authorization_revision"] == 1, "A enroll revision must be 1")
    device_b = manager_json(
        "trust", "provision", "--root", str(root), "--label", "Second Mac",
        "--scope", "profile_read", "--initial-profile", "default_profile",
    )
    check(device_b["authorization_revision"] == 2, "B enroll revision must be 2")
    registry = json.loads(registry_path.read_bytes())
    a = next(item for item in registry["devices"] if item["device_id"] == device_a["device_id"])
    b = next(item for item in registry["devices"] if item["device_id"] == device_b["device_id"])
    check(a["scopes"] == ["observe_human_control", "profile_read", "profile_select"], "A scopes changed")
    check(a["initial_profile_grant"]["selection"] == {"profile_id": "work"}, "A selection changed")
    check(a["client_certificate_sha256"] == device_a["client_certificate_sha256"], "A cert changed")
    check(a["scope_revision"] == 1, "A scope_revision must not advance on rebind")
    check(a["initial_profile_grant"]["authorization_revision"] == 2, "A grant was not rebound")
    check(b["initial_profile_grant"]["authorization_revision"] == 2, "B grant revision mismatch")
    check(b["scope_revision"] == 2, "B scope_revision must equal its enroll revision")

    # 6. Real DER verification of the enrolled client identity.
    record = json.loads((trust / "devices" / f"{device_a['device_id']}.json").read_bytes())
    client_der = root / "client.der"
    client_der.write_bytes(base64.b64decode(record["client_certificate_der_base64"]))
    check(sha256_file(client_der) == a["client_certificate_sha256"], "client DER digest mismatch")
    client_pem = root / "client.pem"
    der_to_pem(client_der, client_pem)
    openssl("verify", "-CAfile", str(ca_pem), str(client_pem))
    client_text = openssl("x509", "-in", str(client_pem), "-noout", "-text").stdout
    check("CA:FALSE" in client_text, "client certificate is not an end-entity cert")
    client_eku = openssl("x509", "-in", str(client_pem), "-noout", "-ext", "extendedKeyUsage").stdout
    check("TLS Web Client Authentication" in client_eku, f"client EKU mismatch: {client_eku!r}")

    # 7. Revoke A: B stays active at the current revision and A leaves the projection.
    revoked = manager_json("trust", "revoke", "--root", str(root), "--device-id", device_a["device_id"])
    check(revoked["authorization_revision"] == 3, "revoke revision must be 3")
    registry = json.loads(registry_path.read_bytes())
    a = next(item for item in registry["devices"] if item["device_id"] == device_a["device_id"])
    b = next(item for item in registry["devices"] if item["device_id"] == device_b["device_id"])
    check(a["status"] == "revoked", "A was not revoked")
    check(b["status"] == "active", "B must stay active after A revoke")
    check(b["scope_revision"] == 2, "B scope_revision changed on A revoke")
    check(b["initial_profile_grant"]["authorization_revision"] == 3, "B grant was not rebound on A revoke")
    projection = json.loads(projection_path.read_bytes())
    check(
        [client["device_id"] for client in projection["clients"]] == [device_b["device_id"]],
        "revoked A must be excluded from the projection",
    )
    check(
        projection["authorization_revision"] == 3
        and projection["registry_sha256"] == sha256_file(registry_path),
        "projection does not bind the committed registry revision/material",
    )
    check(
        sha256_file(projection_path) == revoked["projection_sha256"],
        "external projection digest does not match the published bytes",
    )

    # 8. Invalid scope or selection never grants a device.
    revision_before = json.loads(registry_path.read_bytes())["authorization_revision"]
    count_before = len(json.loads(registry_path.read_bytes())["devices"])
    code = manager_error(
        "trust", "provision", "--root", str(root), "--label", "Bad",
        "--scope", "not_a_scope", "--initial-profile", "default_profile",
    )
    check(code == "REGISTRY_SCOPE_REQUIRED", f"invalid scope code was {code}")
    code = manager_error(
        "trust", "provision", "--root", str(root), "--label", "Bad",
        "--scope", "profile_read", "--initial-profile", "not_a_selection",
    )
    check(code == "MISSING_INITIAL_PROFILE_GRANT", f"invalid selection code was {code}")
    registry = json.loads(registry_path.read_bytes())
    check(registry["authorization_revision"] == revision_before, "invalid enroll advanced the revision")
    check(len(registry["devices"]) == count_before, "invalid enroll created a device")

    # 9. An explicit empty scope set is a legal enrolled identity.
    scopeless_root = root / "scopeless-root"
    scopeless_root.mkdir(mode=0o700)
    manager_json(
        "trust", "init", "--root", str(scopeless_root),
        "--endpoint-host", "100.64.0.1", "--endpoint-port", "8443",
    )
    scopeless = host_service.enroll_device(
        scopeless_root,
        label="Scopeless device",
        scopes=[],
        selection="default_profile",
    )
    check(scopeless["state"] == "enrolled", f"empty scope enroll failed: {scopeless}")
    check(scopeless["scopes"] == [], "empty scope set was not preserved")

    # 10. A real own-filesystem projection write failure exposes committed/partial truth.
    projection_path.unlink()
    projection_path.mkdir(mode=0o700)
    result = manager(
        "trust", "provision", "--root", str(root), "--label", "Third device",
        "--scope", "profile_read", "--initial-profile", "default_profile",
    )
    check(result.returncode == 4, f"partial enroll exit was {result.returncode}\n{result.stderr}")
    partial = json.loads(result.stdout)
    check(partial["state"] == "partial", f"partial receipt missing: {partial}")
    check(partial["error"] == "PARTIAL_REGISTRY_COMMIT", f"partial error was {partial['error']}")
    check(partial["authorization_revision"] == 4, "partial receipt did not expose the committed revision")
    registry = json.loads(registry_path.read_bytes())
    check(registry["authorization_revision"] == 4, "registry did not commit revision 4")
    check(any(device["device_id"] == partial["device_id"] for device in registry["devices"]), "committed device missing")
    projection_path.rmdir()
    status = manager_json("trust", "status", "--root", str(root))
    check(status["authorization_state"] == "not_refreshed", "missing projection must report not_refreshed")

    # 11. Premature export refuses and leaves no usable output.
    output = root / "bundle.abconnection"
    code = manager_error(
        "trust", "export", "--root", str(root),
        "--device-id", device_b["device_id"], "--output", str(output),
    )
    check(code == "PROFILE_CATALOG_UNAVAILABLE", f"premature export code was {code}")
    check(not output.exists(), "premature export left an output file")

    # 12. Real PKCS#12 container with exactly the three allowed aliases.
    transfer_code = host_service.generate_transfer_code()
    check(len(transfer_code) == 16, "transfer code length drifted")
    check(
        all(character not in "0O1IL" for character in transfer_code),
        "transfer code contains an ambiguous character",
    )
    p12 = host_service.build_device_transfer_p12(root, device_b["device_id"], transfer_code)
    p12_path = root / "device.p12"
    p12_path.write_bytes(p12)
    info = openssl(
        "pkcs12", "-in", str(p12_path), "-passin", "stdin", "-info", "-nodes",
        stdin=f"{transfer_code}\n",
    )
    aliases = sorted(
        line.split(":", 1)[1].strip()
        for line in (info.stdout + info.stderr).splitlines()
        if "friendlyName" in line
    )
    check(
        set(aliases) == {"agentbrowser-host-ca", "agentbrowser-endpoint", "agentbrowser-client"},
        f"PKCS#12 aliases are {aliases}",
    )
    extracted_cert = root / "extracted-client.pem"
    extracted_key = root / "extracted-key.pem"
    openssl(
        "pkcs12", "-in", str(p12_path), "-passin", "stdin",
        "-clcerts", "-nokeys", "-out", str(extracted_cert),
        stdin=f"{transfer_code}\n",
    )
    openssl(
        "pkcs12", "-in", str(p12_path), "-passin", "stdin",
        "-nocerts", "-nodes", "-out", str(extracted_key),
        stdin=f"{transfer_code}\n",
    )
    openssl("verify", "-CAfile", str(ca_pem), str(extracted_cert))
    cert_public = openssl("x509", "-in", str(extracted_cert), "-noout", "-pubkey").stdout
    key_public = openssl("pkey", "-in", str(extracted_key), "-pubout").stdout
    check(cert_public == key_public, "client certificate does not match the enclosed private key")
    extracted_der = root / "extracted-client.der"
    openssl("x509", "-in", str(extracted_cert), "-outform", "DER", "-out", str(extracted_der))
    device_b_record = json.loads((trust / "devices" / f"{device_b['device_id']}.json").read_bytes())
    device_b_der = base64.b64decode(device_b_record["client_certificate_der_base64"])
    check(extracted_der.read_bytes() == device_b_der, "exported client cert is not the enrolled B cert")

    # 13. r3 review repairs: public mutation lock, one status owner, IPv6 URL,
    #     and verified public export denials.
    test_public_mixed_mutation_no_lost_success(root)
    test_status_owner_and_export_denials(root)
    test_ipv6_url_san(root)
    # 14. r4 source-preparation: typed local-admin client, strict v1 applied
    #     receipt consumer, and the removed/omitting membership export gate.
    test_admin_client_public_refusals(root)
    test_wait_endpoint_applied_public(root)
    # 15. The declared service entry owns the nine-step refresh sequence and
    #     exposes typed not-refreshed partial facts on failure.
    test_refresh_sequence_public(root)
    # 16. r2 review repairs: the DNS locator resolves to one shared numeric
    #     bind basis, and a post-side-effect failure stays typed.
    test_endpoint_bind_basis_public(root)
    test_refresh_missing_binary_typed_failure(root)

    # No private material may appear in the emitted receipts.
    for receipt in (initialized, device_a, device_b, revoked, status, partial):
        text = json.dumps(receipt)
        check("PRIVATE KEY" not in text, "receipt leaked private key material")


if __name__ == "__main__":
    raise SystemExit(main())
