#!/usr/bin/env python3
"""Own the local Obscura Host process without owning browser Session state."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
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
PROCESS_PATH_FORBIDDEN = frozenset(" \t\r\n'\"\\")
SOCKET_PATH_LIMIT = 100
STATE_NAME = "state.json"
CONFIG_NAME = "config.json"

# Local trust authority layout and frozen wire values (agentbrowser.connection@1).
TRUST_DIR_NAME = "trust"
TRUST_DEVICES_NAME = "devices"
TRUST_CA_CERT_NAME = "ca.der"
TRUST_CA_KEY_NAME = "ca-key.der"
TRUST_SERVER_CERT_NAME = "server.der"
TRUST_SERVER_KEY_NAME = "server-key.der"
TRUST_ENDPOINT_NAME = "endpoint.json"
TRUST_REGISTRY_NAME = "registry.json"
TRUST_PROJECTION_NAME = "authorized-clients.json"

REGISTRY_SCHEMA_VERSION = 2
PROJECTION_SCHEMA_VERSION = 2
DEVICE_RECORD_VERSION = 1

# RemoteScope wire names are fixed, lower-case, deduplicated and ordered.
REMOTE_SCOPES = (
    "observe_human_control",
    "profile_read",
    "profile_create",
    "profile_select",
)
REMOTE_SCOPE_ORDER = {name: index for index, name in enumerate(REMOTE_SCOPES)}

# Transfer code: 16 opaque characters without the ambiguous 0/O/1/I/L.
TRANSFER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TRANSFER_CODE_LENGTH = 16

# PKCS#12 friendly names are fixed and only these three are allowed.
P12_ALIAS_HOST_CA = "agentbrowser-host-ca"
P12_ALIAS_ENDPOINT = "agentbrowser-endpoint"
P12_ALIAS_CLIENT = "agentbrowser-client"

CA_VALIDITY_DAYS = 3650
DEFAULT_CERT_VALIDITY_DAYS = 397
MAX_CERT_VALIDITY_DAYS = 398
KEY_SIZE = 2048
LABEL_MAX_LENGTH = 64

PARTIAL_EXIT_CODE = 4
TRUST_EXIT_CODE = 3


class ServiceError(Exception):
    """A user-facing service error with a stable non-zero result."""

    def __init__(self, message: str, exit_code: int = 2, code: str | None = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code


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
    validate_process_path(str(result), "runtime root")
    return result


def validate_process_path(value: str, field: str) -> None:
    if any(character in PROCESS_PATH_FORBIDDEN for character in value):
        raise ServiceError(f"{field} cannot contain whitespace, quotes, or backslashes")


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
    validate_process_path(str(path), "host binary")
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ServiceError(f"host binary is not executable: {path}")
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
    validate_process_path(value, "existing profile root")
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
        validate_process_path(str(profile_root), "profile root")
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
        validate_process_path(state_profile_root, "service state profile root")
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
    snapshot: tuple[str, str],
    binary: str,
    socket_dir: str,
    profile_root: str | None,
) -> bool:
    try:
        argv = shlex.split(snapshot[1])
    except ValueError:
        return False
    if len(argv) < 3 or argv[:3] != [binary, "--socket-dir", socket_dir]:
        return False
    remainder = argv[3:]
    if profile_root is None:
        return remainder in ([], ["--allow-private-network"])
    if len(remainder) not in {2, 3} or remainder[:2] != ["--profile-root", profile_root]:
        return False
    return remainder[2:] in ([], ["--allow-private-network"])


def stop_target_status(pid: int, binary: str, socket_dir: str, profile_root: str | None) -> str:
    snapshot = process_snapshot(pid)
    if snapshot is None:
        if pid_alive(pid):
            return "unknown"
        return "gone"
    if snapshot[0].startswith("Z"):
        return "gone"
    if not host_command_matches(snapshot, binary, socket_dir, profile_root):
        return "changed"
    return "owned"


def stop_target_is_verified(pid: int, binary: str, socket_dir: str, profile_root: str | None) -> bool:
    status = stop_target_status(pid, binary, socket_dir, profile_root)
    if status == "gone":
        return False
    if status != "owned":
        raise ServiceError(f"Host PID identity changed; refusing to signal: {pid}")
    return True


def state_identity(state: dict[str, object]) -> tuple[bool, str]:
    pid = pid_from_state(state)
    if not pid_alive(pid):
        return False, "dead"
    snapshot = process_snapshot(pid)
    if snapshot is None:
        return False, "foreign"
    if snapshot[0].startswith("Z"):
        return False, "dead"
    command = snapshot[1]
    binary = str(state.get("host_binary", ""))
    socket_dir = str(state.get("socket_dir", ""))
    profile_root = state.get("profile_root")
    if not host_command_matches(snapshot, binary, socket_dir, profile_root if isinstance(profile_root, str) else None):
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
    for _ in range(5):
        path = runtime_root / f"host-{uuid.uuid4().hex}"
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


def stop_pid(pid: int, binary: str, socket_dir: str, profile_root: str | None) -> None:
    if not stop_target_is_verified(pid, binary, socket_dir, profile_root):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = stop_target_status(pid, binary, socket_dir, profile_root)
        if status == "gone":
            return
        time.sleep(0.05)
    term_status = stop_target_status(pid, binary, socket_dir, profile_root)
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
        status = stop_target_status(pid, binary, socket_dir, profile_root)
        if status == "gone":
            return
        if status != "owned":
            raise ServiceError(f"Host PID identity changed; refusing to signal: {pid}")
        time.sleep(0.05)
    final_status = stop_target_status(pid, binary, socket_dir, profile_root)
    if final_status == "owned":
        raise ServiceError(f"Host did not stop after explicit PID termination: {pid}")
    if final_status != "gone":
        raise ServiceError(f"cannot verify Host PID after termination: {pid}")


def stale_or_running(root: Path, state: dict[str, object] | None) -> str:
    if state is None:
        return "none"
    alive, detail = state_identity(state)
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


def host_command(config: dict[str, object], socket_dir: Path) -> list[str]:
    command = [str(config["host_binary"]), "--socket-dir", str(socket_dir)]
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


def flush_before_stop(state: dict[str, object]) -> None:
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
    if profile_root is not None:
        validate_process_path(str(profile_root), "profile root")
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
        command = host_command(config, socket_dir)
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
    alive, detail = state_identity(state)
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
    alive, detail = state_identity(state)
    if detail == "foreign":
        raise ServiceError(f"recorded PID is owned by another process; refusing to stop: {state.get('pid')}")
    if state.get("launch_mode") == "launchd_rejected":
        if alive:
            raise ServiceError("rejected launchd state still has a live PID; refusing cleanup")
        cleanup_runtime(state)
        remove_state(root)
        return
    if alive and state.get("launch_mode") == "launchd":
        raise ServiceError("launchd-managed Host must be stopped through launchctl; refusing subprocess stop")
    if alive:
        flush_before_stop(state)
        stop_pid(
            pid_from_state(state),
            str(state.get("host_binary", "")),
            str(state.get("socket_dir", "")),
            str(state["profile_root"]) if state.get("profile_root") is not None else None,
        )
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
        command = host_command(config, socket_dir)
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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def trust_root(root: Path) -> Path:
    return root / TRUST_DIR_NAME


def trust_path(root: Path, name: str) -> Path:
    return trust_root(root) / name


def trust_device_path(root: Path, device_id: str) -> Path:
    return trust_root(root) / TRUST_DEVICES_NAME / f"{device_id}.json"


def crypto_modules() -> SimpleNamespace:
    try:
        import datetime as datetime_module
        import ipaddress as ipaddress_module
        from cryptography import x509 as x509_module
        from cryptography.hazmat.primitives import hashes as hashes_module
        from cryptography.hazmat.primitives import serialization as serialization_module
        from cryptography.hazmat.primitives.asymmetric import rsa as rsa_module
        from cryptography.hazmat.primitives.serialization import pkcs12 as pkcs12_module
        from cryptography.x509.oid import ExtendedKeyUsageOID as eku_oid
        from cryptography.x509.oid import NameOID as name_oid
    except ImportError as error:
        raise ServiceError(
            "trust primitives require the installed OpenSSL-backed cryptography library",
            TRUST_EXIT_CODE,
            code="TRUST_CRYPTO_UNAVAILABLE",
        ) from error
    return SimpleNamespace(
        datetime=datetime_module,
        ipaddress=ipaddress_module,
        x509=x509_module,
        hashes=hashes_module,
        serialization=serialization_module,
        rsa=rsa_module,
        pkcs12=pkcs12_module,
        ExtendedKeyUsageOID=eku_oid,
        NameOID=name_oid,
    )


def generate_transfer_code() -> str:
    import secrets

    return "".join(secrets.choice(TRANSFER_CODE_ALPHABET) for _ in range(TRANSFER_CODE_LENGTH))


def validate_transfer_code(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != TRANSFER_CODE_LENGTH
        or any(character not in TRANSFER_CODE_ALPHABET for character in value)
    ):
        raise ServiceError("transfer code has an invalid shape", TRUST_EXIT_CODE, code="TRANSFER_CODE_INVALID")
    return value


def endpoint_host_san(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise ServiceError(
            "endpoint host must be a non-empty DNS name or IP address",
            TRUST_EXIT_CODE,
            code="ENDPOINT_HOST_INVALID",
        )
    ipaddress = crypto_modules().ipaddress
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if len(value) > 253 or ":" in value or value.endswith(".") or "/" in value:
            raise ServiceError(
                f"endpoint host is not a valid DNS name: {value}",
                TRUST_EXIT_CODE,
                code="ENDPOINT_HOST_INVALID",
            )
        return "dns", value
    if address.is_unspecified or address.is_multicast:
        raise ServiceError(
            f"endpoint host cannot be a wildcard or multicast address: {value}",
            TRUST_EXIT_CODE,
            code="ENDPOINT_HOST_INVALID",
        )
    return "ip", str(address)


def endpoint_ws_url(host: str, host_kind: str, port: int) -> str:
    """Build the WSS endpoint URL for an already accepted endpoint host.

    The certificate SAN uses the unbracketed IP literal, but a URL authority
    must bracket an IPv6 literal so the host and port stay parseable.
    """
    authority = host
    if host_kind == "ip" and crypto_modules().ipaddress.ip_address(host).version == 6:
        authority = f"[{host}]"
    return f"wss://{authority}:{port}/"


def _key_usage(x509: object, *, digital_signature: bool, key_encipherment: bool, key_cert_sign: bool) -> object:
    return x509.KeyUsage(
        digital_signature=digital_signature,
        content_commitment=False,
        key_encipherment=key_encipherment,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=key_cert_sign,
        crl_sign=key_cert_sign,
        encipher_only=False,
        decipher_only=False,
    )


def _der_certificate(certificate: object, serialization: object) -> bytes:
    return certificate.public_bytes(serialization.Encoding.DER)


def _der_private_key(key: object, serialization: object) -> bytes:
    return key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def generate_ca(common_name: str) -> tuple[bytes, bytes]:
    modules = crypto_modules()
    x509 = modules.x509
    now = modules.datetime.datetime.now(modules.datetime.timezone.utc)
    key = modules.rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
    subject = x509.Name([x509.NameAttribute(modules.NameOID.COMMON_NAME, common_name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - modules.datetime.timedelta(minutes=5))
        .not_valid_after(now + modules.datetime.timedelta(days=CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            _key_usage(x509, digital_signature=False, key_encipherment=False, key_cert_sign=True),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, modules.hashes.SHA256())
    )
    return (
        _der_certificate(certificate, modules.serialization),
        _der_private_key(key, modules.serialization),
    )


def generate_leaf(
    ca_certificate: object,
    ca_key: object,
    *,
    common_name: str,
    server_auth: bool,
    validity_days: int,
    san: tuple[str, str] | None = None,
) -> tuple[bytes, bytes, bytes, int, int]:
    modules = crypto_modules()
    x509 = modules.x509
    now = modules.datetime.datetime.now(modules.datetime.timezone.utc)
    not_before = now - modules.datetime.timedelta(minutes=5)
    not_after = now + modules.datetime.timedelta(days=validity_days)
    key = modules.rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(modules.NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            _key_usage(
                x509,
                digital_signature=True,
                key_encipherment=server_auth,
                key_cert_sign=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [modules.ExtendedKeyUsageOID.SERVER_AUTH if server_auth else modules.ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
    )
    if san is not None:
        kind, value = san
        general_name = x509.IPAddress(modules.ipaddress.ip_address(value)) if kind == "ip" else x509.DNSName(value)
        builder = builder.add_extension(
            x509.SubjectAlternativeName([general_name]),
            critical=False,
        )
    certificate = builder.sign(ca_key, modules.hashes.SHA256())
    return (
        _der_certificate(certificate, modules.serialization),
        _der_private_key(key, modules.serialization),
        _der_certificate(ca_certificate, modules.serialization),
        int(not_before.timestamp()),
        int(not_after.timestamp()),
    )


def serialize_registry(registry: dict[str, object]) -> bytes:
    return (json.dumps(registry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def projection_content(
    registry_sha256: str,
    authorization_revision: int,
    clients: list[dict[str, object]],
) -> bytes:
    document = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "authorization_revision": authorization_revision,
        "registry_sha256": registry_sha256,
        "clients": clients,
    }
    return (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def projection_client(device: dict[str, object]) -> dict[str, object]:
    grant = device["initial_profile_grant"]
    return {
        "device_id": device["device_id"],
        "client_certificate_sha256": device["client_certificate_sha256"],
        "scopes": list(device["scopes"]),
        "initial_profile_grant": {
            "selection": grant["selection"],
            "authorization_revision": grant["authorization_revision"],
        },
    }


def normalize_scopes(values: list[str] | None) -> list[str]:
    if values is None:
        raise ServiceError(
            "an explicit remote scope set is required",
            TRUST_EXIT_CODE,
            code="REGISTRY_SCOPE_REQUIRED",
        )
    if not isinstance(values, list):
        raise ServiceError(
            "remote scopes must be an explicit list",
            TRUST_EXIT_CODE,
            code="REGISTRY_SCOPE_REQUIRED",
        )
    unique = set()
    for value in values:
        if value not in REMOTE_SCOPE_ORDER:
            raise ServiceError(
                f"unsupported remote scope: {value}",
                TRUST_EXIT_CODE,
                code="REGISTRY_SCOPE_REQUIRED",
            )
        unique.add(value)
    return sorted(unique, key=lambda name: REMOTE_SCOPE_ORDER[name])


def parse_initial_profile(value: str) -> object:
    if value == "default_profile":
        return "default_profile"
    prefix = "profile_id:"
    if isinstance(value, str) and value.startswith(prefix):
        profile_id = value[len(prefix):]
        if profile_id and PROFILE_PATTERN.fullmatch(profile_id):
            return {"profile_id": profile_id}
    raise ServiceError(
        "an explicit initial profile grant is required",
        TRUST_EXIT_CODE,
        code="MISSING_INITIAL_PROFILE_GRANT",
    )


def validate_selection(selection: object) -> None:
    if selection == "default_profile":
        return
    if (
        isinstance(selection, dict)
        and set(selection) == {"profile_id"}
        and isinstance(selection["profile_id"], str)
        and PROFILE_PATTERN.fullmatch(selection["profile_id"])
    ):
        return
    raise ServiceError(
        "initial profile grant selection is invalid",
        TRUST_EXIT_CODE,
        code="MISSING_INITIAL_PROFILE_GRANT",
    )


def validate_device(device: object) -> None:
    if not isinstance(device, dict):
        raise ServiceError("registry device must be an object")
    expected = {
        "device_id",
        "device_label",
        "client_certificate_sha256",
        "scopes",
        "initial_profile_grant",
        "scope_revision",
        "status",
        "not_before_unix",
        "not_after_unix",
        "created_at_unix",
        "revoked_at_unix",
    }
    if set(device) != expected:
        raise ServiceError("registry device has unsupported or missing fields")
    if not isinstance(device["device_id"], str) or not device["device_id"]:
        raise ServiceError("registry device_id is invalid")
    if not isinstance(device["device_label"], str) or not 1 <= len(device["device_label"]) <= LABEL_MAX_LENGTH:
        raise ServiceError("registry device_label is invalid")
    fingerprint = device["client_certificate_sha256"]
    if not isinstance(fingerprint, str) or not SHA256_PATTERN.fullmatch(fingerprint):
        raise ServiceError("registry client_certificate_sha256 is invalid")
    scopes = device["scopes"]
    if not isinstance(scopes, list) or any(scope not in REMOTE_SCOPE_ORDER for scope in scopes):
        raise ServiceError("registry scopes are invalid")
    if scopes != sorted(set(scopes), key=lambda name: REMOTE_SCOPE_ORDER[name]):
        raise ServiceError("registry scopes are not deduplicated and ordered")
    grant = device["initial_profile_grant"]
    if not isinstance(grant, dict) or set(grant) != {"selection", "authorization_revision"}:
        raise ServiceError("registry initial_profile_grant is invalid")
    validate_selection(grant["selection"])
    for field in ("scope_revision", "not_before_unix", "not_after_unix", "created_at_unix"):
        if type(device[field]) is not int or device[field] < 0:
            raise ServiceError(f"registry {field} is invalid")
    if type(grant["authorization_revision"]) is not int or grant["authorization_revision"] < 0:
        raise ServiceError("registry initial_profile_grant authorization_revision is invalid")
    if device["status"] not in {"active", "revoked"}:
        raise ServiceError("registry device status is invalid")
    revoked_at = device["revoked_at_unix"]
    if device["status"] == "revoked":
        if type(revoked_at) is not int or revoked_at < 0:
            raise ServiceError("revoked device requires revoked_at_unix")
    elif revoked_at is not None:
        raise ServiceError("active device cannot have revoked_at_unix")


def validate_registry(registry: object) -> None:
    if not isinstance(registry, dict):
        raise ServiceError("trust registry must be an object")
    if set(registry) != {"schema_version", "authorization_revision", "host_id", "devices"}:
        raise ServiceError("trust registry has unsupported or missing fields")
    if registry["schema_version"] != REGISTRY_SCHEMA_VERSION:
        raise ServiceError("trust registry schema_version must be 2")
    if type(registry["authorization_revision"]) is not int or registry["authorization_revision"] < 0:
        raise ServiceError("trust registry authorization_revision is invalid")
    if not isinstance(registry["host_id"], str) or not registry["host_id"]:
        raise ServiceError("trust registry host_id is invalid")
    devices = registry["devices"]
    if not isinstance(devices, list):
        raise ServiceError("trust registry devices must be a list")
    seen = set()
    for device in devices:
        validate_device(device)
        if device["device_id"] in seen:
            raise ServiceError("trust registry has duplicate device_id")
        seen.add(device["device_id"])


def parse_trust_registry(registry_bytes: bytes) -> dict[str, object]:
    try:
        registry = json.loads(registry_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ServiceError(f"invalid trust registry: {error}") from error
    if not isinstance(registry, dict):
        raise ServiceError("trust registry must be a JSON object")
    validate_registry(registry)
    return registry


def load_trust_registry(root: Path) -> dict[str, object]:
    path = trust_path(root, TRUST_REGISTRY_NAME)
    if not path.is_file():
        raise ServiceError("trust authority is not initialized", TRUST_EXIT_CODE, code="TRUST_NOT_INITIALIZED")
    return parse_trust_registry(path.read_bytes())


def find_device(registry: dict[str, object], device_id: str) -> dict[str, object] | None:
    for device in registry["devices"]:
        if device["device_id"] == device_id:
            return device
    return None


def load_device_record(root: Path, device_id: str) -> dict[str, object] | None:
    path = trust_device_path(root, device_id)
    if not path.is_file():
        return None
    return load_json(path, "device record")


def rebind_active_grants(registry: dict[str, object], revision: int) -> None:
    for device in registry["devices"]:
        if device["status"] == "active":
            device["initial_profile_grant"]["authorization_revision"] = revision


def refresh_projection(root: Path, registry: dict[str, object], registry_bytes: bytes) -> tuple[bytes, str]:
    registry_sha = sha256_bytes(registry_bytes)
    clients = [
        projection_client(device)
        for device in registry["devices"]
        if device["status"] == "active"
    ]
    content = projection_content(registry_sha, registry["authorization_revision"], clients)
    return content, registry_sha


def write_registry(root: Path, registry: dict[str, object]) -> bytes:
    content = serialize_registry(registry)
    try:
        write_bytes(trust_path(root, TRUST_REGISTRY_NAME), content, 0o600)
    except OSError as error:
        raise ServiceError(
            f"cannot persist trust registry: {error}",
            PARTIAL_EXIT_CODE,
            code="REGISTRY_WRITE_FAILED",
        ) from error
    return content


def write_projection(root: Path, content: bytes) -> str:
    try:
        write_bytes(trust_path(root, TRUST_PROJECTION_NAME), content, 0o600)
    except OSError as error:
        raise ServiceError(
            f"cannot persist authorized-clients projection: {error}",
            PARTIAL_EXIT_CODE,
            code="PROJECTION_WRITE_FAILED",
        ) from error
    return sha256_bytes(content)


def trust_endpoint(root: Path) -> dict[str, object]:
    return load_json(trust_path(root, TRUST_ENDPOINT_NAME), "trust endpoint")


def trust_init_locked(args: argparse.Namespace, root: Path) -> int:
    directory = trust_root(root)
    if directory.exists() or trust_path(root, TRUST_REGISTRY_NAME).exists():
        raise ServiceError(
            "trust authority already initialized; reinitialize is refused",
            TRUST_EXIT_CODE,
            code="TRUST_ALREADY_INITIALIZED",
        )
    validity_days = args.validity_days
    if type(validity_days) is not int or not 1 <= validity_days <= MAX_CERT_VALIDITY_DAYS:
        raise ServiceError(
            f"leaf validity must be 1..{MAX_CERT_VALIDITY_DAYS} days",
            TRUST_EXIT_CODE,
            code="CERT_VALIDITY_INVALID",
        )
    san = endpoint_host_san(args.endpoint_host)
    assert_private_directory(root)
    assert_private_directory(directory)
    assert_private_directory(directory / TRUST_DEVICES_NAME)
    ca_cert, ca_key = generate_ca("AgentBrowser Host CA")
    server_cert, server_key, _, not_before, not_after = generate_leaf(
        crypto_modules().x509.load_der_x509_certificate(ca_cert),
        crypto_modules().serialization.load_der_private_key(ca_key, password=None),
        common_name=san[1],
        server_auth=True,
        validity_days=validity_days,
        san=san,
    )
    host_id = uuid.uuid4().hex
    write_bytes(trust_path(root, TRUST_CA_CERT_NAME), ca_cert, 0o644)
    write_bytes(trust_path(root, TRUST_CA_KEY_NAME), ca_key, 0o600)
    write_bytes(trust_path(root, TRUST_SERVER_CERT_NAME), server_cert, 0o644)
    write_bytes(trust_path(root, TRUST_SERVER_KEY_NAME), server_key, 0o600)
    write_json(
        trust_path(root, TRUST_ENDPOINT_NAME),
        {
            "endpoint_host": san[1],
            "endpoint_host_kind": san[0],
            "endpoint_port": args.endpoint_port,
        },
    )
    registry: dict[str, object] = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "authorization_revision": 0,
        "host_id": host_id,
        "devices": [],
    }
    registry_bytes = write_registry(root, registry)
    content, registry_sha = refresh_projection(root, registry, registry_bytes)
    projection_sha = write_projection(root, content)
    emit({
        "state": "initialized",
        "host_id": host_id,
        "authorization_revision": 0,
        "registry_sha256": registry_sha,
        "projection_sha256": projection_sha,
        "ca_sha256": sha256_bytes(ca_cert),
        "server_sha256": sha256_bytes(server_cert),
        "endpoint_host": san[1],
        "endpoint_port": args.endpoint_port,
        "server_not_before_unix": not_before,
        "server_not_after_unix": not_after,
    })
    return 0


def command_trust_init(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    lock_fd = acquire_start_lock(root)
    try:
        return trust_init_locked(args, root)
    finally:
        release_start_lock(lock_fd)


def projection_publication_state(
    root: Path,
    registry: dict[str, object],
    registry_bytes: bytes,
) -> tuple[str, str | None]:
    projection_path = trust_path(root, TRUST_PROJECTION_NAME)
    if not projection_path.is_file():
        return "missing", None
    projection_bytes = projection_path.read_bytes()
    projection_sha = sha256_bytes(projection_bytes)
    expected, _ = refresh_projection(root, registry, registry_bytes)
    return ("current" if projection_bytes == expected else "stale"), projection_sha


def trust_status(root: Path) -> dict[str, object]:
    """Single public calculation of committed trust state.

    Projection publication is a file fact owned here. Endpoint applied
    authorization is not: it requires the endpoint owner's typed receipt
    (revision + registry/projection material + PID). That receipt boundary is
    not implemented in this module, so ``authorization_state`` stays
    ``not_refreshed`` and no endpoint PID is invented. The declared
    ``deny_all``/``refreshed`` values remain reserved for the endpoint receipt.
    """
    registry_path = trust_path(root, TRUST_REGISTRY_NAME)
    if not registry_path.is_file():
        raise ServiceError("trust authority is not initialized", TRUST_EXIT_CODE, code="TRUST_NOT_INITIALIZED")
    registry_bytes = registry_path.read_bytes()
    registry = parse_trust_registry(registry_bytes)
    registry_sha = sha256_bytes(registry_bytes)
    projection_state, projection_sha = projection_publication_state(root, registry, registry_bytes)
    active_devices = [device for device in registry["devices"] if device["status"] == "active"]
    return {
        "state": "trust",
        "host_id": registry["host_id"],
        "authorization_revision": registry["authorization_revision"],
        "registry_sha256": registry_sha,
        "projection_sha256": projection_sha,
        "projection_state": projection_state,
        "authorization_state": "not_refreshed",
        "endpoint_pid": None,
        "active_clients": len(active_devices),
        "devices": [
            {
                "device_id": device["device_id"],
                "device_label": device["device_label"],
                "client_certificate_sha256": device["client_certificate_sha256"],
                "scopes": device["scopes"],
                "initial_profile_grant": device["initial_profile_grant"],
                "scope_revision": device["scope_revision"],
                "status": device["status"],
            }
            for device in registry["devices"]
        ],
    }


def command_trust_status(args: argparse.Namespace) -> int:
    emit(trust_status(root_from(args.root)))
    return 0


def enroll_device(
    root: Path,
    *,
    label: str,
    scopes: list[str] | None,
    selection: object,
    validity_days: int = DEFAULT_CERT_VALIDITY_DAYS,
) -> dict[str, object]:
    lock_fd = acquire_start_lock(root, blocking=True)
    try:
        return _enroll_device_locked(
            root,
            label=label,
            scopes=scopes,
            selection=selection,
            validity_days=validity_days,
        )
    finally:
        release_start_lock(lock_fd)


def _enroll_device_locked(
    root: Path,
    *,
    label: str,
    scopes: list[str] | None,
    selection: object,
    validity_days: int = DEFAULT_CERT_VALIDITY_DAYS,
) -> dict[str, object]:
    if not isinstance(label, str) or not 1 <= len(label) <= LABEL_MAX_LENGTH or not label.isprintable():
        raise ServiceError(
            f"device label must be 1..{LABEL_MAX_LENGTH} printable characters",
            TRUST_EXIT_CODE,
            code="DEVICE_LABEL_INVALID",
        )
    if type(validity_days) is not int or not 1 <= validity_days <= MAX_CERT_VALIDITY_DAYS:
        raise ServiceError(
            f"leaf validity must be 1..{MAX_CERT_VALIDITY_DAYS} days",
            TRUST_EXIT_CODE,
            code="CERT_VALIDITY_INVALID",
        )
    normalized_scopes = normalize_scopes(scopes)
    validate_selection(selection)
    registry = load_trust_registry(root)
    modules = crypto_modules()
    ca_cert_bytes = trust_path(root, TRUST_CA_CERT_NAME).read_bytes()
    ca_key_bytes = trust_path(root, TRUST_CA_KEY_NAME).read_bytes()
    ca_certificate = modules.x509.load_der_x509_certificate(ca_cert_bytes)
    ca_key = modules.serialization.load_der_private_key(ca_key_bytes, password=None)
    device_id = str(uuid.uuid4())
    client_cert, client_key, _, not_before, not_after = generate_leaf(
        ca_certificate,
        ca_key,
        common_name=f"AgentBrowser device {device_id}",
        server_auth=False,
        validity_days=validity_days,
    )
    fingerprint = sha256_bytes(client_cert)
    created_at = int(time.time())
    record = {
        "device_record_version": DEVICE_RECORD_VERSION,
        "device_id": device_id,
        "device_label": label,
        "client_certificate_sha256": fingerprint,
        "client_certificate_der_base64": base64.b64encode(client_cert).decode("ascii"),
        "client_key_pkcs8_der_base64": base64.b64encode(client_key).decode("ascii"),
        "not_before_unix": not_before,
        "not_after_unix": not_after,
    }
    record_path = trust_device_path(root, device_id)
    write_json(record_path, record)
    revision = registry["authorization_revision"] + 1
    rebind_active_grants(registry, revision)
    registry["authorization_revision"] = revision
    registry["devices"].append({
        "device_id": device_id,
        "device_label": label,
        "client_certificate_sha256": fingerprint,
        "scopes": normalized_scopes,
        "initial_profile_grant": {
            "selection": selection,
            "authorization_revision": revision,
        },
        "scope_revision": revision,
        "status": "active",
        "not_before_unix": not_before,
        "not_after_unix": not_after,
        "created_at_unix": created_at,
        "revoked_at_unix": None,
    })
    try:
        registry_bytes = write_registry(root, registry)
    except ServiceError:
        try:
            record_path.unlink()
        except FileNotFoundError:
            pass
        raise
    content, registry_sha = refresh_projection(root, registry, registry_bytes)
    try:
        projection_sha = write_projection(root, content)
    except ServiceError as error:
        return {
            "state": "partial",
            "error": "PARTIAL_REGISTRY_COMMIT",
            "cause": error.code,
            "message": str(error),
            "device_id": device_id,
            "authorization_revision": revision,
            "registry_sha256": registry_sha,
            "projection_sha256": None,
            "endpoint_refresh": "required",
            "catalog_membership": "unavailable",
        }
    return {
        "state": "enrolled",
        "device_id": device_id,
        "authorization_revision": revision,
        "registry_sha256": registry_sha,
        "projection_sha256": projection_sha,
        "client_certificate_sha256": fingerprint,
        "scopes": normalized_scopes,
        "initial_profile_grant": registry["devices"][-1]["initial_profile_grant"],
        "endpoint_refresh": "required",
        "catalog_membership": "unavailable",
    }


def command_trust_provision(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    result = enroll_device(
        root,
        label=args.label,
        scopes=args.scope,
        selection=parse_initial_profile(args.initial_profile),
        validity_days=args.validity_days,
    )
    emit(result)
    return 0 if result.get("state") != "partial" else PARTIAL_EXIT_CODE


def revoke_device(root: Path, device_id: str) -> dict[str, object]:
    lock_fd = acquire_start_lock(root, blocking=True)
    try:
        return _revoke_device_locked(root, device_id)
    finally:
        release_start_lock(lock_fd)


def _revoke_device_locked(root: Path, device_id: str) -> dict[str, object]:
    registry = load_trust_registry(root)
    device = find_device(registry, device_id)
    if device is None:
        raise ServiceError(
            f"unknown device: {device_id}",
            TRUST_EXIT_CODE,
            code="DEVICE_NOT_FOUND",
        )
    if device["status"] == "revoked":
        registry_bytes = trust_path(root, TRUST_REGISTRY_NAME).read_bytes()
        content, registry_sha = refresh_projection(root, registry, registry_bytes)
        projection_sha = write_projection(root, content)
        return {
            "state": "revoked",
            "device_id": device_id,
            "authorization_revision": registry["authorization_revision"],
            "registry_sha256": registry_sha,
            "projection_sha256": projection_sha,
            "already_revoked": True,
            "endpoint_refresh": "required",
        }
    revision = registry["authorization_revision"] + 1
    rebind_active_grants(registry, revision)
    registry["authorization_revision"] = revision
    device["status"] = "revoked"
    device["revoked_at_unix"] = int(time.time())
    registry_bytes = write_registry(root, registry)
    content, registry_sha = refresh_projection(root, registry, registry_bytes)
    try:
        projection_sha = write_projection(root, content)
    except ServiceError as error:
        return {
            "state": "partial",
            "error": "PARTIAL_REGISTRY_COMMIT",
            "cause": error.code,
            "message": str(error),
            "device_id": device_id,
            "authorization_revision": revision,
            "registry_sha256": registry_sha,
            "projection_sha256": None,
            "endpoint_refresh": "required",
        }
    return {
        "state": "revoked",
        "device_id": device_id,
        "authorization_revision": revision,
        "registry_sha256": registry_sha,
        "projection_sha256": projection_sha,
        "already_revoked": False,
        "endpoint_refresh": "required",
    }


def command_trust_revoke(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    result = revoke_device(root, args.device_id)
    emit(result)
    return 0 if result.get("state") != "partial" else PARTIAL_EXIT_CODE


def build_device_transfer_p12(root: Path, device_id: str, transfer_code: str) -> bytes:
    validate_transfer_code(transfer_code)
    registry = load_trust_registry(root)
    device = find_device(registry, device_id)
    if device is None:
        raise ServiceError(f"unknown device: {device_id}", TRUST_EXIT_CODE, code="DEVICE_NOT_FOUND")
    if device["status"] != "active":
        raise ServiceError(
            "device is not a current active registry entry",
            TRUST_EXIT_CODE,
            code="BUNDLE_NOT_READY",
        )
    record = load_device_record(root, device_id)
    if record is None:
        raise ServiceError(f"device material is missing: {device_id}", TRUST_EXIT_CODE, code="BUNDLE_NOT_READY")
    modules = crypto_modules()
    ca_certificate = modules.x509.load_der_x509_certificate(trust_path(root, TRUST_CA_CERT_NAME).read_bytes())
    server_certificate = modules.x509.load_der_x509_certificate(trust_path(root, TRUST_SERVER_CERT_NAME).read_bytes())
    client_certificate = modules.x509.load_der_x509_certificate(
        base64.b64decode(record["client_certificate_der_base64"], validate=True)
    )
    client_key = modules.serialization.load_der_private_key(
        base64.b64decode(record["client_key_pkcs8_der_base64"], validate=True),
        password=None,
    )
    return modules.pkcs12.serialize_key_and_certificates(
        name=P12_ALIAS_CLIENT.encode("ascii"),
        key=client_key,
        cert=client_certificate,
        cas=[
            modules.pkcs12.PKCS12Certificate(server_certificate, P12_ALIAS_ENDPOINT.encode("ascii")),
            modules.pkcs12.PKCS12Certificate(ca_certificate, P12_ALIAS_HOST_CA.encode("ascii")),
        ],
        encryption_algorithm=modules.serialization.BestAvailableEncryption(transfer_code.encode("ascii")),
    )


def export_bundle(
    root: Path,
    device_id: str,
    output_path: Path,
    *,
    membership_receipt: dict[str, object] | None = None,
    endpoint_receipt: dict[str, object] | None = None,
) -> dict[str, object]:
    registry = load_trust_registry(root)
    device = find_device(registry, device_id)
    if device is None or device["status"] != "active" or load_device_record(root, device_id) is None:
        raise ServiceError(
            "device is not a current active registry entry",
            TRUST_EXIT_CODE,
            code="BUNDLE_NOT_READY",
        )
    if device["initial_profile_grant"]["authorization_revision"] != registry["authorization_revision"]:
        raise ServiceError(
            "device grant is not bound to the current registry revision",
            TRUST_EXIT_CODE,
            code="BUNDLE_NOT_READY",
        )
    if membership_receipt is None:
        raise ServiceError(
            "Host profile catalog membership is unavailable; bundle cannot be marked usable",
            TRUST_EXIT_CODE,
            code="PROFILE_CATALOG_UNAVAILABLE",
        )
    if (
        not isinstance(membership_receipt, dict)
        or membership_receipt.get("device_id") != device_id
        or type(membership_receipt.get("catalog_revision")) is not int
        or membership_receipt["catalog_revision"] < 0
        or not isinstance(membership_receipt.get("profile_id"), str)
        or not membership_receipt["profile_id"]
    ):
        raise ServiceError(
            "profile membership receipt does not match the enrolled device and profile",
            TRUST_EXIT_CODE,
            code="PROFILE_ACCESS_WRITE_FAILED",
        )
    selection = device["initial_profile_grant"]["selection"]
    if isinstance(selection, dict) and membership_receipt["profile_id"] != selection["profile_id"]:
        raise ServiceError(
            "profile membership receipt does not match the initial profile grant",
            TRUST_EXIT_CODE,
            code="PROFILE_ACCESS_WRITE_FAILED",
        )
    if endpoint_receipt is None:
        raise ServiceError(
            "running endpoint has not applied the current authorization",
            TRUST_EXIT_CODE,
            code="ENDPOINT_REFRESH_REQUIRED",
        )
    registry_bytes = trust_path(root, TRUST_REGISTRY_NAME).read_bytes()
    registry_sha = sha256_bytes(registry_bytes)
    projection_path = trust_path(root, TRUST_PROJECTION_NAME)
    if not projection_path.is_file():
        raise ServiceError(
            "authorized-clients projection is missing",
            TRUST_EXIT_CODE,
            code="ENDPOINT_REFRESH_REQUIRED",
        )
    expected_projection, _ = refresh_projection(root, registry, registry_bytes)
    projection_bytes = projection_path.read_bytes()
    if projection_bytes != expected_projection:
        raise ServiceError(
            "authorized-clients projection does not match the current registry",
            TRUST_EXIT_CODE,
            code="PROJECTION_MATERIAL_CONFLICT",
        )
    projection_sha = sha256_bytes(projection_bytes)
    if (
        not isinstance(endpoint_receipt, dict)
        or endpoint_receipt.get("status") != "refreshed"
        or type(endpoint_receipt.get("endpoint_pid")) is not int
        or endpoint_receipt["endpoint_pid"] <= 0
        or endpoint_receipt.get("authorization_revision") != registry["authorization_revision"]
        or endpoint_receipt.get("registry_sha256") != registry_sha
        or endpoint_receipt.get("projection_sha256") != projection_sha
    ):
        raise ServiceError(
            "endpoint applied receipt does not match the current registry revision or material",
            TRUST_EXIT_CODE,
            code="PROJECTION_MATERIAL_CONFLICT",
        )
    transfer_code = generate_transfer_code()
    p12 = build_device_transfer_p12(root, device_id, transfer_code)
    endpoint = trust_endpoint(root)
    endpoint_url = endpoint_ws_url(
        str(endpoint["endpoint_host"]),
        str(endpoint["endpoint_host_kind"]),
        int(endpoint["endpoint_port"]),
    )
    document = {
        "format": "agentbrowser.connection",
        "version": 1,
        "transport": "wss",
        "endpoint": endpoint_url,
        "device_id": device_id,
        "device_label": device["device_label"],
        "created_at_unix": int(time.time()),
        "p12_base64": base64.b64encode(p12).decode("ascii"),
        "p12_sha256": sha256_bytes(p12),
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
        "transfer_code": transfer_code,
        "authorization_revision": registry["authorization_revision"],
        "registry_sha256": registry_sha,
        "projection_sha256": projection_sha,
    }


def command_trust_export(args: argparse.Namespace) -> int:
    root = root_from(args.root)
    output_path = normalize_absolute(args.output, "bundle output")
    result = export_bundle(root, args.device_id, output_path)
    # The one-time transfer code is a sensitive in-process value and is never
    # emitted to stdout, argv, the bundle, or the registry.
    result = {key: value for key, value in result.items() if key != "transfer_code"}
    emit(result)
    return 0


def trust_summary(root: Path) -> dict[str, object] | None:
    if not trust_path(root, TRUST_REGISTRY_NAME).is_file():
        return None
    status = trust_status(root)
    return {
        "authorization_revision": status["authorization_revision"],
        "authorization_state": status["authorization_state"],
        "registry_sha256": status["registry_sha256"],
        "projection_sha256": status["projection_sha256"],
        "projection_state": status["projection_state"],
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

    trust = commands.add_parser("trust", help="own the local CA, device registry and authorized-client projection")
    trust_commands = trust.add_subparsers(dest="trust_command", required=True)

    trust_init = trust_commands.add_parser("init", help="initialize the private trust root exactly once")
    add_root_option(trust_init)
    trust_init.add_argument("--endpoint-host", required=True, help="endpoint DNS name or IP used as the server SAN")
    trust_init.add_argument("--endpoint-port", type=int, default=8443)
    trust_init.add_argument("--validity-days", type=int, default=DEFAULT_CERT_VALIDITY_DAYS)

    trust_status = trust_commands.add_parser("status", help="report the committed registry and projection")
    add_root_option(trust_status)

    trust_provision = trust_commands.add_parser("provision", help="enroll one device with explicit scopes and initial grant")
    add_root_option(trust_provision)
    trust_provision.add_argument("--label", required=True)
    trust_provision.add_argument("--scope", action="append")
    trust_provision.add_argument("--initial-profile", required=True)
    trust_provision.add_argument("--validity-days", type=int, default=DEFAULT_CERT_VALIDITY_DAYS)

    trust_revoke = trust_commands.add_parser("revoke", help="revoke one enrolled device")
    add_root_option(trust_revoke)
    trust_revoke.add_argument("--device-id", required=True)

    trust_export = trust_commands.add_parser("export", help="export a connection bundle after every receipt matches")
    add_root_option(trust_export)
    trust_export.add_argument("--device-id", required=True)
    trust_export.add_argument("--output", required=True)
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
            if args.trust_command == "provision":
                return command_trust_provision(args)
            if args.trust_command == "revoke":
                return command_trust_revoke(args)
            if args.trust_command == "export":
                return command_trust_export(args)
            raise ServiceError(f"unsupported trust command: {args.trust_command}")
        raise ServiceError(f"unsupported command: {args.command}")
    except ServiceError as error:
        if error.code:
            print(
                json.dumps({"error": {"code": error.code, "message": str(error)}}, ensure_ascii=False),
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
