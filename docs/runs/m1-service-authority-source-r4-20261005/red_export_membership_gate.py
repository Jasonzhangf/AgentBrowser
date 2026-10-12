#!/usr/bin/env python3
"""RED evidence: export_bundle accepts a membership receipt that has no
receipt_type and no applied members.

Before the r4 source change, export_bundle only checks device_id,
catalog_revision and profile_id. A receipt with none of the required
grant/removal proof fields therefore passes the membership gate and reaches
the endpoint gate. The new contract requires PROFILE_ACCESS_WRITE_FAILED.

This harness uses only the public CLI and public module API. It never writes a
positive endpoint receipt and never produces a usable bundle.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT_DIR = REPO / "scripts" / "host-service"
sys.path.insert(0, str(SCRIPT_DIR))

import host_service  # noqa: E402

MANAGER = SCRIPT_DIR / "host-service"


def manager_json(*arguments: str) -> dict[str, object]:
    result = subprocess.run([str(MANAGER), *arguments], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"manager failed {arguments}: {result.stderr}")
    return json.loads(result.stdout)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ab-r4-red-"))
    root.chmod(0o700)
    manager_json(
        "trust", "init", "--root", str(root),
        "--endpoint-host", "100.64.0.1", "--endpoint-port", "8443",
    )
    device = manager_json(
        "trust", "provision", "--root", str(root), "--label", "Red device",
        "--scope", "profile_read", "--initial-profile", "profile_id:work",
    )
    device_id = device["device_id"]
    old_shape_receipt = {"device_id": device_id, "catalog_revision": 1, "profile_id": "work"}
    output = root / "red.abconnection"
    try:
        host_service.export_bundle(root, device_id, output, membership_receipt=old_shape_receipt)
    except host_service.ServiceError as error:
        print(f"RED_EXPORT_RESULT code={error.code}")
        print(f"RED_EXPECTED_GATE PROFILE_ACCESS_WRITE_FAILED")
        print(f"RED_GAP {'CONFIRMED' if error.code != 'PROFILE_ACCESS_WRITE_FAILED' else 'CLOSED'}")
        return 0
    print("RED_EXPORT_RESULT succeeded (unexpected)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
