#!/usr/bin/env python3
"""RED evidence: the base service source lacks the five accepted public APIs.

The pre-change RED observation was recorded in the task notes but its raw file
was not retained. This harness reconstructs the same observation from the
immutable base blob (a082e5f7fd65e568da4d53e2104eca9bf2e8fc2a), so the evidence
does not depend on the current working tree. It only reads the base blob and
never imports or executes the modified service source.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BASE = "a082e5f7fd65e568da4d53e2104eca9bf2e8fc2a"
SOURCE_PATH = "scripts/host-service/host_service.py"
REQUIRED_APIS = (
    "admin_catalog_info",
    "admin_catalog_create",
    "admin_catalog_select",
    "admin_membership_set",
    "wait_endpoint_applied",
)


def main() -> int:
    blob = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{BASE}:{SOURCE_PATH}"],
        check=True,
        capture_output=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="ab-r4-red-") as directory:
        module_path = Path(directory) / "base_host_service.py"
        module_path.write_bytes(blob)
        spec = importlib.util.spec_from_file_location("base_host_service", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        missing = [name for name in REQUIRED_APIS if not hasattr(module, name)]
    print(f"BASE_SHA {BASE}")
    print(f"MISSING_PUBLIC_APIS {','.join(missing)}")
    print(f"MISSING_COUNT {len(missing)}")
    print(f"RED_GAP {'CONFIRMED' if missing else 'CLOSED'}")
    return 0 if missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
