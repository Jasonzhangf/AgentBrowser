#!/usr/bin/env python3
"""Own the local Obscura Host process without owning browser Session state."""

from __future__ import annotations

import argparse
import base64
import ctypes
import datetime
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape as xml_escape


CONFIG_VERSION = 2
U32_MAX = (1 << 32) - 1
DEFAULT_LABEL = "com.agentbrowser.obscura-host"
DEFAULT_ROOT = Path.home() / "Library" / "Application Support" / "AgentBrowser" / "host-service"
DEFAULT_RUNTIME_BASE = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path("/tmp")
DEFAULT_RUNTIME_ROOT = DEFAULT_RUNTIME_BASE / f"agentbrowser-host-{os.getuid()}"
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SOCKET_PATH_LIMIT = 100
STATE_NAME = "state.json"
CONFIG_NAME = "config.json"

# Local trust authority layout and frozen wire values (agentbrowser.connection@1).
# The authority carries only the endpoint locator and the access token. There is
# no certificate authority, no device registry and no authorization projection.
TRUST_DIR_NAME = "trust"
TRUST_SERVER_CERT_NAME = "server.der"
TRUST_SERVER_KEY_NAME = "server-key.der"
TRUST_ENDPOINT_NAME = "endpoint.json"
TRUST_TOKEN_NAME = "token"

# Endpoint applied receipt v1 published by the endpoint after a real bind.
ENDPOINT_APPLIED_DIR_NAME = "endpoint-applied"
ENDPOINT_APPLIED_SCHEMA_VERSION = 1
ENDPOINT_APPLIED_FRAME_LIMIT = 64 * 1024
ENDPOINT_APPLIED_WAIT_INTERVAL_SECONDS = 0.05
ENDPOINT_REFRESH_TIMEOUT_SECONDS = 30.0
# Declared service-owned endpoint process record. It carries only process
# identity (PID + exact argv) so the service can stop the endpoint it started
# through the same verified-PID lifecycle it uses for the Host. The endpoint
# generation is never duplicated here; it lives only in the trust locator.
ENDPOINT_PROCESS_RECORD_NAME = "endpoint-process.json"
ENDPOINT_PROCESS_RECORD_VERSION = 1
ENDPOINT_APPLIED_FIELDS = frozenset(
    {
        "schema_version",
        "endpoint_generation",
        "endpoint_pid",
        "bound_address",
    }
)

# The listener's own TLS identity is service-owned and never enters the user's
# connection configuration. The client verifies nothing, so no CA exists.
SERVER_KEY_SIZE = 2048
DEFAULT_CERT_VALIDITY_DAYS = 397
MAX_CERT_VALIDITY_DAYS = 398

# The frozen bundle identity.
BUNDLE_FORMAT = "agentbrowser.connection"
BUNDLE_VERSION = 1
TOKEN_BYTES = 32

PARTIAL_EXIT_CODE = 4
TRUST_EXIT_CODE = 3


