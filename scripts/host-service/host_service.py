#!/usr/bin/env python3
"""Own the local Obscura Host process without owning browser Session state."""

from __future__ import annotations

import argparse
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
from xml.sax.saxutils import escape as xml_escape


CONFIG_VERSION = 1
HOST_PROTOCOL_VERSION = 4
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


class ServiceError(Exception):
    """A user-facing service error with a stable non-zero result."""

    def __init__(self, message: str, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


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


def write_json(path: Path, value: dict[str, object]) -> None:
    assert_private_directory(path.parent)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def expected_config_keys() -> set[str]:
    return {
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


def load_config(root: Path) -> tuple[dict[str, object], str]:
    path = config_path(root)
    if not path.is_file():
        raise ServiceError(f"service is not installed: missing {path}", 1)
    if mode(path) != 0o600:
        raise ServiceError(f"config permissions must be 0600: {path}")
    config = load_json(path, "service config")
    unknown = sorted(set(config) - expected_config_keys())
    if unknown:
        raise ServiceError(f"unsupported config keys: {', '.join(unknown)}")
    if config.get("config_version") != CONFIG_VERSION:
        raise ServiceError("unsupported host-service config version")
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
    if config.get("protocol_version") != HOST_PROTOCOL_VERSION:
        raise ServiceError(f"host protocol version must be {HOST_PROTOCOL_VERSION}")
    profile_id = config.get("profile_id")
    if not isinstance(profile_id, str):
        raise ServiceError("profile_id must be a string")
    validate_profile_id(profile_id)
    if config.get("profile_mode") != "in_memory":
        raise ServiceError("profile_mode must be in_memory; disk profile persistence is not supported by this Host")
    if not isinstance(config.get("allow_private_network"), bool):
        raise ServiceError("allow_private_network must be boolean")
    runtime_root = runtime_root_from(str(config.get("runtime_root", "")))
    if str(runtime_root) != config["runtime_root"]:
        raise ServiceError("runtime_root must be the canonical absolute path")
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
        "runtime_root",
        "socket_dir",
        "session_id",
        "config_id",
        "config_sha256",
        "launch_mode",
        "started_at_unix",
    }
    missing = sorted(expected - set(state))
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
    if state["protocol_version"] != HOST_PROTOCOL_VERSION:
        raise ServiceError(f"service state protocol version must be {HOST_PROTOCOL_VERSION}")
    profile_id = state["profile_id"]
    if not isinstance(profile_id, str):
        raise ServiceError("service state profile_id must be a string")
    validate_profile_id(profile_id)
    if state["profile_mode"] != "in_memory":
        raise ServiceError("service state profile_mode must be in_memory")
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
    if not isinstance(launch_mode, str) or launch_mode not in {"subprocess", "subprocess_starting", "launchd"}:
        raise ServiceError("service state launch_mode is invalid")
    session_id = state["session_id"]
    if session_id is not None and (not isinstance(session_id, str) or not session_id):
        raise ServiceError("service state session_id is invalid")
    if launch_mode == "subprocess" and session_id is None:
        raise ServiceError("subprocess service state requires session_id")
    if launch_mode == "subprocess_starting" and session_id is not None:
        raise ServiceError("starting subprocess state cannot have session_id")
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


def host_command_matches(snapshot: tuple[str, str], binary: str, socket_dir: str) -> bool:
    try:
        argv = shlex.split(snapshot[1])
    except ValueError:
        return False
    if len(argv) < 3 or argv[:3] != [binary, "--socket-dir", socket_dir]:
        return False
    return argv[3:] in ([], ["--allow-private-network"])


def stop_target_status(pid: int, binary: str, socket_dir: str) -> str:
    snapshot = process_snapshot(pid)
    if snapshot is None:
        if pid_alive(pid):
            return "unknown"
        return "gone"
    if snapshot[0].startswith("Z"):
        return "gone"
    if not host_command_matches(snapshot, binary, socket_dir):
        return "changed"
    return "owned"


def stop_target_is_verified(pid: int, binary: str, socket_dir: str) -> bool:
    status = stop_target_status(pid, binary, socket_dir)
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
    if not host_command_matches(snapshot, binary, socket_dir):
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


def ready_message(socket_path: Path, timeout_seconds: float, pid: int | None = None) -> dict[str, object]:
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
                raise ServiceError("Host closed before ready response")
            value = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
            if not isinstance(value, dict) or value.get("type") != "ready":
                raise ServiceError("Host ready response has invalid type")
            if value.get("version") != HOST_PROTOCOL_VERSION:
                raise ServiceError(f"Host protocol version is not {HOST_PROTOCOL_VERSION}")
            session_id = value.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise ServiceError("Host ready response has no session_id")
            return value
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ServiceError) as error:
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


