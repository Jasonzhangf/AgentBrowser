#!/usr/bin/env python3
"""Replay the local AgentBrowser -> Obscura direct path.

This runner is an evidence harness.  It deliberately does not implement the
Browser ABI, start a relay, or manufacture a successful result when one of
the real entry points is absent.  The fixture and the client executable are
provided by the candidate under test (or by an explicitly selected external
artifact).
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import hashlib
import ipaddress
import json
import os
import pathlib
import selectors
import signal
import stat
import subprocess
import sys
import time
import urllib.parse
import uuid
from typing import Any, Deque, Iterable, Mapping, Optional, Sequence


STAGES = (
    "preflight",
    "fixture_start",
    "socket_validation",
    "agent_start",
    "ui_driver",
    "connected",
    "video_displayed",
    "atomic_operations",
    "disconnect",
    "reconnect",
    "cleanup",
)

MAX_LOG_LINES = 240
MAX_LOG_LINE_BYTES = 16_384
MAX_BRIDGE_HEADER_BYTES = 1_048_576
MAX_BRIDGE_FRAME_BYTES = 4 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_POLL_SECONDS = 0.2
RELEASE_INSPECT_SETTLE_SECONDS = 0.05
DEFAULT_TEXT = "你好，Mac 输入"
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


class ReplayAbort(Exception):
    """Internal control flow after the first structured failure."""


class ProcessProtocolError(Exception):
    """A child process produced an invalid or incomplete transport record."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bounded_text(value: Any, limit: int = MAX_LOG_LINE_BYTES) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text.encode("utf-8", errors="replace")) <= limit:
        return text
    raw = text.encode("utf-8", errors="replace")[:limit]
    return raw.decode("utf-8", errors="ignore") + "…[truncated]"


def command_result(command: Sequence[str], cwd: pathlib.Path, timeout: float = 10.0) -> tuple[int, str]:
    """Run a read-only helper without invoking a shell."""

    try:
        result = subprocess.run(
            list(command),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, bounded_text(error)
    return result.returncode, bounded_text(result.stdout)


def git_value(cwd: pathlib.Path, *args: str) -> Optional[str]:
    code, output = command_result(("git", *args), cwd)
    if code != 0:
        return None
    value = output.strip()
    return value or None


def file_sha256(path: pathlib.Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"
    except OSError:
        return None


def artifact_identity(path: Optional[pathlib.Path]) -> dict[str, Any]:
    if path is None:
        return {"path": None, "exists": False, "executable": False}
    resolved = path.expanduser().resolve(strict=False)
    try:
        metadata = resolved.stat()
    except OSError as error:
        return {
            "path": str(resolved),
            "exists": False,
            "executable": False,
            "error": bounded_text(error),
        }
    return {
        "path": str(resolved),
        "exists": True,
        "regular_file": stat.S_ISREG(metadata.st_mode),
        "executable": os.access(resolved, os.X_OK),
        "size": metadata.st_size,
        "mode": oct(stat.S_IMODE(metadata.st_mode)),
        "sha256": file_sha256(resolved),
    }


def executable_file(path: Optional[pathlib.Path]) -> bool:
    if path is None:
        return False
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def process_rows() -> list[dict[str, Any]]:
    code, output = command_result(("ps", "-axo", "pid=,ppid=,command="), pathlib.Path.cwd())
    if code != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        value = line.strip()
        parts = value.split(None, 2)
        if len(parts) != 3:
            continue
        try:
            rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "command": parts[2]})
        except ValueError:
            continue
    return rows


def process_row(pid: int) -> Optional[dict[str, Any]]:
    return next((row for row in process_rows() if row["pid"] == pid), None)


def command_matches(row: Optional[Mapping[str, Any]], executable: pathlib.Path) -> bool:
    if row is None:
        return False
    raw = str(row.get("command", "")).strip()
    expected = str(executable.resolve(strict=False))
    variants = {expected}
    if expected.startswith("/tmp/"):
        variants.add(f"/private{expected}")
    return any(raw == candidate or raw.startswith(f"{candidate} ") for candidate in variants)


def descendants(pid: int, rows: Optional[Iterable[Mapping[str, Any]]] = None) -> list[dict[str, Any]]:
    source = list(rows if rows is not None else process_rows())
    by_parent: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in source:
        by_parent[int(row["ppid"])].append(dict(row))
    result: list[dict[str, Any]] = []
    pending = [pid]
    seen = {pid}
    while pending:
        parent = pending.pop()
        for row in by_parent.get(parent, []):
            child = int(row["pid"])
            if child in seen:
                continue
            seen.add(child)
            result.append(row)
            pending.append(child)
    return result


def parse_json_line(line: str) -> Optional[Any]:
    try:
        return json.loads(line)
    except (TypeError, json.JSONDecodeError):
        return None