class ServiceError(Exception):
    """A user-facing service error with a stable non-zero result."""

    def __init__(
        self,
        message: str,
        exit_code: int = 2,
        code: str | None = None,
        facts: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code
        # Typed partial facts for a multi-step operation that failed partway
        # through (for example the endpoint refresh sequence). They are emitted
        # next to the error code and never mutate the committed state truth.
        self.facts = facts


class ReadyRejected(ServiceError):
    """A parsed Host ready response that cannot become valid by retrying."""


def protocol_version_from(value: object, field: str) -> int:
    if type(value) is not int or not 1 <= value <= U32_MAX:
        raise ServiceError(f"{field} must be a positive u32")
    return value


def protocol_version_argument(value: str) -> int:
    try:
        return protocol_version_from(int(value, 10), "protocol version")
    except (ValueError, ServiceError) as error:
        raise argparse.ArgumentTypeError("protocol version must be a positive u32") from error


def emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def assert_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ServiceError(f"refusing symlink directory: {path}")
    if path.exists():
        if not path.is_dir():
            raise ServiceError(f"expected directory: {path}")
        if mode(path) != 0o700:
            raise ServiceError(f"directory must already be private (expected 0700): {path}")
        return
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        if path.is_symlink() or not path.is_dir():
            raise ServiceError(f"expected private directory: {path}")
        if mode(path) != 0o700:
            raise ServiceError(f"directory must already be private (expected 0700): {path}")
        return
    path.chmod(0o700)
    if mode(path) != 0o700:
        raise ServiceError(f"directory is not private (expected 0700): {path}")


def ensure_directory(path: Path) -> None:
    if path.is_symlink():
        raise ServiceError(f"refusing symlink directory: {path}")
    if path.exists() and not path.is_dir():
        raise ServiceError(f"expected directory: {path}")
    path.mkdir(parents=True, exist_ok=True)


def assert_private_file(path: Path) -> None:
    if path.is_symlink():
        raise ServiceError(f"refusing symlink file: {path}")
    if path.exists() and not path.is_file():
        raise ServiceError(f"expected file: {path}")
    path.chmod(0o600)
    if mode(path) != 0o600:
        raise ServiceError(f"file is not private (expected 0600): {path}")


def normalize_absolute(path_value: str, field: str) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise ServiceError(f"{field} must be an absolute path")
    return path.resolve()


def root_from(value: str | None) -> Path:
    return normalize_absolute(value or str(DEFAULT_ROOT), "service root")


def runtime_root_from(value: str | None) -> Path:
    path = Path(value or str(DEFAULT_RUNTIME_ROOT)).expanduser()
    if not path.is_absolute():
        raise ServiceError("runtime root must be an absolute path")
    # Keep the lexical path. macOS resolves /tmp to /private/tmp, but the
    # shorter spelling is part of the Unix-domain-socket path budget.
    result = Path(os.path.abspath(os.fspath(path)))
    return result


def validate_label(value: str) -> str:
    if not LABEL_PATTERN.fullmatch(value):
        raise ServiceError("service label must contain only letters, digits, '.', '_' and '-'")
    return value


def validate_profile_id(value: str) -> str:
    if not PROFILE_PATTERN.fullmatch(value):
        raise ServiceError("profile id must contain only letters, digits, '.', '_' and '-'")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ServiceError(f"cannot read binary {path}: {error}") from error
    return digest.hexdigest()


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def executable_path(value: str) -> Path:
    path = normalize_absolute(value, "host binary")
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ServiceError(f"host binary is not executable: {path}")
    return path


def executable_file(value: str, field: str) -> Path:
    path = normalize_absolute(value, field)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ServiceError(f"{field} is not executable: {path}")
    return path


def verify_obscura_entrypoint(binary: Path) -> None:
    try:
        result = subprocess.run(
            [str(binary), "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ServiceError(f"cannot inspect host binary {binary}: {error}") from error
    if result.returncode != 0:
        raise ServiceError(f"host binary --help failed with exit {result.returncode}")
    if "Local persistent browser Session" not in (result.stdout + result.stderr):
        raise ServiceError("host binary is not the supported obscura-host entrypoint")


def config_path(root: Path) -> Path:
    return root / CONFIG_NAME


def existing_profile_root(root: Path) -> str | None:
    path = config_path(root)
    if not path.is_file():
        return None
    if mode(path) != 0o600:
        raise ServiceError(f"existing config permissions must be 0600: {path}")
    config = load_json(path, "service config")
    version = config.get("config_version")
    if type(version) is not int or version not in {1, CONFIG_VERSION}:
        raise ServiceError("cannot preserve profile root from unsupported service config")
    if version == 1:
        if "profile_root" in config:
            raise ServiceError("legacy service config cannot contain profile_root")
        return None
    if "profile_root" not in config:
        raise ServiceError("v2 service config is missing profile_root")
    value = config.get("profile_root")
    if value is None:
        if config.get("profile_mode") != "in_memory":
            raise ServiceError("existing persistent service config requires profile_root")
        return None
    if not isinstance(value, str) or not value:
        raise ServiceError("existing service config profile_root is invalid")
    normalized = normalize_absolute(value, "existing profile root")
    if str(normalized) != value:
        raise ServiceError("existing service config profile_root must be canonical")
    if config.get("profile_mode") != "persistent":
        raise ServiceError("existing profile_root requires persistent mode")
    return value


def state_path(root: Path) -> Path:
    return root / "run" / STATE_NAME


def log_path(root: Path) -> Path:
    return root / "logs" / "host.log"


def load_json(path: Path, description: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ServiceError(f"invalid {description}: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ServiceError(f"{description} must be a JSON object: {path}")
    return value


def write_bytes(path: Path, value: bytes, mode_value: int = 0o600) -> None:
    assert_private_directory(path.parent)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, mode_value)
        with os.fdopen(fd, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        path.chmod(mode_value)
        if stat.S_IMODE(path.stat().st_mode) != stat.S_IMODE(mode_value):
            raise ServiceError(
                f"file mode mismatch (expected {oct(mode_value)}): {path}"
            )
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, value: dict[str, object]) -> None:
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    write_bytes(path, content.encode("utf-8"), 0o600)


def expected_config_keys(config_version: object) -> set[str]:
    keys = {
        "config_version",
        "config_id",
        "service_label",
        "host_binary",
        "binary_sha256",
        "protocol_version",
        "profile_id",
        "profile_mode",
        "allow_private_network",
        "runtime_root",
    }
    if config_version == CONFIG_VERSION:
        keys.add("profile_root")
    return keys


def load_config(root: Path) -> tuple[dict[str, object], str]:
    path = config_path(root)
    if not path.is_file():
        raise ServiceError(f"service is not installed: missing {path}", 1)
    if mode(path) != 0o600:
        raise ServiceError(f"config permissions must be 0600: {path}")
    config = load_json(path, "service config")
    config_version = config.get("config_version")
    if type(config_version) is not int or config_version not in {1, CONFIG_VERSION}:
        raise ServiceError("unsupported host-service config version")
    expected_keys = expected_config_keys(config_version)
    missing = sorted(expected_keys - set(config))
    if missing:
        raise ServiceError(f"service config is missing keys: {', '.join(missing)}")
    unknown = sorted(set(config) - expected_keys)
    if unknown:
        raise ServiceError(f"unsupported config keys: {', '.join(unknown)}")
    if not isinstance(config.get("config_id"), str) or not config["config_id"]:
        raise ServiceError("config_id is required")
    service_label = config.get("service_label")
    if not isinstance(service_label, str):
        raise ServiceError("service_label must be a string")
    validate_label(service_label)
    host_binary = config.get("host_binary")
    if not isinstance(host_binary, str):
        raise ServiceError("host_binary must be a string")
    binary = executable_path(host_binary)
    if str(binary) != config["host_binary"]:
        raise ServiceError("host_binary must be the canonical absolute path")
    binary_sha = config.get("binary_sha256")
    if not isinstance(binary_sha, str) or not SHA256_PATTERN.fullmatch(binary_sha):
        raise ServiceError("binary_sha256 is invalid")
    protocol_version_from(config.get("protocol_version"), "host protocol version")
    profile_id = config.get("profile_id")
    if not isinstance(profile_id, str):
        raise ServiceError("profile_id must be a string")
    validate_profile_id(profile_id)
    profile_mode = config.get("profile_mode")
    if profile_mode not in {"in_memory", "persistent"}:
        raise ServiceError("profile_mode must be in_memory or persistent")
    if config_version == 1:
        if profile_mode != "in_memory":
            raise ServiceError("legacy config cannot enable persistent profile mode")
        profile_root_value = None
    else:
        profile_root_value = config.get("profile_root")
    if profile_root_value is not None:
        if not isinstance(profile_root_value, str):
            raise ServiceError("profile_root must be an absolute path or null")
        profile_root = normalize_absolute(profile_root_value, "profile root")
        if str(profile_root) != profile_root_value:
            raise ServiceError("profile_root must be the canonical absolute path")
        if profile_mode != "persistent":
            raise ServiceError("profile_root requires profile_mode persistent")
    else:
        profile_root = None
        if profile_mode != "in_memory":
            raise ServiceError("persistent profile_mode requires profile_root")
    if not isinstance(config.get("allow_private_network"), bool):
        raise ServiceError("allow_private_network must be boolean")
    runtime_root = runtime_root_from(str(config.get("runtime_root", "")))
    if str(runtime_root) != config["runtime_root"]:
        raise ServiceError("runtime_root must be the canonical absolute path")
    # Normalize the pre-profile config version to the complete v2 shape so
    # every downstream consumer reads one config identity. The legacy schema
    # never carried a root and cannot enable persistence.
    config["profile_root"] = str(profile_root) if profile_root is not None else None
    assert_private_directory(root)
    assert_private_directory(root / "run")
    assert_private_directory(root / "logs")
    assert_private_directory(runtime_root)
    return config, sha256_path(path)


def load_state(root: Path) -> dict[str, object] | None:
    path = state_path(root)
    if not path.exists():
        return None
    if mode(path) != 0o600:
        raise ServiceError(f"state permissions must be 0600: {path}")
    state = load_json(path, "service state")
    validate_state(state)
    return state


def validate_state(state: dict[str, object]) -> None:
    expected = {
        "state_version",
        "service_label",
        "pid",
        "host_binary",
        "binary_sha256",
        "protocol_version",
        "profile_id",
        "profile_mode",
        "profile_root",
        "runtime_root",
        "socket_dir",
        "session_id",
        "config_id",
        "config_sha256",
        "launch_mode",
        "started_at_unix",
        "ready_error",
    }
    missing = sorted(expected - set(state) - {"ready_error", "profile_root"})
    unknown = sorted(set(state) - expected)
    if missing:
        raise ServiceError(f"service state is missing keys: {', '.join(missing)}")
    if unknown:
        raise ServiceError(f"unsupported service state keys: {', '.join(unknown)}")
    if type(state["state_version"]) is not int or state["state_version"] != 1:
        raise ServiceError("unsupported service state version")
    if type(state["pid"]) is not int or state["pid"] <= 0:
        raise ServiceError("service state has invalid pid")
    service_label = state["service_label"]
    if not isinstance(service_label, str):
        raise ServiceError("service state service_label must be a string")
    validate_label(service_label)
    host_binary = state["host_binary"]
    if not isinstance(host_binary, str):
        raise ServiceError("service state host_binary must be a string")
    if str(normalize_absolute(host_binary, "service state host_binary")) != host_binary:
        raise ServiceError("service state host_binary must be canonical")
    binary_sha = state["binary_sha256"]
    if not isinstance(binary_sha, str) or not SHA256_PATTERN.fullmatch(binary_sha):
        raise ServiceError("service state binary_sha256 is invalid")
    protocol_version_from(state["protocol_version"], "service state protocol version")
    profile_id = state["profile_id"]
    if not isinstance(profile_id, str):
        raise ServiceError("service state profile_id must be a string")
    validate_profile_id(profile_id)
    if state["profile_mode"] not in {"in_memory", "persistent"}:
        raise ServiceError("service state profile_mode is invalid")
    state_profile_root = state.get("profile_root")
    if state_profile_root is not None:
        if not isinstance(state_profile_root, str):
            raise ServiceError("service state profile_root must be a string or null")
        normalized_root = normalize_absolute(state_profile_root, "service state profile root")
        if str(normalized_root) != state_profile_root:
            raise ServiceError("service state profile_root must be canonical")
        if state["profile_mode"] != "persistent":
            raise ServiceError("service state profile_root requires persistent mode")
    elif state["profile_mode"] == "persistent":
        raise ServiceError("persistent service state requires profile_root")
    runtime_root_value = state["runtime_root"]
    if not isinstance(runtime_root_value, str):
        raise ServiceError("service state runtime_root must be a string")
    runtime_root = runtime_root_from(runtime_root_value)
    if str(runtime_root) != runtime_root_value:
        raise ServiceError("service state runtime_root must be canonical")
    socket_dir_value = state["socket_dir"]
    if not isinstance(socket_dir_value, str):
        raise ServiceError("service state socket_dir must be a string")
    socket_dir = Path(socket_dir_value)
    if not socket_dir_is_owned(socket_dir, runtime_root):
        raise ServiceError("service state socket_dir is not service-owned")
    config_id = state["config_id"]
    if not isinstance(config_id, str) or not config_id:
        raise ServiceError("service state config_id is required")
    config_sha = state["config_sha256"]
    if not isinstance(config_sha, str) or not SHA256_PATTERN.fullmatch(config_sha):
        raise ServiceError("service state config_sha256 is invalid")
    launch_mode = state["launch_mode"]
    if not isinstance(launch_mode, str) or launch_mode not in {
        "subprocess",
        "subprocess_starting",
        "launchd_starting",
        "launchd",
        "launchd_rejected",
    }:
        raise ServiceError("service state launch_mode is invalid")
    session_id = state["session_id"]
    ready_error = state.get("ready_error")
    if ready_error is not None and not isinstance(ready_error, str):
        raise ServiceError("service state ready_error must be a string or null")
    if session_id is not None and (not isinstance(session_id, str) or not session_id):
        raise ServiceError("service state session_id is invalid")
    if launch_mode == "subprocess" and session_id is None:
        raise ServiceError("subprocess service state requires session_id")
    if launch_mode == "subprocess_starting" and session_id is not None:
        raise ServiceError("starting subprocess state cannot have session_id")
    if launch_mode == "launchd_starting" and session_id is not None:
        raise ServiceError("starting launchd state cannot have session_id")
    if launch_mode == "launchd" and session_id is None:
        raise ServiceError("launchd service state requires session_id")
    if launch_mode == "launchd_rejected":
        if session_id is not None:
            raise ServiceError("rejected launchd state cannot have session_id")
        if not isinstance(ready_error, str) or not ready_error:
            raise ServiceError("rejected launchd state requires ready_error")
    elif ready_error is not None:
        raise ServiceError("ready_error is only valid in rejected launchd state")
    if type(state["started_at_unix"]) is not int or state["started_at_unix"] <= 0:
        raise ServiceError("service state started_at_unix is invalid")


def pid_from_state(state: dict[str, object]) -> int:
    value = state.get("pid")
    if type(value) is not int or value <= 0:
        raise ServiceError("service state has invalid pid")
    return value


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_argv(pid: int) -> list[str] | None:
    """Return the exact process argv on macOS without shell re-parsing.

    ``ps`` prints one command string, so a path containing a space cannot be
    reconstructed into its original argument boundaries. ``KERN_PROCARGS2``
    returns the kernel-held argv entries as NUL-separated bytes; this is the
    identity basis for owned Host and endpoint processes.
    """
    if sys.platform != "darwin":
        return None
    libc = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, pid)
    size = ctypes.c_size_t()
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        return None
    if size.value < 4 or size.value > 1024 * 1024:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
        return None
    data = buffer.raw[: size.value]
    argc = int.from_bytes(data[:4], sys.byteorder)
    if argc <= 0:
        return None

    def read_string(offset: int) -> tuple[str, int] | None:
        end = data.find(b"\x00", offset)
        if end < 0:
            return None
        try:
            return data[offset:end].decode("utf-8"), end + 1
        except UnicodeDecodeError:
            return None

    first = read_string(4)
    if first is None:
        return None
    cursor = first[1]
    while cursor < len(data) and data[cursor] == 0:
        cursor += 1
    second = read_string(cursor)
    if second is None:
        return None
    argv: list[str] = []
    argv.append(second[0])
    cursor = second[1]
    for _ in range(1, argc):
        entry = read_string(cursor)
        if entry is None:
            return None
        argv.append(entry[0])
        cursor = entry[1]
    return argv


def process_snapshot(pid: int) -> tuple[str, str] | None:
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "stat=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    snapshot = result.stdout.strip()
    if not snapshot:
        return None
    parts = snapshot.split(None, 1)
    return parts[0], parts[1] if len(parts) == 2 else ""


def host_command_matches(
    argv: list[str],
    binary: str,
    socket_dir: str,
    profile_root: str | None,
    access_token: str,
) -> bool:
    if len(argv) < 5 or argv[:3] != [binary, "--socket-dir", socket_dir]:
        return False
    remainder = argv[3:]
    if remainder[:2] != ["--access-token", access_token]:
        return False
    remainder = remainder[2:]
    # Only the exact declared flags may follow. Each optional flag is present
    # at most once; the order is fixed by host_command so ownership comparison
    # stays exact and an unexpected argument is rejected as a foreign PID.
    if profile_root is not None:
        if remainder[:2] != ["--profile-root", profile_root]:
            return False
        remainder = remainder[2:]
    if remainder == ["--allow-private-network"]:
        return True
    return remainder == []


def stop_target_status(
    pid: int,
    binary: str,
    socket_dir: str,
    profile_root: str | None,
    access_token: str,
) -> str:
    snapshot = process_snapshot(pid)
    if snapshot is None:
        if pid_alive(pid):
            return "unknown"
        return "gone"
    if snapshot[0].startswith("Z"):
        return "gone"
    argv = process_argv(pid)
    if argv is None:
        return "unknown"
    if not host_command_matches(argv, binary, socket_dir, profile_root, access_token):
        return "changed"
    return "owned"


def stop_target_is_verified(
    pid: int,
    binary: str,
    socket_dir: str,
    profile_root: str | None,
    access_token: str,
) -> bool:
    status = stop_target_status(pid, binary, socket_dir, profile_root, access_token)
    if status == "gone":
        return False
    if status != "owned":
        raise ServiceError(f"Host PID identity changed; refusing to signal: {pid}")
    return True


def state_identity(root: Path, state: dict[str, object]) -> tuple[bool, str]:
    pid = pid_from_state(state)
    if not pid_alive(pid):
        return False, "dead"
    snapshot = process_snapshot(pid)
    if snapshot is None:
        return False, "foreign"
    if snapshot[0].startswith("Z"):
        return False, "dead"
    argv = process_argv(pid)
    if argv is None:
        return False, "foreign"
    command = " ".join(argv)
    binary = str(state.get("host_binary", ""))
    socket_dir = str(state.get("socket_dir", ""))
    profile_root = state.get("profile_root")
    # The service always starts the Host with `--access-token`. A recorded state
    # with no committed token cannot be confirmed as this service's Host, so it
    # is foreign and is never signalled.
    try:
        access_token = load_token(root)
    except ServiceError:
        return False, "foreign"
    if not host_command_matches(
        argv,
        binary,
        socket_dir,
        profile_root if isinstance(profile_root, str) else None,
        access_token,
    ):
        return False, "foreign"
    return True, command


def socket_dir_is_owned(path: Path, runtime_root: Path) -> bool:
    return (
        path.is_absolute()
        and not path.is_symlink()
        and path.parent == runtime_root
        and path.name.startswith("host-")
    )


def cleanup_runtime(state: dict[str, object]) -> None:
    socket_dir = Path(str(state.get("socket_dir", "")))
    runtime_root = Path(str(state.get("runtime_root", "")))
    remove_owned_runtime(socket_dir, runtime_root)


def remove_owned_runtime(socket_dir: Path, runtime_root: Path) -> None:
    if not socket_dir_is_owned(socket_dir, runtime_root):
        raise ServiceError(f"refusing to remove unowned runtime path: {socket_dir}")
    if socket_dir.exists():
        if socket_dir.is_symlink() or not socket_dir.is_dir():
            raise ServiceError(f"refusing to remove non-directory runtime path: {socket_dir}")
        shutil.rmtree(socket_dir)


def remove_state(root: Path) -> None:
    try:
        state_path(root).unlink()
    except FileNotFoundError:
        pass


def ready_message(
    socket_path: Path,
    timeout_seconds: float,
    expected_version: object,
    pid: int | None = None,
) -> dict[str, object]:
    expected_version = protocol_version_from(expected_version, "expected host protocol version")
    deadline = time.monotonic() + timeout_seconds
    last_error = "not ready"
    while time.monotonic() < deadline:
        if pid is not None and not pid_alive(pid):
            raise ServiceError(f"Host exited before ready response (pid {pid})")
        remaining = max(0.05, min(1.0, deadline - time.monotonic()))
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(remaining)
        try:
            client.connect(str(socket_path))
            data = b""
            while b"\n" not in data and len(data) <= 4096:
                chunk = client.recv(4096)
                if not chunk:
                    break
                data += chunk
            if b"\n" not in data:
                raise ReadyRejected("Host closed before ready response")
            value = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
            if not isinstance(value, dict) or value.get("type") != "ready":
                raise ReadyRejected("Host ready response has invalid type")
            ready_version = protocol_version_from(value.get("version"), "Host ready protocol version")
            if ready_version != expected_version:
                raise ReadyRejected(
                    f"Host protocol version is {ready_version}, expected {expected_version}"
                )
            session_id = value.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise ReadyRejected("Host ready response has no session_id")
            return value
        except ReadyRejected:
            raise
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            last_error = str(error)
        finally:
            client.close()
        time.sleep(0.05)
    raise ServiceError(f"Host did not become ready within {timeout_seconds:.1f}s: {last_error}")


def socket_path_is_safe(socket_dir: Path) -> None:
    for name in ("host.sock", "frames.sock"):
        path = socket_dir / name
        if len(os.fsencode(str(path))) >= SOCKET_PATH_LIMIT:
            raise ServiceError(
                f"{name} path is too long for Unix sockets: {path}; choose a shorter --runtime-root"
            )
def acquire_start_lock(root: Path, *, blocking: bool = False) -> int:
    lock = root / "run" / ".start.lock"
    assert_private_directory(root)
    assert_private_directory(lock.parent)
    if lock.is_symlink():
        raise ServiceError(f"refusing symlink lifecycle lock: {lock}")
    if lock.exists() and not lock.is_file():
        raise ServiceError(f"expected lifecycle lock file: {lock}")
    fd: int | None = None
    try:
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        if fd is not None:
            os.close(fd)
        raise ServiceError("another lifecycle mutation is in progress") from error
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise
    assert fd is not None
    try:
        os.fchmod(fd, 0o600)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.fsync(fd)
    except BaseException:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        raise
    return fd


def release_start_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def make_socket_dir(runtime_root: Path) -> Path:
    # The real Obscura Host binds an additional `session-<36-char-uuid>.sock`
    # inside this directory, which must stay under the 104-byte macOS Unix
    # socket bound. Keep the owned directory name short so an ordinary private
    # runtime root still fits, and let the Host report an explicit error if a
    # caller supplies a runtime root that cannot.
    for _ in range(5):
        path = runtime_root / f"host-{uuid.uuid4().hex[:8]}"
        if not path.exists():
            socket_path_is_safe(path)
            return path
    raise ServiceError("could not allocate a fresh Host socket directory")


def state_for(
    config: dict[str, object],
    config_hash: str,
    pid: int,
    socket_dir: Path,
    mode_name: str,
    session_id: str | None,
    ready_error: str | None = None,
) -> dict[str, object]:
    return {
        "state_version": 1,
        "service_label": config["service_label"],
        "pid": pid,
        "host_binary": config["host_binary"],
        "binary_sha256": config["binary_sha256"],
        "protocol_version": config["protocol_version"],
        "profile_id": config["profile_id"],
        "profile_mode": config["profile_mode"],
        "profile_root": config["profile_root"],
        "runtime_root": config["runtime_root"],
        "socket_dir": str(socket_dir),
        "session_id": session_id,
        "config_id": config["config_id"],
        "config_sha256": config_hash,
        "launch_mode": mode_name,
        "started_at_unix": int(time.time()),
        "ready_error": ready_error,
    }


def stop_child_process(process: subprocess.Popen[object]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    if process.poll() is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as error:
        raise ServiceError(f"Host child did not stop after owned termination: {process.pid}") from error


def stop_pid(
    pid: int,
    binary: str,
    socket_dir: str,
    profile_root: str | None,
    access_token: str,
) -> None:
    if not stop_target_is_verified(pid, binary, socket_dir, profile_root, access_token):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = stop_target_status(pid, binary, socket_dir, profile_root, access_token)
        if status == "gone":
            return
        time.sleep(0.05)
    term_status = stop_target_status(pid, binary, socket_dir, profile_root, access_token)
    if term_status == "gone":
        return
    if term_status != "owned":
        raise ServiceError(f"Host PID identity changed; refusing to signal: {pid}")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = stop_target_status(pid, binary, socket_dir, profile_root, access_token)
        if status == "gone":
            return
        if status != "owned":
            raise ServiceError(f"Host PID identity changed; refusing to signal: {pid}")
        time.sleep(0.05)
    final_status = stop_target_status(pid, binary, socket_dir, profile_root, access_token)
    if final_status == "owned":
        raise ServiceError(f"Host did not stop after explicit PID termination: {pid}")
    if final_status != "gone":
        raise ServiceError(f"cannot verify Host PID after termination: {pid}")


def stale_or_running(root: Path, state: dict[str, object] | None) -> str:
    if state is None:
        return "none"
    alive, detail = state_identity(root, state)
    if alive:
        return "running"
    if detail == "foreign":
        raise ServiceError(f"recorded PID is owned by another process; refusing to start: {state.get('pid')}")
    return "stale"


def cleanup_stale(root: Path, state: dict[str, object]) -> None:
    if pid_alive(pid_from_state(state)):
        raise ServiceError("cannot clean state while its PID is alive")
    cleanup_runtime(state)
    remove_state(root)


def host_command(
    config: dict[str, object], socket_dir: Path, access_token: str
) -> list[str]:
    """The Host argv. The access token is the one credential on this path."""
    command = [
        str(config["host_binary"]),
        "--socket-dir",
        str(socket_dir),
        "--access-token",
        access_token,
    ]
    profile_root = config.get("profile_root")
    if profile_root is not None:
        command.extend(["--profile-root", str(profile_root)])
    if config["allow_private_network"]:
        command.append("--allow-private-network")
    return command


def flush_profiles(socket_path: Path, expected_version: object) -> int:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(10)
    try:
        client.connect(str(socket_path))
        ready = json.loads(client.makefile("rb").readline().decode("utf-8"))
        if not isinstance(ready, dict) or ready.get("type") != "ready":
            raise ServiceError("cannot confirm profile flush: invalid Host ready response")
        ready_version = protocol_version_from(ready.get("version"), "Host ready protocol version")
        if ready_version != protocol_version_from(expected_version, "expected host protocol version"):
            raise ServiceError(
                f"cannot confirm profile flush: Host protocol version is {ready_version}, "
                f"expected {expected_version}"
            )
        request = {"id": 1, "command": {"type": "flush_profiles"}}
        client.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
        response = json.loads(client.makefile("rb").readline().decode("utf-8"))
        if not isinstance(response, dict) or response.get("id") != 1:
            raise ServiceError("cannot confirm profile flush: invalid Host maintenance response")
        if response.get("type") == "error":
            code = response.get("code")
            message = response.get("message")
            raise ServiceError(f"profile flush failed ({code}): {message}")
        if response.get("type") != "result":
            raise ServiceError("cannot confirm profile flush: Host did not return a typed result")
        value = response.get("value")
        if not isinstance(value, dict) or "flushed" not in value or "profiles" not in value:
            raise ServiceError("cannot confirm profile flush: Host returned an unexpected result")
        if value.get("flushed") is not True:
            raise ServiceError("profile flush did not report flushed=true")
        profiles = value.get("profiles")
        if type(profiles) is not int or profiles < 0:
            raise ServiceError("profile flush returned an invalid profile count")
        return profiles
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ServiceError(f"cannot confirm profile flush: {error}") from error
    finally:
        client.close()


def flush_loaded_profiles(socket_dir: Path, protocol_version: object, profile_mode: object) -> None:
    if profile_mode != "persistent":
        return
    flush_profiles(socket_dir / "host.sock", protocol_version)


# Live ready probe used by the pre-stop flush. It matches the one-second probe
# the status command uses, so both report the same verdict for a Host that
# stopped answering.
READY_PROBE_TIMEOUT = 1.0


def host_answers_ready(state: dict[str, object]) -> bool:
    """Report whether the recorded Host still answers its ready handshake.

    Only an unanswered handshake is False. A typed rejection is a real answer:
    it stays with the flush path, which reports it exactly as before.
    """
    try:
        ready_message(
            Path(str(state.get("socket_dir", ""))) / "host.sock",
            READY_PROBE_TIMEOUT,
            state.get("protocol_version"),
        )
    except ReadyRejected:
        return True
    except ServiceError:
        return False
    return True


def flush_before_stop(state: dict[str, object]) -> None:
    """Flush a still-answering Host before the stop signal.

    The flush runs before the signal, so an owned Host that no longer answers
    its ready handshake made it time out and aborted the stop before the
    verified-PID path ran. That left the wedged Host alive and the service
    unrecoverable. Such a Host cannot confirm a flush either, so it is reported
    and then stopped and reaped without one.
    """
    if not host_answers_ready(state):
        print(
            "host-service: owned Host did not answer ready; "
            "skipping the profile flush before stop",
            file=sys.stderr,
        )
        return
    flush_loaded_profiles(
        Path(str(state.get("socket_dir", ""))),
        state.get("protocol_version"),
        state.get("profile_mode"),
    )


def supervise_launchd_process(
    process: subprocess.Popen[object],
    root: Path,
    config: dict[str, object],
    config_hash: str,
    socket_dir: Path,
) -> int:
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    # The signal handler only records the request. Flushing and reaping must
    # run in the main thread: Popen.wait() is not reentrant, so stopping the
    # child from inside a handler that interrupted an outer wait() would
    # deadlock until the stop deadline expired.
    previous_handlers = {
        signum: signal.signal(signum, request_stop)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        while process.poll() is None:
            if stopping:
                exit_code = 0
                try:
                    flush_loaded_profiles(
                        socket_dir,
                        config["protocol_version"],
                        config["profile_mode"],
                    )
                    stop_child_process(process)
                    cleanup_runtime(state_for(config, config_hash, process.pid, socket_dir, "launchd", "closed"))
                    remove_state(root)
                except ServiceError as error:
                    # The confirmed flush is the precondition for terminating
                    # the launchd-owned child. A busy or failed store leaves the
                    # Host and its state in place and reports the typed error; it
                    # never claims a saved stop.
                    print(f"host-service: {error}", file=sys.stderr)
                    exit_code = error.exit_code
                return exit_code
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
        cleanup_runtime(state_for(config, config_hash, process.pid, socket_dir, "launchd", "closed"))
        remove_state(root)
        return process.returncode or 0
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)


def write_plist(config: dict[str, object], root: Path, launchd_root: Path) -> Path:
    ensure_directory(launchd_root)
    label = str(config["service_label"])
    plist = launchd_root / f"{label}.plist"
    script = Path(__file__).resolve()
    arguments = [sys.executable, str(script), "launchd", "--root", str(root)]

    def text_element(value: str) -> str:
        return f"<string>{xml_escape(value)}</string>"

    argument_xml = "\n".join(text_element(value) for value in arguments)
    content = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
        "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
        "<plist version=\"1.0\"><dict>\n"
        f"<key>Label</key>{text_element(label)}\n"
        f"<key>ProgramArguments</key><array>\n{argument_xml}\n</array>\n"
        "<key>RunAtLoad</key><true/>\n"
        "<key>KeepAlive</key><dict><key>SuccessfulExit</key><true/></dict>\n"
        "<key>ThrottleInterval</key><integer>5</integer>\n"
        f"<key>WorkingDirectory</key>{text_element(str(root))}\n"
        f"<key>StandardOutPath</key>{text_element(str(log_path(root)))}\n"
        f"<key>StandardErrorPath</key>{text_element(str(log_path(root)))}\n"
        "</dict></plist>\n"
    )
    fd, temporary_name = tempfile.mkstemp(prefix=f".{plist.name}.", dir=launchd_root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, plist)
        plist.chmod(0o600)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return plist


def install_locked(args: argparse.Namespace, root: Path) -> int:
    binary = executable_path(args.binary)
    label = validate_label(args.service_label)
    profile_id = validate_profile_id(args.profile_id)
    protocol_version = protocol_version_from(args.protocol_version, "protocol version")
    runtime_root = runtime_root_from(args.runtime_root)
    profile_root_value = args.profile_root
    if profile_root_value is None:
        profile_root_value = existing_profile_root(root)
    profile_root = (
        normalize_absolute(profile_root_value, "profile root")
        if profile_root_value is not None
        else None
    )
    existing = load_state(root) if state_path(root).is_file() else None
    if existing is not None:
        state_status = stale_or_running(root, existing)
        if state_status == "running":
            raise ServiceError("cannot reinstall while Host is running; stop it first")
        cleanup_stale(root, existing)
    verify_obscura_entrypoint(binary)
    assert_private_directory(root)
    assert_private_directory(root / "run")
    assert_private_directory(root / "logs")
    assert_private_directory(runtime_root)
    config = {
        "config_version": CONFIG_VERSION,
        "config_id": uuid.uuid4().hex,
        "service_label": label,
        "host_binary": str(binary),
        "binary_sha256": sha256_file(binary),
        "protocol_version": protocol_version,
        "profile_id": profile_id,
        "profile_mode": "persistent" if profile_root is not None else "in_memory",
        "profile_root": str(profile_root) if profile_root is not None else None,
        "allow_private_network": bool(args.allow_private_network),
        "runtime_root": str(runtime_root),
    }
    write_json(config_path(root), config)
    # The Host compares one token and nothing else, so it must exist before the
    # first start. `install` owns it: created here, never rotated, so the user
    # path is install -> start -> panel -> export with no separate trust step.
    ensure_access_token(root)
    log = log_path(root)
    if not log.exists():
        log.touch(mode=0o600)
    assert_private_file(log)
    launchd_root = normalize_absolute(args.launchd_root, "launchd root") if args.launchd_root else root / "launchd"
    plist = write_plist(config, root, launchd_root)
    emit({
        "state": "installed",
        "config": str(config_path(root)),
        "service_label": label,
        "host_binary": str(binary),
        "binary_sha256": config["binary_sha256"],
        "protocol_version": config["protocol_version"],
        "profile_id": profile_id,
        "profile_mode": config["profile_mode"],
        "profile_root": config["profile_root"],
        "runtime_root": str(runtime_root),
        "launchd_plist": str(plist),
        "launchd_loaded": False,
    })
    return 0


def command_install(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    lock_fd = acquire_start_lock(root)
    try:
        return install_locked(args, root)
    finally:
        release_start_lock(lock_fd)


def configured_binary(config: dict[str, object]) -> Path:
    binary = executable_path(str(config["host_binary"]))
    if sha256_file(binary) != config["binary_sha256"]:
        raise ServiceError("host binary changed; reinstall to bind a new binary identity")
    return binary


def start_locked(args: argparse.Namespace, root: Path, config: dict[str, object], config_hash: str) -> int:
    binary = configured_binary(config)
    process = None
    socket_dir: Path | None = None
    log_handle = None
    state_written = False
    try:
        existing = load_state(root)
        state_status = stale_or_running(root, existing)
        if state_status == "running":
            raise ServiceError(f"Host already running (pid {existing['pid']})")
        if state_status == "stale" and existing is not None:
            cleanup_stale(root, existing)
        socket_dir = make_socket_dir(runtime_root_from(str(config["runtime_root"])))
        log_handle = log_path(root).open("ab")
        command = host_command(config, socket_dir, load_token(root))
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
        write_json(
            state_path(root),
            state_for(config, config_hash, process.pid, socket_dir, "subprocess_starting", None),
        )
        state_written = True
        ready = ready_message(
            socket_dir / "host.sock",
            args.ready_timeout,
            config["protocol_version"],
            process.pid,
        )
        state = state_for(config, config_hash, process.pid, socket_dir, "subprocess", str(ready["session_id"]))
        write_json(state_path(root), state)
        emit({
            "state": "running",
            "service_label": config["service_label"],
            "pid": process.pid,
            "session_id": ready["session_id"],
            "protocol_version": config["protocol_version"],
            "profile_id": config["profile_id"],
            "profile_mode": config["profile_mode"],
            "profile_root": config["profile_root"],
            "socket_dir": str(socket_dir),
            "config_id": config["config_id"],
            "config_sha256": config_hash,
        })
        return 0
    except BaseException:
        if process is not None:
            stop_child_process(process)
        if socket_dir is not None:
            remove_owned_runtime(socket_dir, runtime_root_from(str(config["runtime_root"])))
        if state_written:
            remove_state(root)
        raise
    finally:
        if log_handle is not None:
            log_handle.close()


def command_start(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    lock_fd = acquire_start_lock(root)
    try:
        config, config_hash = load_config(root)
        return start_locked(args, root, config, config_hash)
    finally:
        release_start_lock(lock_fd)


def command_status(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    config, config_hash = load_config(root)
    state = load_state(root)
    payload: dict[str, object] = {
        "service_label": config["service_label"],
        "host_binary": config["host_binary"],
        "binary_sha256": config["binary_sha256"],
        "protocol_version": config["protocol_version"],
        "profile_id": config["profile_id"],
        "profile_mode": config["profile_mode"],
        "profile_root": config["profile_root"],
        "runtime_root": config["runtime_root"],
        "config_id": config["config_id"],
        "config_sha256": config_hash,
        "config_permissions": oct(mode(config_path(root))),
    }
    summary = trust_summary(root)
    if summary is not None:
        payload.update(summary)
    if state is None:
        payload["state"] = "stopped"
        emit(payload)
        return 0
    if state.get("launch_mode") == "launchd_rejected":
        payload.update({
            "pid": state.get("pid"),
            "socket_dir": state.get("socket_dir"),
            "state_config_id": state.get("config_id"),
            "state_config_sha256": state.get("config_sha256"),
            "config_identity_match": state.get("config_id") == config["config_id"] and state.get("config_sha256") == config_hash,
        })
        payload["state"] = "ready_rejected"
        payload["ready_error"] = state.get("ready_error")
        emit(payload)
        return 1
    alive, detail = state_identity(root, state)
    payload.update({
        "pid": state.get("pid"),
        "session_id": state.get("session_id"),
        "socket_dir": state.get("socket_dir"),
        "state_config_id": state.get("config_id"),
        "state_config_sha256": state.get("config_sha256"),
        "config_identity_match": state.get("config_id") == config["config_id"] and state.get("config_sha256") == config_hash,
    })
    if detail == "foreign":
        payload["state"] = "foreign_pid"
        emit(payload)
        return 1
    if not alive:
        payload["state"] = "stale"
        emit(payload)
        return 1
    socket_dir = Path(str(state.get("socket_dir", "")))
    if not socket_dir.is_dir():
        payload["state"] = "starting" if state.get("launch_mode") in {"subprocess_starting", "launchd_starting"} else "stale"
        payload["ready_error"] = "Host runtime directory is not present" if payload["state"] == "stale" else None
        emit(payload)
        return 0 if payload["state"] == "starting" else 1
    try:
        ready = ready_message(
            socket_dir / "host.sock",
            1.0,
            config["protocol_version"],
        )
    except ReadyRejected as error:
        payload["state"] = "ready_rejected"
        payload["ready_error"] = str(error)
        emit(payload)
        return 1
    except ServiceError as error:
        payload["state"] = "starting"
        payload["ready_error"] = str(error)
        emit(payload)
        return 0
    if ready.get("session_id") != state.get("session_id") and state.get("session_id") is not None:
        payload["state"] = "identity_mismatch"
        payload["ready_session_id"] = ready.get("session_id")
        emit(payload)
        return 1
    payload["session_id"] = ready.get("session_id")
    payload["socket_permissions"] = oct(mode(socket_dir / "host.sock")) if (socket_dir / "host.sock").exists() else None
    payload["state"] = "running"
    emit(payload)
    return 0


def command_stop(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    lock_fd = acquire_start_lock(root)
    try:
        state = load_state(root)
        if state is None:
            emit({"state": "stopped", "service_label": validate_label(args.service_label)})
            return 0
        stop_recorded_state(root, state)
        emit({"state": "stopped", "service_label": state.get("service_label"), "pid": state.get("pid")})
        return 0
    finally:
        release_start_lock(lock_fd)


def stop_recorded_state(root: Path, state: dict[str, object]) -> None:
    alive, detail = state_identity(root, state)
    if detail == "foreign":
        raise ServiceError(f"recorded PID is owned by another process; refusing to stop: {state.get('pid')}")
    if state.get("launch_mode") == "launchd_rejected":
        if alive:
            raise ServiceError("rejected launchd state still has a live PID; refusing cleanup")
        if config_path(root).is_file():
            config, _ = load_config(root)
            cleanup_endpoint_lifecycle(root, config)
        cleanup_runtime(state)
        remove_state(root)
        return
    if alive and state.get("launch_mode") == "launchd":
        raise ServiceError("launchd-managed Host must be stopped through launchctl; refusing subprocess stop")
    if alive:
        # An owned Host that stopped answering is still stopped and reaped: the
        # pre-stop flush reports the skipped flush instead of aborting the stop.
        flush_before_stop(state)
        stop_pid(
            pid_from_state(state),
            str(state.get("host_binary", "")),
            str(state.get("socket_dir", "")),
            str(state["profile_root"]) if state.get("profile_root") is not None else None,
            load_token(root),
        )
    # An owned endpoint is part of this service's lifecycle: stop it through the
    # same declared verified-PID path before removing the Host runtime. A
    # foreign endpoint PID is reported, never silently signaled or deleted.
    if config_path(root).is_file():
        config, _ = load_config(root)
        cleanup_endpoint_lifecycle(root, config)
    cleanup_runtime(state)
    remove_state(root)


def command_restart(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    lock_fd = acquire_start_lock(root)
    try:
        config, config_hash = load_config(root)
        configured_binary(config)
        state = load_state(root)
        if state is not None:
            stop_recorded_state(root, state)
        return start_locked(args, root, config, config_hash)
    finally:
        release_start_lock(lock_fd)


def command_launchd(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    lock_fd = acquire_start_lock(root)
    try:
        config, config_hash = load_config(root)
        binary = configured_binary(config)
        existing = load_state(root)
        state_status = stale_or_running(root, existing)
        if state_status == "running":
            raise ServiceError(f"Host already running (pid {existing['pid']})")
        if state_status == "stale" and existing is not None:
            cleanup_stale(root, existing)
        socket_dir = make_socket_dir(runtime_root_from(str(config["runtime_root"])))
        command = host_command(config, socket_dir, load_token(root))
        write_json(
            state_path(root),
            state_for(config, config_hash, os.getpid(), socket_dir, "launchd_starting", None),
        )
        log_handle = log_path(root).open("ab")
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=True,
            )
            write_json(
                state_path(root),
                state_for(config, config_hash, process.pid, socket_dir, "launchd_starting", None),
            )
            ready = ready_message(
                socket_dir / "host.sock",
                10.0,
                config["protocol_version"],
                process.pid,
            )
            write_json(
                state_path(root),
                state_for(config, config_hash, process.pid, socket_dir, "launchd", str(ready["session_id"])),
            )
        except ReadyRejected as error:
            if "process" in locals() and process is not None:
                stop_child_process(process)
            cleanup_runtime(state_for(config, config_hash, os.getpid(), socket_dir, "launchd_starting", None))
            write_json(
                state_path(root),
                state_for(
                    config,
                    config_hash,
                    os.getpid(),
                    socket_dir,
                    "launchd_rejected",
                    None,
                    str(error),
                ),
            )
            if "process" in locals() and process is not None:
                process.wait(timeout=5)
            raise
        except BaseException:
            if "process" in locals() and process is not None:
                stop_child_process(process)
            cleanup_runtime(state_for(config, config_hash, os.getpid(), socket_dir, "launchd_starting", None))
            remove_state(root)
            raise
        finally:
            log_handle.close()
        return supervise_launchd_process(process, root, config, config_hash, socket_dir)
    finally:
        if lock_fd != -1:
            release_start_lock(lock_fd)


def crypto_modules() -> SimpleNamespace:
    """Load the cryptography primitives the service-owned listener identity needs."""
    import cryptography.hazmat.primitives.asymmetric.ec as ec
    import cryptography.hazmat.primitives.hashes as hashes
    import cryptography.hazmat.primitives.serialization as serialization
    import cryptography.x509 as x509
    import cryptography.x509.oid as oid

    return SimpleNamespace(
        ec=ec, hashes=hashes, serialization=serialization, x509=x509, oid=oid
    )


def generate_server_certificate(
    host: str, host_kind: str, validity_days: int
) -> tuple[bytes, bytes]:
    """Generate the listener's own self-signed TLS identity.

    This certificate authenticates the endpoint to nobody: the client verifies
    no server certificate and the token is the only credential. It exists so the
    endpoint can terminate TLS.
    """
    modules = crypto_modules()
    key = modules.ec.generate_private_key(modules.ec.SECP256R1())
    name = modules.x509.Name(
        [modules.x509.NameAttribute(modules.oid.NameOID.COMMON_NAME, host)]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    subject_alternative_name = (
        modules.x509.IPAddress(ipaddress.ip_address(host))
        if host_kind == "ip"
        else modules.x509.DNSName(host)
    )
    certificate = (
        modules.x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(modules.x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=validity_days))
        .add_extension(
            modules.x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        .add_extension(
            modules.x509.ExtendedKeyUsage(
                [modules.oid.ExtendedKeyUsageOID.SERVER_AUTH]
            ),
            critical=False,
        )
        .add_extension(
            modules.x509.SubjectAlternativeName([subject_alternative_name]),
            critical=False,
        )
        .sign(key, modules.hashes.SHA256())
    )
    return (
        certificate.public_bytes(modules.serialization.Encoding.DER),
        key.private_bytes(
            modules.serialization.Encoding.DER,
            modules.serialization.PrivateFormat.PKCS8,
            modules.serialization.NoEncryption(),
        ),
    )


def endpoint_host_san(value: str) -> tuple[str, str]:
    """Classify the declared public endpoint host as a DNS name or an IP literal."""
    host = value.strip()
    if not host or any(character.isspace() for character in host):
        raise ServiceError(
            "endpoint host is not a bare host or IP literal",
            TRUST_EXIT_CODE,
            code="ENDPOINT_HOST_INVALID",
        )
    try:
        return "ip", str(ipaddress.ip_address(host))
    except ValueError:
        return "dns", host


def trust_root(root: Path) -> Path:
    return root / TRUST_DIR_NAME


def trust_path(root: Path, name: str) -> Path:
    return trust_root(root) / name


def ensure_access_token(root: Path) -> str:
    """Create the connection token once, at install time, and never rotate it.

    The Host compares this token and nothing else, so it must exist before the
    first start. It lives beside the locator, outside the config, so creating it
    cannot change the config hash that service state pins.
    """
    assert_private_directory(root)
    assert_private_directory(trust_root(root))
    path = trust_path(root, TRUST_TOKEN_NAME)
    if path.is_file():
        return load_token(root)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    write_bytes(path, token.encode("ascii"), 0o600)
    return token


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def endpoint_text(host: str, host_kind: str, port: int) -> str:
    """The frozen bundle endpoint: exactly ``<host>:<port>``."""
    if host_kind == "ip" and ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def load_token(root: Path) -> str:
    path = trust_path(root, TRUST_TOKEN_NAME)
    if not path.is_file():
        raise ServiceError(
            "trust authority has no access token",
            TRUST_EXIT_CODE,
            code="TRUST_NOT_INITIALIZED",
        )
    token = path.read_text(encoding="ascii").strip()
    if not token:
        raise ServiceError(
            "trust authority access token is empty",
            TRUST_EXIT_CODE,
            code="TRUST_NOT_INITIALIZED",
        )
    return token


def trust_endpoint(root: Path) -> dict[str, object]:
    return load_json(trust_path(root, TRUST_ENDPOINT_NAME), "trust endpoint")


def trust_init_locked(args: argparse.Namespace, root: Path) -> int:
    host_kind, host = endpoint_host_san(args.endpoint_host)
    port = args.endpoint_port
    if type(port) is not int or not 1 <= port <= 65535:
        raise ServiceError(
            "endpoint port must be an integer in 1..65535",
            TRUST_EXIT_CODE,
            code="ENDPOINT_PORT_INVALID",
        )
    # `install` already created the token. This step records where the endpoint
    # is reachable and keeps the listener identity; it is a panel step, not a
    # lifecycle prerequisite, so it is idempotent and never rotates the token.
    token = ensure_access_token(root)
    server_cert_path = trust_path(root, TRUST_SERVER_CERT_NAME)
    server_key_path = trust_path(root, TRUST_SERVER_KEY_NAME)
    if server_cert_path.is_file() and server_key_path.is_file():
        server_cert = server_cert_path.read_bytes()
    else:
        server_cert, server_key = generate_server_certificate(
            host, host_kind, DEFAULT_CERT_VALIDITY_DAYS
        )
        write_bytes(server_cert_path, server_cert, 0o644)
        write_bytes(server_key_path, server_key, 0o600)
    locator_path = trust_path(root, TRUST_ENDPOINT_NAME)
    generation = 0
    if locator_path.is_file():
        generation = int(trust_endpoint(root)["endpoint_generation"])
    write_json(
        locator_path,
        {
            "endpoint_host": host,
            "endpoint_host_kind": host_kind,
            "endpoint_port": port,
            # The applied-receipt generation is a service-owned monotonic stamp
            # that lives only in this locator. It starts at 0 before any
            # endpoint bind; the endpoint refresh sequence advances it and the
            # strict consumer reads it from here, never from a second store.
            "endpoint_generation": generation,
        },
    )
    emit({
        "state": "initialized",
        "endpoint": endpoint_text(host, host_kind, port),
        "endpoint_host": host,
        "endpoint_port": port,
        "token_sha256": sha256_bytes(token.encode("ascii")),
        "server_sha256": sha256_bytes(server_cert),
    })
    return 0


def command_trust_init(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    lock_fd = acquire_start_lock(root)
    try:
        return trust_init_locked(args, root)
    finally:
        release_start_lock(lock_fd)


def trust_status(root: Path) -> dict[str, object]:
    """Single public calculation of the committed connection configuration."""
    if not trust_path(root, TRUST_ENDPOINT_NAME).is_file():
        raise ServiceError(
            "trust authority is not initialized",
            TRUST_EXIT_CODE,
            code="TRUST_NOT_INITIALIZED",
        )
    locator = trust_endpoint(root)
    token = load_token(root)
    authorization = _endpoint_endpoint_state(root)
    host = str(locator["endpoint_host"])
    host_kind = str(locator["endpoint_host_kind"])
    port = int(locator["endpoint_port"])
    return {
        "state": "trust",
        "endpoint": endpoint_text(host, host_kind, port),
        "endpoint_host": host,
        "endpoint_port": port,
        "endpoint_generation": locator.get("endpoint_generation", 0),
        "token_sha256": sha256_bytes(token.encode("ascii")),
        "endpoint_state": authorization["endpoint_state"],
        "endpoint_pid": authorization["endpoint_pid"],
    }


def command_trust_status(args: argparse.Namespace) -> int:
    emit(trust_status(root_from(args.root)))
    return 0


def _endpoint_applied_path(root: Path, generation: int) -> Path:
    config, _ = load_config(root)
    runtime_root = runtime_root_from(str(config["runtime_root"]))
    return runtime_root / ENDPOINT_APPLIED_DIR_NAME / f"{generation}.json"


def _parse_bound_address(value: str) -> tuple[str, int] | None:
    if value.startswith("["):
        host, separator, tail = value[1:].partition("]:")
        if not separator or not tail.isdigit():
            return None
        return host, int(tail)
    host, separator, tail = value.rpartition(":")
    if not separator or not host or not tail.isdigit():
        return None
    return host, int(tail)


def endpoint_bind_address(locator: dict[str, object]) -> tuple[str, int]:
    """Resolve the public locator into the numeric address the endpoint binds.

    The locator keeps the accepted public host (DNS name or IP literal) because
    that is what the certificate SAN and the exported WSS URL must carry. The
    endpoint producer only accepts a numeric ``SocketAddr`` on ``--listen``, so
    the service resolves a DNS locator to the same numeric binding basis that
    the applied-receipt verifier compares against. Both the start path and the
    verifier call this one function; no second bind truth is stored.
    """
    host = locator.get("endpoint_host")
    host_kind = locator.get("endpoint_host_kind")
    port = locator.get("endpoint_port")
    if not isinstance(host, str) or not host or host_kind not in ("dns", "ip"):
        raise ServiceError(
            "endpoint locator has no declared host",
            TRUST_EXIT_CODE,
            code="ENDPOINT_BIND_MISMATCH",
        )
    if type(port) is not int or not 1 <= port <= 65535:
        raise ServiceError(
            "endpoint locator has no usable port",
            TRUST_EXIT_CODE,
            code="ENDPOINT_BIND_MISMATCH",
        )
    if host_kind == "ip":
        try:
            return str(ipaddress.ip_address(host)), port
        except ValueError as error:
            raise ServiceError(
                f"endpoint locator host is not a valid IP address: {host}",
                TRUST_EXIT_CODE,
                code="ENDPOINT_BIND_MISMATCH",
            ) from error
    try:
        infos = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (socket.gaierror, OSError, ValueError, OverflowError) as error:
        raise ServiceError(
            f"endpoint locator host does not resolve to a numeric bind address: {host}",
            TRUST_EXIT_CODE,
            code="ENDPOINT_BIND_MISMATCH",
        ) from error
    if not infos:
        raise ServiceError(
            f"endpoint locator host has no numeric bind address: {host}",
            TRUST_EXIT_CODE,
            code="ENDPOINT_BIND_MISMATCH",
        )
    return str(infos[0][4][0]), port


def validate_endpoint_bind_address(locator: dict[str, object]) -> tuple[str, int]:
    """Confirm the resolved numeric address is locally assignable.

    This runs before the refresh advances the generation or stops the old
    endpoint. It binds an ephemeral port on the exact address the endpoint will
    use, so a locator that cannot become a local listener is refused without a
    side effect. The real endpoint port is not probed because the old endpoint
    may still own it at this point.
    """
    host, port = endpoint_bind_address(locator)
    address = ipaddress.ip_address(host)
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind((host, 0))
    except OSError as error:
        raise ServiceError(
            f"endpoint bind address is not locally assignable: {host}: {error}",
            TRUST_EXIT_CODE,
            code="ENDPOINT_BIND_MISMATCH",
        ) from error
    finally:
        probe.close()
    return host, port


def _declared_endpoint_listen(record: dict[str, object] | None) -> str | None:
    """Return the ``--listen`` value declared by one endpoint process record.

    The typed process record is the generation-scoped declaration of the exact
    numeric bind the service started. Receipt verification compares against
    this recorded fact instead of resolving the public locator again, so a
    changing DNS answer cannot reject an endpoint that bound correctly.
    """
    if record is None:
        return None
    command = record.get("command")
    if not isinstance(command, list):
        return None
    for index, item in enumerate(command):
        if item == "--listen" and index + 1 < len(command):
            value = command[index + 1]
            return value if isinstance(value, str) and value else None
    return None


def _bound_address_matches(receipt_address: str, declared_listen: str) -> bool:
    parsed = _parse_bound_address(receipt_address)
    declared = _parse_bound_address(declared_listen)
    if parsed is None or declared is None:
        return False
    host, port = parsed
    bind_host, bind_port = declared
    if port != bind_port:
        return False
    try:
        return ipaddress.ip_address(host) == ipaddress.ip_address(bind_host)
    except ValueError:
        return False


def _verify_endpoint_applied(root: Path, path: Path, generation: int) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ServiceError(
            f"cannot read endpoint applied receipt: {error}",
            TRUST_EXIT_CODE,
            code="ENDPOINT_NOT_REFRESHED",
        ) from error
    if len(raw) > ENDPOINT_APPLIED_FRAME_LIMIT:
        raise ServiceError(
            "endpoint applied receipt exceeds the size limit",
            TRUST_EXIT_CODE,
            code="ENDPOINT_RECEIPT_MALFORMED",
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ServiceError(
            "endpoint applied receipt is not a UTF-8 JSON object",
            TRUST_EXIT_CODE,
            code="ENDPOINT_RECEIPT_MALFORMED",
        ) from error
    # The v1 applied receipt has exactly these fields and never a status field.
    if not isinstance(document, dict) or set(document) != ENDPOINT_APPLIED_FIELDS:
        raise ServiceError(
            "endpoint applied receipt has unsupported or missing fields",
            TRUST_EXIT_CODE,
            code="ENDPOINT_RECEIPT_MALFORMED",
        )
    if document["schema_version"] != ENDPOINT_APPLIED_SCHEMA_VERSION:
        raise ServiceError(
            "endpoint applied receipt schema_version must be 1",
            TRUST_EXIT_CODE,
            code="ENDPOINT_RECEIPT_MALFORMED",
        )
    for field in ("endpoint_generation", "endpoint_pid"):
        if type(document[field]) is not int or document[field] < 0:
            raise ServiceError(
                f"endpoint applied receipt {field} is invalid",
                TRUST_EXIT_CODE,
                code="ENDPOINT_RECEIPT_MALFORMED",
            )
    if document["endpoint_pid"] <= 0:
        raise ServiceError(
            "endpoint applied receipt endpoint_pid is invalid",
            TRUST_EXIT_CODE,
            code="ENDPOINT_RECEIPT_MALFORMED",
        )
    if not isinstance(document["bound_address"], str) or not document["bound_address"]:
        raise ServiceError(
            "endpoint applied receipt bound_address is invalid",
            TRUST_EXIT_CODE,
            code="ENDPOINT_RECEIPT_MALFORMED",
        )
    locator = trust_endpoint(root)
    if document["endpoint_generation"] != generation:
        raise ServiceError(
            "endpoint applied receipt generation does not match the requested generation",
            TRUST_EXIT_CODE,
            code="ENDPOINT_GENERATION_STALE",
        )
    if type(locator.get("endpoint_generation")) is int and locator["endpoint_generation"] != generation:
        raise ServiceError(
            "endpoint locator generation is stale",
            TRUST_EXIT_CODE,
            code="ENDPOINT_GENERATION_STALE",
        )
    if not pid_alive(document["endpoint_pid"]):
        raise ServiceError(
            "endpoint applied receipt PID is not a live process",
            TRUST_EXIT_CODE,
            code="ENDPOINT_PID_MISMATCH",
        )
    # Bind fact: the receipt must match the numeric ``--listen`` the service
    # actually started for this generation, taken from the typed process
    # record. The public locator is never re-resolved here.
    try:
        record = load_endpoint_process_record(root)
    except OSError as error:
        raise ServiceError(
            f"cannot read endpoint process record: {error}",
            TRUST_EXIT_CODE,
            code="ENDPOINT_NOT_REFRESHED",
        ) from error
    declared_listen = _declared_endpoint_listen(record)
    if declared_listen is None:
        raise ServiceError(
            "endpoint process record does not declare the applied bind address",
            TRUST_EXIT_CODE,
            code="ENDPOINT_BIND_MISMATCH",
        )
    if not _bound_address_matches(document["bound_address"], declared_listen):
        raise ServiceError(
            "endpoint applied receipt bound_address does not match the declared listen address",
            TRUST_EXIT_CODE,
            code="ENDPOINT_BIND_MISMATCH",
        )
    return {
        "status": "refreshed",
        "endpoint_pid": document["endpoint_pid"],
        "endpoint_generation": document["endpoint_generation"],
        "bound_address": document["bound_address"],
    }


def wait_endpoint_applied(
    root: Path,
    *,
    generation: int,
    timeout_seconds: float,
) -> dict[str, object]:
    """Wait for and strictly verify one endpoint applied receipt generation.

    Only the declared generation file is consumed. A missing file at timeout is
    an explicit failure; endpoint stderr and any other file never substitute.
    """
    if type(generation) is not int or generation < 0:
        raise ServiceError(
            "endpoint generation must be a non-negative integer",
            TRUST_EXIT_CODE,
            code="ENDPOINT_GENERATION_STALE",
        )
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds < 0:
        raise ServiceError("endpoint wait timeout must be a non-negative number", TRUST_EXIT_CODE)
    path = _endpoint_applied_path(root, generation)
    deadline = time.monotonic() + float(timeout_seconds)
    while True:
        if path.is_file():
            return _verify_endpoint_applied(root, path, generation)
        if time.monotonic() >= deadline:
            raise ServiceError(
                "running endpoint has not applied the current authorization",
                TRUST_EXIT_CODE,
                code="ENDPOINT_NOT_REFRESHED",
            )
        time.sleep(ENDPOINT_APPLIED_WAIT_INTERVAL_SECONDS)


# --- Endpoint process lifecycle (declared service-owned refresh sequence) -----


def endpoint_process_record_path(root: Path) -> Path:
    return root / "run" / ENDPOINT_PROCESS_RECORD_NAME


def endpoint_binary_from(config: dict[str, object]) -> Path:
    """Resolve the endpoint binary beside the bound Host binary.

    The endpoint producer ships next to the Host binary in the same release
    directory. Resolving from the typed ``host_binary`` config path keeps the
    producer identity declared; it is never discovered from a log, a payload,
    or a health probe.
    """
    value = str(Path(str(config["host_binary"])).with_name("obscura-endpoint"))
    return executable_file(value, "endpoint binary")


def endpoint_media_binary_from(config: dict[str, object]) -> Path:
    value = str(Path(str(config["host_binary"])).with_name("obscura-media"))
    return executable_file(value, "endpoint media binary")


def endpoint_ffmpeg_from(config: dict[str, object]) -> str:
    # The endpoint's declared default is the PATH-resolved `ffmpeg`; the
    # service passes it explicitly and never invents an absolute path.
    return "ffmpeg"


def endpoint_listen_address(host: str, port: int) -> str:
    # The producer's --listen is a numeric SocketAddr, never the public DNS
    # locator. The caller passes the one numeric binding basis already resolved
    # and preflighted for this generation, so this never makes a second DNS
    # query that could disagree with the preflight.
    if ipaddress.ip_address(host).version == 6:
        host = f"[{host}]"
    return f"{host}:{port}"


def make_endpoint_socket_dir(runtime_root: Path) -> Path:
    for _ in range(5):
        path = runtime_root / f"endpoint-{uuid.uuid4().hex[:8]}"
        if not path.exists():
            socket_path_is_safe(path)
            return path
    raise ServiceError("could not allocate a fresh endpoint socket directory")


def endpoint_runtime_dir_is_owned(path: Path, runtime_root: Path) -> bool:
    return (
        path.is_absolute()
        and not path.is_symlink()
        and path.parent == runtime_root
        and path.name.startswith("endpoint-")
    )


def remove_owned_endpoint_runtime(socket_dir: Path, runtime_root: Path) -> None:
    if not endpoint_runtime_dir_is_owned(socket_dir, runtime_root):
        raise ServiceError(f"refusing to remove unowned endpoint runtime path: {socket_dir}")
    if socket_dir.exists():
        if socket_dir.is_symlink() or not socket_dir.is_dir():
            raise ServiceError(f"refusing to remove non-directory endpoint runtime path: {socket_dir}")
        shutil.rmtree(socket_dir)


def endpoint_command(
    config: dict[str, object],
    listen_address: str,
    host_socket_dir: Path,
    socket_dir: Path,
    receipt_path: Path,
    generation: int,
    root: Path,
) -> list[str]:
    endpoint_binary = endpoint_binary_from(config)
    media_binary = endpoint_media_binary_from(config)
    trust = trust_root(root)
    return [
        str(endpoint_binary),
        "--listen",
        listen_address,
        "--host-dir",
        str(host_socket_dir),
        "--socket-dir",
        str(socket_dir),
        "--server-cert",
        str(trust / TRUST_SERVER_CERT_NAME),
        "--server-key",
        str(trust / TRUST_SERVER_KEY_NAME),
        "--media-bin",
        str(media_binary),
        "--ffmpeg",
        endpoint_ffmpeg_from(config),
        "--applied-receipt-path",
        str(receipt_path),
        "--endpoint-generation",
        str(generation),
    ]


def endpoint_command_matches(argv: list[str], expected_argv: list[str]) -> bool:
    return argv == expected_argv


def load_endpoint_process_record(root: Path) -> dict[str, object] | None:
    path = endpoint_process_record_path(root)
    if not path.exists():
        return None
    if mode(path) != 0o600:
        raise ServiceError(f"endpoint process record permissions must be 0600: {path}")
    record = load_json(path, "endpoint process record")
    expected = {"process_version", "pid", "binary", "binary_sha256", "socket_dir", "runtime_root", "command"}
    if set(record) != expected:
        raise ServiceError("endpoint process record has unsupported or missing fields")
    if type(record["process_version"]) is not int or record["process_version"] != ENDPOINT_PROCESS_RECORD_VERSION:
        raise ServiceError("unsupported endpoint process record version")
    if type(record["pid"]) is not int or record["pid"] <= 0:
        raise ServiceError("endpoint process record has invalid pid")
    for field in ("binary", "socket_dir", "runtime_root"):
        if not isinstance(record[field], str):
            raise ServiceError(f"endpoint process record {field} must be a string")
    if not isinstance(record["binary_sha256"], str) or not SHA256_PATTERN.fullmatch(record["binary_sha256"]):
        raise ServiceError("endpoint process record binary_sha256 is invalid")
    if not isinstance(record["command"], list) or any(not isinstance(item, str) for item in record["command"]):
        raise ServiceError("endpoint process record command must be a list of strings")
    return record


def remove_endpoint_process_record(root: Path) -> None:
    try:
        endpoint_process_record_path(root).unlink()
    except FileNotFoundError:
        pass


def endpoint_process_status(record: dict[str, object]) -> str:
    pid = int(record["pid"])
    snapshot = process_snapshot(pid)
    if snapshot is None:
        return "gone" if not pid_alive(pid) else "unknown"
    if snapshot[0].startswith("Z"):
        return "gone"
    argv = process_argv(pid)
    if argv is None:
        return "unknown"
    if not endpoint_command_matches(argv, [str(item) for item in record["command"]]):
        return "changed"
    return "owned"


def stop_endpoint_process(root: Path, record: dict[str, object]) -> None:
    """Stop one previously started endpoint through the declared lifecycle.

    Only the exact recorded PID and argv may be signaled. A changed or unknown
    identity raises explicitly; a dead PID is a no-op.
    """
    status = endpoint_process_status(record)
    if status == "gone":
        return
    if status != "owned":
        raise ServiceError(f"endpoint PID identity changed; refusing to signal: {record.get('pid')}")
    pid = int(record["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if endpoint_process_status(record) == "gone":
            return
        time.sleep(0.05)
    if endpoint_process_status(record) == "gone":
        return
    if endpoint_process_status(record) != "owned":
        raise ServiceError(f"endpoint PID identity changed; refusing to signal: {pid}")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = endpoint_process_status(record)
        if status == "gone":
            return
        if status != "owned":
            raise ServiceError(f"endpoint PID identity changed; refusing to signal: {pid}")
        time.sleep(0.05)
    raise ServiceError(f"endpoint did not stop after explicit PID termination: {pid}")


def cleanup_endpoint_lifecycle(root: Path, config: dict[str, object]) -> None:
    """Stop the recorded endpoint and remove its owned runtime/process record.

    Used by Host `stop` so no owned endpoint survives a service shutdown. A
    foreign or changed PID is reported, never silently signaled or deleted.
    """
    runtime_root = runtime_root_from(str(config["runtime_root"]))
    record = load_endpoint_process_record(root)
    if record is not None:
        stop_endpoint_process(root, record)
        socket_dir = Path(str(record["socket_dir"]))
        remove_owned_endpoint_runtime(socket_dir, runtime_root)
    remove_endpoint_process_record(root)


def _endpoint_endpoint_state(root: Path) -> dict[str, object]:
    """Compute the single public endpoint applied projection.

    Reads the declared locator generation, then strictly verifies the exact
    ``<generation>.json`` endpoint receipt. A verified receipt yields
    ``refreshed`` with its PID and generation; anything else stays
    ``not_refreshed`` with no invented endpoint PID.
    """
    locator = trust_endpoint(root)
    generation = locator.get("endpoint_generation")
    if type(generation) is not int or generation < 0:
        return {
            "endpoint_state": "not_refreshed",
            "endpoint_pid": None,
            "endpoint_generation": None,
        }
    try:
        verified = wait_endpoint_applied(root, generation=generation, timeout_seconds=0.0)
    except ServiceError:
        return {
            "endpoint_state": "not_refreshed",
            "endpoint_pid": None,
            "endpoint_generation": generation,
        }
    return {
        "endpoint_state": "refreshed",
        "endpoint_pid": verified["endpoint_pid"],
        "endpoint_generation": verified["endpoint_generation"],
    }


def _endpoint_partial_facts(
    *,
    step: str,
    code: str,
    cause: str,
    generation: int | None = None,
    endpoint_pid: int | None = None,
    bound_address: str | None = None,
) -> dict[str, object]:
    return {
        "endpoint_state": "not_refreshed",
        "error": code,
        "cause": cause,
        "last_step": step,
        "endpoint_generation": generation,
        "endpoint_pid": endpoint_pid,
        "bound_address": bound_address,
    }


def _endpoint_refresh_failure(
    error: BaseException,
    *,
    step: str,
    generation: int | None,
    endpoint_pid: int | None = None,
    cause: str | None = None,
    code: str | None = None,
    message: str | None = None,
) -> ServiceError:
    """Wrap one refresh step's failure with typed partial facts.

    An existing typed code and the original exception chain are preserved. A
    failure that has no declared code becomes ``ENDPOINT_NOT_REFRESHED``.
    """
    original_code = error.code if isinstance(error, ServiceError) else None
    resolved_code = code or original_code or "ENDPOINT_NOT_REFRESHED"
    resolved_cause = cause or original_code or step
    return ServiceError(
        message or str(error),
        TRUST_EXIT_CODE,
        code=resolved_code,
        facts=_endpoint_partial_facts(
            step=step,
            code=resolved_code,
            cause=resolved_cause,
            generation=generation,
            endpoint_pid=endpoint_pid,
        ),
    )


def _reclaim_failed_endpoint_child(
    process: subprocess.Popen[object] | None,
    socket_dir: Path | None,
    runtime_root: Path,
    root: Path,
) -> str | None:
    """Stop a just-started endpoint and remove only its own runtime.

    Used only in the interval between a successful ``Popen`` and a persisted
    process record. After the record exists the declared lifecycle owns the
    child. Cleanup problems are returned so the primary failure can report
    them instead of silently claiming a clean reclaim.
    """
    problems: list[str] = []
    if process is not None:
        try:
            stop_child_process(process)
        except BaseException as error:  # noqa: BLE001 - report, never mask the primary failure
            problems.append(f"could not stop endpoint pid {process.pid}: {error}")
    if socket_dir is not None:
        try:
            remove_owned_endpoint_runtime(socket_dir, runtime_root)
        except BaseException as error:  # noqa: BLE001 - report, never mask the primary failure
            problems.append(f"could not remove endpoint runtime {socket_dir}: {error}")
    try:
        remove_endpoint_process_record(root)
    except OSError as error:
        problems.append(f"could not remove endpoint process record: {error}")
    return "; ".join(problems) if problems else None


def refresh_endpoint(root: Path, *, timeout_seconds: float) -> dict[str, object]:
    """Own the declared endpoint refresh sequence.

    Steps: allocate the next service-owned generation in the existing locator;
    stop the old endpoint through the declared lifecycle; remove the old
    generation receipt file; start the endpoint with the new generation receipt
    path; bounded-wait for that exact file; verify
    JSON/generation/PID/address; expose ``refreshed`` with PID and generation;
    otherwise expose ``not_refreshed`` with typed partial facts. Cancel of the
    bounded wait releases only the waiter and never stops or cleans up the
    endpoint.
    """
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds < 0:
        raise ServiceError(
            "endpoint refresh timeout must be a non-negative number",
            TRUST_EXIT_CODE,
            code="ENDPOINT_NOT_REFRESHED",
        )
    try:
        config, _ = load_config(root)
        runtime_root = runtime_root_from(str(config["runtime_root"]))
    except ServiceError as error:
        raise _endpoint_refresh_failure(error, step="load_config", generation=None) from error
    host_socket_dir = _running_socket_dir(root)
    if host_socket_dir is None or not host_socket_dir.is_dir():
        raise ServiceError(
            "Host must be running before the endpoint refresh sequence",
            TRUST_EXIT_CODE,
            code="ENDPOINT_NOT_REFRESHED",
            facts=_endpoint_partial_facts(
                step="host_required", code="ENDPOINT_NOT_REFRESHED", cause="host_not_running"
            ),
        )
    # Preflight: resolve the public locator into the numeric local bind and
    # confirm it is assignable before the generation changes or the old
    # endpoint stops. The one resolved numeric bind is kept and reused for the
    # start command, so preflight, start and receipt verification share a single
    # declared basis instead of three independent DNS answers.
    try:
        locator = trust_endpoint(root)
        bind_host, bind_port = validate_endpoint_bind_address(locator)
    except ServiceError as error:
        raise _endpoint_refresh_failure(error, step="resolve_bind_address", generation=None) from error
    listen_address = endpoint_listen_address(bind_host, bind_port)
    # Step 2: allocate the next service-owned generation in the locator.
    current = locator.get("endpoint_generation")
    if type(current) is not int or current < 0:
        raise ServiceError(
            "endpoint locator has no declared generation",
            TRUST_EXIT_CODE,
            code="ENDPOINT_GENERATION_STALE",
            facts=_endpoint_partial_facts(
                step="allocate_generation", code="ENDPOINT_GENERATION_STALE", cause="locator_generation_invalid"
            ),
        )
    previous_generation = current
    generation = current + 1
    locator["endpoint_generation"] = generation
    try:
        write_json(trust_path(root, TRUST_ENDPOINT_NAME), locator)
    except (ServiceError, OSError) as error:
        raise _endpoint_refresh_failure(error, step="allocate_generation", generation=generation) from error
    # Step 3: stop the old endpoint through the declared lifecycle.
    old_pid: int | None = None
    try:
        record = load_endpoint_process_record(root)
        if record is not None:
            old_pid = int(record["pid"])
            stop_endpoint_process(root, record)
            remove_owned_endpoint_runtime(Path(str(record["socket_dir"])), runtime_root)
            remove_endpoint_process_record(root)
    except (ServiceError, OSError, KeyError, ValueError, TypeError) as error:
        raise _endpoint_refresh_failure(
            error, step="stop_old_endpoint", generation=generation, endpoint_pid=old_pid
        ) from error
    # Step 4: remove the superseded generation receipt file.
    try:
        applied_dir = runtime_root / ENDPOINT_APPLIED_DIR_NAME
        if not applied_dir.exists():
            applied_dir.mkdir(mode=0o700)
        old_receipt = _endpoint_applied_path(root, previous_generation)
        try:
            old_receipt.unlink()
        except FileNotFoundError:
            pass
        receipt_path = _endpoint_applied_path(root, generation)
        receipt_path.unlink(missing_ok=True)
    except (ServiceError, OSError) as error:
        raise _endpoint_refresh_failure(error, step="clear_superseded_receipt", generation=generation) from error
    # Step 5: precompute the binary identity and exact argv, then start the
    # endpoint with the new generation receipt path.
    socket_dir: Path | None = None
    try:
        socket_dir = make_endpoint_socket_dir(runtime_root)
        endpoint_binary = endpoint_binary_from(config)
        binary_sha256 = sha256_file(endpoint_binary)
        command = endpoint_command(config, listen_address, host_socket_dir, socket_dir, receipt_path, generation, root)
    except (ServiceError, OSError) as error:
        cleanup = _reclaim_failed_endpoint_child(None, socket_dir, runtime_root, root)
        message = str(error) if not cleanup else f"{error}; endpoint cleanup failed: {cleanup}"
        raise _endpoint_refresh_failure(
            error, step="start_endpoint", generation=generation, cause="endpoint_start_failed", message=message
        ) from error
    try:
        log_handle = (root / "logs" / "endpoint.log").open("ab")
    except OSError as error:
        cleanup = _reclaim_failed_endpoint_child(None, socket_dir, runtime_root, root)
        message = str(error) if not cleanup else f"{error}; endpoint cleanup failed: {cleanup}"
        raise _endpoint_refresh_failure(
            error, step="start_endpoint", generation=generation, cause="endpoint_log_open_failed", message=message
        ) from error
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as error:
        log_handle.close()
        cleanup = _reclaim_failed_endpoint_child(None, socket_dir, runtime_root, root)
        message = str(error) if not cleanup else f"{error}; endpoint cleanup failed: {cleanup}"
        raise _endpoint_refresh_failure(
            error, step="start_endpoint", generation=generation, cause="endpoint_start_failed", message=message
        ) from error
    endpoint_pid = process.pid
    try:
        write_json(
            endpoint_process_record_path(root),
            {
                "process_version": ENDPOINT_PROCESS_RECORD_VERSION,
                "pid": endpoint_pid,
                "binary": str(endpoint_binary),
                "binary_sha256": binary_sha256,
                "socket_dir": str(socket_dir),
                "runtime_root": str(runtime_root),
                "command": command,
            },
        )
    except BaseException as error:
        cleanup = _reclaim_failed_endpoint_child(process, socket_dir, runtime_root, root)
        log_handle.close()
        if isinstance(error, (ServiceError, OSError)):
            message = str(error) if not cleanup else f"{error}; endpoint cleanup failed: {cleanup}"
            raise _endpoint_refresh_failure(
                error,
                step="persist_endpoint_record",
                generation=generation,
                endpoint_pid=endpoint_pid,
                cause="endpoint_record_write_failed",
                message=message,
            ) from error
        raise
    log_handle.close()
    # Step 6/7: bounded-wait for the exact receipt, then strict verification.
    # The record now exists, so cancellation here releases only the waiter and
    # never stops or cleans up the endpoint.
    try:
        verified = wait_endpoint_applied(root, generation=generation, timeout_seconds=float(timeout_seconds))
    except (ServiceError, OSError) as error:
        raise _endpoint_refresh_failure(
            error,
            step="verify_applied",
            generation=generation,
            endpoint_pid=process.pid if endpoint_process_status(
                {
                    "pid": process.pid,
                    "command": command,
                }
            ) == "owned" else None,
        ) from error
    # Expose the refreshed result with the endpoint PID and generation.
    return {
        "state": "refreshed",
        "endpoint_state": "refreshed",
        "endpoint_pid": verified["endpoint_pid"],
        "endpoint_generation": verified["endpoint_generation"],
        "bound_address": verified["bound_address"],
    }


def command_trust_endpoint_refresh(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    lock_fd = acquire_start_lock(root)
    try:
        result = refresh_endpoint(root, timeout_seconds=args.timeout)
    finally:
        release_start_lock(lock_fd)
    emit(result)
    return 0


def _running_socket_dir(root: Path) -> Path | None:
    state = load_state(root)
    if state is None or state["launch_mode"] not in {"subprocess", "launchd"}:
        return None
    return Path(str(state["socket_dir"]))


def export_bundle(
    root: Path,
    output_path: Path,
    *,
    endpoint_receipt: dict[str, object] | None = None,
) -> dict[str, object]:
    if not isinstance(endpoint_receipt, dict) or endpoint_receipt.get("status") != "refreshed":
        raise ServiceError(
            "running endpoint has not applied the current endpoint generation",
            TRUST_EXIT_CODE,
            code="ENDPOINT_REFRESH_REQUIRED",
        )
    locator = trust_endpoint(root)
    host = str(locator["endpoint_host"])
    host_kind = str(locator["endpoint_host_kind"])
    port = int(locator["endpoint_port"])
    token = load_token(root)
    document = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "endpoint": endpoint_text(host, host_kind, port),
        "token": token,
    }
    content = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        write_bytes(output_path, content, 0o600)
    except OSError as error:
        raise ServiceError(
            f"cannot write connection bundle: {error}",
            TRUST_EXIT_CODE,
            code="BUNDLE_WRITE_FAILED",
        ) from error
    return {
        "state": "exported",
        "verified_bundle": str(output_path),
        "endpoint": document["endpoint"],
        "token_sha256": sha256_bytes(token.encode("ascii")),
    }


def _export_endpoint_receipt(root: Path) -> dict[str, object]:
    locator = trust_endpoint(root)
    generation = locator.get("endpoint_generation")
    if type(generation) is not int or generation < 0:
        raise ServiceError(
            "endpoint has not been refreshed with a declared generation",
            TRUST_EXIT_CODE,
            code="ENDPOINT_REFRESH_REQUIRED",
        )
    return wait_endpoint_applied(
        root, generation=generation, timeout_seconds=ENDPOINT_REFRESH_TIMEOUT_SECONDS
    )


def command_trust_export(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    output_path = normalize_absolute(args.output, "bundle output")
    endpoint_receipt = _export_endpoint_receipt(root)
    emit(export_bundle(root, output_path, endpoint_receipt=endpoint_receipt))
    return 0


def trust_summary(root: Path) -> dict[str, object] | None:
    if not trust_path(root, TRUST_ENDPOINT_NAME).is_file():
        return None
    status = trust_status(root)
    return {
        "endpoint": status["endpoint"],
        "endpoint_generation": status["endpoint_generation"],
        "endpoint_state": status["endpoint_state"],
        "endpoint_pid": status["endpoint_pid"],
    }


def add_root_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=argparse.SUPPRESS, help="private service state root")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Install and supervise one independent Obscura Host process")
    result.add_argument("--root", default=str(DEFAULT_ROOT), help="private service state root")
    commands = result.add_subparsers(dest="command", required=True)

    install = commands.add_parser("install", help="bind binary/config and write an unloaded launchd plist")
    add_root_option(install)
    install.add_argument("--binary", required=True)
    install.add_argument("--protocol-version", required=True, type=protocol_version_argument)
    install.add_argument("--runtime-root")
    install.add_argument("--profile-root")
    install.add_argument("--launchd-root")
    install.add_argument("--service-label", default=DEFAULT_LABEL)
    install.add_argument("--profile-id", default="default")
    install.add_argument("--allow-private-network", action="store_true")

    for name in ("start", "restart"):
        command = commands.add_parser(name, help=f"{name} the Host subprocess")
        add_root_option(command)
        command.add_argument("--ready-timeout", type=float, default=60.0)

    status = commands.add_parser("status", help="report config, PID, Session and socket identity")
    add_root_option(status)

    stop = commands.add_parser("stop", help="stop the recorded Host PID")
    add_root_option(stop)
    stop.add_argument("--service-label", default=DEFAULT_LABEL)

    launchd = commands.add_parser("launchd", help="exec the configured Host for an already loaded launchd agent")
    add_root_option(launchd)

    trust = commands.add_parser("trust", help="own the endpoint locator and the access token")
    trust_commands = trust.add_subparsers(dest="trust_command", required=True)

    trust_init = trust_commands.add_parser(
        "init",
        help=(
            "record the endpoint locator, keeping the existing access token and listener identity"
        ),
    )
    add_root_option(trust_init)
    trust_init.add_argument("--endpoint-host", required=True, help="endpoint DNS name or IP literal")
    trust_init.add_argument("--endpoint-port", type=int, default=8443)

    trust_status = trust_commands.add_parser("status", help="report the endpoint locator and the token digest")
    add_root_option(trust_status)

    trust_export = trust_commands.add_parser(
        "export", help="write the connection bundle for the running endpoint"
    )
    add_root_option(trust_export)
    trust_export.add_argument("--output", required=True)

    trust_endpoint_refresh = trust_commands.add_parser(
        "endpoint-refresh",
        help="own the declared endpoint refresh sequence and expose the refreshed result",
    )
    add_root_option(trust_endpoint_refresh)
    trust_endpoint_refresh.add_argument("--timeout", type=float, default=ENDPOINT_REFRESH_TIMEOUT_SECONDS)

    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "install":
            return command_install(args)
        if args.command == "start":
            return command_start(args)
        if args.command == "restart":
            return command_restart(args)
        if args.command == "status":
            return command_status(args)
        if args.command == "stop":
            return command_stop(args)
        if args.command == "launchd":
            return command_launchd(args)
        if args.command == "trust":
            if args.trust_command == "init":
                return command_trust_init(args)
            if args.trust_command == "status":
                return command_trust_status(args)
            if args.trust_command == "export":
                return command_trust_export(args)
            if args.trust_command == "endpoint-refresh":
                return command_trust_endpoint_refresh(args)
            raise ServiceError(f"unsupported trust command: {args.trust_command}")
        raise ServiceError(f"unsupported command: {args.command}")
    except ServiceError as error:
        if error.code:
            payload: dict[str, object] = {"code": error.code, "message": str(error)}
            if error.facts is not None:
                payload["facts"] = error.facts
            print(
                json.dumps({"error": payload}, ensure_ascii=False),
                file=sys.stderr,
            )
        else:
            print(f"host-service: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("host-service: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
