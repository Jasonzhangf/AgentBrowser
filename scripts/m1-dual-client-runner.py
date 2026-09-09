#!/usr/bin/env python3
"""Run the Android and Mac clients against one live Host fixture.

This is a composition and evidence runner.  It does not implement either
client, the Browser ABI, or a transport.  The Mac bridge framing reader is
reused from ``scripts/local-direct/replay.py`` so frame acknowledgement keeps
one owner.  Android's existing instrumentation test remains the Android
entrypoint.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any, Callable, Mapping, Optional, Sequence


ROOT = pathlib.Path(__file__).resolve().parent.parent
DIRECT_REPLAY = ROOT / "scripts" / "local-direct" / "replay.py"
SCHEMA = "agentbrowser.m1.dual-client-runner/v1"
DEFAULT_ANDROID_CLASS = "com.agentbrowser.probe.NetworkDeviceTest"
DEFAULT_TEST_COMPONENT = "com.agentbrowser.probe.test/android.test.InstrumentationTestRunner"
STAGES = (
    "preflight",
    "fixture_start",
    "android_install",
    "mac_connect",
    "android_replay",
    "mac_control",
    "mac_reconnect",
    "correlation",
    "cleanup",
)


def _load_direct_replay() -> Any:
    """Load the existing bridge process/framing owner without executing main."""

    spec = importlib.util.spec_from_file_location("agentbrowser_local_direct_replay", DIRECT_REPLAY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load bridge framing owner: {DIRECT_REPLAY}")
    module = importlib.util.module_from_spec(spec)
    # ``dataclasses`` resolves postponed annotations through ``sys.modules``
    # while the direct replay module is being imported.  Register the module
    # before executing it, just as the normal import machinery does.
    sys.modules[spec.name] = module
    # Loading the shared helper must not dirty a clean candidate with an
    # untracked ``__pycache__`` entry.  The runner records candidate status as
    # evidence, so a helper import must be observationally side-effect free.
    write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = write_bytecode
    return module


_DIRECT = _load_direct_replay()
BridgeProcess = _DIRECT.BridgeProcess
ProcessPipes = _DIRECT.ProcessPipes
ProcessProtocolError = _DIRECT.ProcessProtocolError
artifact_identity = _DIRECT.artifact_identity
bounded_text = _DIRECT.bounded_text
file_sha256 = _DIRECT.file_sha256
is_rejection = _DIRECT.is_rejection
loopback_endpoint = _DIRECT.loopback_endpoint
parse_json_line = _DIRECT.parse_json_line


class RunnerAbort(Exception):
    """Stop dependent stages after preserving the first structured failure."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def safe_run_id(value: str) -> bool:
    return bool(value) and len(value) <= 128 and all(char.isalnum() or char in "._-" for char in value)


def git_probe(cwd: pathlib.Path, *args: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "value": None, "error": bounded_text(error)}
    output = result.stdout.strip()
    if result.returncode != 0:
        return {"ok": False, "value": None, "error": output or f"git exited {result.returncode}"}
    return {"ok": True, "value": output, "error": None}


def git_value(cwd: pathlib.Path, *args: str) -> Optional[str]:
    probe = git_probe(cwd, *args)
    return probe["value"] if probe["ok"] else None


def candidate_git_probes(cwd: pathlib.Path) -> dict[str, dict[str, Any]]:
    return {
        "branch": git_probe(cwd, "symbolic-ref", "--quiet", "--short", "HEAD"),
        "commit": git_probe(cwd, "rev-parse", "HEAD"),
        "tree": git_probe(cwd, "rev-parse", "HEAD^{tree}"),
        "status": git_probe(cwd, "status", "--porcelain=v1", "--untracked-files=all"),
    }


