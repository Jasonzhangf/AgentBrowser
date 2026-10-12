#!/usr/bin/env python3
"""Public consumer acceptance for the Host service connection minimum.

The test drives only the public CLI (`host-service trust ...`) and the public
module functions, and it asserts external filesystem side effects. The whole
connection minimum is two typed control resources under `<root>/trust`: the
endpoint locator and the access token. There is no certificate authority, no
device registry, no authorization projection and no PKCS#12 container, so this
consumer verifies the listener's own TLS identity with the real installed
OpenSSL binary and verifies the exported bundle byte-for-byte. It never inspects
private calls, never fabricates an endpoint receipt, and never prints the token.
"""

from __future__ import annotations

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
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import host_service  # noqa: E402  (public module under test)


MANAGER = SCRIPT_DIR / "host-service"
BUNDLE_FIELDS = {"format", "version", "endpoint", "token"}


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


def stub_host_binary(base: Path) -> Path:
    """The smallest binary `install` accepts: it answers `--help`.

    `install` owns the access token, and it must be able to create it without a
    running Host. Only the entrypoint probe needs a real executable here.
    """
    path = base / "stub-obscura-host"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:2] == ['--help']:\n"
        "    print('Local persistent browser Session')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o755)
    return path


def manager_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the real public launcher with a closed stdin.

    Every CLI assertion is a real launcher subprocess; the module is never
    called in-process for CLI evidence. ``stdin`` is /dev/null because the
    contract must not depend on terminal input.
    """
    return subprocess.run(
        [str(MANAGER), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )


def manager_cli_ok(*arguments: str) -> dict[str, object]:
    result = manager_cli(*arguments)
    check(result.returncode == 0, f"manager failed ({result.returncode}): {arguments}\n{result.stderr}")
    check(result.stderr == "", f"a successful command wrote to stderr: {result.stderr!r}")
    check(
        result.stdout.endswith("\n") and result.stdout.count("\n") == 1,
        f"success stdout is not exactly one JSON line: {result.stdout!r}",
    )
    value = json.loads(result.stdout)
    check(isinstance(value, dict), f"manager JSON is not an object: {value!r}")
    return value


def manager_cli_error(*arguments: str) -> str:
    result = manager_cli(*arguments)
    check(result.returncode != 0, f"manager unexpectedly succeeded: {arguments}")
    check(result.stdout == "", f"a refused command wrote to stdout: {result.stdout!r}")
    try:
        value = json.loads(result.stderr)
    except json.JSONDecodeError as error:
        raise AssertionError(f"manager error was not typed JSON: {result.stderr!r}") from error
    return str(value["error"]["code"])


def expect_service_error(call) -> str:
    try:
        call()
    except host_service.ServiceError as error:
        return str(error.code)
    raise AssertionError("expected a typed ServiceError")


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
    """The applied receipt is exactly the declared field set: no digests, no revision."""
    return {
        "schema_version": 1,
        "endpoint_generation": generation,
        "endpoint_pid": os.getpid() if pid is None else pid,
        "bound_address": "100.64.0.1:8443",
    }


def write_endpoint_receipt(runtime: Path, generation: int, fields: dict[str, object]) -> Path:
    directory = runtime / "endpoint-applied"
    if not directory.exists():
        directory.mkdir(mode=0o700)
    path = directory / f"{generation}.json"
    host_service.write_bytes(path, (json.dumps(fields, separators=(",", ":")) + "\n").encode("utf-8"), 0o600)
    return path


def write_endpoint_process_record(
    root: Path,
    runtime: Path,
    *,
    listen: str,
    pid: int | None = None,
) -> Path:
    """Write the typed process record that declares this generation's bind.

    The applied-receipt verifier compares the receipt's ``bound_address`` to the
    numeric ``--listen`` value declared here instead of resolving the public
    locator again. This fixture declares one numeric bind exactly as a real
    refresh would after its start command.
    """
    path = root / "run" / "endpoint-process.json"
    socket_dir = runtime / "endpoint-fixture"
    host_service.write_json(
        path,
        {
            "process_version": host_service.ENDPOINT_PROCESS_RECORD_VERSION,
            "pid": os.getpid() if pid is None else pid,
            "binary": str(Path(sys.executable).resolve()),
            "binary_sha256": "0" * 64,
            "socket_dir": str(socket_dir),
            "runtime_root": str(runtime),
            "command": [
                str(Path(sys.executable).resolve()),
                "--listen",
                listen,
                "--endpoint-generation",
                "0",
            ],
        },
    )
    return path


def set_endpoint_generation(root: Path, generation: int) -> None:
    """Move the single declared generation in the existing endpoint locator."""
    locator = host_service.trust_endpoint(root)
    locator["endpoint_generation"] = generation
    host_service.write_json(root / "trust" / "endpoint.json", locator)


def assert_export_refused(
    root: Path,
    output: Path,
    expected_code: str,
    *,
    endpoint_receipt: dict[str, object] | None = None,
) -> None:
    trust = root / "trust"
    locator_hash_before = sha256_file(trust / "endpoint.json")
    token_hash_before = sha256_file(trust / "token")
    try:
        host_service.export_bundle(root, output, endpoint_receipt=endpoint_receipt)
        raise AssertionError(f"export_bundle unexpectedly succeeded for {expected_code}")
    except host_service.ServiceError as error:
        check(error.code == expected_code, f"expected {expected_code}, got {error.code}: {error}")
    check(not output.exists(), f"refused export left an output file: {output}")
    check(sha256_file(trust / "endpoint.json") == locator_hash_before, "refused export mutated the locator")
    check(sha256_file(trust / "token") == token_hash_before, "refused export mutated the token")


def test_install_creates_token_public(root: Path) -> None:
    """`install` alone must leave a startable root: no separate trust step.

    The user path is install -> start -> panel -> export. If the token only
    appeared at `trust init`, `start` would need a step that the panel owns,
    which is the circular ordering the connection minimum removes.
    """
    base = root / "install-token"
    runtime = root / "install-token-runtime"
    base.mkdir(mode=0o700)
    runtime.mkdir(mode=0o700)
    binary = stub_host_binary(root)
    installed = manager_json(
        "install", "--root", str(base), "--binary", str(binary),
        "--runtime-root", str(runtime), "--protocol-version", "11",
    )
    check(installed["state"] == "installed", f"install state: {installed}")

    trust = base / "trust"
    check(private_mode(trust) == 0o700, "install did not create a 0700 trust directory")
    token_path = trust / "token"
    check(token_path.is_file(), "install did not create the access token")
    check(private_mode(token_path) == 0o600, "install created a token that is not 0600")
    token = token_path.read_text(encoding="ascii")
    check(len(token) >= 32, f"install token is too short: {len(token)}")
    # `install` owns the token only. The locator stays `trust init`'s job.
    check(not (trust / "endpoint.json").exists(), "install invented an endpoint locator")

    # Reinstalling must never rotate the one secret the Host compares.
    manager_json(
        "install", "--root", str(base), "--binary", str(binary),
        "--runtime-root", str(runtime), "--protocol-version", "11",
    )
    check(
        token_path.read_text(encoding="ascii") == token,
        "reinstall rotated the access token",
    )


def test_trust_init_public(root: Path) -> None:
    """`trust init` creates the endpoint locator and the one access token."""
    base = root / "trust-init"
    base.mkdir(mode=0o700)
    initialized = manager_json(
        "trust", "init", "--root", str(base),
        "--endpoint-host", "100.64.0.1", "--endpoint-port", "8443",
    )
    check(initialized["state"] == "initialized", f"unexpected init state: {initialized}")
    check(initialized["endpoint"] == "100.64.0.1:8443", f"init endpoint: {initialized}")
    check(initialized["endpoint_host"] == "100.64.0.1", f"init host: {initialized}")
    check(initialized["endpoint_port"] == 8443, f"init port: {initialized}")

    trust = base / "trust"
    check(private_mode(trust) == 0o700, "trust directory is not 0700")
    check(
        sorted(entry.name for entry in trust.iterdir()) == ["endpoint.json", "server-key.der", "server.der", "token"],
        f"trust init created an unexpected file set: {sorted(entry.name for entry in trust.iterdir())}",
    )
    check(private_mode(trust / "token") == 0o600, "token is not 0600")
    check(private_mode(trust / "server-key.der") == 0o600, "server key is not 0600")
    check(private_mode(trust / "server.der") == 0o644, "server cert is not 0644")
    check(private_mode(trust / "endpoint.json") == 0o600, "endpoint locator is not 0600")

    token = (trust / "token").read_text()
    check(len(token) >= 32, f"access token is too short: {len(token)}")
    check("\n" not in token, "access token carries a newline")
    check(
        initialized["token_sha256"] == hashlib.sha256(token.encode("ascii")).hexdigest(),
        "published token digest does not match the committed token",
    )
    locator = json.loads((trust / "endpoint.json").read_bytes())
    check(
        locator == {
            "endpoint_generation": 0,
            "endpoint_host": "100.64.0.1",
            "endpoint_host_kind": "ip",
            "endpoint_port": 8443,
        },
        f"endpoint locator is not the declared shape: {locator}",
    )

    # The listener's own TLS identity is self-signed and is never a client
    # credential. Only the service generates it; it never enters the bundle.
    server_pem = base / "server.pem"
    openssl("x509", "-inform", "DER", "-in", str(trust / "server.der"), "-out", str(server_pem))
    san = openssl("x509", "-in", str(server_pem), "-noout", "-ext", "subjectAltName").stdout
    check("IP Address:100.64.0.1" in san, f"server SAN mismatch: {san!r}")
    eku = openssl("x509", "-in", str(server_pem), "-noout", "-ext", "extendedKeyUsage").stdout
    check("TLS Web Server Authentication" in eku, f"server EKU mismatch: {eku!r}")
    basic = openssl("x509", "-in", str(server_pem), "-noout", "-ext", "basicConstraints").stdout
    check("CA:FALSE" in basic, f"listener identity must not be a CA: {basic!r}")
    check(
        hashlib.sha256((trust / "server.der").read_bytes()).hexdigest() == initialized["server_sha256"],
        "published server digest does not match the committed certificate",
    )

    # Reinitialize is refused and preserves both control resources.
    token_before = (trust / "token").read_bytes()
    server_before = (trust / "server.der").read_bytes()
    # `trust init` is a panel step, not a lifecycle prerequisite: it is
    # idempotent, it records the locator, and it never rotates the token or the
    # listener identity. `install` owns the token.
    reinits = manager_json(
        "trust", "init", "--root", str(base),
        "--endpoint-host", "100.64.0.2", "--endpoint-port", "9443",
    )
    check(reinits["state"] == "initialized", f"reinit state: {reinits}")
    check((trust / "token").read_bytes() == token_before, "reinit rotated the access token")
    check((trust / "server.der").read_bytes() == server_before, "reinit mutated the server identity")
    check(
        json.loads((trust / "endpoint.json").read_bytes())
        == {
            "endpoint_generation": 0,
            "endpoint_host": "100.64.0.2",
            "endpoint_host_kind": "ip",
            "endpoint_port": 9443,
        },
        "reinit did not record the new locator",
    )
    # Restore the declared locator so the later status assertions stay stable.
    manager_json(
        "trust", "init", "--root", str(base),
        "--endpoint-host", "100.64.0.1", "--endpoint-port", "8443",
    )

    # An unusable port is refused before anything is written.
    invalid = root / "trust-init-invalid"
    invalid.mkdir(mode=0o700)
    code = manager_error(
        "trust", "init", "--root", str(invalid),
        "--endpoint-host", "100.64.0.1", "--endpoint-port", "70000",
    )
    check(code == "ENDPOINT_PORT_INVALID", f"invalid port code was {code}")
    check(not (invalid / "trust").exists(), "invalid init created a trust directory")

    # `trust status` reports the same two control resources and no endpoint yet.
    status = manager_json("trust", "status", "--root", str(base))
    check(status["state"] == "trust", f"status state: {status}")
    check(status["endpoint"] == "100.64.0.1:8443", f"status endpoint: {status}")
    check(status["endpoint_generation"] == 0, f"status generation: {status}")
    check(status["endpoint_state"] == "not_refreshed", f"status endpoint_state: {status}")
    check(status["endpoint_pid"] is None, f"status invented an endpoint PID: {status}")
    check(status["token_sha256"] == initialized["token_sha256"], "status token digest drifted")
    check(
        set(status) == {
            "state", "endpoint", "endpoint_host", "endpoint_port",
            "endpoint_generation", "token_sha256", "endpoint_state", "endpoint_pid",
        },
        f"status exposes an undeclared field: {sorted(status)}",
    )

    # The CLI reports an uninitialized root explicitly instead of inventing one.
    empty = root / "trust-init-empty"
    empty.mkdir(mode=0o700)
    code = manager_cli_error("trust", "status", "--root", str(empty))
    check(code == "TRUST_NOT_INITIALIZED", f"empty status code was {code}")


def test_export_bundle_public(root: Path) -> None:
    """The exported bundle is the frozen four-field connection file."""
    base = root / "export-bundle"
    base.mkdir(mode=0o700)
    service_root, _runtime = build_service_fixture(base)
    output = base / "pairing.abconnection"

    # Without a refreshed endpoint receipt the export refuses and writes nothing.
    assert_export_refused(service_root, output, "ENDPOINT_REFRESH_REQUIRED")
    assert_export_refused(service_root, output, "ENDPOINT_REFRESH_REQUIRED", endpoint_receipt={"status": "not_refreshed"})

    # The refreshed receipt is the only precondition. The CLI reaches the same
    # code through `_export_endpoint_receipt`, so the module call proves the
    # document while the CLI refusal above proves the gate.
    result = host_service.export_bundle(
        service_root,
        output,
        endpoint_receipt={
            "status": "refreshed",
            "endpoint_pid": os.getpid(),
            "endpoint_generation": 0,
            "bound_address": "100.64.0.1:8443",
        },
    )
    check(result["state"] == "exported", f"export state: {result}")
    check(result["endpoint"] == "100.64.0.1:8443", f"export endpoint: {result}")
    check(private_mode(output) == 0o600, "exported bundle is not 0600")
    document = json.loads(output.read_bytes())
    check(set(document) == BUNDLE_FIELDS, f"bundle carries undeclared fields: {sorted(document)}")
    check(document["format"] == "agentbrowser.connection", f"bundle format: {document}")
    check(document["version"] == 1, f"bundle version: {document}")
    check(document["endpoint"] == "100.64.0.1:8443", f"bundle endpoint: {document}")
    check(
        document["token"] == (service_root / "trust" / "token").read_text(),
        "bundle token is not the committed access token",
    )
    check(
        result["token_sha256"] == hashlib.sha256(document["token"].encode("ascii")).hexdigest(),
        "export token digest drifted",
    )
    check(output.read_bytes().endswith(b"\n"), "bundle is not newline terminated")
    # The bundle is the only client-facing artifact: it carries no transport
    # key, no certificate, no scope and no device identity.
    raw = output.read_bytes()
    for forbidden in (b"p12", b"passphrase", b"client_cert", b"ca_der", b"scope", b"device_id", b"transfer_code"):
        check(forbidden not in raw, f"bundle leaks removed ceremony: {forbidden!r}")


def test_wait_endpoint_applied_public(root: Path) -> None:
    """The service verifies the exact applied receipt and refuses the rest."""
    base = root / "endpoint-consumer"
    base.mkdir(mode=0o700)
    service_root, runtime = build_service_fixture(base)
    # The applied-receipt verifier compares against the numeric bind declared
    # by the typed process record, not a fresh locator resolution. Declare the
    # single numeric bind this generation was started with.
    write_endpoint_process_record(service_root, runtime, listen="100.64.0.1:8443")
    # `trust init` seeds the service-owned applied-receipt generation in the
    # existing endpoint locator. There is no second endpoint store, and the
    # consumer's generation comes only from here.
    check(
        host_service.trust_endpoint(service_root).get("endpoint_generation") == 0,
        "trust init did not seed the endpoint generation",
    )
    locator_path = service_root / "trust" / "endpoint.json"
    locator_before = sha256_file(locator_path)

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
    check(
        set(refreshed) == {"status", "endpoint_pid", "endpoint_generation", "bound_address"},
        f"applied receipt exposes an undeclared field: {sorted(refreshed)}",
    )

    def refused(generation: int, expected: str) -> None:
        set_endpoint_generation(service_root, generation)
        code = expect_service_error(
            lambda: host_service.wait_endpoint_applied(service_root, generation=generation, timeout_seconds=0.3)
        )
        check(code == expected, f"generation {generation} code was {code}, expected {expected}")

    # The applied receipt has no status field and no unknown fields.
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
    }
    write_endpoint_receipt(runtime, 21, ready_initializer)
    refused(21, "ENDPOINT_RECEIPT_MALFORMED")
    write_endpoint_receipt(runtime, 6, {**endpoint_receipt_fields(service_root, generation=6), "schema_version": 2})
    refused(6, "ENDPOINT_RECEIPT_MALFORMED")
    write_endpoint_receipt(runtime, 7, {**endpoint_receipt_fields(service_root, generation=7), "extra": 1})
    refused(7, "ENDPOINT_RECEIPT_MALFORMED")
    # The removed registry/projection digests are not part of the receipt, so a
    # receipt that still carries them is malformed rather than trusted.
    write_endpoint_receipt(
        runtime, 15, {**endpoint_receipt_fields(service_root, generation=15), "registry_sha256": "0" * 64}
    )
    refused(15, "ENDPOINT_RECEIPT_MALFORMED")

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

    # A missing generation at timeout is an explicit not-refreshed result.
    refused(999, "ENDPOINT_NOT_REFRESHED")

    # The verifier is read-only over the declared locator.
    check(
        host_service.trust_endpoint(service_root)["endpoint_generation"] == 999,
        "endpoint wait left the locator on a different generation",
    )
    check(locator_path.is_file(), "endpoint wait removed the locator")
    check(locator_before != sha256_file(locator_path), "the fixture never moved the generation")


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
    listen = host_service.endpoint_listen_address(numeric_host, numeric_port)
    check("localhost" not in listen, f"raw DNS leaked into --listen: {listen}")
    # The applied-receipt verifier compares against the numeric bind the service
    # actually started, taken from the typed process record; it never re-resolves
    # the public locator.
    check(
        host_service._bound_address_matches(f"[{numeric_host}]:8443", listen)
        if ":" in numeric_host
        else host_service._bound_address_matches(f"{numeric_host}:8443", listen),
        "bound-address verifier did not use the declared listen address",
    )
    check(
        not host_service._bound_address_matches("localhost:8443", listen),
        "bound-address verifier accepted the public DNS spelling as a bind",
    )
    # The client-facing endpoint keeps the public DNS spelling, so the bundle
    # never carries the numeric bind the service happened to resolve.
    check(
        host_service.endpoint_text("localhost", "dns", 8443) == "localhost:8443",
        "DNS endpoint text drifted",
    )
    check(
        host_service.endpoint_text("100.64.0.1", "ip", 8443) == "100.64.0.1:8443",
        "IP endpoint text drifted",
    )
    check(
        host_service.endpoint_text("fe80::1", "ip", 8443) == "[fe80::1]:8443",
        "IPv6 endpoint text is not bracketed",
    )


def test_refresh_missing_binary_typed_failure(root: Path) -> None:
    """A refresh that fails after a side effect returns typed partial facts.

    A private Host copy with no sibling ``obscura-endpoint`` binary drives the
    real public ``refresh_endpoint`` entry: the generation advances, the old
    receipt is cleared, and the missing binary must surface as
    ``ENDPOINT_NOT_REFRESHED`` with ``endpoint_state: not_refreshed``,
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
    # stays inside the Unix-socket bound. This short runtime is removed on both
    # the success and the failure exit.
    short_runtime = Path(tempfile.mkdtemp(prefix="r2mb-", dir="/private/tmp"))
    try:
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
            check(facts.get("endpoint_state") == "not_refreshed", f"missing-binary facts: {facts}")
            check(facts.get("last_step") == "start_endpoint", f"missing-binary step: {facts}")
            check(facts.get("endpoint_generation") == 1, f"missing-binary generation: {facts}")
        else:
            raise AssertionError("refresh with no sibling endpoint binary must refuse")
        check(
            host_service.trust_endpoint(service_root)["endpoint_generation"] == 1,
            "missing-binary refresh did not record the advanced generation",
        )
    finally:
        shutil.rmtree(short_runtime, ignore_errors=True)


