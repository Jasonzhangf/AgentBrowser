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
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
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


def openssl(*arguments: str, check_result: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [OPENSSL, *arguments],
        check=False,
        capture_output=True,
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


def der_to_pem(der_path: Path, pem_path: Path) -> None:
    openssl("x509", "-inform", "DER", "-in", str(der_path), "-out", str(pem_path))


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

    # 9. A real own-filesystem projection write failure exposes committed/partial truth.
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

    # 10. Premature export refuses and leaves no usable output.
    output = root / "bundle.abconnection"
    code = manager_error(
        "trust", "export", "--root", str(root),
        "--device-id", device_b["device_id"], "--output", str(output),
    )
    check(code == "PROFILE_CATALOG_UNAVAILABLE", f"premature export code was {code}")
    check(not output.exists(), "premature export left an output file")

    # 11. Real PKCS#12 container with exactly the three allowed aliases.
    transfer_code = host_service.generate_transfer_code()
    check(len(transfer_code) == 16, "transfer code length drifted")
    check(
        all(character not in "0O1IL" for character in transfer_code),
        "transfer code contains an ambiguous character",
    )
    p12 = host_service.build_device_transfer_p12(root, device_b["device_id"], transfer_code)
    p12_path = root / "device.p12"
    p12_path.write_bytes(p12)
    info = openssl("pkcs12", "-in", str(p12_path), "-passin", f"pass:{transfer_code}", "-info", "-nodes")
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
        "pkcs12", "-in", str(p12_path), "-passin", f"pass:{transfer_code}",
        "-clcerts", "-nokeys", "-out", str(extracted_cert),
    )
    openssl(
        "pkcs12", "-in", str(p12_path), "-passin", f"pass:{transfer_code}",
        "-nocerts", "-nodes", "-out", str(extracted_key),
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

    # No private material may appear in the emitted receipts.
    for receipt in (initialized, device_a, device_b, revoked, status, partial):
        text = json.dumps(receipt)
        check("PRIVATE KEY" not in text, "receipt leaked private key material")


if __name__ == "__main__":
    raise SystemExit(main())