class ProcessPipes:
    """Small selector based pipe reader for children with bounded output."""

    def __init__(
        self,
        command: Sequence[str],
        cwd: pathlib.Path,
        env: Mapping[str, str],
        *,
        binary_stdout: bool = False,
    ):
        self.command = [str(item) for item in command]
        self.executable = pathlib.Path(self.command[0]).expanduser().resolve(strict=False)
        self.binary_stdout = binary_stdout
        self.proc = subprocess.Popen(
            self.command,
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        self.selector = selectors.DefaultSelector()
        self.stdout_buffer = bytearray()
        self.stderr_buffer = bytearray()
        self.stdout_lines: Deque[str] = collections.deque()
        self.stderr_lines: Deque[str] = collections.deque()
        self.logs: Deque[tuple[str, str]] = collections.deque(maxlen=MAX_LOG_LINES)
        self.registered: dict[int, str] = {}
        if self.proc.stdout is not None:
            self.proc.stdout_fd = self.proc.stdout.fileno()  # type: ignore[attr-defined]
            self.selector.register(self.proc.stdout, selectors.EVENT_READ, "stdout")
            self.registered[self.proc.stdout_fd] = "stdout"  # type: ignore[attr-defined]
        if self.proc.stderr is not None:
            self.proc.stderr_fd = self.proc.stderr.fileno()  # type: ignore[attr-defined]
            self.selector.register(self.proc.stderr, selectors.EVENT_READ, "stderr")
            self.registered[self.proc.stderr_fd] = "stderr"  # type: ignore[attr-defined]

    @property
    def pid(self) -> int:
        return int(self.proc.pid)

    def _append_log(self, stream: str, line: str) -> None:
        self.logs.append((stream, bounded_text(line)))

    def _consume_lines(self, stream: str, buffer: bytearray, *, final: bool = False) -> None:
        while b"\n" in buffer:
            raw, _, remaining = buffer.partition(b"\n")
            buffer.clear()
            buffer.extend(remaining)
            line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
            self._append_log(stream, line)
            if stream == "stdout":
                self.stdout_lines.append(line)
            else:
                self.stderr_lines.append(line)
        if final and buffer:
            line = bytes(buffer).decode("utf-8", errors="replace")
            buffer.clear()
            self._append_log(stream, line)
            if stream == "stdout":
                self.stdout_lines.append(line)
            else:
                self.stderr_lines.append(line)

    def _read_stream(self, file_object: Any, stream: str) -> None:
        fd = file_object.fileno()
        try:
            data = os.read(fd, 65_536)
        except OSError as error:
            self._append_log(stream, f"read failed: {error}")
            data = b""
        if data:
            if stream == "stdout" and self.binary_stdout:
                self.stdout_buffer.extend(data)
            else:
                target = self.stdout_buffer if stream == "stdout" else self.stderr_buffer
                target.extend(data)
                self._consume_lines(stream, target)
            return
        try:
            self.selector.unregister(file_object)
        except (KeyError, ValueError):
            pass
        self.registered.pop(fd, None)
        if stream == "stdout" and not self.binary_stdout:
            self._consume_lines(stream, self.stdout_buffer, final=True)
        if stream == "stderr":
            self._consume_lines(stream, self.stderr_buffer, final=True)

    def pump(self, timeout: float = 0.0) -> None:
        if not self.registered:
            if timeout > 0:
                time.sleep(min(timeout, 0.05))
            return
        for key, _ in self.selector.select(max(0.0, timeout)):
            self._read_stream(key.fileobj, str(key.data))

    def next_stdout_line(self, timeout: float) -> Optional[str]:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self.stdout_lines:
                return self.stdout_lines.popleft()
            if self.proc.poll() is not None and not self.registered:
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.pump(min(remaining, 0.2))

    def send_bytes(self, value: bytes) -> None:
        if self.proc.stdin is None:
            raise ProcessProtocolError("PROCESS_STDIN_CLOSED", "child stdin is unavailable")
        if self.proc.poll() is not None:
            raise ProcessProtocolError("PROCESS_EXITED", f"child exited with {self.proc.returncode}")
        try:
            self.proc.stdin.write(value)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise ProcessProtocolError("PROCESS_STDIN_FAILED", bounded_text(error)) from error

    def send_line(self, value: str) -> None:
        self.send_bytes(value.encode("utf-8") + b"\n")

    def wait_exit(self, timeout: float) -> Optional[int]:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            code = self.proc.poll()
            if code is not None:
                self.pump(0.0)
                return int(code)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.pump(min(remaining, 0.2))

    def close(self) -> None:
        try:
            self.selector.close()
        except OSError:
            pass
        for handle in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass


class BridgeProcess(ProcessPipes):
    """Reader for AgentBrowserMacBridge's length-prefixed stdout framing."""

    def __init__(self, command: Sequence[str], cwd: pathlib.Path, env: Mapping[str, str]):
        super().__init__(command, cwd, env, binary_stdout=True)
        self.next_request_id = 0
        self.pending_responses: dict[int, Any] = {}
        self.ack_requests: dict[int, tuple[Any, int]] = {}
        self.ack_requested: set[tuple[Any, int]] = set()
        self.acked_tickets: set[tuple[Any, int]] = set()
        self.frames: list[dict[str, Any]] = []
        self.ack_receipts: list[dict[str, Any]] = []

    def _parse_event(self) -> Optional[dict[str, Any]]:
        data = self.stdout_buffer
        if len(data) < 5:
            return None
        kind = data[0]
        header_length = int.from_bytes(data[1:5], "big")
        if header_length > MAX_BRIDGE_HEADER_BYTES:
            raise ProcessProtocolError("BRIDGE_HEADER_TOO_LARGE", str(header_length))
        header_end = 5 + header_length
        if len(data) < header_end + 4:
            return None
        raw_header = bytes(data[5:header_end])
        payload_length = int.from_bytes(data[header_end:header_end + 4], "big")
        if payload_length > MAX_BRIDGE_FRAME_BYTES:
            raise ProcessProtocolError("BRIDGE_FRAME_TOO_LARGE", str(payload_length))
        end = header_end + 4 + payload_length
        if len(data) < end:
            return None
        header = parse_json_line(raw_header.decode("utf-8", errors="replace"))
        if not isinstance(header, dict):
            raise ProcessProtocolError("BRIDGE_HEADER_INVALID_JSON", bounded_text(raw_header))
        payload = bytes(data[end - payload_length:end]) if payload_length else b""
        del data[:end]
        if kind == 0:
            if payload_length:
                raise ProcessProtocolError("BRIDGE_RESPONSE_HAS_PAYLOAD", str(payload_length))
            return {"kind": "response", "header": header}
        if kind != 1:
            raise ProcessProtocolError("BRIDGE_KIND_UNKNOWN", str(kind))
        declared_length = header.get("byte_length")
        if declared_length != payload_length:
            raise ProcessProtocolError(
                "BRIDGE_FRAME_LENGTH_MISMATCH",
                f"header={declared_length!r} payload={payload_length}",
            )
        if payload_length == 0:
            raise ProcessProtocolError("BRIDGE_FRAME_EMPTY", "display frame has no Annex B bytes")
        return {"kind": "frame", "header": header, "payload": payload}

    def read_event(self, timeout: float) -> Optional[dict[str, Any]]:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            event = self._parse_event()
            if event is not None:
                return event
            if self.proc.poll() is not None and not self.registered:
                if self.stdout_buffer:
                    raise ProcessProtocolError("BRIDGE_TRUNCATED_OUTPUT", str(len(self.stdout_buffer)))
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.pump(min(remaining, 0.2))

    def send_command(self, command: Mapping[str, Any]) -> int:
        self.next_request_id += 1
        request_id = self.next_request_id
        raw_command = json_text(dict(command))
        envelope = json_text({"id": request_id, "command": raw_command})
        self.send_line(envelope)
        return request_id

    @staticmethod
    def response_value(event: Mapping[str, Any]) -> Any:
        header = event.get("header")
        if not isinstance(header, Mapping):
            raise ProcessProtocolError("BRIDGE_RESPONSE_HEADER_INVALID", repr(header))
        if not isinstance(header.get("id"), int):
            raise ProcessProtocolError("BRIDGE_RESPONSE_ID_INVALID", repr(header.get("id")))
        value = header.get("value")
        if isinstance(value, str):
            parsed = parse_json_line(value)
            return value if parsed is None else parsed
        return value

    def command_response(self, command: Mapping[str, Any], timeout: float) -> Any:
        request_id = self.send_command(command)
        if request_id in self.pending_responses:
            return self.pending_responses.pop(request_id)
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessProtocolError("BRIDGE_RESPONSE_TIMEOUT", f"request {request_id}")
            event = self.read_event(remaining)
            if event is None:
                raise ProcessProtocolError("BRIDGE_OUTPUT_CLOSED", f"request {request_id}")
            if event["kind"] == "frame":
                self._handle_frame(event)
                continue
            header = event["header"]
            response_id = header.get("id")
            value = self.response_value(event)
            if not isinstance(response_id, int):
                raise ProcessProtocolError("BRIDGE_RESPONSE_ID_INVALID", repr(response_id))
            frame_key = self.ack_requests.pop(response_id, None)
            if frame_key is not None:
                if is_rejection(value):
                    raise ProcessProtocolError("BRIDGE_FRAME_ACK_REJECTED", json_text(value))
                self.acked_tickets.add(frame_key)
                self.ack_receipts.append(
                    {"generation": frame_key[0], "ticket": frame_key[1], "response": value}
                )
                continue
            if response_id == request_id:
                return value
            self.pending_responses[response_id] = value

    def _handle_frame(self, event: Mapping[str, Any]) -> None:
        header = event.get("header")
        if not isinstance(header, Mapping):
            raise ProcessProtocolError("BRIDGE_FRAME_HEADER_INVALID", repr(header))
        ticket = header.get("ticket")
        if not isinstance(ticket, int) or ticket <= 0:
            raise ProcessProtocolError("BRIDGE_FRAME_TICKET_INVALID", repr(ticket))
        frame_key = (header.get("generation"), ticket)
        self.frames.append(
            {
                "ticket": ticket,
                "generation": header.get("generation"),
                "session_id": header.get("session_id"),
                "sequence": header.get("sequence"),
                "document_revision": header.get("document_revision"),
                "viewport_revision": header.get("viewport_revision"),
                "coded_width": header.get("coded_width"),
                "coded_height": header.get("coded_height"),
                "visible_width": header.get("visible_width"),
                "visible_height": header.get("visible_height"),
                "pts_us": header.get("pts_us"),
                "byte_length": len(event.get("payload", b"")),
            }
        )
        if frame_key in self.ack_requested:
            return
        ack_id = self.send_command({"op": "ack_frame", "ticket": ticket})
        self.ack_requested.add(frame_key)
        self.ack_requests[ack_id] = frame_key


def is_rejection(value: Any) -> bool:
    return isinstance(value, Mapping) and isinstance(value.get("rejection"), str)


def inspect_objects(value: Any) -> Iterable[dict[str, Any]]:
    """Yield nested JSON objects, including JSON encoded by Host responses."""

    if isinstance(value, Mapping):
        yield dict(value)
        for child in value.values():
            yield from inspect_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from inspect_objects(child)
    elif isinstance(value, str):
        parsed = parse_json_line(value)
        if parsed is not None:
            yield from inspect_objects(parsed)


def fixture_snapshot(value: Any) -> Optional[dict[str, Any]]:
    required = {"clicked", "text", "scrollY", "maxScroll"}
    for candidate in inspect_objects(value):
        if required.issubset(candidate):
            return candidate
    return None


def loopback_endpoint(raw: Any) -> tuple[dict[str, Any], Optional[str]]:
    if not isinstance(raw, str) or not raw:
        return {}, "ENDPOINT_MISSING"
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        return {}, f"ENDPOINT_INVALID:{error}"
    if parsed.scheme.lower() != "wss":
        return {"raw": raw, "scheme": parsed.scheme, "host": host}, "RELAY_ENDPOINT_REJECTED"
    if host not in LOOPBACK_HOSTS:
        return {"raw": raw, "scheme": parsed.scheme, "host": host}, "RELAY_ENDPOINT_REJECTED"
    if parsed.username is not None or parsed.password is not None:
        return {"raw": raw, "scheme": parsed.scheme, "host": host}, "ENDPOINT_USERINFO_FORBIDDEN"
    if port is None or not (1 <= port <= 65_535):
        return {"raw": raw, "scheme": parsed.scheme, "host": host, "port": port}, "ENDPOINT_PORT_INVALID"
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return {"raw": raw, "scheme": parsed.scheme, "host": host, "port": port}, "ENDPOINT_PATH_INVALID"
    return {
        "raw": raw,
        "scheme": "wss",
        "host": host,
        "port": port,
        "network_path": "local",
        "transport": "WSS",
        "relay_rejected": True,
    }, None


def socket_info(path: pathlib.Path) -> dict[str, Any]:
    try:
        metadata = path.stat()
    except OSError as error:
        return {"path": str(path), "exists": False, "error": bounded_text(error)}
    return {
        "path": str(path),
        "exists": True,
        "socket": stat.S_ISSOCK(metadata.st_mode),
        "mode": oct(stat.S_IMODE(metadata.st_mode)),
    }


def safe_run_id(raw: str) -> bool:
    return bool(raw) and all(char.isalnum() or char in "._-" for char in raw)


@dataclasses.dataclass
class Arguments:
    mode: str
    fixture_bin: Optional[pathlib.Path]
    obscura_bin_dir: Optional[pathlib.Path]
    agent_bin: Optional[pathlib.Path]
    agent_args: list[str]
    fixture_args: list[str]
    ui_driver: Optional[pathlib.Path]
    ui_driver_args: list[str]
    evidence_dir: pathlib.Path
    requested_evidence_dir: pathlib.Path
    run_id: str
    timeout: float
    poll: float
    bind_ip: str
    expected_text: str
    click_x: float
    click_y: float
    field_x: float
    field_y: float
    scroll_x: float
    scroll_y: float
    scroll_dy: float
    viewport_width: int
    viewport_height: int
    orientation: str


class Runner:
    def __init__(self, args: Arguments):
        self.args = args
        self.worktree = pathlib.Path.cwd().resolve()
        self.fixture: Optional[ProcessPipes] = None
        self.agent: Optional[ProcessPipes] = None
        self.ui_driver: Optional[ProcessPipes] = None
        self.fixture_root: Optional[pathlib.Path] = None
        self.endpoint: Optional[str] = None
        self.endpoint_info: dict[str, Any] = {}
        self.daemon_rows: list[dict[str, Any]] = []
        self.first_failure: Optional[dict[str, Any]] = None
        self.operation_receipts: list[dict[str, Any]] = []
        self.fixture_inspections: list[dict[str, Any]] = []
        self.ui_events: list[dict[str, Any]] = []
        self.appkit_surface_ready = False
        self.evidence = self._initial_evidence()

    def _initial_evidence(self) -> dict[str, Any]:
        commit = git_value(self.worktree, "rev-parse", "HEAD")
        tree = git_value(self.worktree, "rev-parse", "HEAD^{tree}")
        branch = git_value(self.worktree, "symbolic-ref", "--quiet", "--short", "HEAD")
        status = git_value(self.worktree, "status", "--porcelain=v1", "--untracked-files=all")
        stages = {
            name: {
                "result": "not_run",
                "started_at": None,
                "finished_at": None,
                "first_failure": None,
                "raw_log": [],
                "details": {},
            }
            for name in STAGES
        }
        return {
            "schema": "agentbrowser.local-direct.evidence/v1",
            "run_id": self.args.run_id,
            "started_at": utc_now(),
            "finished_at": None,
            "result": "pending",
            "mode": self.args.mode,
            "candidate": {
                "commit": commit,
                "tree": tree,
                "branch": branch,
                "worktree": str(self.worktree),
                "status_before": status.splitlines() if status else [],
                "status_after": None,
            },
            "invocation": {
                "mode": self.args.mode,
                "run_id": self.args.run_id,
                "requested_evidence_dir": str(self.args.requested_evidence_dir),
                "evidence_dir": str(self.args.evidence_dir),
                "bind_ip": self.args.bind_ip,
            },
            "paths": {
                "host_endpoint": {
                    "network_path": "local",
                    "transport": "unix_socket",
                    "semantic_role": "Host <-> endpoint local IPC",
                    "observed": False,
                },
                "agent_endpoint": {
                    "network_path": "local",
                    "transport": "WSS",
                    "semantic_role": "Agent <-> endpoint loopback data path",
                    "observed": False,
                    "relay_rejected": True,
                },
                "ui_bridge": {
                    "network_path": "local",
                    "transport": "process_pipe",
                    "semantic_role": "Mac UI <-> native bridge stdin/stdout",
                    "observed": False,
                },
            },
            "fixture": {"identity": None, "ready": None, "root": None, "endpoint": None, "session": None},
            "daemon": {"identity": [], "observed": False},
            "agent": {"identity": None, "mode": self.args.mode},
            "ui_driver": {"identity": None, "events": []},
            "stages": stages,
            "first_failure": None,
            "proved": [],
            "unknown": [],
            "cleanup": {"fixture": None, "agent": None, "ui_driver": None, "daemons": [], "root": None},
            "process_logs": {},
        }

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

    def stage_log(self, name: str, text: Any) -> None:
        stage = self.evidence["stages"][name]
        logs = stage["raw_log"]
        item = bounded_text(text)
        if len(logs) < MAX_LOG_LINES:
            logs.append(item)

    def fail(
        self,
        code: str,
        message: str,
        *,
        owner: str,
        next_action: str,
        stage: Optional[str] = None,
    ) -> None:
        failure = {
            "code": code,
            "message": bounded_text(message),
            "owner": owner,
            "next_action": next_action,
            "at": utc_now(),
        }
        if stage is not None:
            self.stage_log(stage, f"{code}: {message}")
            self.evidence["stages"][stage]["first_failure"] = self.evidence["stages"][stage]["first_failure"] or failure
            self.stage_finish(stage, "failed")
        if self.first_failure is None:
            self.first_failure = failure
            self.evidence["first_failure"] = failure

    def abort(
        self,
        code: str,
        message: str,
        *,
        owner: str,
        next_action: str,
        stage: Optional[str] = None,
    ) -> None:
        self.fail(code, message, owner=owner, next_action=next_action, stage=stage)
        raise ReplayAbort(message)

    def prove(self, proof_id: str, detail: str, *, stage: Optional[str] = None) -> None:
        entry = {"id": proof_id, "detail": detail, "stage": stage}
        if entry not in self.evidence["proved"]:
            self.evidence["proved"].append(entry)

    def know_unknown(self, unknown_id: str, detail: str) -> None:
        entry = {"id": unknown_id, "detail": detail}
        if entry not in self.evidence["unknown"]:
            self.evidence["unknown"].append(entry)

    def preflight(self) -> None:
        self.stage_start("preflight")
        branch = self.evidence["candidate"].get("branch")
        if branch in {"main", "master"}:
            self.abort(
                "PROTECTED_BRANCH",
                f"candidate branch {branch!r} is protected",
                owner="workspace Git boundary",
                next_action="run the replay from an owner worktree branch",
                stage="preflight",
            )
        status = self.evidence["candidate"].get("status_before", [])
        if status:
            self.abort(
                "DIRTY_CANDIDATE_TREE",
                "candidate tree has uncommitted or untracked paths: " + "; ".join(status),
                owner="candidate worktree",
                next_action="commit or move unrelated changes before replay",
                stage="preflight",
            )
        requested = self.args.requested_evidence_dir.resolve(strict=False)
        if requested == self.worktree or self.worktree in requested.parents:
            code, ignored_output = command_result(("git", "check-ignore", "-q", "--", str(requested)), self.worktree)
            if code != 0:
                self.abort(
                    "EVIDENCE_PATH_IN_CANDIDATE_TREE",
                    f"evidence path is inside the candidate and is not ignored: {requested}",
                    owner="replay evidence writer",
                    next_action="use a temporary or ignored evidence directory",
                    stage="preflight",
                )
            self.stage_log("preflight", f"git check-ignore: {ignored_output.strip() or 'matched'}")
        if self.args.fixture_bin is None:
            self.abort(
                "MISSING_FIXTURE_ENTRYPOINT",
                "--fixture-bin was not supplied and LOCAL_DIRECT_FIXTURE_BIN is unset",
                owner="AgentBrowser fixture owner",
                next_action="build or provide the real device_fixture executable",
                stage="preflight",
            )
        if not executable_file(self.args.fixture_bin):
            self.abort(
                "MISSING_FIXTURE_ENTRYPOINT",
                f"fixture executable is missing or not executable: {self.args.fixture_bin}",
                owner="AgentBrowser fixture owner",
                next_action="provide an executable device_fixture artifact",
                stage="preflight",
            )
        self.evidence["fixture"]["identity"] = artifact_identity(self.args.fixture_bin)
        if self.args.obscura_bin_dir is None:
            self.abort(
                "MISSING_OBSCURA_BIN_DIR",
                "--obscura-bin-dir was not supplied and OBSCURA_BIN_DIR is unset",
                owner="Obscura binary owner",
                next_action="provide the release directory containing the three real Obscura binaries",
                stage="preflight",
            )
        obscura_dir = self.args.obscura_bin_dir.resolve(strict=False)
        self.args.obscura_bin_dir = obscura_dir
        if not obscura_dir.is_dir():
            self.abort(
                "MISSING_OBSCURA_BIN_DIR",
                f"Obscura binary directory does not exist: {obscura_dir}",
                owner="Obscura binary owner",
                next_action="provide a directory containing obscura-host, obscura-endpoint and obscura-media",
                stage="preflight",
            )
        binaries: dict[str, Any] = {}
        for name in ("obscura-host", "obscura-endpoint", "obscura-media"):
            binary = obscura_dir / name
            binaries[name] = artifact_identity(binary)
            if not executable_file(binary):
                self.abort(
                    "MISSING_OBSCURA_BINARY",
                    f"required Obscura executable is missing or not executable: {binary}",
                    owner="Obscura binary owner",
                    next_action=f"provide an executable {name} from the same candidate build",
                    stage="preflight",
                )
        self.evidence["daemon"]["binaries"] = binaries
        try:
            address = ipaddress.ip_address(self.args.bind_ip)
        except ValueError as error:
            self.abort(
                "BIND_IP_INVALID",
                str(error),
                owner="local direct runner",
                next_action="use 127.0.0.1 or ::1 for the local endpoint",
                stage="preflight",
            )
        if not address.is_loopback:
            self.abort(
                "NON_LOOPBACK_BIND_IP",
                f"endpoint bind address is not loopback: {self.args.bind_ip}",
                owner="local direct runner",
                next_action="bind the local direct endpoint to 127.0.0.1 or ::1",
                stage="preflight",
            )
        self.stage_finish("preflight", "passed", {"candidate_branch": branch, "bind_ip": self.args.bind_ip})

    def start_fixture(self) -> None:
        self.stage_start("fixture_start")
        assert self.args.fixture_bin is not None
        assert self.args.obscura_bin_dir is not None
        environment = dict(os.environ)
        environment["OBSCURA_BIN_DIR"] = str(self.args.obscura_bin_dir)
        environment["OBSCURA_ENDPOINT_BIND_IP"] = self.args.bind_ip
        environment["AGENTBROWSER_LOCAL_DIRECT_RUN_ID"] = self.args.run_id
        command = [str(self.args.fixture_bin), *self.args.fixture_args]
        self.stage_log("fixture_start", "command: " + json_text(command))
        try:
            self.fixture = ProcessPipes(command, self.worktree, environment)
        except OSError as error:
            self.abort(
                "FIXTURE_START_FAILED",
                bounded_text(error),
                owner="AgentBrowser fixture owner",
                next_action="start the real device_fixture and preserve its stderr",
                stage="fixture_start",
            )
        assert self.fixture is not None
        self.evidence["fixture"]["identity"] = {
            **(self.evidence["fixture"].get("identity") or {}),
            "pid": self.fixture.pid,
            "command": self.fixture.command,
        }
        ready = self._wait_fixture_ready(self.args.timeout)
        if ready is None:
            return
        root_raw = ready.get("fixture")
        endpoint_raw = ready.get("endpoint")
        session = ready.get("session")
        if not isinstance(root_raw, str) or not root_raw:
            self.abort(
                "FIXTURE_READY_INVALID",
                "ready record has no fixture root",
                owner="AgentBrowser fixture owner",
                next_action="emit fixture, endpoint and session in one ready record",
                stage="fixture_start",
            )
        if not isinstance(session, str) or not session:
            self.abort(
                "FIXTURE_READY_INVALID",
                "ready record has no session identity",
                owner="AgentBrowser fixture owner",
                next_action="emit a non-empty session identity in the ready record",
                stage="fixture_start",
            )
        endpoint_info, endpoint_error = loopback_endpoint(endpoint_raw)
        if endpoint_error is not None:
            self.endpoint_info = endpoint_info
            self.abort(
                endpoint_error,
                f"fixture endpoint rejected: {endpoint_raw!r}",
                owner="AgentBrowser fixture / endpoint owner",
                next_action="emit a loopback wss://127.0.0.1:<port> endpoint for direct replay",
                stage="fixture_start",
            )
        self.endpoint = str(endpoint_raw)
        self.endpoint_info = endpoint_info
        self.fixture_root = pathlib.Path(root_raw).expanduser().resolve(strict=False)
        self.evidence["fixture"]["ready"] = ready
        self.evidence["fixture"]["root"] = str(self.fixture_root)
        self.evidence["fixture"]["endpoint"] = self.endpoint
        self.evidence["fixture"]["session"] = session
        self.evidence["paths"]["agent_endpoint"].update(self.endpoint_info)
        self.evidence["fixture"]["identity"]["root"] = str(self.fixture_root)
        self.stage_finish("fixture_start", "passed", {"pid": self.fixture.pid, "session": session})
        self.prove(
            "fixture_ready_record",
            "fixture emitted a ready record with root, loopback WSS endpoint and session",
            stage="fixture_start",
        )

    def _wait_fixture_ready(self, timeout: float) -> Optional[dict[str, Any]]:
        assert self.fixture is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.fixture.next_stdout_line(min(0.5, deadline - time.monotonic()))
            if line is None:
                if self.fixture.proc.poll() is not None:
                    self.abort(
                        "FIXTURE_PROCESS_EXITED",
                        f"fixture exited before ready with code {self.fixture.proc.returncode}",
                        owner="AgentBrowser fixture owner",
                        next_action="inspect fixture stderr and preserve its root/process evidence",
                        stage="fixture_start",
                    )
                continue
            self.stage_log("fixture_start", f"stdout: {line}")
            parsed = parse_json_line(line)
            if not isinstance(parsed, dict):
                continue
            if {"fixture", "endpoint", "session"}.issubset(parsed):
                return parsed
        self.abort(
            "FIXTURE_READY_TIMEOUT",
            f"fixture did not emit a ready record within {timeout:.1f}s",
            owner="AgentBrowser fixture owner",
            next_action="inspect the preserved fixture stderr and daemon startup state",
            stage="fixture_start",
        )
        return None

    def validate_sockets(self) -> None:
        self.stage_start("socket_validation")
        if self.fixture_root is None:
            self.abort(
                "FIXTURE_ROOT_UNAVAILABLE",
                "fixture did not provide a root",
                owner="AgentBrowser fixture owner",
                next_action="repair fixture ready output",
                stage="socket_validation",
            )
        assert self.fixture_root is not None
        host_socket = self.fixture_root / "host" / "host.sock"
        endpoint_socket = self.fixture_root / "endpoint" / "encoded.sock"
        host_info = socket_info(host_socket)
        endpoint_socket_info = socket_info(endpoint_socket)
        self.evidence["paths"]["host_endpoint"].update(
            {
                "host_socket": host_info,
                "endpoint_socket": endpoint_socket_info,
                "root": str(self.fixture_root),
            }
        )
        for label, info in (("host/host.sock", host_info), ("endpoint/encoded.sock", endpoint_socket_info)):
            if not info.get("socket"):
                self.abort(
                    "LOCAL_IPC_SOCKET_MISSING",
                    f"required Unix socket is unavailable or not a socket: {label}",
                    owner="Obscura Host/endpoint owner",
                    next_action="start the real Host and endpoint and retain their socket paths",
                    stage="socket_validation",
                )
        pairing_files: dict[str, Any] = {}
        for name in ("endpoint.txt", "ca.der", "client.der", "key.der"):
            path = self.fixture_root / name
            try:
                metadata = path.stat()
                info: dict[str, Any] = {
                    "path": str(path),
                    "regular_file": stat.S_ISREG(metadata.st_mode),
                    "size": metadata.st_size,
                    "mode": oct(stat.S_IMODE(metadata.st_mode)),
                }
                if name == "key.der" and stat.S_IMODE(metadata.st_mode) & 0o077:
                    self.abort(
                        "PAIRING_KEY_PERMISSIONS",
                        f"pairing key is accessible by group or other: {path}",
                        owner="AgentBrowser pairing owner",
                        next_action="write the pairing key with mode 0600",
                        stage="socket_validation",
                    )
            except OSError as error:
                info = {"path": str(path), "exists": False, "error": bounded_text(error)}
                self.abort(
                    "PAIRING_MATERIAL_MISSING",
                    f"required pairing file is unavailable: {path}",
                    owner="AgentBrowser fixture owner",
                    next_action="emit endpoint.txt and the four ephemeral TLS files",
                    stage="socket_validation",
                )
            pairing_files[name] = info
        try:
            pairing_endpoint = (self.fixture_root / "endpoint.txt").read_text(encoding="utf-8").strip()
        except OSError as error:
            self.abort(
                "PAIRING_ENDPOINT_READ_FAILED",
                bounded_text(error),
                owner="AgentBrowser fixture owner",
                next_action="preserve endpoint.txt and emit the same loopback endpoint in the ready record",
                stage="socket_validation",
            )
        if pairing_endpoint != self.endpoint:
            self.abort(
                "PAIRING_ENDPOINT_MISMATCH",
                f"endpoint.txt={pairing_endpoint!r} does not match ready endpoint={self.endpoint!r}",
                owner="AgentBrowser fixture owner",
                next_action="make pairing endpoint and fixture ready endpoint identical",
                stage="socket_validation",
            )
        self.evidence["paths"]["agent_endpoint"]["pairing_endpoint"] = pairing_endpoint
        self.evidence["paths"]["agent_endpoint"]["pairing_files"] = pairing_files
        self.evidence["paths"]["host_endpoint"]["observed"] = True
        self.daemon_rows = self._observe_daemons()
        self.evidence["daemon"]["identity"] = self.daemon_rows
        self.evidence["daemon"]["observed"] = bool(self.daemon_rows)
        self.stage_finish(
            "socket_validation",
            "passed",
            {
                "host_socket": host_info,
                "endpoint_socket": endpoint_socket_info,
                "daemon_process_count": len(self.daemon_rows),
            },
        )
        self.prove(
            "local_ipc_sockets",
            "Host and endpoint Unix socket paths exist as sockets under the fixture root",
            stage="socket_validation",
        )
        if not self.daemon_rows:
            self.know_unknown("daemon_process_identity", "ps did not expose an Obscura child row; socket evidence remains structural")
        else:
            self.prove("daemon_process_identity", "Obscura child processes were observed under the fixture", stage="socket_validation")

    def _observe_daemons(self) -> list[dict[str, Any]]:
        if self.fixture is None:
            return []
        rows = descendants(self.fixture.pid)
        result: list[dict[str, Any]] = []
        for row in rows:
            command = str(row.get("command", ""))
            matched_name = next(
                (name for name in ("obscura-host", "obscura-endpoint", "obscura-media") if name in pathlib.Path(command.split(None, 1)[0]).name),
                None,
            )
            if matched_name is None:
                continue
            binary = (self.args.obscura_bin_dir / matched_name) if self.args.obscura_bin_dir else None
            result.append(
                {
                    "role": matched_name.removeprefix("obscura-"),
                    "pid": row["pid"],
                    "ppid": row["ppid"],
                    "command": command,
                    "binary": artifact_identity(binary),
                }
            )
        return result

    def _write_context(self) -> pathlib.Path:
        context_path = self.args.evidence_dir / "context.json"
        context = {
            "schema": "agentbrowser.local-direct.context/v1",
            "run_id": self.args.run_id,
            "mode": self.args.mode,
            "fixture_root": str(self.fixture_root) if self.fixture_root else None,
            "endpoint": self.endpoint,
            "session": self.evidence["fixture"].get("session"),
            "pairing_directory": str(self.fixture_root) if self.fixture_root else None,
            "paths": self.evidence["paths"],
            "evidence_dir": str(self.args.evidence_dir),
        }
        try:
            context_path.write_text(json_text(context) + "\n", encoding="utf-8")
        except OSError as error:
            self.abort(
                "CONTEXT_WRITE_FAILED",
                bounded_text(error),
                owner="local direct runner",
                next_action="provide a writable evidence directory outside the candidate tree",
                stage="agent_start",
            )
        self.evidence["invocation"]["context"] = str(context_path)
        return context_path

    def start_agent(self) -> None:
        self.stage_start("agent_start")
        if self.args.agent_bin is None or not executable_file(self.args.agent_bin):
            self.abort(
                "MISSING_AGENT_ENTRYPOINT",
                "agent executable is missing or not executable",
                owner="AgentBrowser Mac client owner",
                next_action="build/provide AgentBrowserMacBridge for bridge mode or AgentBrowserMac for AppKit mode",
                stage="agent_start",
            )
        if self.args.mode == "appkit" and (self.args.ui_driver is None or not executable_file(self.args.ui_driver)):
            self.abort(
                "MISSING_UI_ENTRYPOINT",
                "AppKit mode requires an executable --ui-driver that emits structured replay events",
                owner="AgentBrowser Mac UI owner",
                next_action="provide a real UI driver and its event schema output",
                stage="agent_start",
            )
        context_path = self._write_context()
        environment = dict(os.environ)
        environment["AGENTBROWSER_MAC_PAIRING"] = str(self.fixture_root)
        environment["AGENTBROWSER_LOCAL_DIRECT_CONTEXT"] = str(context_path)
        environment["AGENTBROWSER_LOCAL_DIRECT_EVIDENCE_DIR"] = str(self.args.evidence_dir)
        environment["AGENTBROWSER_LOCAL_DIRECT_ENDPOINT"] = self.endpoint or ""
        environment["AGENTBROWSER_LOCAL_DIRECT_FIXTURE_ROOT"] = str(self.fixture_root or "")
        command = [str(self.args.agent_bin), *self.args.agent_args]
        self.stage_log("agent_start", "command: " + json_text(command))
        try:
            if self.args.mode == "bridge":
                self.agent = BridgeProcess(command, self.worktree, environment)
            else:
                self.agent = ProcessPipes(command, self.worktree, environment)
        except OSError as error:
            self.abort(
                "AGENT_START_FAILED",
                bounded_text(error),
                owner="AgentBrowser Mac client owner",
                next_action="start the real client artifact with AGENTBROWSER_MAC_PAIRING",
                stage="agent_start",
            )
        assert self.agent is not None
        self.evidence["agent"]["identity"] = {
            **artifact_identity(self.args.agent_bin),
            "pid": self.agent.pid,
            "command": self.agent.command,
        }
        if not command_matches(process_row(self.agent.pid), self.agent.executable):
            self.abort(
                "AGENT_PROCESS_IDENTITY_MISMATCH",
                f"ps command for pid {self.agent.pid} does not match {self.agent.executable}",
                owner="local direct runner",
                next_action="preserve the process table and start an executable with a stable argv[0]",
                stage="agent_start",
            )
        if self.agent.proc.poll() is not None:
            self.abort(
                "AGENT_PROCESS_EXITED",
                f"agent exited immediately with code {self.agent.proc.returncode}",
                owner="AgentBrowser Mac client owner",
                next_action="inspect the preserved agent stderr",
                stage="agent_start",
            )
        self.evidence["paths"]["ui_bridge"].update(
            {
                "observed": self.args.mode == "bridge",
                "agent_pid": self.agent.pid,
                "stdin": "agent stdin",
                "stdout": "agent stdout",
            }
        )
        self.stage_finish("agent_start", "passed", {"pid": self.agent.pid})
        self.prove(
            "agent_process_identity",
            f"agent process {self.agent.pid} matches the supplied executable",
            stage="agent_start",
        )
        if self.args.mode == "appkit":
            self.start_ui_driver(context_path, environment)

    def start_ui_driver(self, context_path: pathlib.Path, environment: Mapping[str, str]) -> None:
        self.stage_start("ui_driver")
        assert self.args.ui_driver is not None
        driver_environment = dict(environment)
        driver_environment["AGENTBROWSER_LOCAL_DIRECT_AGENT_PID"] = str(self.agent.pid if self.agent else "")
        command = [str(self.args.ui_driver), *self.args.ui_driver_args]
        self.stage_log("ui_driver", "command: " + json_text(command))
        try:
            self.ui_driver = ProcessPipes(command, self.worktree, driver_environment)
        except OSError as error:
            self.abort(
                "UI_DRIVER_START_FAILED",
                bounded_text(error),
                owner="AgentBrowser Mac UI owner",
                next_action="start the real UI driver and preserve its stderr",
                stage="ui_driver",
            )
        assert self.ui_driver is not None
        self.evidence["ui_driver"]["identity"] = {
            **artifact_identity(self.args.ui_driver),
            "pid": self.ui_driver.pid,
            "command": self.ui_driver.command,
        }
        if not command_matches(process_row(self.ui_driver.pid), self.ui_driver.executable):
            self.abort(
                "UI_DRIVER_PROCESS_IDENTITY_MISMATCH",
                f"ps command for pid {self.ui_driver.pid} does not match {self.ui_driver.executable}",
                owner="local direct runner",
                next_action="preserve the process table and start a stable UI driver executable",
                stage="ui_driver",
            )
        self.stage_finish("ui_driver", "running", {"pid": self.ui_driver.pid})

    def run_bridge(self) -> None:
        if not isinstance(self.agent, BridgeProcess):
            self.abort(
                "BRIDGE_PROCESS_UNAVAILABLE",
                "bridge mode did not start a framed bridge process",
                owner="AgentBrowser Mac bridge owner",
                next_action="provide AgentBrowserMacBridge as --agent-bin",
                stage="connected",
            )
        bridge = self.agent
        self.stage_start("connected")
        try:
            initial = bridge.command_response({"op": "connect"}, self.args.timeout)
            self._require_bridge_snapshot(initial, "connected", "connect")
            connected = self._wait_bridge_snapshot(
                lambda value: value.get("connectionState") == "connected", "connected", "DIRECT_CONNECT_TIMEOUT"
            )
            if connected.get("source") != "network":
                code = "RELAY_PATH_OBSERVED" if connected.get("source") == "relay" else "MISSING_LOCAL_PATH_EVIDENCE"
                self.abort(
                    code,
                    f"connected snapshot has source={connected.get('source')!r}",
                    owner="AgentBrowser connection owner",
                    next_action="preserve the path event and require local loopback WSS",
                    stage="connected",
                )
            self.evidence["paths"]["agent_endpoint"]["observed"] = True
            self.evidence["paths"]["agent_endpoint"]["source"] = connected.get("source")
            self.stage_finish("connected", "passed", {"snapshot": connected})
            self.prove(
                "loopback_wss_connected",
                "bridge connected with a loopback WSS pairing and source=network",
                stage="connected",
            )
            viewport_response = bridge.command_response(
                {
                    "op": "viewport",
                    "width": self.args.viewport_width,
                    "height": self.args.viewport_height,
                    "orientation": self.args.orientation,
                },
                self.args.timeout,
            )
            self._require_bridge_snapshot(viewport_response, "video_displayed", "viewport")
            self.stage_start("video_displayed")
            displayed = self._wait_bridge_snapshot(
                lambda value: value.get("renderedFrames", 0) > 0
                and value.get("codec") == "h264_annex_b"
                and bool(bridge.acked_tickets),
                "video_displayed",
                "VIDEO_DISPLAY_TIMEOUT",
            )
            self.stage_finish(
                "video_displayed",
                "passed",
                {"snapshot": displayed, "frames": len(bridge.frames), "acks": len(bridge.acked_tickets)},
            )
            self.prove(
                "h264_frame_displayed",
                "bridge emitted an Annex B frame and accepted an explicit frame acknowledgement",
                stage="video_displayed",
            )
            self.run_bridge_operations(bridge)
            self.run_bridge_disconnect_reconnect(bridge)
        except ProcessProtocolError as error:
            stage = self._running_stage("connected", "video_displayed", "atomic_operations", "disconnect", "reconnect")
            self.abort(
                error.code,
                error.message,
                owner="AgentBrowser Mac bridge owner",
                next_action="inspect the preserved bridge framing and stderr",
                stage=stage,
            )

    def _running_stage(self, *names: str) -> Optional[str]:
        for name in names:
            if self.evidence["stages"][name]["result"] == "running":
                return name
        return None

    def _require_bridge_snapshot(self, value: Any, stage: str, operation: str) -> dict[str, Any]:
        if is_rejection(value):
            self.abort(
                "AGENT_COMMAND_REJECTED",
                f"{operation} rejected: {json_text(value)}",
                owner="AgentBrowser Mac bridge owner",
                next_action="fix the bridge command or its local pairing input",
                stage=stage,
            )
        if not isinstance(value, dict):
            self.abort(
                "AGENT_SNAPSHOT_INVALID",
                f"{operation} returned {value!r}",
                owner="AgentBrowser Mac bridge owner",
                next_action="return a structured bridge snapshot for each command",
                stage=stage,
            )
        if value.get("source") == "relay":
            self.abort(
                "RELAY_PATH_OBSERVED",
                f"{operation} snapshot selected relay path",
                owner="AgentBrowser connection owner",
                next_action="stop and preserve relay evidence; local direct replay cannot pass",
                stage=stage,
            )
        return value

    def _wait_bridge_snapshot(
        self,
        predicate: Any,
        stage: str,
        timeout_code: str,
    ) -> dict[str, Any]:
        assert isinstance(self.agent, BridgeProcess)
        deadline = time.monotonic() + self.args.timeout
        last: Any = None
        while time.monotonic() < deadline:
            try:
                value = self.agent.command_response({"op": "status"}, min(self.args.timeout, 5.0))
            except ProcessProtocolError:
                raise
            last = self._require_bridge_snapshot(value, stage, "status")
            if predicate(last):
                return last
            if last.get("connectionState") == "error":
                self.abort(
                    "DIRECT_CONNECT_FAILED",
                    f"bridge entered error state: {last.get('error')!r}",
                    owner="AgentBrowser connection owner",
                    next_action="inspect the bridge error and endpoint pairing",
                    stage=stage,
                )
            time.sleep(min(self.args.poll, max(0.0, deadline - time.monotonic())))
        self.abort(
            timeout_code,
            f"bridge did not reach the expected state; last snapshot={last!r}",
            owner="AgentBrowser Mac bridge owner",
            next_action="preserve bridge status, frame and daemon logs before retrying",
            stage=stage,
        )
        return {}

    def run_bridge_operations(self, bridge: BridgeProcess) -> None:
        self.stage_start("atomic_operations")
        ready = self._wait_bridge_snapshot(lambda value: value.get("inputReady") is True, "atomic_operations", "INPUT_NOT_READY")
        epoch = ready.get("epoch")
        if not isinstance(epoch, int):
            self.abort(
                "CONTROL_EPOCH_MISSING",
                f"input-ready snapshot has no integer epoch: {ready!r}",
                owner="AgentBrowser Host control owner",
                next_action="return the Host control epoch in the bridge snapshot",
                stage="atomic_operations",
            )
        operations = (
            ("click_button", {"op": "click", "epoch": epoch, "x": self.args.click_x, "y": self.args.click_y}),
            ("click_field", {"op": "click", "epoch": epoch, "x": self.args.field_x, "y": self.args.field_y}),
            ("input_text", {"op": "input_text", "epoch": epoch, "text": self.args.expected_text}),
            (
                "scroll",
                {
                    "op": "scroll",
                    "epoch": epoch,
                    "x": self.args.scroll_x,
                    "y": self.args.scroll_y,
                    "dx": 0.0,
                    "dy": self.args.scroll_dy,
                },
            ),
        )
        for operation_id, command in operations:
            before = ready
            response = bridge.command_response(command, self.args.timeout)
            after = self._require_bridge_snapshot(response, "atomic_operations", operation_id)
            receipt = {
                "operation": operation_id,
                "command": dict(command),
                "before": before,
                "after": after,
                "receipt": "bridge response snapshot",
            }
            self.operation_receipts.append(receipt)
            self.stage_log("atomic_operations", f"receipt {operation_id}: {json_text(after)}")
            if after.get("connectionState") != "connected" or after.get("error"):
                self.abort(
                    "ATOMIC_OPERATION_FAILED",
                    f"{operation_id} did not return a healthy connected snapshot",
                    owner="AgentBrowser Host operation owner",
                    next_action="inspect the operation response and preserve the exact Host error",
                    stage="atomic_operations",
                )
            if operation_id != "scroll":
                ready = self._wait_bridge_snapshot(
                    lambda value: value.get("inputReady") is True,
                    "atomic_operations",
                    "INPUT_NOT_READY_AFTER_OPERATION",
                )
        release = self._require_bridge_snapshot(
            bridge.command_response({"op": "release", "epoch": epoch}, self.args.timeout),
            "atomic_operations",
            "release",
        )
        if release.get("connectionState") != "connected" or release.get("controlMode") not in {"observe", "waiting"}:
            self.abort(
                "RELEASE_FAILED",
                f"release did not return a connected non-control snapshot: {release!r}",
                owner="AgentBrowser Host control owner",
                next_action="preserve the typed release response before fixture inspection",
                stage="atomic_operations",
            )
        self.operation_receipts.append(
            {
                "operation": "release",
                "command": {"op": "release", "epoch": epoch},
                "before": after,
                "after": release,
                "receipt": "bridge response snapshot",
            }
        )
        self.stage_log("atomic_operations", f"receipt release: {json_text(release)}")
        self.prove("control_released_for_inspect", "bridge released Host control before independent fixture inspection", stage="atomic_operations")
        time.sleep(RELEASE_INSPECT_SETTLE_SECONDS)
        self.stage_log(
            "atomic_operations",
            f"release-to-inspect settle: {RELEASE_INSPECT_SETTLE_SECONDS:.2f}s",
        )
        inspect = self.fixture_request("inspect", "atomic_operations")
        snapshot = fixture_snapshot(inspect)
        if snapshot is None:
            self.abort(
                "FIXTURE_INSPECT_SHAPE_INVALID",
                f"fixture inspect did not contain clicked/text/scrollY/maxScroll: {inspect!r}",
                owner="AgentBrowser fixture owner",
                next_action="preserve Host Evaluate output and expose the fixture state object",
                stage="atomic_operations",
            )
        assert snapshot is not None
        self.fixture_inspections.append(snapshot)
        self.evidence["fixture"]["inspection_after_operations"] = snapshot
        try:
            clicked = int(snapshot["clicked"])
            max_scroll = float(snapshot["maxScroll"])
        except (TypeError, ValueError) as error:
            self.abort(
                "FIXTURE_INSPECT_VALUES_INVALID",
                bounded_text(error),
                owner="AgentBrowser fixture owner",
                next_action="return numeric clicked and maxScroll values from Host Evaluate",
                stage="atomic_operations",
            )
        if clicked < 1 or snapshot.get("text") != self.args.expected_text or max_scroll <= 0:
            self.abort(
                "ATOMIC_OPERATION_RECEIPT_INVALID",
                f"fixture state does not prove click/text/scroll: {snapshot!r}",
                owner="AgentBrowser Host operation owner",
                next_action="preserve the first diverging operation and its Host response",
                stage="atomic_operations",
            )
        self.stage_finish(
            "atomic_operations",
            "passed",
            {
                "receipts": self.operation_receipts,
                "fixture_inspect": snapshot,
                "release_to_inspect_settle_seconds": RELEASE_INSPECT_SETTLE_SECONDS,
            },
        )
        self.prove(
            "atomic_operation_receipts",
            "click, input_text and scroll returned healthy Host snapshots and fixture state changed",
            stage="atomic_operations",
        )

    def run_bridge_disconnect_reconnect(self, bridge: BridgeProcess) -> None:
        self.stage_start("disconnect")
        before = self.evidence["fixture"].get("inspection_after_operations")
        response = bridge.command_response({"op": "disconnect"}, self.args.timeout)
        stopped = self._require_bridge_snapshot(response, "disconnect", "disconnect")
        if stopped.get("connectionState") != "stopped":
            self.abort(
                "DISCONNECT_STATE_INVALID",
                f"disconnect returned {stopped!r}",
                owner="AgentBrowser Mac connection owner",
                next_action="return stopped state after fencing the old generation",
                stage="disconnect",
            )
        self.stage_finish("disconnect", "passed", {"snapshot": stopped})
        self.prove("disconnect_fenced", "bridge reported stopped after the first direct generation", stage="disconnect")
        self.stage_start("reconnect")
        response = bridge.command_response({"op": "connect"}, self.args.timeout)
        self._require_bridge_snapshot(response, "reconnect", "reconnect")
        reconnected = self._wait_bridge_snapshot(
            lambda value: value.get("connectionState") == "connected", "reconnect", "RECONNECT_TIMEOUT"
        )
        if reconnected.get("source") != "network":
            self.abort(
                "RELAY_PATH_OBSERVED",
                f"reconnect snapshot selected source={reconnected.get('source')!r}",
                owner="AgentBrowser connection owner",
                next_action="preserve the reconnect path and keep direct replay failed",
                stage="reconnect",
            )
        generation = reconnected.get("generation")
        first_generation = next(
            (item.get("after", {}).get("generation") for item in self.operation_receipts if isinstance(item.get("after"), dict)),
            None,
        )
        if not isinstance(generation, int) or (isinstance(first_generation, int) and generation <= first_generation):
            self.abort(
                "RECONNECT_GENERATION_INVALID",
                f"reconnect did not advance generation: first={first_generation!r} current={generation!r}",
                owner="AgentBrowser connection owner",
                next_action="fence old connection events and increment generation on reconnect",
                stage="reconnect",
            )
        viewport_response = bridge.command_response(
            {
                "op": "viewport",
                "width": self.args.viewport_width,
                "height": self.args.viewport_height,
                "orientation": self.args.orientation,
            },
            self.args.timeout,
        )
        self._require_bridge_snapshot(viewport_response, "reconnect", "viewport")
        frame_count_before = len(bridge.frames)
        ack_count_before = len(bridge.acked_tickets)
        displayed = self._wait_bridge_snapshot(
            lambda value: value.get("renderedFrames", 0) > 0
            and len(bridge.frames) > frame_count_before
            and len(bridge.acked_tickets) > ack_count_before,
            "reconnect",
            "RECONNECT_VIDEO_TIMEOUT",
        )
        inspect = self.fixture_request("inspect", "reconnect")
        after = fixture_snapshot(inspect)
        if after is None or before is None:
            self.abort(
                "RECONNECT_INSPECT_UNAVAILABLE",
                f"reconnect fixture state is unavailable: {inspect!r}",
                owner="AgentBrowser fixture owner",
                next_action="preserve the post-reconnect Host Evaluate response",
                stage="reconnect",
            )
        assert after is not None
        self.fixture_inspections.append(after)
        self.evidence["fixture"]["inspection_after_reconnect"] = after
        if after.get("clicked") != before.get("clicked") or after.get("text") != before.get("text"):
            self.abort(
                "RECONNECT_PAGE_STATE_CHANGED",
                f"page state changed across reconnect: before={before!r} after={after!r}",
                owner="Obscura Host/session owner",
                next_action="retain the BrowserSession and inspect generation fencing",
                stage="reconnect",
            )
        self.stage_finish("reconnect", "passed", {"snapshot": displayed, "fixture_inspect": after})
        self.prove(
            "reconnect_retained_page_state",
            "new direct generation displayed media and retained fixture click/text state",
            stage="reconnect",
        )
        # Leave the client stopped so cleanup does not need to race a final connection.
        final = bridge.command_response({"op": "disconnect"}, self.args.timeout)
        self._require_bridge_snapshot(final, "reconnect", "final disconnect")

    def fixture_request(self, command: str, stage: str) -> Any:
        if self.fixture is None:
            self.abort(
                "FIXTURE_PROCESS_UNAVAILABLE",
                f"cannot send fixture command {command}",
                owner="AgentBrowser fixture owner",
                next_action="preserve fixture process identity before retrying",
                stage=stage,
            )
        try:
            assert self.fixture is not None
            self.fixture.send_line(command)
        except ProcessProtocolError as error:
            self.abort(
                error.code,
                error.message,
                owner="AgentBrowser fixture owner",
                next_action="inspect fixture process state and stderr",
                stage=stage,
            )
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline:
            assert self.fixture is not None
            line = self.fixture.next_stdout_line(min(0.5, deadline - time.monotonic()))
            if line is None:
                if self.fixture.proc.poll() is not None:
                    self.abort(
                        "FIXTURE_PROCESS_EXITED",
                        f"fixture exited while handling {command}: {self.fixture.proc.returncode}",
                        owner="AgentBrowser fixture owner",
                        next_action="preserve fixture stderr and root state",
                        stage=stage,
                    )
                continue
            self.stage_log(stage, f"fixture {command} stdout: {line}")
            parsed = parse_json_line(line)
            if parsed is not None:
                return parsed
        self.abort(
            "FIXTURE_COMMAND_TIMEOUT",
            f"fixture did not answer {command!r} within {self.args.timeout:.1f}s",
            owner="AgentBrowser fixture owner",
            next_action="inspect Host IPC response and fixture stderr",
            stage=stage,
        )
        return None

    def run_appkit(self) -> None:
        self.stage_start("connected")
        self.stage_start("video_displayed")
        self.stage_start("atomic_operations")
        self.stage_start("disconnect")
        self.stage_start("reconnect")
        assert self.ui_driver is not None
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline:
            if self.ui_driver.proc.poll() is not None:
                break
            line = self.ui_driver.next_stdout_line(min(0.5, deadline - time.monotonic()))
            if line is None:
                continue
            self.stage_log("ui_driver", f"stdout: {line}")
            parsed = parse_json_line(line)
            if not isinstance(parsed, dict):
                self.abort(
                    "UI_DRIVER_EVENT_INVALID_JSON",
                    f"UI driver emitted a non-JSON line: {line!r}",
                    owner="AgentBrowser Mac UI owner",
                    next_action="emit one JSON object per UI event",
                    stage="ui_driver",
                )
            self.handle_ui_event(parsed)
            if self.first_failure is not None:
                raise ReplayAbort("UI event failure")
        code = self.ui_driver.proc.poll()
        if code is None:
            self.abort(
                "UI_DRIVER_TIMEOUT",
                f"UI driver did not finish within {self.args.timeout:.1f}s",
                owner="AgentBrowser Mac UI owner",
                next_action="preserve the UI event stream and add an explicit completion event",
                stage="ui_driver",
            )
        if code != 0:
            self.abort(
                "UI_DRIVER_FAILED",
                f"UI driver exited with code {code}",
                owner="AgentBrowser Mac UI owner",
                next_action="inspect the preserved UI driver stderr",
                stage="ui_driver",
            )
        self._finish_appkit_stages()

    def handle_ui_event(self, event: dict[str, Any]) -> None:
        self.ui_events.append(event)
        self.evidence["ui_driver"]["events"] = self.ui_events
        name = event.get("event") or event.get("type")
        if not isinstance(name, str):
            self.abort(
                "UI_DRIVER_EVENT_NAME_MISSING",
                f"event has no event/type name: {event!r}",
                owner="AgentBrowser Mac UI owner",
                next_action="emit a named structured UI event",
                stage="ui_driver",
            )
        lowered = name.lower()
        if lowered in {"ui_ready", "appkit_ready", "surface_ready"}:
            if event.get("surface") not in {"appkit", "AppKit", "macos"}:
                self.abort(
                    "UI_SURFACE_INVALID",
                    f"UI ready event does not identify AppKit: {event!r}",
                    owner="AgentBrowser Mac UI owner",
                    next_action="identify the real AppKit surface in ui_ready",
                    stage="ui_driver",
                )
            self.appkit_surface_ready = True
            return
        if lowered in {"connected", "connection_ready"}:
            self._validate_ui_path_event(event, "connected")
            return
        if lowered in {"video_displayed", "frame_displayed", "frame_ack"}:
            frames = event.get("frames", event.get("frame_count", 0))
            if event.get("displayed") is False or not isinstance(frames, (int, float)) or frames <= 0:
                self.abort(
                    "UI_VIDEO_EVIDENCE_INVALID",
                    f"UI event does not prove a displayed frame: {event!r}",
                    owner="AgentBrowser Mac UI owner",
                    next_action="emit displayed=true and a positive frame count after VideoToolbox display",
                    stage="video_displayed",
                )
            self.stage_finish("video_displayed", "passed", {"event": event})
            self.prove("appkit_video_displayed", "AppKit UI driver observed a displayed native frame", stage="video_displayed")
            return
        if lowered in {"operation_receipt", "operation_completed", "receipt"}:
            operation = event.get("operation")
            operation_id = event.get("operation_id", event.get("id"))
            outcome = event.get("outcome", event.get("status"))
            if operation not in {"click", "click_button", "click_field", "input_text", "scroll"} or not operation_id or outcome not in {"applied", "completed", "success"}:
                self.abort(
                    "UI_OPERATION_RECEIPT_INVALID",
                    f"UI event is not an applied operation receipt: {event!r}",
                    owner="AgentBrowser Host operation owner",
                    next_action="emit operation, operation_id and applied/completed outcome",
                    stage="atomic_operations",
                )
            self.operation_receipts.append(event)
            return
        if lowered in {"inspect", "fixture_inspect", "state_inspect"}:
            snapshot = fixture_snapshot(event)
            if snapshot is None:
                self.abort(
                    "UI_INSPECT_INVALID",
                    f"UI inspect event lacks fixture state: {event!r}",
                    owner="AgentBrowser fixture/UI owner",
                    next_action="emit clicked, text, scrollY and maxScroll in the inspect event",
                    stage="atomic_operations",
                )
            assert snapshot is not None
            self.fixture_inspections.append(snapshot)
            return
        if lowered in {"disconnected", "disconnect"}:
            self.stage_finish("disconnect", "passed", {"event": event})
            self.prove("appkit_disconnect", "UI driver observed a direct connection disconnect", stage="disconnect")
            return
        if lowered in {"reconnected", "reconnect"}:
            self._validate_ui_path_event(event, "reconnected")
            self.stage_finish("reconnect", "passed", {"event": event})
            self.prove("appkit_reconnect", "UI driver observed a direct reconnection", stage="reconnect")
            return
        if lowered in {"done", "complete"}:
            return
        self.stage_log("ui_driver", f"ignored event: {json_text(event)}")

    def _validate_ui_path_event(self, event: Mapping[str, Any], label: str) -> None:
        if event.get("endpoint") != self.endpoint or event.get("transport") != "WSS" or event.get("network_path") != "local":
            code = "RELAY_PATH_OBSERVED" if event.get("network_path") == "relay" or event.get("transport") == "relay" else "UI_PATH_EVIDENCE_INVALID"
            self.abort(
                code,
                f"{label} event does not prove the expected loopback WSS path: {event!r}",
                owner="AgentBrowser Mac connection owner",
                next_action="emit endpoint, transport=WSS and network_path=local from the real UI path",
                stage="connected" if label == "connected" else "reconnect",
            )
        self.evidence["paths"]["agent_endpoint"]["ui_event"] = dict(event)
        if label == "connected":
            self.stage_finish("connected", "passed", {"event": event})
            self.prove("appkit_loopback_wss_connected", "AppKit UI driver observed the loopback WSS endpoint", stage="connected")

    def _finish_appkit_stages(self) -> None:
        received = {event.get("operation") for event in self.operation_receipts}
        required_operations = {
            "click": bool(received.intersection({"click", "click_button", "click_field"})),
            "input_text": "input_text" in received,
            "scroll": "scroll" in received,
        }
        if not all(required_operations.values()):
            self.abort(
                "UI_OPERATION_RECEIPT_INCOMPLETE",
                f"missing operation receipts: {[name for name, present in required_operations.items() if not present]}",
                owner="AgentBrowser Host operation owner",
                next_action="replay click, input_text and scroll and emit each receipt",
                stage="atomic_operations",
            )
        if not self.appkit_surface_ready:
            self.abort(
                "UI_SURFACE_READY_EVENT_MISSING",
                "UI driver emitted no AppKit surface ready event",
                owner="AgentBrowser Mac UI owner",
                next_action="emit ui_ready with surface=appkit after the real window and video surface exist",
                stage="ui_driver",
            )
        if not self.fixture_inspections:
            self.abort(
                "UI_INSPECT_EVIDENCE_MISSING",
                "UI driver emitted no fixture state inspection",
                owner="AgentBrowser fixture/UI owner",
                next_action="emit an inspect event with Host Evaluate state after operations",
                stage="atomic_operations",
            )
        valid_inspection = next(
            (
                snapshot
                for snapshot in self.fixture_inspections
                if isinstance(snapshot.get("clicked"), (int, float))
                and snapshot.get("clicked", 0) >= 1
                and snapshot.get("text") == self.args.expected_text
                and isinstance(snapshot.get("maxScroll"), (int, float))
                and snapshot.get("maxScroll", 0) > 0
            ),
            None,
        )
        if valid_inspection is None:
            self.abort(
                "UI_INSPECT_VALUES_INVALID",
                f"UI inspect events do not prove click/text/scroll state: {self.fixture_inspections!r}",
                owner="AgentBrowser fixture/UI owner",
                next_action="emit the Host Evaluate state after operations with the expected values",
                stage="atomic_operations",
            )
        self.stage_finish("atomic_operations", "passed", {"receipts": self.operation_receipts, "inspections": self.fixture_inspections})
        self.prove("appkit_atomic_operation_receipts", "AppKit driver emitted all required operation receipts", stage="atomic_operations")
        for name in ("connected", "video_displayed", "disconnect", "reconnect"):
            if self.evidence["stages"][name]["result"] != "passed":
                self.abort(
                    "UI_EVENT_INCOMPLETE",
                    f"required UI event did not complete stage {name}",
                    owner="AgentBrowser Mac UI owner",
                    next_action=f"emit the structured {name} event from the real UI flow",
                    stage=name,
                )
        self.stage_finish("ui_driver", "passed", {"event_count": len(self.ui_events)})

    def execute(self) -> int:
        try:
            self.preflight()
            self.start_fixture()
            self.validate_sockets()
            self.start_agent()
            if self.args.mode == "bridge":
                self.run_bridge()
            else:
                self.run_appkit()
        except ReplayAbort:
            pass
        except ProcessProtocolError as error:
            self.fail(
                error.code,
                error.message,
                owner="local direct runner",
                next_action="inspect the preserved child output",
                stage=self._running_stage(*STAGES),
            )
        except (OSError, ValueError, TypeError) as error:
            self.fail(
                "RUNNER_UNEXPECTED_ERROR",
                bounded_text(error),
                owner="local direct runner",
                next_action="preserve evidence and inspect the first unexpected exception",
                stage=self._running_stage(*STAGES),
            )
        finally:
            self.cleanup()
            self.finalize()
        return 0 if self.evidence["result"] in {"pass", "bridge_transport_pass"} else 2

    def cleanup(self) -> None:
        self.stage_start("cleanup")
        cleanup = self.evidence["cleanup"]
        cleanup["started_at"] = utc_now()
        if self.ui_driver is not None:
            cleanup["ui_driver"] = self._terminate_process(self.ui_driver, "ui_driver")
        else:
            cleanup["ui_driver"] = {"started": False}
        if self.agent is not None:
            cleanup["agent"] = self._terminate_process(self.agent, "agent")
        else:
            cleanup["agent"] = {"started": False}
        if self.fixture is not None:
            quit_result: dict[str, Any] = {"pid": self.fixture.pid, "command": self.fixture.command}
            if self.fixture.proc.poll() is None:
                try:
                    self.fixture.send_line("quit")
                    exit_code = self.fixture.wait_exit(8.0)
                    quit_result.update({"quit_sent": True, "exit_code": exit_code, "graceful": exit_code is not None})
                except ProcessProtocolError as error:
                    quit_result.update({"quit_sent": False, "error": {"code": error.code, "message": error.message}})
            if self.fixture.proc.poll() is None:
                quit_result.update(self._terminate_process(self.fixture, "fixture"))
            else:
                quit_result["exit_code"] = self.fixture.proc.returncode
            cleanup["fixture"] = quit_result
        else:
            cleanup["fixture"] = {"started": False}
        cleanup["daemons"] = self._cleanup_daemons()
        if self.fixture_root is None:
            cleanup["root"] = {"path": None, "exists": False, "checked": False}
        else:
            cleanup["root"] = {"path": str(self.fixture_root), "exists": self.fixture_root.exists(), "checked": True}
            if self.fixture_root.exists() and self.first_failure is None:
                self.fail(
                    "FIXTURE_ROOT_NOT_CLEANED",
                    f"fixture root remains after quit: {self.fixture_root}",
                    owner="AgentBrowser fixture owner",
                    next_action="repair fixture cleanup and do not remove this root from the runner",
                    stage="cleanup",
                )
            elif self.fixture_root.exists():
                self.stage_log("cleanup", f"root retained after earlier failure: {self.fixture_root}")
        cleanup["finished_at"] = utc_now()
        cleanup_ok = self._cleanup_results_ok(cleanup)
        if self.first_failure is None and not cleanup_ok:
            self.fail(
                "CLEANUP_INCOMPLETE",
                f"one or more owned processes were not cleanly terminated: {cleanup!r}",
                owner="local direct runner",
                next_action="inspect saved PID/command identities and terminate only the matching owner",
                stage="cleanup",
            )
        if self.first_failure is None and cleanup["root"].get("exists") is False:
            self.stage_finish("cleanup", "passed", cleanup)
            self.prove("owned_process_cleanup", "runner used saved PIDs and fixture quit; root is absent", stage="cleanup")
        elif self.evidence["stages"]["cleanup"]["result"] == "running":
            self.stage_finish("cleanup", "completed_with_prior_failure", cleanup)

    @staticmethod
    def _cleanup_results_ok(cleanup: Mapping[str, Any]) -> bool:
        allowed = {"already_exited", "terminated_sigterm", "terminated_sigkill"}
        for label in ("ui_driver", "agent"):
            value = cleanup.get(label)
            if not isinstance(value, Mapping) or value.get("started") is False:
                continue
            result = value.get("result")
            if result is not None and result not in allowed:
                return False
        fixture = cleanup.get("fixture")
        if isinstance(fixture, Mapping) and fixture.get("started") is not False:
            if fixture.get("graceful") is False:
                return False
            result = fixture.get("result")
            if result is not None and result not in allowed:
                return False
        daemons = cleanup.get("daemons")
        if isinstance(daemons, list):
            for daemon in daemons:
                if not isinstance(daemon, Mapping):
                    return False
                result = daemon.get("result")
                if result not in {"already_exited", *allowed}:
                    return False
        return True

    def _cleanup_daemons(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in self.daemon_rows:
            pid = item.get("pid")
            binary = item.get("binary", {}).get("path") if isinstance(item.get("binary"), Mapping) else None
            if not isinstance(pid, int) or not isinstance(binary, str):
                results.append({"pid": pid, "result": "identity_unavailable"})
                continue
            row = process_row(pid)
            if row is None:
                results.append({"pid": pid, "result": "already_exited"})
                continue
            executable = pathlib.Path(binary)
            if not command_matches(row, executable):
                results.append({"pid": pid, "result": "identity_mismatch", "command": row.get("command")})
                continue
            results.append(self._terminate_pid(pid, executable, "daemon"))
        return results

    def _terminate_process(self, process: ProcessPipes, label: str) -> dict[str, Any]:
        if process.proc.poll() is not None:
            return {"pid": process.pid, "label": label, "result": "already_exited", "exit_code": process.proc.returncode}
        return self._terminate_pid(process.pid, process.executable, label, process=process)

    def _terminate_pid(
        self,
        pid: int,
        executable: pathlib.Path,
        label: str,
        *,
        process: Optional[ProcessPipes] = None,
    ) -> dict[str, Any]:
        row = process_row(pid)
        result: dict[str, Any] = {"pid": pid, "label": label, "executable": str(executable), "command_before": row}
        if not command_matches(row, executable):
            result["result"] = "identity_mismatch_not_signalled"
            return result
        try:
            os.kill(pid, signal.SIGTERM)
            result["sigterm"] = True
        except OSError as error:
            result["sigterm"] = False
            result["error"] = bounded_text(error)
            return result
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if process is not None:
                process.pump(0.1)
                if process.proc.poll() is not None:
                    result["result"] = "terminated_sigterm"
                    result["exit_code"] = process.proc.returncode
                    return result
            elif process_row(pid) is None:
                result["result"] = "terminated_sigterm"
                return result
            time.sleep(0.1)
        row = process_row(pid)
        if not command_matches(row, executable):
            result["result"] = "identity_changed_before_sigkill"
            return result
        try:
            os.kill(pid, signal.SIGKILL)
            result["sigkill"] = True
        except OSError as error:
            result["sigkill"] = False
            result["error"] = bounded_text(error)
            return result
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if process is not None:
                process.pump(0.1)
                if process.proc.poll() is not None:
                    result["result"] = "terminated_sigkill"
                    result["exit_code"] = process.proc.returncode
                    return result
            elif process_row(pid) is None:
                result["result"] = "terminated_sigkill"
                return result
            time.sleep(0.1)
        result["result"] = "still_running_after_sigkill"
        return result

    def finalize(self) -> None:
        for name, stage in self.evidence["stages"].items():
            if stage["result"] in {"not_run", "running"}:
                if self.first_failure is not None:
                    stage["result"] = "skipped_after_failure"
                else:
                    stage["result"] = "unknown"
                stage["finished_at"] = utc_now()
        self.evidence["candidate"]["status_after"] = (
            git_value(self.worktree, "status", "--porcelain=v1", "--untracked-files=all") or ""
        ).splitlines()
        self.evidence["finished_at"] = utc_now()
        if self.first_failure is not None:
            self.evidence["result"] = "failed"
        elif self.args.mode == "bridge":
            self.evidence["result"] = "bridge_transport_pass"
            self.know_unknown("appkit_ui_surface", "bridge mode proves native bridge transport, not an AppKit window replay")
        else:
            self.evidence["result"] = "pass"
        if self.args.mode == "appkit":
            self.know_unknown("bridge_binary_framing", "AppKit mode delegates native bridge framing to the supplied UI process")
        self.evidence["agent"]["operation_receipts"] = self.operation_receipts
        self.evidence["fixture"]["inspections"] = self.fixture_inspections
        for label, process in (("fixture", self.fixture), ("agent", self.agent), ("ui_driver", self.ui_driver)):
            if process is not None:
                self.evidence["process_logs"][label] = [
                    {"stream": stream, "line": line} for stream, line in process.logs
                ]
        try:
            output = self.args.evidence_dir / "evidence.json"
            output.write_text(json.dumps(self.evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.evidence["invocation"]["evidence"] = str(output)
            # Write the final invocation path into the same evidence document.
            output.write_text(json.dumps(self.evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            print(f"local-direct: unable to write evidence: {error}", file=sys.stderr)
        print(json_text({"result": self.evidence["result"], "evidence": str(self.args.evidence_dir / 'evidence.json'), "first_failure": self.first_failure}), file=sys.stdout)


def default_evidence_dir(run_id: str) -> pathlib.Path:
    temporary_root = pathlib.Path(os.environ.get("TMPDIR", "/tmp")).expanduser()
    return temporary_root / "agentbrowser-local-direct" / run_id


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Replay and record the local AgentBrowser <-> Obscura direct path; no relay or ABI implementation is included."
    )
    value.add_argument("--mode", choices=("bridge", "appkit"), default="bridge")
    value.add_argument("--fixture-bin", type=pathlib.Path, default=None, help="real device_fixture executable")
    value.add_argument("--obscura-bin-dir", type=pathlib.Path, default=None, help="directory with obscura-host/endpoint/media")
    value.add_argument("--agent-bin", type=pathlib.Path, default=None, help="AgentBrowserMacBridge or AgentBrowserMac executable")
    value.add_argument("--agent-arg", action="append", default=[], help="argument passed to --agent-bin; repeatable")
    value.add_argument("--fixture-arg", action="append", default=[], help="argument passed to --fixture-bin; repeatable")
    value.add_argument("--ui-driver", type=pathlib.Path, default=None, help="AppKit UI driver emitting JSONL events")
    value.add_argument("--ui-driver-arg", action="append", default=[], help="argument passed to --ui-driver; repeatable")
    value.add_argument("--evidence-dir", type=pathlib.Path, default=None)
    value.add_argument("--run-id", default=None)
    value.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    value.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS)
    value.add_argument("--bind-ip", default=None, help="loopback bind address; defaults to OBSCURA_ENDPOINT_BIND_IP or 127.0.0.1")
    value.add_argument("--expected-text", default=DEFAULT_TEXT)
    value.add_argument("--click-x", type=float, default=90.0)
    value.add_argument("--click-y", type=float, default=50.0)
    value.add_argument("--field-x", type=float, default=90.0)
    value.add_argument("--field-y", type=float, default=125.0)
    value.add_argument("--scroll-x", type=float, default=90.0)
    value.add_argument("--scroll-y", type=float, default=400.0)
    value.add_argument("--scroll-dy", type=float, default=560.0)
    value.add_argument("--viewport-width", type=int, default=391)
    value.add_argument("--viewport-height", type=int, default=845)
    value.add_argument("--orientation", choices=("portrait", "landscape"), default="portrait")
    return value


def build_arguments(namespace: argparse.Namespace, cwd: pathlib.Path) -> Arguments:
    run_id = namespace.run_id or f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    if not safe_run_id(run_id):
        raise ValueError(f"invalid --run-id: {run_id!r}")
    fixture_bin = namespace.fixture_bin or (pathlib.Path(os.environ["LOCAL_DIRECT_FIXTURE_BIN"]) if os.environ.get("LOCAL_DIRECT_FIXTURE_BIN") else None)
    obscura_bin_dir = namespace.obscura_bin_dir or (pathlib.Path(os.environ["OBSCURA_BIN_DIR"]) if os.environ.get("OBSCURA_BIN_DIR") else None)
    agent_bin = namespace.agent_bin or (pathlib.Path(os.environ["LOCAL_DIRECT_AGENT_BIN"]) if os.environ.get("LOCAL_DIRECT_AGENT_BIN") else None)
    if fixture_bin is not None:
        fixture_bin = fixture_bin.expanduser().resolve(strict=False)
    if obscura_bin_dir is not None:
        obscura_bin_dir = obscura_bin_dir.expanduser().resolve(strict=False)
    if agent_bin is not None:
        agent_bin = agent_bin.expanduser().resolve(strict=False)
    ui_driver = namespace.ui_driver.expanduser().resolve(strict=False) if namespace.ui_driver is not None else None
    bind_ip = namespace.bind_ip or os.environ.get("OBSCURA_ENDPOINT_BIND_IP", "127.0.0.1")
    requested = namespace.evidence_dir or default_evidence_dir(run_id)
    requested = requested if requested.is_absolute() else cwd / requested
    actual = requested
    requested_resolved = requested.resolve(strict=False)
    if requested_resolved == cwd or cwd in requested_resolved.parents:
        ignored_code, _ = command_result(("git", "check-ignore", "-q", "--", str(requested_resolved)), cwd)
        if ignored_code != 0:
            # Never create runtime evidence in an unignored candidate path.  The
            # requested location remains visible in invocation evidence while
            # the actual write is redirected to a private temporary directory.
            actual = default_evidence_dir(run_id)
    if actual.exists() and (actual / "evidence.json").exists():
        actual = actual.parent / f"{actual.name}-{uuid.uuid4().hex[:8]}"
    actual.mkdir(parents=True, exist_ok=True)
    return Arguments(
        mode=namespace.mode,
        fixture_bin=fixture_bin,
        obscura_bin_dir=obscura_bin_dir,
        agent_bin=agent_bin,
        agent_args=list(namespace.agent_arg),
        fixture_args=list(namespace.fixture_arg),
        ui_driver=ui_driver,
        ui_driver_args=list(namespace.ui_driver_arg),
        evidence_dir=actual.resolve(strict=False),
        requested_evidence_dir=requested_resolved,
        run_id=run_id,
        timeout=max(0.1, float(namespace.timeout)),
        poll=max(0.01, float(namespace.poll)),
        bind_ip=str(bind_ip),
        expected_text=str(namespace.expected_text),
        click_x=float(namespace.click_x),
        click_y=float(namespace.click_y),
        field_x=float(namespace.field_x),
        field_y=float(namespace.field_y),
        scroll_x=float(namespace.scroll_x),
        scroll_y=float(namespace.scroll_y),
        scroll_dy=float(namespace.scroll_dy),
        viewport_width=int(namespace.viewport_width),
        viewport_height=int(namespace.viewport_height),
        orientation=str(namespace.orientation),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    cli = parser()
    namespace = cli.parse_args(argv)
    try:
        args = build_arguments(namespace, pathlib.Path.cwd().resolve())
    except (OSError, ValueError) as error:
        print(f"local-direct: argument/evidence setup failed: {error}", file=sys.stderr)
        return 2
    runner = Runner(args)
    return runner.execute()


if __name__ == "__main__":
    raise SystemExit(main())