def write_exclusive(path: pathlib.Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with path.open(mode, encoding=None if mode == "wb" else "utf-8", errors=None if mode == "wb" else "replace", newline="" if mode == "w" else None) as handle:  # type: ignore[arg-type]
        handle.write(content)


def known(value: Any, evidence: Sequence[str]) -> dict[str, Any]:
    return {"status": "known", "value": value, "evidence": list(evidence)}


def unknown(reason: str) -> dict[str, Any]:
    return {"status": "unknown", "value": None, "reason": reason}


def failed(reason: str) -> dict[str, Any]:
    return {"status": "failed", "value": None, "reason": reason}


def integer_pair(value: Any) -> Optional[list[int]]:
    """Normalize the protocol's numeric dimension pair without rounding it."""

    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        numeric = float(item)
        if not math.isfinite(numeric) or numeric <= 0 or not numeric.is_integer():
            return None
        result.append(int(numeric))
    return result


def dimension_evidence(value: Any, evidence: Sequence[str], reason: str) -> dict[str, Any]:
    normalized = integer_pair(value)
    return known(normalized, evidence) if normalized is not None else unknown(reason)


def integer_evidence(value: Any, evidence: Sequence[str], reason: str) -> dict[str, Any]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return unknown(reason)
    return known(value, evidence)


def boolean_evidence(value: Any, evidence: Sequence[str], reason: str) -> dict[str, Any]:
    if not isinstance(value, bool):
        return unknown(reason)
    return known(value, evidence)


def combined_status(items: Mapping[str, Mapping[str, Any]]) -> str:
    """Combine dependent claims without turning unknown evidence into PASS."""

    if any(item.get("status") == "failed" for item in items.values()):
        return "failed"
    if any(item.get("status") == "known" and item.get("value") is False for item in items.values()):
        return "failed"
    if all(item.get("status") in {"proved", "known"} for item in items.values()):
        return "proved"
    return "unknown"


def compare_field(field: str, sides: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Compare one typed field without upgrading missing side evidence."""

    missing: list[str] = []
    values: dict[str, Any] = {}
    for side, side_evidence in sides.items():
        item = side_evidence.get(field)
        if not isinstance(item, Mapping) or item.get("status") != "known":
            missing.append(side)
            continue
        values[side] = item.get("value")
    if missing:
        return {
            "status": "unknown",
            "field": field,
            "values": values,
            "missing_sides": missing,
            "reason": f"{field} is not emitted by every side",
        }
    if not values:
        return {"status": "unknown", "field": field, "values": {}, "reason": "no side evidence"}
    unique = {json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values.values()}
    if len(unique) != 1:
        return {
            "status": "failed",
            "field": field,
            "values": values,
            "reason": "side values differ; no single-session claim is allowed",
        }
    return {"status": "proved", "field": field, "values": values}


def classify_result(first_failure: Optional[Mapping[str, Any]], claims: Mapping[str, Mapping[str, Any]]) -> str:
    if first_failure is not None:
        return "failed"
    if any(value.get("status") == "failed" for value in claims.values()):
        return "failed"
    if any(value.get("status") != "proved" for value in claims.values()):
        return "partial"
    return "pass"


def acknowledged_active_frames(bridge: Any) -> list[dict[str, Any]]:
    active_generation = getattr(bridge, "active_generation", None)
    acknowledged = getattr(bridge, "acked_tickets", set())
    if not isinstance(active_generation, int) or active_generation < 0:
        return []
    return [
        dict(frame)
        for frame in getattr(bridge, "frames", [])
        if isinstance(frame, Mapping)
        and frame.get("generation") == active_generation
        and (active_generation, frame.get("ticket")) in acknowledged
    ]


def schema_document() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA,
        "type": "object",
        "required": [
            "schema",
            "run_id",
            "candidate",
            "environment",
            "fixture",
            "sides",
            "cross_side",
            "required_claims",
            "result",
            "cleanup",
        ],
        "properties": {
            "schema": {"const": SCHEMA},
            "run_id": {"type": "string", "minLength": 1},
            "result": {"enum": ["pending", "dry_run", "pass", "partial", "failed"]},
            "sides": {"type": "object", "required": ["host", "android", "mac"]},
            "cross_side": {"type": "object"},
            "required_claims": {"type": "object"},
        },
        "additionalProperties": True,
    }


def adb_executable() -> Optional[pathlib.Path]:
    configured = os.environ.get("ADB", "adb")
    path = pathlib.Path(configured).expanduser()
    if path.is_absolute():
        return path.resolve(strict=False) if path.is_file() else None
    found = shutil.which(configured)
    return pathlib.Path(found).resolve(strict=False) if found else None


def adb_reverse_mappings(output: bytes | str, device_port: int, serial: Optional[str] = None) -> list[str]:
    """Return exact reverse rows for one device-side port and optional serial."""

    text = output.decode(errors="replace") if isinstance(output, bytes) else output
    device = f"tcp:{device_port}"
    return [
        line.strip()
        for line in text.splitlines()
        if len(line.split()) == 3
        and (serial is None or line.split()[0] == serial)
        and line.split()[1] == device
    ]


def exact_adb_reverse_mapping(rows: Sequence[str], serial: str, port: int) -> Optional[str]:
    expected = f"{serial} tcp:{port} tcp:{port}"
    matches = [row for row in rows if row == expected]
    return expected if len(matches) == 1 else None


def environment_identity(
    serial: str,
    adb_call: Optional[Callable[[Sequence[str]], tuple[int, str]]] = None,
    *,
    probe: bool = True,
) -> dict[str, Any]:
    """Return platform identity; all Android values identify their source."""

    mac = {
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "source": "live runner host",
    }
    android: dict[str, Any] = {
        "serial": serial or None,
        "source": "live adb" if probe else "not probed",
        "state": None,
        "model": None,
        "sdk": None,
        "abi": None,
        "availability": "unknown",
        "errors": [],
    }
    if not probe:
        android["availability"] = "not_probed"
    else:
        call = adb_call
        if call is None:
            executable = adb_executable()

            def live_call(arguments: Sequence[str]) -> tuple[int, str]:
                if executable is None:
                    return 127, "ADB_NOT_FOUND"
                try:
                    result = subprocess.run(
                        [str(executable), "-s", serial, *arguments],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                        timeout=15,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    return 127, bounded_text(error)
                return result.returncode, result.stdout

            call = live_call

        fields = {
            "state": ("get-state",),
            "model": ("shell", "getprop", "ro.product.model"),
            "sdk": ("shell", "getprop", "ro.build.version.sdk"),
            "abi": ("shell", "getprop", "ro.product.cpu.abi"),
        }
        for name, command in fields.items():
            code, output = call(command)
            value = output.strip()
            if code == 0 and value:
                android[name] = value
            else:
                android["errors"].append({"field": name, "command": list(command), "code": code, "output": bounded_text(output)})
        android["availability"] = "known" if android["state"] == "device" and not android["errors"] else "unknown"
    environment_id = f"{mac['platform'].lower()}-{mac['machine']}-{serial or 'unspecified'}"
    return {"environment_id": environment_id, "mac": mac, "android": android}


class Arguments:
    def __init__(
        self,
        *,
        dry_run: bool,
        fixture_bin: Optional[pathlib.Path],
        obscura_bin_dir: Optional[pathlib.Path],
        mac_bridge_bin: Optional[pathlib.Path],
        android_serial: str,
        evidence_dir: pathlib.Path,
        requested_evidence_dir: pathlib.Path,
        run_id: str,
        timeout: float,
        poll: float,
        android_test_timeout: float,
        bind_ip: str,
        android_test_class: str,
        android_test_component: str,
        skip_android_build: bool,
        fixture_args: list[str],
        mac_bridge_args: list[str],
        agent_commit: Optional[str],
        agent_tree: Optional[str],
        obscura_commit: Optional[str],
        obscura_tree: Optional[str],
        android_build_provenance: Optional[pathlib.Path],
    ):
        self.dry_run = dry_run
        self.fixture_bin = fixture_bin
        self.obscura_bin_dir = obscura_bin_dir
        self.mac_bridge_bin = mac_bridge_bin
        self.android_serial = android_serial
        self.evidence_dir = evidence_dir
        self.requested_evidence_dir = requested_evidence_dir
        self.run_id = run_id
        self.timeout = timeout
        self.poll = poll
        self.android_test_timeout = android_test_timeout
        self.bind_ip = bind_ip
        self.android_test_class = android_test_class
        self.android_test_component = android_test_component
        self.skip_android_build = skip_android_build
        self.fixture_args = fixture_args
        self.mac_bridge_args = mac_bridge_args
        self.agent_commit = agent_commit
        self.agent_tree = agent_tree
        self.obscura_commit = obscura_commit
        self.obscura_tree = obscura_tree
        self.android_build_provenance = android_build_provenance


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run Android and Mac clients against one live AgentBrowser Host fixture")
    value.add_argument("--dry-run", action="store_true", help="validate the invocation and emit unknown runtime claims")
    value.add_argument("--print-schema", action="store_true", help="print the evidence schema and exit")
    value.add_argument("--fixture-bin", type=pathlib.Path, default=None)
    value.add_argument("--obscura-bin-dir", type=pathlib.Path, default=None)
    value.add_argument("--mac-bridge-bin", type=pathlib.Path, default=None)
    value.add_argument("--android-serial", default=os.environ.get("ANDROID_SERIAL", ""))
    value.add_argument("--evidence-dir", type=pathlib.Path, default=None)
    value.add_argument("--run-id", default=None)
    value.add_argument("--timeout", type=float, default=45.0)
    value.add_argument("--poll", type=float, default=0.5)
    value.add_argument("--android-test-timeout", type=float, default=180.0)
    value.add_argument("--bind-ip", default=os.environ.get("OBSCURA_ENDPOINT_BIND_IP", "127.0.0.1"))
    value.add_argument("--android-test-class", default=DEFAULT_ANDROID_CLASS)
    value.add_argument("--android-test-component", default=DEFAULT_TEST_COMPONENT)
    value.add_argument("--skip-android-build", action="store_true")
    value.add_argument("--android-build-provenance", type=pathlib.Path, default=None)
    value.add_argument("--fixture-arg", action="append", default=[])
    value.add_argument("--mac-bridge-arg", action="append", default=[])
    value.add_argument("--agent-commit", default=None)
    value.add_argument("--agent-tree", default=None)
    value.add_argument("--obscura-commit", default=None)
    value.add_argument("--obscura-tree", default=None)
    return value


def build_arguments(namespace: argparse.Namespace, cwd: pathlib.Path = ROOT) -> Arguments:
    run_id = namespace.run_id or f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    if not safe_run_id(run_id):
        raise ValueError(f"invalid --run-id: {run_id!r}")
    if namespace.print_schema and namespace.dry_run:
        raise ValueError("--print-schema and --dry-run are mutually exclusive")
    if any(
        not math.isfinite(number) or number <= 0
        for number in (namespace.timeout, namespace.poll, namespace.android_test_timeout)
    ):
        raise ValueError("timeouts and poll interval must be positive")
    if namespace.android_test_class != DEFAULT_ANDROID_CLASS:
        raise ValueError(f"--android-test-class must be {DEFAULT_ANDROID_CLASS}")
    if namespace.android_test_component != DEFAULT_TEST_COMPONENT:
        raise ValueError(f"--android-test-component must be {DEFAULT_TEST_COMPONENT}")
    serial = str(namespace.android_serial or "").strip()
    if not namespace.dry_run and (not serial or any(char.isspace() for char in serial)):
        raise ValueError("--android-serial or ANDROID_SERIAL is required and must not contain whitespace")
    try:
        bind = ipaddress.ip_address(namespace.bind_ip)
    except ValueError as error:
        raise ValueError(f"invalid --bind-ip: {error}") from error
    if not bind.is_loopback:
        raise ValueError("--bind-ip must be a loopback address for the local combined runner")

    requested = namespace.evidence_dir
    if requested is None:
        requested = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "agentbrowser-m1-dual" / run_id
    requested = requested if requested.is_absolute() else cwd / requested
    requested = requested.resolve(strict=False)
    path_values: dict[str, Optional[pathlib.Path]] = {
        "fixture_bin": namespace.fixture_bin,
        "obscura_bin_dir": namespace.obscura_bin_dir,
        "mac_bridge_bin": namespace.mac_bridge_bin,
    }
    for key, path in path_values.items():
        if path is not None:
            expanded = path.expanduser()
            path_values[key] = (expanded if expanded.is_absolute() else cwd / expanded).resolve(strict=False)
    provenance = namespace.android_build_provenance
    if provenance is not None:
        expanded = provenance.expanduser()
        provenance = (expanded if expanded.is_absolute() else cwd / expanded).resolve(strict=False)
    return Arguments(
        dry_run=namespace.dry_run,
        fixture_bin=path_values["fixture_bin"],
        obscura_bin_dir=path_values["obscura_bin_dir"],
        mac_bridge_bin=path_values["mac_bridge_bin"],
        android_serial=serial,
        evidence_dir=requested,
        requested_evidence_dir=requested,
        run_id=run_id,
        timeout=namespace.timeout,
        poll=namespace.poll,
        android_test_timeout=namespace.android_test_timeout,
        bind_ip=namespace.bind_ip,
        android_test_class=namespace.android_test_class,
        android_test_component=namespace.android_test_component,
        skip_android_build=namespace.skip_android_build,
        fixture_args=list(namespace.fixture_arg),
        mac_bridge_args=list(namespace.mac_bridge_arg),
        agent_commit=namespace.agent_commit,
        agent_tree=namespace.agent_tree,
        obscura_commit=namespace.obscura_commit,
        obscura_tree=namespace.obscura_tree,
        android_build_provenance=provenance,
    )


class CombinedRunner:
    def __init__(self, args: Arguments, *, worktree: pathlib.Path = ROOT):
        self.args = args
        self.worktree = worktree.resolve()
        self.fixture: Optional[Any] = None
        self.bridge: Optional[Any] = None
        self.android_process: Optional[subprocess.Popen[bytes]] = None
        self.android_log_handle: Optional[Any] = None
        self.fixture_root: Optional[pathlib.Path] = None
        self.adb_path: Optional[pathlib.Path] = None
        self.adb_reverse_port: Optional[int] = None
        self.adb_reverse_owned: Optional[dict[str, Any]] = None
        self.pairing_installed = False
        self.first_failure: Optional[dict[str, Any]] = None
        self.host_statuses: list[dict[str, Any]] = []
        self.mac_snapshots: list[dict[str, Any]] = []
        self.mac_operations: list[dict[str, Any]] = []
        self.android_result: Optional[dict[str, Any]] = None
        self.android_install: dict[str, Any] = {}
        self.evidence_dir_ready = False
        self.evidence = self._initial_evidence()
        self._prepare_evidence_dir()

    def _initial_evidence(self) -> dict[str, Any]:
        probes = candidate_git_probes(self.worktree)
        value = lambda name: probes[name]["value"] if probes[name]["ok"] else None
        status = value("status")
        return {
            "schema": SCHEMA,
            "run_id": self.args.run_id,
            "started_at": utc_now(),
            "finished_at": None,
            "result": "pending",
            "candidate": {
                "worktree": str(self.worktree),
                "branch": value("branch"),
                "commit": value("commit"),
                "tree": value("tree"),
                "status_before": None if status is None else status.splitlines(),
                "git_probes_before": probes,
                "status_after": None,
                "requested_agent_commit": self.args.agent_commit,
                "requested_agent_tree": self.args.agent_tree,
                "requested_obscura_commit": self.args.obscura_commit,
                "requested_obscura_tree": self.args.obscura_tree,
            },
            "invocation": {
                "mode": "dry-run" if self.args.dry_run else "real",
                "run_id": self.args.run_id,
                "requested_evidence_dir": str(self.args.requested_evidence_dir),
                "evidence_dir": str(self.args.evidence_dir),
                "bind_ip": self.args.bind_ip,
                "android_test_class": self.args.android_test_class,
                "android_test_component": self.args.android_test_component,
            },
            "environment": None,
            "adb_reverse": {"status": "not_configured", "serial": self.args.android_serial},
            "fixture": {
                "identity": None,
                "ready": None,
                "root": None,
                "endpoint": None,
                "session": None,
                "status_snapshots": [],
            },
            "sides": {"host": {}, "android": {}, "mac": {}},
            "cross_side": {},
            "required_claims": {},
            "stages": {
                name: {"result": "not_run", "started_at": None, "finished_at": None, "details": {}, "first_failure": None}
                for name in STAGES
            },
            "failures": [],
            "unknown": [],
            "processes": {},
            "events": {"host": [], "android": [], "mac": []},
            "cleanup": {"fixture": None, "mac": None, "android": None, "pairing": None, "adb_reverse": None, "fixture_root": None},
        }

    def _refresh_candidate_git(self, label: str) -> bool:
        probes = candidate_git_probes(self.worktree)
        candidate = self.evidence["candidate"]
        candidate[f"git_probes_{label}"] = probes
        if not all(probe["ok"] for probe in probes.values()):
            candidate[f"status_{label}"] = None
            return False
        candidate["branch"] = probes["branch"]["value"]
        candidate["commit"] = probes["commit"]["value"]
        candidate["tree"] = probes["tree"]["value"]
        status = probes["status"]["value"]
        candidate[f"status_{label}"] = status.splitlines()
        if label == "before":
            candidate["status_before"] = status.splitlines()
        if label == "after":
            candidate["status_after"] = status.splitlines()
        return True

    def _prepare_evidence_dir(self) -> None:
        requested = self.args.requested_evidence_dir
        if self.worktree == requested or self.worktree in requested.parents:
            result = subprocess.run(
                ["git", "check-ignore", "-q", "--", str(requested)],
                cwd=str(self.worktree),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if result.returncode != 0:
                raise ValueError(f"evidence directory inside candidate is not ignored: {requested}")
        requested.mkdir(parents=True, exist_ok=False)
        self.evidence_dir_ready = True

    def _write_json(self, name: str, value: Any) -> pathlib.Path:
        path = self.args.evidence_dir / name
        write_exclusive(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        return path

    def stage_start(self, name: str) -> None:
        stage = self.evidence["stages"][name]
        if stage["started_at"] is None:
            stage["started_at"] = utc_now()
        stage["result"] = "running"

    def stage_finish(self, name: str, result: str, details: Optional[Mapping[str, Any]] = None) -> None:
        stage = self.evidence["stages"][name]
        if stage["started_at"] is None:
            stage["started_at"] = utc_now()
        stage["result"] = result
        stage["finished_at"] = utc_now()
        if details:
            stage["details"].update(dict(details))

    def fail(self, code: str, message: str, *, owner: str, next_action: str, stage: Optional[str] = None) -> None:
        failure = {
            "code": code,
            "message": bounded_text(message),
            "owner": owner,
            "next_action": next_action,
            "stage": stage,
            "at": utc_now(),
        }
        self.evidence["failures"].append(failure)
        if stage is not None:
            self.evidence["stages"][stage]["first_failure"] = self.evidence["stages"][stage]["first_failure"] or failure
            self.stage_finish(stage, "failed")
        if self.first_failure is None:
            self.first_failure = failure

    def abort(self, code: str, message: str, *, owner: str, next_action: str, stage: Optional[str] = None) -> None:
        self.fail(code, message, owner=owner, next_action=next_action, stage=stage)
        raise RunnerAbort(message)

    def add_unknown(self, item: str, reason: str) -> None:
        value = {"item": item, "reason": reason}
        if value not in self.evidence["unknown"]:
            self.evidence["unknown"].append(value)

    def _require_executable(self, path: Optional[pathlib.Path], code: str, owner: str, stage: str) -> pathlib.Path:
        if path is None or not path.is_file() or not os.access(path, os.X_OK):
            self.abort(
                code,
                f"missing or non-executable entrypoint: {path}",
                owner=owner,
                next_action="provide the exact executable artifact for this client or fixture",
                stage=stage,
            )
        return path

    def preflight(self) -> None:
        self.stage_start("preflight")
        candidate = self.evidence["candidate"]
        if not self._refresh_candidate_git("before"):
            self.abort(
                "GIT_PROBE_FAILED",
                f"candidate Git probe failed: {candidate.get('git_probes_before')}",
                owner="workspace Git boundary",
                next_action="restore Git access and rerun from this candidate worktree",
                stage="preflight",
            )
        if any(candidate.get(field) is None for field in ("branch", "commit", "tree", "status_before")):
            self.abort("GIT_PROBE_FAILED", f"candidate Git values incomplete: {candidate}", owner="workspace Git boundary", next_action="restore Git access and rerun from this candidate worktree", stage="preflight")
        branch = candidate["branch"]
        if branch in {"main", "master"}:
            self.abort("PROTECTED_BRANCH", f"candidate branch is protected: {branch}", owner="workspace Git boundary", next_action="run from the assigned owner worktree branch", stage="preflight")
        if candidate["status_before"]:
            self.abort("DIRTY_CANDIDATE_TREE", json_text(candidate["status_before"]), owner="candidate worktree", next_action="commit or move unrelated changes before replay", stage="preflight")
        if self.args.agent_commit and candidate["commit"] != self.args.agent_commit:
            self.abort("AGENT_CANDIDATE_MISMATCH", f"HEAD={candidate['commit']} requested={self.args.agent_commit}", owner="AgentBrowser candidate binding", next_action="select the requested AgentBrowser candidate tree", stage="preflight")
        if self.args.agent_tree and candidate["tree"] != self.args.agent_tree:
            self.abort("AGENT_TREE_MISMATCH", f"HEAD tree={candidate['tree']} requested={self.args.agent_tree}", owner="AgentBrowser candidate binding", next_action="select the requested AgentBrowser tree", stage="preflight")
        self._require_executable(self.args.fixture_bin, "MISSING_FIXTURE_ENTRYPOINT", "AgentBrowser fixture owner", "preflight")
        self._require_executable(self.args.mac_bridge_bin, "MISSING_MAC_BRIDGE_ENTRYPOINT", "AgentBrowser Mac bridge owner", "preflight")
        if self.args.obscura_bin_dir is None or not self.args.obscura_bin_dir.is_dir():
            self.abort("MISSING_OBSCURA_BIN_DIR", f"missing Obscura binary directory: {self.args.obscura_bin_dir}", owner="Obscura binary owner", next_action="provide the release directory containing the selected Obscura binaries", stage="preflight")
        binaries: dict[str, Any] = {}
        assert self.args.obscura_bin_dir is not None
        for name in ("obscura-host", "obscura-endpoint", "obscura-media"):
            binary = self.args.obscura_bin_dir / name
            binaries[name] = artifact_identity(binary)
            self._require_executable(binary, "MISSING_OBSCURA_BINARY", "Obscura binary owner", "preflight")
        self.evidence["candidate"]["obscura_binaries"] = binaries
        self.adb_path = adb_executable()
        if self.adb_path is None:
            self.abort("ADB_NOT_FOUND", "adb executable is not available", owner="Android environment owner", next_action="provide a live adb executable and selected device", stage="preflight")
        environment = environment_identity(self.args.android_serial)
        self.evidence["environment"] = environment
        self._write_json("environment.json", environment)
        if environment["android"]["availability"] != "known":
            self.abort("ANDROID_ENV_UNAVAILABLE", json_text(environment["android"]), owner="Android environment owner", next_action="restore the selected adb device and rerun from the same entrypoint", stage="preflight")
        self.evidence["candidate"]["fixture_artifact"] = artifact_identity(self.args.fixture_bin)
        self.evidence["candidate"]["mac_bridge_artifact"] = artifact_identity(self.args.mac_bridge_bin)
        self.stage_finish("preflight", "passed", {"branch": branch, "bind_ip": self.args.bind_ip, "environment_id": environment["environment_id"]})

    def _fixture_line(self, timeout: float, stage: str) -> Any:
        assert self.fixture is not None
        line = self.fixture.next_stdout_line(timeout)
        if line is None:
            self.abort("FIXTURE_OUTPUT_CLOSED", "fixture stdout closed before a JSON response", owner="AgentBrowser fixture owner", next_action="preserve fixture stderr and process identity", stage=stage)
        try:
            return json.loads(line)
        except json.JSONDecodeError as error:
            self.abort("FIXTURE_OUTPUT_INVALID", f"{error}: {line}", owner="AgentBrowser fixture owner", next_action="emit one JSON object per fixture response", stage=stage)

    def start_fixture(self) -> None:
        self.stage_start("fixture_start")
        assert self.args.fixture_bin is not None and self.args.obscura_bin_dir is not None
        env = dict(os.environ)
        env.update(
            {
                "OBSCURA_BIN_DIR": str(self.args.obscura_bin_dir),
                "OBSCURA_ENDPOINT_BIND_IP": self.args.bind_ip,
                "AGENTBROWSER_M1_DUAL_RUN_ID": self.args.run_id,
            }
        )
        command = [str(self.args.fixture_bin), *self.args.fixture_args]
        try:
            self.fixture = ProcessPipes(command, self.worktree, env)
        except OSError as error:
            self.abort("FIXTURE_START_FAILED", bounded_text(error), owner="AgentBrowser fixture owner", next_action="start the selected device_fixture artifact", stage="fixture_start")
        assert self.fixture is not None
        self.evidence["processes"]["fixture"] = {"pid": self.fixture.pid, "command": self.fixture.command}
        ready = self._fixture_line(self.args.timeout, "fixture_start")
        if not isinstance(ready, Mapping):
            self.abort("FIXTURE_READY_INVALID", f"ready record is not an object: {ready!r}", owner="AgentBrowser fixture owner", next_action="emit fixture, endpoint and session in one ready record", stage="fixture_start")
        root_raw = ready.get("fixture")
        endpoint_raw = ready.get("endpoint")
        session = ready.get("session")
        if not isinstance(root_raw, str) or not root_raw:
            self.abort("FIXTURE_READY_INVALID", "ready record has no fixture root", owner="AgentBrowser fixture owner", next_action="emit an owned /tmp/an-* fixture root", stage="fixture_start")
        fixture_root = pathlib.Path(root_raw).expanduser().resolve(strict=False)
        if fixture_root.name[:3] != "an-" or fixture_root.parent != pathlib.Path("/tmp").resolve():
            self.abort("FIXTURE_ROOT_INVALID", str(fixture_root), owner="AgentBrowser fixture owner", next_action="use the owned /tmp/an-* fixture directory", stage="fixture_start")
        if not isinstance(session, str) or not session:
            self.abort("FIXTURE_READY_INVALID", "ready record has no session identity", owner="AgentBrowser fixture owner", next_action="emit a non-empty session identity", stage="fixture_start")
        endpoint_info, endpoint_error = loopback_endpoint(endpoint_raw)
        if endpoint_error is not None:
            self.abort(endpoint_error, f"fixture endpoint rejected: {endpoint_raw!r}", owner="fixture endpoint owner", next_action="emit a loopback wss:// endpoint for the direct path", stage="fixture_start")
        endpoint_file = fixture_root / "endpoint.txt"
        try:
            if endpoint_file.read_text(encoding="utf-8").strip() != endpoint_raw:
                self.abort("PAIRING_ENDPOINT_MISMATCH", "endpoint.txt does not match ready endpoint", owner="fixture endpoint owner", next_action="keep pairing endpoint and ready record identical", stage="fixture_start")
        except OSError as error:
            self.abort("PAIRING_FILE_MISSING", bounded_text(error), owner="fixture endpoint owner", next_action="preserve all fixture pairing files", stage="fixture_start")
        self.fixture_root = fixture_root
        self.evidence["fixture"].update({"identity": artifact_identity(self.args.fixture_bin), "ready": dict(ready), "root": str(fixture_root), "endpoint": endpoint_raw, "session": session, "endpoint_info": endpoint_info})
        self._write_json("fixture-ready.json", self.evidence["fixture"])
        self.record_host_status("fixture_ready", "fixture_start")
        self.stage_finish("fixture_start", "passed", {"pid": self.fixture.pid, "session": session, "endpoint": endpoint_raw})

    def fixture_command(self, command: str, stage: str) -> Any:
        if self.fixture is None:
            self.abort("FIXTURE_UNAVAILABLE", f"cannot send fixture command {command}", owner="AgentBrowser fixture owner", next_action="preserve fixture process state", stage=stage)
        try:
            self.fixture.send_line(command)
            return self._fixture_line(self.args.timeout, stage)
        except ProcessProtocolError as error:
            self.abort(error.code, error.message, owner="AgentBrowser fixture owner", next_action="inspect fixture process and stderr", stage=stage)

    def record_host_status(self, label: str, stage: str) -> Optional[dict[str, Any]]:
        value = self.fixture_command("status", stage)
        if not isinstance(value, Mapping):
            self.abort("HOST_STATUS_INVALID", f"status is not an object: {value!r}", owner="Obscura Host owner", next_action="return typed SessionStatus from fixture status", stage=stage)
        required = ("session_id", "attachments", "control", "viewport_revision", "document_revision")
        missing = [name for name in required if name not in value]
        if missing:
            self.abort("HOST_STATUS_FIELDS_MISSING", f"missing fields: {missing}", owner="Obscura Host owner", next_action="preserve the complete typed SessionStatus", stage=stage)
        snapshot = {"label": label, "observed_at": utc_now(), "value": dict(value)}
        self.host_statuses.append(snapshot)
        self.evidence["fixture"]["status_snapshots"] = self.host_statuses
        self.evidence["events"]["host"].append(snapshot)
        return dict(value)

    def adb(self, arguments: Sequence[str], *, timeout: float = 30.0) -> tuple[int, bytes]:
        if self.adb_path is None:
            return 127, b"ADB_NOT_FOUND"
        try:
            result = subprocess.run(
                [str(self.adb_path), "-s", self.args.android_serial, *arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return 127, bounded_text(error).encode()
        return result.returncode, result.stdout

    def pairing_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment["ANDROID_SERIAL"] = self.args.android_serial
        if self.adb_path is not None:
            environment["PATH"] = str(self.adb_path.parent) + os.pathsep + environment.get("PATH", "")
        return environment

    def configure_adb_reverse(self, stage: str) -> None:
        endpoint_info = self.evidence["fixture"].get("endpoint_info")
        port = endpoint_info.get("port") if isinstance(endpoint_info, Mapping) else None
        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65_535):
            self.abort(
                "ADB_REVERSE_ENDPOINT_MISSING",
                f"fixture endpoint has no valid TCP port: {endpoint_info!r}",
                owner="fixture endpoint owner",
                next_action="emit the selected loopback endpoint port before Android install",
                stage=stage,
            )
        list_code, list_output = self.adb(["reverse", "--list"])
        existing = adb_reverse_mappings(list_output, port, self.args.android_serial)
        self.evidence["adb_reverse"] = {
            "status": "checking",
            "serial": self.args.android_serial,
            "host_port": port,
            "device_port": port,
            "existing": existing,
        }
        if list_code != 0:
            self.evidence["adb_reverse"]["status"] = "failed"
            self.evidence["adb_reverse"]["list_output"] = bounded_text(list_output)
            self.abort(
                "ADB_REVERSE_LIST_FAILED",
                bounded_text(list_output),
                owner="Android environment owner",
                next_action="preserve adb reverse listing output and selected device state",
                stage=stage,
            )
        if existing:
            self.evidence["adb_reverse"]["status"] = "blocked"
            self.abort(
                "ADB_REVERSE_PORT_IN_USE",
                f"device-side tcp:{port} already has reverse mappings: {existing}",
                owner="Android environment owner",
                next_action="release or select an unused device-side reverse port under the resource owner",
                stage=stage,
            )

        expected = f"{self.args.android_serial} tcp:{port} tcp:{port}"
        add_code, add_output = self.adb(["reverse", f"tcp:{port}", f"tcp:{port}"])
        self.evidence["adb_reverse"]["add_output"] = bounded_text(add_output)
        if add_code != 0:
            self.evidence["adb_reverse"]["status"] = "failed"
            self.abort(
                "ADB_REVERSE_SETUP_FAILED",
                bounded_text(add_output),
                owner="Android environment owner",
                next_action="preserve adb reverse setup output and selected device state",
                stage=stage,
            )
        self.adb_reverse_port = port
        self.adb_reverse_owned = {"serial": self.args.android_serial, "port": port, "mapping": expected}
        verify_code, verify_output = self.adb(["reverse", "--list"])
        mappings = adb_reverse_mappings(verify_output, port, self.args.android_serial)
        owned_mapping = exact_adb_reverse_mapping(mappings, self.args.android_serial, port)
        self.evidence["adb_reverse"].update(
            {"status": "configured" if verify_code == 0 and owned_mapping is not None else "failed", "verified": mappings, "verify_output": bounded_text(verify_output), "mapping": owned_mapping}
        )
        if verify_code != 0 or owned_mapping is None:
            self.abort(
                "ADB_REVERSE_NOT_CONFIRMED",
                f"expected {expected!r}, observed {mappings!r}; {bounded_text(verify_output)}",
                owner="Android environment owner",
                next_action="preserve adb reverse setup and listing output for the selected device",
                stage=stage,
            )
        self._write_json("adb-reverse.json", self.evidence["adb_reverse"])

    def install_android(self) -> None:
        self.stage_start("android_install")
        assert self.adb_path is not None
        if not self.args.skip_android_build:
            try:
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts" / "android.sh"), "assembleDebug", "assembleDebugAndroidTest"],
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=max(300.0, self.args.android_test_timeout),
                )
            except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as error:
                output = getattr(error, "output", None) or bounded_text(error)
                write_exclusive(self.args.evidence_dir / "android-build.log", output)
                self.abort("ANDROID_BUILD_FAILED", bounded_text(output), owner="Android packaging owner", next_action="preserve Gradle startup/timeout output and repair the Android build owner", stage="android_install")
            write_exclusive(self.args.evidence_dir / "android-build.log", result.stdout)
            if result.returncode != 0:
                self.abort("ANDROID_BUILD_FAILED", bounded_text(result.stdout), owner="Android packaging owner", next_action="preserve Gradle output and repair the Android build owner", stage="android_install")
        self.configure_adb_reverse("android_install")
        artifacts = {
            "com.agentbrowser.probe": ROOT / "apps" / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk",
            "com.agentbrowser.probe.test": ROOT / "apps" / "android" / "app" / "build" / "outputs" / "apk" / "androidTest" / "debug" / "app-debug-androidTest.apk",
        }
        if self.args.skip_android_build:
            self.verify_android_build_provenance(artifacts, "android_install")
        installed: dict[str, Any] = {}
        for package, artifact in artifacts.items():
            if not artifact.is_file():
                self.abort("ANDROID_ARTIFACT_MISSING", str(artifact), owner="Android packaging owner", next_action="build the main and instrumentation APKs from this candidate", stage="android_install")
            digest = file_sha256(artifact)
            code, output = self.adb(["install", "-r", str(artifact)], timeout=180.0)
            if code != 0:
                self.abort("ANDROID_INSTALL_FAILED", bounded_text(output), owner="Android environment owner", next_action="preserve adb install output and device identity", stage="android_install")
            path_code, path_output = self.adb(["shell", "pm", "path", package])
            paths = [line.strip()[8:] for line in path_output.decode(errors="replace").splitlines() if line.strip().startswith("package:")]
            if path_code != 0 or len(paths) != 1:
                self.abort("ANDROID_INSTALLED_PATH_INVALID", f"{package}: {paths}; {bounded_text(path_output)}", owner="Android environment owner", next_action="return exactly one installed APK path", stage="android_install")
            content_code, content = self.adb(["exec-out", "cat", paths[0]])
            installed_digest = hashlib.sha256(content).hexdigest() if content_code == 0 else None
            if digest is None or installed_digest != digest.removeprefix("sha256:"):
                self.abort("ANDROID_APK_IDENTITY_MISMATCH", f"{package}: artifact={digest} installed={installed_digest}", owner="Android packaging owner", next_action="install the APK produced by this candidate and preserve both hashes", stage="android_install")
            installed[package] = {"artifact": str(artifact), "artifact_sha256": digest, "installed_path": paths[0], "installed_sha256": installed_digest}
        self.android_install = {"serial": self.args.android_serial, "packages": installed, "observed_at": utc_now()}
        self._write_json("installed-apks.json", self.android_install)
        if self.fixture_root is None:
            self.abort("PAIRING_FIXTURE_MISSING", "fixture root unavailable before Android pairing", owner="combined runner", next_action="start the same fixture before installing pairing", stage="android_install")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "device-pairing.py"), "install", str(self.fixture_root)],
            cwd=str(ROOT),
            env=self.pairing_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
        write_exclusive(self.args.evidence_dir / "android-pairing-install.log", result.stdout)
        if result.returncode != 0:
            self.abort("ANDROID_PAIRING_INSTALL_FAILED", bounded_text(result.stdout), owner="Android pairing owner", next_action="preserve the absent-private-pairing failure and selected device", stage="android_install")
        self.pairing_installed = True
        self.stage_finish("android_install", "passed", self.android_install)

    def verify_android_build_provenance(self, artifacts: Mapping[str, pathlib.Path], stage: str) -> None:
        path = self.args.android_build_provenance
        if path is None:
            self.abort("ANDROID_BUILD_PROVENANCE_REQUIRED", "--skip-android-build requires an explicit current-candidate build provenance file", owner="Android packaging owner", next_action="provide provenance containing this candidate commit/tree and APK hashes", stage=stage)
        assert path is not None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            self.abort("ANDROID_BUILD_PROVENANCE_INVALID", f"{path}: {bounded_text(error)}", owner="Android packaging owner", next_action="provide readable JSON provenance from the current candidate build", stage=stage)
        if not isinstance(value, Mapping):
            self.abort("ANDROID_BUILD_PROVENANCE_INVALID", f"{path}: root is not an object", owner="Android packaging owner", next_action="emit structured candidate build provenance", stage=stage)
        candidate = self.evidence["candidate"]
        if value.get("candidate_commit") != candidate.get("commit") or value.get("candidate_tree") != candidate.get("tree"):
            self.abort("ANDROID_BUILD_PROVENANCE_MISMATCH", f"provenance candidate={value.get('candidate_commit')}/{value.get('candidate_tree')} current={candidate.get('commit')}/{candidate.get('tree')}", owner="Android packaging owner", next_action="build APKs from this exact candidate and regenerate provenance", stage=stage)
        recorded = value.get("artifacts")
        if not isinstance(recorded, Mapping):
            self.abort("ANDROID_BUILD_PROVENANCE_INVALID", f"{path}: artifacts is not an object", owner="Android packaging owner", next_action="record both APK artifact hashes", stage=stage)
        for package, artifact in artifacts.items():
            item = recorded.get(package)
            digest = file_sha256(artifact)
            if digest is None or not isinstance(item, Mapping) or item.get("path") != str(artifact) or item.get("sha256") != digest:
                self.abort("ANDROID_BUILD_PROVENANCE_MISMATCH", f"{package}: provenance={item!r} current={{'path': {str(artifact)!r}, 'sha256': {digest!r}}}", owner="Android packaging owner", next_action="use APKs and provenance generated by the same candidate build", stage=stage)
        self.evidence["android_build_provenance"] = {"path": str(path), "candidate_commit": value["candidate_commit"], "candidate_tree": value["candidate_tree"]}

    def _record_mac(self, operation: str, value: Any, stage: str) -> dict[str, Any]:
        if is_rejection(value):
            self.abort("MAC_COMMAND_REJECTED", f"{operation}: {json_text(value)}", owner="AgentBrowser Mac bridge owner", next_action="preserve the typed bridge rejection", stage=stage)
        if not isinstance(value, Mapping):
            self.abort("MAC_SNAPSHOT_INVALID", f"{operation}: {value!r}", owner="AgentBrowser Mac bridge owner", next_action="return a structured bridge snapshot", stage=stage)
        snapshot = {"operation": operation, "observed_at": utc_now(), "value": dict(value)}
        self.mac_snapshots.append(snapshot)
        self.evidence["events"]["mac"].append(snapshot)
        if value.get("source") == "relay":
            self.abort("RELAY_PATH_OBSERVED", f"{operation} selected relay", owner="AgentBrowser connection owner", next_action="preserve relay path evidence; local combined replay cannot pass", stage=stage)
        return dict(value)

    def mac_command(self, operation: str, command: Mapping[str, Any], stage: str) -> dict[str, Any]:
        if self.bridge is None:
            self.abort("MAC_BRIDGE_UNAVAILABLE", f"cannot send {operation}", owner="AgentBrowser Mac bridge owner", next_action="start the selected Mac bridge executable", stage=stage)
        try:
            value = self.bridge.command_response(command, self.args.timeout)
        except ProcessProtocolError as error:
            self.abort(error.code, error.message, owner="AgentBrowser Mac bridge owner", next_action="preserve bridge framing and stderr", stage=stage)
        return self._record_mac(operation, value, stage)

    def wait_mac(self, predicate: Callable[[Mapping[str, Any]], bool], label: str, stage: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.args.timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.mac_command(f"status:{label}", {"op": "status"}, stage)
            if predicate(last):
                return last
            if last.get("connectionState") == "error":
                self.abort("MAC_CONNECTION_FAILED", json_text(last), owner="AgentBrowser connection owner", next_action="preserve Mac bridge error and pairing evidence", stage=stage)
            time.sleep(min(self.args.poll, max(0.0, deadline - time.monotonic())))
        self.abort("MAC_STATUS_TIMEOUT", f"{label}: {last}", owner="AgentBrowser Mac bridge owner", next_action="preserve the last bridge snapshot and endpoint state", stage=stage)
        return {}

    def start_mac(self) -> None:
        self.stage_start("mac_connect")
        assert self.args.mac_bridge_bin is not None and self.fixture_root is not None
        env = dict(os.environ)
        env.update(
            {
                "AGENTBROWSER_MAC_PAIRING": str(self.fixture_root),
                "AGENTBROWSER_M1_DUAL_RUN_ID": self.args.run_id,
                "AGENTBROWSER_M1_DUAL_EVIDENCE_DIR": str(self.args.evidence_dir),
            }
        )
        command = [str(self.args.mac_bridge_bin), *self.args.mac_bridge_args]
        try:
            self.bridge = BridgeProcess(command, self.worktree, env)
        except OSError as error:
            self.abort("MAC_BRIDGE_START_FAILED", bounded_text(error), owner="AgentBrowser Mac bridge owner", next_action="start the exact Mac bridge executable", stage="mac_connect")
        assert self.bridge is not None
        self.evidence["processes"]["mac"] = {"pid": self.bridge.pid, "command": self.bridge.command}
        initial = self.mac_command("connect", {"op": "connect"}, "mac_connect")
        connected = initial if initial.get("connectionState") == "connected" else self.wait_mac(lambda value: value.get("connectionState") == "connected", "connect", "mac_connect")
        if connected.get("source") != "network":
            self.abort("MAC_LOCAL_PATH_MISSING", json_text(connected), owner="AgentBrowser connection owner", next_action="require the local WSS network source", stage="mac_connect")
        session = connected.get("sessionId")
        if not isinstance(session, str) or not session:
            self.abort("MAC_SESSION_ID_MISSING", json_text(connected), owner="AgentBrowser Mac bridge owner", next_action="return the Host Session ID in the bridge snapshot", stage="mac_connect")
        self.mac_command("observe", {"op": "observe"}, "mac_connect")
        displayed = self.wait_mac(lambda value: value.get("renderedFrames", 0) > 0 and bool(self.bridge and self.bridge.acked_tickets), "first_frame", "mac_connect")
        self.record_host_status("mac_observe", "mac_connect")
        self.stage_finish("mac_connect", "passed", {"session_id": session, "displayed": displayed, "acks": len(self.bridge.acked_tickets) if self.bridge else 0})

    def sample_clients_during_android(self) -> None:
        if self.bridge is not None and self.bridge.proc.poll() is None:
            try:
                self.mac_command("android_running", {"op": "status"}, "android_replay")
            except RunnerAbort:
                raise
        self.record_host_status("android_running", "android_replay")

    def run_android(self) -> None:
        self.stage_start("android_replay")
        assert self.adb_path is not None and self.fixture is not None
        fixture = self.evidence["fixture"]["ready"]
        initial_url = fixture.get("initialUrl") if isinstance(fixture, Mapping) else None
        if not isinstance(initial_url, str) or not initial_url:
            self.abort("ANDROID_INITIAL_URL_MISSING", "fixture ready record has no initialUrl", owner="AgentBrowser fixture owner", next_action="emit the exact initial URL for instrumentation", stage="android_replay")
        encoded = base64.b64encode(initial_url.encode()).decode()
        command = [
            str(self.adb_path),
            "-s",
            self.args.android_serial,
            "shell",
            "am",
            "instrument",
            "-w",
            "-r",
            "-e",
            "runId",
            self.args.run_id,
            "-e",
            "initialUrlBase64",
            encoded,
            "-e",
            "class",
            self.args.android_test_class,
            self.args.android_test_component,
        ]
        log_path = self.args.evidence_dir / "android-instrumentation.log"
        self.android_log_handle = log_path.open("wb")
        try:
            self.android_process = subprocess.Popen(command, stdout=self.android_log_handle, stderr=subprocess.STDOUT, cwd=str(ROOT))
        except OSError as error:
            self.android_log_handle.close()
            self.android_log_handle = None
            self.abort("ANDROID_INSTRUMENTATION_START_FAILED", bounded_text(error), owner="Android environment owner", next_action="start the selected instrumentation component", stage="android_replay")
        self.evidence["processes"]["android"] = {"pid": self.android_process.pid, "command": command, "log": str(log_path)}
        deadline = time.monotonic() + self.args.android_test_timeout
        next_sample = time.monotonic()
        while self.android_process.poll() is None:
            now = time.monotonic()
            if now >= deadline:
                self.abort("ANDROID_INSTRUMENTATION_TIMEOUT", f"deadline exceeded: {self.args.android_test_timeout}s", owner="Android environment owner", next_action="preserve the instrumentation log and device state", stage="android_replay")
            if now >= next_sample:
                self.sample_clients_during_android()
                next_sample = now + self.args.poll
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        code = self.android_process.returncode
        self.android_log_handle.close()
        self.android_log_handle = None
        output = log_path.read_bytes()
        if code != 0 or b"OK (1 test)" not in output:
            self.abort("ANDROID_INSTRUMENTATION_FAILED", f"exit={code}; output={bounded_text(output)}", owner="Android client owner", next_action="preserve Android instrumentation output and first failing test", stage="android_replay")
        result_code, result_bytes = self.adb(["exec-out", "run-as", "com.agentbrowser.probe", "cat", "files/network-evidence/result.json"])
        if result_code != 0:
            self.abort("ANDROID_RESULT_MISSING", bounded_text(result_bytes), owner="Android client owner", next_action="emit this run's files/network-evidence/result.json", stage="android_replay")
        try:
            result = json.loads(result_bytes)
        except json.JSONDecodeError as error:
            self.abort("ANDROID_RESULT_INVALID", f"{error}: {bounded_text(result_bytes)}", owner="Android client owner", next_action="emit structured JSON evidence for the instrumentation run", stage="android_replay")
        if not isinstance(result, Mapping) or result.get("runId") != self.args.run_id:
            self.abort("ANDROID_RESULT_RUN_MISMATCH", f"expected {self.args.run_id}, got {result!r}", owner="Android client owner", next_action="bind the pulled result to this runner run ID", stage="android_replay")
        self.android_result = dict(result)
        self._write_json("android-result.json", self.android_result)
        self.evidence["events"]["android"].append({"event": "instrumentation_result", "observed_at": utc_now(), "value": self.android_result})
        self.record_host_status("android_complete", "android_replay")
        self.stage_finish("android_replay", "passed", {"exit_code": code, "result": str(self.args.evidence_dir / "android-result.json")})

    def mac_control(self) -> None:
        self.stage_start("mac_control")
        before = self.mac_command("status_before_takeover", {"op": "status"}, "mac_control")
        epoch = before.get("epoch")
        if not isinstance(epoch, int):
            self.abort("MAC_CONTROL_EPOCH_MISSING", json_text(before), owner="Obscura Host control owner", next_action="return the typed control epoch before takeover", stage="mac_control")
        takeover = self.mac_command("takeover", {"op": "takeover", "epoch": epoch}, "mac_control")
        self.mac_operations.append({"operation": "takeover", "before": before, "after": takeover})
        if takeover.get("connectionState") != "connected" or takeover.get("controlMode") != "control":
            self.abort("MAC_TAKEOVER_FAILED", json_text(takeover), owner="Obscura Host control owner", next_action="preserve the typed takeover result and Host status", stage="mac_control")
        self.record_host_status("mac_takeover", "mac_control")
        granted_epoch = takeover.get("epoch")
        if not isinstance(granted_epoch, int):
            self.abort("MAC_GRANTED_EPOCH_MISSING", json_text(takeover), owner="Obscura Host control owner", next_action="return the new control epoch after takeover", stage="mac_control")
        release = self.mac_command("release", {"op": "release", "epoch": granted_epoch}, "mac_control")
        self.mac_operations.append({"operation": "release", "before": takeover, "after": release})
        if release.get("connectionState") != "connected" or release.get("controlMode") not in {"observe", "waiting"}:
            self.abort("MAC_RELEASE_FAILED", json_text(release), owner="Obscura Host control owner", next_action="preserve the typed release result and Host status", stage="mac_control")
        self.record_host_status("mac_release", "mac_control")
        self.stage_finish("mac_control", "passed", {"operations": self.mac_operations})

    def mac_reconnect(self) -> None:
        self.stage_start("mac_reconnect")
        before_frames = len(self.bridge.frames) if self.bridge is not None else 0
        before_acks = len(self.bridge.acked_tickets) if self.bridge is not None else 0
        disconnected = self.mac_command("disconnect", {"op": "disconnect"}, "mac_reconnect")
        if disconnected.get("connectionState") != "stopped":
            self.abort("MAC_DISCONNECT_STATE_INVALID", json_text(disconnected), owner="AgentBrowser Mac connection owner", next_action="return stopped after disconnect", stage="mac_reconnect")
        self.record_host_status("mac_disconnected", "mac_reconnect")
        self.stage_finish("mac_reconnect", "passed", {"disconnect": disconnected})
        reconnected = self.mac_command("reconnect", {"op": "connect"}, "mac_reconnect")
        if reconnected.get("connectionState") != "connected":
            reconnected = self.wait_mac(lambda value: value.get("connectionState") == "connected", "reconnect", "mac_reconnect")
        self.wait_mac(
            lambda value: value.get("renderedFrames", 0) > 0
            and (len(self.bridge.frames) if self.bridge is not None else 0) > before_frames
            and (len(self.bridge.acked_tickets) if self.bridge is not None else 0) > before_acks,
            "reconnect_frame",
            "mac_reconnect",
        )
        self.record_host_status("mac_reconnected", "mac_reconnect")
        final = self.mac_command("final_disconnect", {"op": "disconnect"}, "mac_reconnect")
        if final.get("connectionState") != "stopped":
            self.abort("MAC_FINAL_DISCONNECT_INVALID", json_text(final), owner="AgentBrowser Mac connection owner", next_action="leave the bridge stopped before cleanup", stage="mac_reconnect")
        self.stage_finish("mac_reconnect", "passed", {"reconnect": reconnected, "frames": len(self.bridge.frames) if self.bridge else 0, "acks": len(self.bridge.acked_tickets) if self.bridge else 0})

    def build_side_evidence(self) -> None:
        host = self.host_statuses[-1]["value"] if self.host_statuses else None
        if not isinstance(host, Mapping):
            host_side = {field: unknown("Host status was not observed") for field in ("session_id", "attachment", "viewport", "source_dimensions", "viewport_revision", "document_revision", "control_epoch", "frame_ack")}
        else:
            phase = host.get("control", {}).get("phase") if isinstance(host.get("control"), Mapping) else None
            host_session = host.get("session_id")
            host_viewport = dimension_evidence(
                host.get("viewport"),
                ["fixture status"],
                "Host SessionStatus omitted a positive integer viewport pair",
            )
            host_side = {
                "session_id": known(host_session, ["fixture status"]) if isinstance(host_session, str) and host_session else unknown("Host SessionStatus omitted session_id"),
                "attachment": known({"count": host.get("attachments"), "local_attachment_id": host.get("attachment_id"), "control_phase": phase}, ["fixture status"]),
                "viewport": host_viewport,
                "source_dimensions": unknown("Host SessionStatus has viewport but no encoded source dimensions"),
                "viewport_revision": integer_evidence(host.get("viewport_revision"), ["fixture status"], "Host SessionStatus omitted viewport_revision"),
                "document_revision": integer_evidence(host.get("document_revision"), ["fixture status"], "Host SessionStatus omitted document_revision"),
                "control_epoch": integer_evidence(host.get("control", {}).get("epoch") if isinstance(host.get("control"), Mapping) else None, ["fixture status"], "Host SessionStatus omitted control epoch"),
                "frame_ack": unknown("Host status does not own client display acknowledgements"),
            }
        android = self.android_result or {}
        viewport = android.get("viewport") if isinstance(android, Mapping) else None
        if isinstance(viewport, Mapping):
            css = [viewport.get("cssWidth"), viewport.get("cssHeight")]
            source = [viewport.get("sourceWidth"), viewport.get("sourceHeight")]
            android_viewport = dimension_evidence(css, ["android-result.json:viewport"], "Android result omitted positive CSS dimensions")
            android_source = dimension_evidence(source, ["android-result.json:viewport"], "Android result omitted positive source dimensions")
        else:
            android_viewport = unknown("Android result omitted viewport dimensions")
            android_source = unknown("Android result omitted source dimensions")
        android_session = android.get("sessionId") if isinstance(android, Mapping) else None
        android_side = {
            "session_id": known(android_session, ["android-result.json:sessionId"]) if isinstance(android_session, str) and android_session else unknown("Android result omitted sessionId"),
            "attachment": unknown("Android instrumentation result does not persist attachment_id"),
            "viewport": android_viewport,
            "source_dimensions": android_source,
            "viewport_revision": unknown("Android instrumentation result does not persist viewport revision"),
            "document_revision": unknown("Android instrumentation result does not persist document revision"),
            "control_epoch": unknown("Android instrumentation result does not persist control epoch"),
            "frame_ack": unknown("Android instrumentation result does not export frame ACK tickets"),
            "actions": {
                "observe": unknown("NetworkDeviceTest does not emit per-operation receipts"),
                "takeover": unknown("NetworkDeviceTest does not emit per-operation receipts"),
                "release": unknown("NetworkDeviceTest does not emit per-operation receipts"),
                "rotation": boolean_evidence(android.get("rotationPreservesState"), ["android-result.json:rotationPreservesState"], "Android result omitted rotationPreservesState"),
                "disconnect": boolean_evidence(android.get("backgroundRelease"), ["android-result.json:backgroundRelease"], "Android result omitted backgroundRelease"),
                "reconnect": boolean_evidence(android.get("reconnectPreservesDocument"), ["android-result.json:reconnectPreservesDocument"], "Android result omitted reconnectPreservesDocument"),
            },
        }
        frames = acknowledged_active_frames(self.bridge)
        last_frame = frames[-1] if frames else None
        if isinstance(last_frame, Mapping):
            mac_viewport = dimension_evidence(
                [last_frame.get("visible_width"), last_frame.get("visible_height")],
                ["Mac bridge frame header"],
                "Mac bridge frame header omitted positive visible dimensions",
            )
            mac_source = dimension_evidence(
                [last_frame.get("coded_width"), last_frame.get("coded_height")],
                ["Mac bridge frame header"],
                "Mac bridge frame header omitted positive coded dimensions",
            )
            mac_session = last_frame.get("session_id")
            mac_vr = last_frame.get("viewport_revision")
            mac_dr = last_frame.get("document_revision")
        else:
            mac_viewport = unknown("Mac bridge emitted no acknowledged active-generation frame header")
            mac_source = unknown("Mac bridge emitted no acknowledged active-generation frame header")
            mac_session = None
            mac_vr = None
            mac_dr = None
        mac_side = {
            "session_id": known(mac_session, ["Mac bridge acknowledged frame header"]) if isinstance(mac_session, str) and mac_session else unknown("Mac bridge acknowledged active-generation frame omitted sessionId"),
            "attachment": unknown("Mac bridge snapshot intentionally omits numeric attachment_id; control ownership is exposed as controlMode"),
            "viewport": mac_viewport,
            "source_dimensions": mac_source,
            "viewport_revision": integer_evidence(mac_vr, ["Mac bridge acknowledged frame header"], "Mac bridge acknowledged active-generation frame emitted no viewport revision"),
            "document_revision": integer_evidence(mac_dr, ["Mac bridge acknowledged frame header"], "Mac bridge acknowledged active-generation frame emitted no document revision"),
            "control_epoch": unknown("Mac bridge acknowledged frame header does not emit control epoch"),
            "frame_ack": known({"count": len(frames), "tickets": sorted({(frame.get("generation"), frame.get("ticket")) for frame in frames})}, ["Mac bridge acknowledged active-generation frames"]) if frames else unknown("Mac bridge emitted no acknowledged active-generation frame"),
            "actions": {
                "observe": known(True, ["Mac bridge observe command and connected observer snapshot"]),
                "takeover": known(any(item.get("operation") == "takeover" for item in self.mac_operations), ["Mac bridge takeover response"]),
                "release": known(any(item.get("operation") == "release" for item in self.mac_operations), ["Mac bridge release response"]),
                "rotation": unknown("Mac bridge has no rotation command; frame size changes have no orientation identity"),
                "disconnect": boolean_evidence(
                    True if self.evidence["stages"]["mac_reconnect"]["result"] == "passed" else None,
                    ["Mac bridge disconnect response"],
                    "Mac bridge disconnect was not observed",
                ),
                "reconnect": boolean_evidence(
                    True if self.evidence["stages"]["mac_reconnect"]["result"] == "passed" else None,
                    ["Mac bridge reconnect frame and ACK"],
                    "Mac bridge reconnect was not observed",
                ),
            },
            "frame_headers": frames,
        }
        self.evidence["sides"] = {"host": host_side, "android": android_side, "mac": mac_side}
        self.evidence["cross_side"] = {
            field: compare_field(field, self.evidence["sides"])
            for field in ("session_id", "attachment", "viewport", "source_dimensions", "viewport_revision", "document_revision", "control_epoch", "frame_ack")
        }
        session_claim = self.evidence["cross_side"]["session_id"]
        reconnect_items = {
            "session_id": session_claim,
            "android_disconnect": android_side["actions"]["disconnect"],
            "android_reconnect": android_side["actions"]["reconnect"],
            "mac_disconnect": mac_side["actions"]["disconnect"],
            "mac_reconnect": mac_side["actions"]["reconnect"],
        }
        reconnect_status = combined_status(reconnect_items)
        self.evidence["required_claims"] = {
            "same_session": session_claim,
            "viewport_and_source_dimensions": {
                "status": combined_status(
                    {
                        "viewport": self.evidence["cross_side"]["viewport"],
                        "source_dimensions": self.evidence["cross_side"]["source_dimensions"],
                    }
                ),
                "viewport": self.evidence["cross_side"]["viewport"],
                "source_dimensions": self.evidence["cross_side"]["source_dimensions"],
                "reason": "both viewport and source dimensions must be proved on Host, Android and Mac",
            },
            "three_side_source_dimensions": self.evidence["cross_side"]["source_dimensions"],
            "three_side_viewport_revision": self.evidence["cross_side"]["viewport_revision"],
            "three_side_document_revision": self.evidence["cross_side"]["document_revision"],
            "three_side_control_epoch": self.evidence["cross_side"]["control_epoch"],
            "three_side_frame_ack": self.evidence["cross_side"]["frame_ack"],
            "mac_observe_takeover_release": {
                "status": "proved" if all(mac_side["actions"][name].get("value") is True for name in ("observe", "takeover", "release")) else "unknown",
                "reason": "Mac bridge typed operation receipts were recorded" if {item.get("operation") for item in self.mac_operations} >= {"takeover", "release"} else "Mac control receipts incomplete",
            },
            "android_observe_takeover_release": {"status": "unknown", "reason": "Android instrumentation does not export per-operation receipts"},
            "rotation": {
                "status": combined_status({"android": android_side["actions"]["rotation"], "mac": mac_side["actions"]["rotation"]}),
                "android": android_side["actions"]["rotation"],
                "mac": mac_side["actions"]["rotation"],
            },
            "disconnect_reconnect": {"status": reconnect_status, "android_disconnect": android_side["actions"]["disconnect"], "android_reconnect": android_side["actions"]["reconnect"], "mac_disconnect": mac_side["actions"]["disconnect"], "mac_reconnect": mac_side["actions"]["reconnect"]},
        }
        self.evidence["unknown"].extend(
            {"item": f"cross_side.{name}", "reason": value.get("reason", "claim incomplete")}
            for name, value in self.evidence["cross_side"].items()
            if value.get("status") == "unknown"
        )
        required_for_pass = self.evidence["required_claims"]
        self.evidence["result"] = classify_result(self.first_failure, required_for_pass)
        self._write_json("correlation.json", {"sides": self.evidence["sides"], "cross_side": self.evidence["cross_side"], "required_claims": self.evidence["required_claims"], "result": self.evidence["result"]})

    def dry_run(self) -> int:
        self.stage_start("preflight")
        if not self._refresh_candidate_git("before"):
            self.fail(
                "GIT_PROBE_FAILED",
                f"candidate Git probe failed: {self.evidence['candidate'].get('git_probes_before')}",
                owner="workspace Git boundary",
                next_action="restore Git access and rerun from this candidate worktree",
                stage="preflight",
            )
        self.evidence["environment"] = environment_identity(self.args.android_serial, probe=False)
        for claim in ("same_session", "viewport", "source_dimensions", "viewport_revision", "document_revision", "control_epoch", "frame_ack", "observe", "takeover", "release", "rotation", "disconnect", "reconnect", "disconnect_reconnect"):
            self.add_unknown(claim, "dry-run does not start fixture or clients")
        self.evidence["result"] = "dry_run" if self.first_failure is None else "failed"
        if self.first_failure is None:
            self.stage_finish("preflight", "passed", {"reason": "dry-run Git validation"})
        self.stage_finish("correlation", "skipped", {"reason": "dry-run"})
        if not self._refresh_candidate_git("after"):
            self.fail("GIT_PROBE_FAILED", "git probe failed while preparing dry-run evidence", owner="workspace Git boundary", next_action="restore Git access and rerun", stage="preflight")
            self.evidence["result"] = "failed"
        self.evidence["finished_at"] = utc_now()
        self.evidence["cleanup"] = {"fixture": {"started": False}, "mac": {"started": False}, "android": {"started": False}, "pairing": {"started": False}, "fixture_root": {"path": None, "exists": False}}
        self._write_json("evidence.json", self.evidence)
        print(json_text({"result": self.evidence["result"], "evidence": str(self.args.evidence_dir / "evidence.json")}))
        return 0 if self.evidence["result"] == "dry_run" else 2

    def _terminate(self, process: Any, label: str) -> dict[str, Any]:
        if process is None:
            return {"started": False}
        child = process.proc if hasattr(process, "proc") else process
        result: dict[str, Any] = {"started": True, "pid": child.pid}
        if child.poll() is not None:
            result.update({"result": "already_exited", "exit_code": child.returncode})
            return result
        try:
            child.terminate()
            child.wait(timeout=8)
            result.update({"result": "terminated", "exit_code": child.returncode})
        except (OSError, subprocess.TimeoutExpired) as error:
            result.update({"result": "termination_failed", "error": bounded_text(error)})
        return result

    def cleanup(self) -> None:
        self.stage_start("cleanup")
        if self.android_process is not None and self.android_process.poll() is None:
            self.evidence["cleanup"]["android"] = self._terminate(self.android_process, "android")
        else:
            self.evidence["cleanup"]["android"] = {"started": self.android_process is not None, "result": "already_exited" if self.android_process is not None else "not_started"}
        if self.evidence["cleanup"]["android"].get("result") == "termination_failed":
            self.fail("ANDROID_TERMINATION_FAILED", json_text(self.evidence["cleanup"]["android"]), owner="Android environment owner", next_action="terminate only the recorded instrumentation process and preserve its device state", stage="cleanup")
        if self.android_log_handle is not None:
            self.android_log_handle.close()
            self.android_log_handle = None
        if self.pairing_installed and self.fixture_root is not None:
            pairing_log = self.args.evidence_dir / "android-pairing-remove.log"
            try:
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "device-pairing.py"), "remove", str(self.fixture_root)],
                    cwd=str(ROOT),
                    env=self.pairing_environment(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=30,
                )
                write_exclusive(pairing_log, result.stdout)
                self.evidence["cleanup"]["pairing"] = {"started": True, "returncode": result.returncode, "log": str(pairing_log)}
                if result.returncode != 0:
                    self.fail("ANDROID_PAIRING_REMOVE_FAILED", bounded_text(result.stdout), owner="Android pairing owner", next_action="preserve the owner marker and remove only this fixture pairing", stage="cleanup")
            except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
                self.evidence["cleanup"]["pairing"] = {"started": True, "error": bounded_text(error), "log": str(pairing_log)}
                self.fail("ANDROID_PAIRING_REMOVE_FAILED", bounded_text(error), owner="Android pairing owner", next_action="preserve the owner marker and remove only this fixture pairing", stage="cleanup")
        else:
            self.evidence["cleanup"]["pairing"] = {"started": False}
        if self.adb_reverse_owned is None:
            self.evidence["cleanup"]["adb_reverse"] = {"started": False}
        else:
            port = self.adb_reverse_owned["port"]
            serial = self.adb_reverse_owned["serial"]
            expected_mapping = self.adb_reverse_owned["mapping"]
            list_code, list_output = self.adb(["reverse", "--list"])
            device_rows = [
                line
                for line in list_output.decode(errors="replace").splitlines()
                if len(line.split()) == 3 and line.split()[0] == serial and line.split()[1] == f"tcp:{port}"
            ]
            exact_rows = adb_reverse_mappings(list_output, port, serial)
            reverse_cleanup: dict[str, Any] = {
                "started": True,
                "serial": serial,
                "host_port": port,
                "device_port": port,
                "expected_mapping": expected_mapping,
                "before_remove": device_rows,
            }
            if list_code != 0:
                reverse_cleanup.update({"result": "list_failed", "output": bounded_text(list_output)})
                self.fail(
                    "ADB_REVERSE_REMOVE_FAILED",
                    bounded_text(list_output),
                    owner="Android environment owner",
                    next_action="preserve adb reverse listing output and remove only the recorded mapping",
                    stage="cleanup",
                )
            elif not device_rows:
                reverse_cleanup["result"] = "already_absent"
            elif len(exact_rows) != 1 or len(device_rows) != 1:
                reverse_cleanup.update({"result": "unexpected_mapping", "output": bounded_text(list_output)})
                self.fail(
                    "ADB_REVERSE_REMOVE_FAILED",
                    f"recorded {serial} tcp:{port} mapping changed before cleanup: {device_rows}",
                    owner="Android environment owner",
                    next_action="preserve the changed reverse mapping and let its resource owner remove it",
                    stage="cleanup",
                )
            else:
                remove_code, remove_output = self.adb(["reverse", "--remove", f"tcp:{port}"])
                reverse_cleanup["remove_output"] = bounded_text(remove_output)
                if remove_code != 0:
                    reverse_cleanup["result"] = "remove_failed"
                    self.fail(
                        "ADB_REVERSE_REMOVE_FAILED",
                        bounded_text(remove_output),
                        owner="Android environment owner",
                        next_action="preserve adb reverse removal output and remove only the recorded mapping",
                        stage="cleanup",
                    )
                else:
                    after_code, after_output = self.adb(["reverse", "--list"])
                    after_rows = [
                        line
                        for line in after_output.decode(errors="replace").splitlines()
                        if len(line.split()) == 3 and line.split()[0] == serial and line.split()[1] == f"tcp:{port}"
                    ]
                    reverse_cleanup.update({"result": "removed" if after_code == 0 and not after_rows else "removal_unconfirmed", "after_remove": after_rows, "after_output": bounded_text(after_output)})
                    if after_code != 0 or after_rows:
                        self.fail(
                            "ADB_REVERSE_REMOVE_FAILED",
                            f"reverse mapping remains after removal: {after_rows}; {bounded_text(after_output)}",
                            owner="Android environment owner",
                            next_action="preserve adb reverse listing output and remove only the recorded mapping",
                            stage="cleanup",
                        )
            self.evidence["cleanup"]["adb_reverse"] = reverse_cleanup
        self.evidence["cleanup"]["mac"] = self._terminate(self.bridge, "mac") if self.bridge is not None else {"started": False}
        if self.evidence["cleanup"]["mac"].get("result") == "termination_failed":
            self.fail("MAC_TERMINATION_FAILED", json_text(self.evidence["cleanup"]["mac"]), owner="AgentBrowser Mac bridge owner", next_action="terminate only the recorded Mac bridge process after identity verification", stage="cleanup")
        if self.fixture is not None:
            quit_result: dict[str, Any] = {"started": True, "pid": self.fixture.pid}
            exit_code: Optional[int] = None
            if self.fixture.proc.poll() is None:
                try:
                    self.fixture.send_line("quit")
                    exit_code = self.fixture.wait_exit(10.0)
                    quit_result.update({"quit_sent": True, "exit_code": exit_code, "graceful": exit_code == 0})
                except ProcessProtocolError as error:
                    quit_result.update({"quit_sent": False, "error": {"code": error.code, "message": error.message}})
            else:
                exit_code = self.fixture.proc.returncode
                quit_result.update({"exit_code": exit_code, "graceful": exit_code == 0})
            self.evidence["cleanup"]["fixture"] = quit_result
            if exit_code is None:
                self.fail("FIXTURE_SHUTDOWN_TIMEOUT", json_text(quit_result), owner="AgentBrowser fixture owner", next_action="complete graceful fixture quit and preserve its child process state", stage="cleanup")
            elif exit_code != 0:
                self.fail("FIXTURE_EXIT_NONZERO", json_text(quit_result), owner="AgentBrowser fixture owner", next_action="inspect fixture stderr and repair its graceful shutdown", stage="cleanup")
        else:
            self.evidence["cleanup"]["fixture"] = {"started": False}
        if self.fixture_root is None:
            self.evidence["cleanup"]["fixture_root"] = {"path": None, "exists": False}
        else:
            self.evidence["cleanup"]["fixture_root"] = {"path": str(self.fixture_root), "exists": self.fixture_root.exists()}
            if self.fixture_root.exists() and self.first_failure is None:
                self.fail("FIXTURE_ROOT_NOT_CLEANED", str(self.fixture_root), owner="AgentBrowser fixture owner", next_action="repair fixture graceful cleanup; do not delete the root from this runner", stage="cleanup")
        if self.first_failure is None and self.evidence["cleanup"]["fixture_root"].get("exists") is False:
            self.stage_finish("cleanup", "passed", self.evidence["cleanup"])
        elif self.evidence["stages"]["cleanup"]["result"] == "running":
            self.stage_finish("cleanup", "completed_with_prior_failure", self.evidence["cleanup"])

    def execute(self) -> int:
        try:
            self.preflight()
            self.start_fixture()
            self.install_android()
            self.start_mac()
            self.run_android()
            self.mac_control()
            self.mac_reconnect()
        except RunnerAbort:
            pass
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            self.fail("RUNNER_UNEXPECTED_ERROR", bounded_text(error), owner="combined runner", next_action="preserve the first exception and child outputs", stage=next((name for name in STAGES if self.evidence["stages"][name]["result"] == "running"), None))
        finally:
            if not self.args.dry_run:
                try:
                    self.build_side_evidence()
                except Exception as error:
                    self.fail(
                        "CORRELATION_BUILD_FAILED",
                        bounded_text(error),
                        owner="combined runner",
                        next_action="preserve the side evidence and repair correlation output generation",
                        stage="correlation",
                    )
                try:
                    self.cleanup()
                except Exception as error:
                    self.fail(
                        "CLEANUP_FAILED",
                        bounded_text(error),
                        owner="combined runner resource owner",
                        next_action="preserve child identities and complete targeted cleanup for this run",
                        stage="cleanup",
                    )
                status_after = git_value(self.worktree, "status", "--porcelain=v1", "--untracked-files=all")
                if status_after is None:
                    self.fail("GIT_PROBE_FAILED", "git status failed after cleanup", owner="workspace Git boundary", next_action="restore Git access and rerun", stage="cleanup")
                    self.evidence["candidate"]["status_after"] = None
                else:
                    self.evidence["candidate"]["status_after"] = status_after.splitlines()
                self.evidence["finished_at"] = utc_now()
                self.evidence["process_logs"] = {
                    label: [{"stream": stream, "line": line} for stream, line in process.logs]
                    for label, process in (("fixture", self.fixture), ("mac", self.bridge))
                    if process is not None
                }
                if self.first_failure is not None:
                    self.evidence["result"] = "failed"
                elif self.evidence["result"] == "pending":
                    self.evidence["result"] = "partial"
                self._write_json("evidence.json", self.evidence)
        if self.args.dry_run:
            return self.dry_run()
        print(json_text({"result": self.evidence["result"], "evidence": str(self.args.evidence_dir / "evidence.json"), "first_failure": self.first_failure}))
        return 0 if self.evidence["result"] == "pass" else 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.print_schema:
        if arguments.dry_run:
            print("m1-dual-client-runner: --print-schema and --dry-run are mutually exclusive", file=sys.stderr)
            return 2
        print(json.dumps(schema_document(), ensure_ascii=False, indent=2))
        return 0
    try:
        args = build_arguments(arguments)
        runner = CombinedRunner(args)
    except (ValueError, OSError) as error:
        print(f"m1-dual-client-runner: {error}", file=sys.stderr)
        return 2
    if args.dry_run:
        return runner.dry_run()
    return runner.execute()


if __name__ == "__main__":
    raise SystemExit(main())