def acquire_start_lock(root: Path) -> int:
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
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
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


def state_for(config: dict[str, object], config_hash: str, pid: int, socket_dir: Path, mode_name: str, session_id: str | None) -> dict[str, object]:
    return {
        "state_version": 1,
        "service_label": config["service_label"],
        "pid": pid,
        "host_binary": config["host_binary"],
        "binary_sha256": config["binary_sha256"],
        "protocol_version": config["protocol_version"],
        "profile_id": config["profile_id"],
        "profile_mode": config["profile_mode"],
        "runtime_root": config["runtime_root"],
        "socket_dir": str(socket_dir),
        "session_id": session_id,
        "config_id": config["config_id"],
        "config_sha256": config_hash,
        "launch_mode": mode_name,
        "started_at_unix": int(time.time()),
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


def stop_pid(pid: int, binary: str, socket_dir: str) -> None:
    if not stop_target_is_verified(pid, binary, socket_dir):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = stop_target_status(pid, binary, socket_dir)
        if status == "gone":
            return
        time.sleep(0.05)
    term_status = stop_target_status(pid, binary, socket_dir)
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
        status = stop_target_status(pid, binary, socket_dir)
        if status == "gone":
            return
        if status != "owned":
            raise ServiceError(f"Host PID identity changed; refusing to signal: {pid}")
        time.sleep(0.05)
    final_status = stop_target_status(pid, binary, socket_dir)
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
        "<key>KeepAlive</key><true/>\n"
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
    runtime_root = runtime_root_from(args.runtime_root)
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
        "protocol_version": HOST_PROTOCOL_VERSION,
        "profile_id": profile_id,
        "profile_mode": "in_memory",
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
        "protocol_version": HOST_PROTOCOL_VERSION,
        "profile_id": profile_id,
        "profile_mode": "in_memory",
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
        command = [str(binary), "--socket-dir", str(socket_dir)]
        if config["allow_private_network"]:
            command.append("--allow-private-network")
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
        ready = ready_message(socket_dir / "host.sock", args.ready_timeout, process.pid)
        state = state_for(config, config_hash, process.pid, socket_dir, "subprocess", str(ready["session_id"]))
        write_json(state_path(root), state)
        emit({
            "state": "running",
            "service_label": config["service_label"],
            "pid": process.pid,
            "session_id": ready["session_id"],
            "protocol_version": HOST_PROTOCOL_VERSION,
            "profile_id": config["profile_id"],
            "profile_mode": config["profile_mode"],
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
        "runtime_root": config["runtime_root"],
        "config_id": config["config_id"],
        "config_sha256": config_hash,
        "config_permissions": oct(mode(config_path(root))),
    }
    if state is None:
        payload["state"] = "stopped"
        emit(payload)
        return 0
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
        payload["state"] = "starting"
        emit(payload)
        return 0
    try:
        ready = ready_message(socket_dir / "host.sock", 1.0)
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
    if alive and state.get("launch_mode") == "launchd":
        raise ServiceError("launchd-managed Host must be stopped through launchctl; refusing subprocess stop")
    if alive:
        stop_pid(
            pid_from_state(state),
            str(state.get("host_binary", "")),
            str(state.get("socket_dir", "")),
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
        write_json(state_path(root), state_for(config, config_hash, os.getpid(), socket_dir, "launchd", None))
        command = [str(binary), "--socket-dir", str(socket_dir)]
        if config["allow_private_network"]:
            command.append("--allow-private-network")
        release_start_lock(lock_fd)
        lock_fd = -1
        os.execv(str(binary), command)
    finally:
        if lock_fd != -1:
            release_start_lock(lock_fd)
    raise ServiceError("launchd exec returned unexpectedly")


def add_root_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=argparse.SUPPRESS, help="private service state root")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Install and supervise one independent Obscura Host process")
    result.add_argument("--root", default=str(DEFAULT_ROOT), help="private service state root")
    commands = result.add_subparsers(dest="command", required=True)

    install = commands.add_parser("install", help="bind binary/config and write an unloaded launchd plist")
    add_root_option(install)
    install.add_argument("--binary", required=True)
    install.add_argument("--runtime-root")
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
        raise ServiceError(f"unsupported command: {args.command}")
    except ServiceError as error:
        print(f"host-service: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("host-service: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