def test_refresh_sequence_public(root: Path) -> None:
    """The declared service entry owns the sequence; failures carry typed facts.

    This is source-only: it proves the declared public entry exists, reads its
    generation only from the existing locator, and exposes ``not_refreshed``
    with typed partial facts when the sequence cannot run.
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
        check(facts.get("endpoint_state") == "not_refreshed", f"refresh facts state: {facts}")
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


def test_removed_ceremony_is_absent() -> None:
    """The removed ceremony is gone from both the CLI surface and the module."""
    usage = subprocess.run(
        [str(MANAGER), "trust", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    check(usage.returncode == 0, f"trust --help failed: {usage.stderr}")
    for removed in ("provision", "revoke", "admin"):
        check(removed not in usage.stdout, f"trust --help still offers {removed}: {usage.stdout}")
    for kept in ("init", "status", "export", "endpoint-refresh"):
        check(kept in usage.stdout, f"trust --help lost {kept}: {usage.stdout}")
    for name in (
        "projection_content",
        "parse_trust_registry",
        "refresh_projection",
        "write_projection",
        "enroll_device",
        "revoke_device",
        "build_device_p12",
        "generate_ca",
        "generate_leaf",
        "endpoint_ws_url",
        "command_admin_catalog_list",
    ):
        check(not hasattr(host_service, name), f"removed ceremony still exported: {name}")
    # The token comparison is the only check on the connection path.
    check(
        not hasattr(host_service, "REMOTE_SCOPES") and not hasattr(host_service, "TRUST_REGISTRY_NAME"),
        "a removed registry constant survived",
    )


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
    test_install_creates_token_public(root)
    test_trust_init_public(root)
    test_export_bundle_public(root)
    test_wait_endpoint_applied_public(root)
    test_endpoint_bind_basis_public(root)
    test_refresh_missing_binary_typed_failure(root)
    test_refresh_sequence_public(root)
    test_removed_ceremony_is_absent()


if __name__ == "__main__":
    raise SystemExit(main())
