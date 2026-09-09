#!/usr/bin/env python3
"""Run the AgentBrowser AppSDK admission preflight once, fail-closed.

The helper binds a committed AgentBrowser candidate to a clean, external
Obscura protocol checkout.  It records command output under the requested
evidence directory and never writes AppSDK lifecycle records.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


class StageFailure(Exception):
    """A first-order preflight failure with an actionable owner boundary."""

    def __init__(self, stage: str, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        failure = {
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
        }
        if self.details is not None:
            failure["details"] = self.details
        return failure


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def relpath(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def run_command(
    argv: list[str],
    cwd: Path,
    log_path: Path | None = None,
    env: dict[str, str] | None = None,
    keep_output: bool = False,
) -> dict[str, Any]:
    """Run one command and preserve its complete combined output."""

    command = [str(item) for item in argv]
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        output = result.stdout or b""
        exit_code = result.returncode
        error = None
    except OSError as exc:
        output = str(exc).encode("utf-8", errors="replace")
        exit_code = None
        error = str(exc)

    if log_path is not None:
        log_path.write_bytes(output)

    record: dict[str, Any] = {
        "argv": command,
        "cwd": str(cwd.resolve()),
        "exit_code": exit_code,
        "output_log": str(log_path) if log_path is not None else None,
        "output_sha256": sha256_bytes(output),
        "output_preview": output.decode("utf-8", errors="replace")[:4096],
    }
    if error is not None:
        record["error"] = error
    if keep_output:
        record["_output"] = output.decode("utf-8", errors="replace")
    return record


def run_git(cwd: Path, args: list[str]) -> dict[str, Any]:
    result = run_command(["git", *args], cwd, keep_output=True)
    if result["exit_code"] != 0:
        raise StageFailure(
            "git_identity",
            "GIT_METADATA_UNAVAILABLE",
            f"git {' '.join(args)} failed",
            result,
        )
    return result


def git_value(cwd: Path, args: list[str]) -> str:
    result = run_git(cwd, args)
    return result["_output"].strip()


def tracked_files(project_root: Path) -> list[str]:
    result = run_git(project_root, ["ls-files", "-z"])
    return [item for item in result["_output"].split("\0") if item]


def candidate_identity(project_root: Path) -> dict[str, Any]:
    branch = git_value(project_root, ["branch", "--show-current"])
    if branch in {"", "main", "master"}:
        raise StageFailure(
            "candidate_identity",
            "CANDIDATE_BRANCH_INVALID",
            "Admission requires a named non-main candidate branch",
            {"branch": branch},
        )

    status = git_value(project_root, ["status", "--porcelain=v1"])
    if status:
        raise StageFailure(
            "candidate_identity",
            "CANDIDATE_WORKTREE_DIRTY",
            "Admission requires a clean candidate worktree",
            {"status": status},
        )

    commit = git_value(project_root, ["rev-parse", "HEAD"])
    tree = git_value(project_root, ["rev-parse", "HEAD^{tree}"])
    base_ref = "origin/main"
    base_commit = git_value(project_root, ["rev-parse", base_ref])
    changed = git_value(project_root, ["diff", "--name-only", f"{base_ref}...HEAD"])
    return {
        "root": str(project_root.resolve()),
        "branch": branch,
        "commit": commit,
        "tree": tree,
        "base_ref": base_ref,
        "base_commit": base_commit,
        "changed_paths": [item for item in changed.splitlines() if item],
        "worktree": "clean",
    }


def protocol_identity(project_root: Path, raw_root: str | None) -> dict[str, Any]:
    if not raw_root:
        raise StageFailure(
            "protocol_binding",
            "OBSCURA_PROTOCOL_ROOT_UNSET",
            "Set OBSCURA_PROTOCOL_ROOT or pass --protocol-root",
        )

    protocol_root = Path(raw_root).expanduser()
    if not protocol_root.is_absolute():
        protocol_root = project_root / protocol_root
    protocol_root = protocol_root.resolve()
    if not protocol_root.is_dir():
        raise StageFailure(
            "protocol_binding",
            "OBSCURA_PROTOCOL_ROOT_MISSING",
            "Obscura protocol root is not a directory",
            {"path": str(protocol_root)},
        )

    input_root = protocol_root
    direct_required = [input_root / "Cargo.toml", input_root / "src/lib.rs"]
    checkout_protocol_root = input_root / "protocol/browser"
    checkout_required = [
        checkout_protocol_root / "Cargo.toml",
        checkout_protocol_root / "src/lib.rs",
    ]
    if all(path.is_file() for path in direct_required):
        input_kind = "crate_root"
        required = direct_required
    elif all(path.is_file() for path in checkout_required):
        input_kind = "checkout_root"
        protocol_root = checkout_protocol_root.resolve()
        required = checkout_required
    else:
        raise StageFailure(
            "protocol_binding",
            "OBSCURA_PROTOCOL_SOURCE_MISSING",
            "Obscura input must be a protocol crate root or contain protocol/browser/Cargo.toml and protocol/browser/src/lib.rs",
            {
                "path": str(protocol_root),
                "missing": [str(path) for path in direct_required if not path.is_file()],
                "checkout_protocol_root": str(checkout_protocol_root),
                "checkout_missing": [
                    str(path) for path in checkout_required if not path.is_file()
                ],
            },
        )

    # Resolve Git metadata from the supplied input path.  The input can be a
    # checkout root, while AppSDK and Cargo must receive the nested crate root.
    repo_root_result = run_command(
        ["git", "-C", str(input_root), "rev-parse", "--show-toplevel"],
        project_root,
        keep_output=True,
    )
    if repo_root_result["exit_code"] != 0:
        raise StageFailure(
            "protocol_binding",
            "OBSCURA_PROTOCOL_GIT_METADATA_MISSING",
            "Obscura protocol root is not inside a Git checkout",
            repo_root_result,
        )
    repo_root = Path(repo_root_result["output_preview"].strip()).resolve()
    try:
        source_rel = protocol_root.relative_to(repo_root)
    except ValueError as exc:
        raise StageFailure(
            "protocol_binding",
            "OBSCURA_PROTOCOL_ROOT_OUTSIDE_REPOSITORY",
            "Obscura protocol root is outside its Git repository",
            {"path": str(protocol_root), "repo_root": str(repo_root)},
        ) from exc

    dirty = git_value(repo_root, ["status", "--porcelain=v1"])
    if dirty:
        raise StageFailure(
            "protocol_binding",
            "OBSCURA_PROTOCOL_REPOSITORY_DIRTY",
            "Obscura protocol repository must be clean for a reproducible bind",
            {"repo_root": str(repo_root), "status": dirty},
        )

    commit = git_value(repo_root, ["rev-parse", "HEAD"])
    repository_tree = git_value(repo_root, ["rev-parse", "HEAD^{tree}"])
    source_ref = "HEAD^{tree}" if not source_rel.parts else f"HEAD:{source_rel.as_posix()}"
    source_tree = git_value(repo_root, ["rev-parse", source_ref])
    source_files = [source_rel / "Cargo.toml", source_rel / "src/lib.rs"]
    untracked = []
    for source_file in source_files:
        tracked = run_command(["git", "ls-files", "--error-unmatch", source_file.as_posix()], repo_root)
        if tracked["exit_code"] != 0:
            untracked.append(source_file.as_posix())
    if untracked:
        raise StageFailure(
            "protocol_binding",
            "OBSCURA_PROTOCOL_SOURCE_UNTRACKED",
            "Obscura protocol source entry is not tracked by its Git owner",
            {"sources": untracked},
        )

    return {
        "root": str(protocol_root),
        "repository_root": str(repo_root),
        "input_root": str(input_root),
        "input_kind": input_kind,
        "source_path": source_rel.as_posix() or ".",
        "source_commit": commit,
        "repository_tree": repository_tree,
        "source_tree": source_tree,
        "status": "bound",
        "source_files": [
            {"path": str(path), "sha256": sha256_file(path)} for path in required
        ],
    }


def connection_binding(project_root: Path, files: list[str]) -> dict[str, Any]:
    copied = [item for item in files if item == "protocol/browser" or item.startswith("protocol/browser/")]
    if copied:
        raise StageFailure(
            "protocol_binding",
            "PROTOCOL_SOURCE_COPIED",
            "AgentBrowser must not contain a copy of the Obscura browser protocol",
            {"paths": copied},
        )

    path = project_root / "scripts/connection.py"
    if not path.is_file():
        cargo_files = [item for item in files if item == "Cargo.toml" or item.endswith("/Cargo.toml")]
        if cargo_files:
            raise StageFailure(
                "protocol_binding",
                "CONNECTION_PROTOCOL_BINDING_MISSING",
                "A Rust candidate with Cargo manifests must expose scripts/connection.py",
                {"cargo_manifests": cargo_files, "expected": "scripts/connection.py"},
            )
        return {"path": "scripts/connection.py", "status": "not_applicable"}

    source = path.read_text(encoding="utf-8")
    if "OBSCURA_PROTOCOL_ROOT" not in source:
        raise StageFailure(
            "protocol_binding",
            "CONNECTION_PROTOCOL_ENV_BINDING_MISSING",
            "scripts/connection.py does not bind OBSCURA_PROTOCOL_ROOT",
            {"path": "scripts/connection.py"},
        )
    patch_names = sorted(set(re.findall(r"patch\.crates-io\.([A-Za-z0-9_-]+)\.path", source)))
    if not patch_names:
        raise StageFailure(
            "protocol_binding",
            "CONNECTION_PROTOCOL_PATCH_MISSING",
            "scripts/connection.py lacks an explicit Cargo path patch for Obscura",
            {"path": "scripts/connection.py"},
        )
    return {
        "path": "scripts/connection.py",
        "status": "bound",
        "protocol_env": "OBSCURA_PROTOCOL_ROOT",
        "cargo_patch_crates": patch_names,
    }


def dependency_commands(
    project_root: Path,
    files: list[str],
    protocol: dict[str, Any],
    connection: dict[str, Any],
) -> list[tuple[list[str], Path, str]]:
    commands: list[tuple[list[str], Path, str]] = []
    patch_crates = connection.get("cargo_patch_crates", [])
    for item in sorted(files):
        path = Path(item)
        if path.name == "package-lock.json":
            package_dir = project_root / path.parent
            if not (package_dir / "package.json").is_file():
                raise StageFailure(
                    "dependency_install",
                    "DEPENDENCY_MANIFEST_INCOMPLETE",
                    "package-lock.json has no sibling package.json",
                    {"lockfile": item},
                )
            commands.append((["npm", "ci"], package_dir, item))
        elif path.name == "pnpm-lock.yaml":
            package_dir = project_root / path.parent
            if not (package_dir / "package.json").is_file():
                raise StageFailure(
                    "dependency_install",
                    "DEPENDENCY_MANIFEST_INCOMPLETE",
                    "pnpm-lock.yaml has no sibling package.json",
                    {"lockfile": item},
                )
            commands.append((["pnpm", "install", "--frozen-lockfile"], package_dir, item))
        elif path.name == "Cargo.lock":
            cargo_dir = project_root / path.parent
            if not (cargo_dir / "Cargo.toml").is_file():
                raise StageFailure(
                    "dependency_install",
                    "DEPENDENCY_MANIFEST_INCOMPLETE",
                    "Cargo.lock has no sibling Cargo.toml",
                    {"lockfile": item},
                )
            command = ["cargo", "fetch", "--locked"]
            for crate in patch_crates:
                patch = f"patch.crates-io.{crate}.path={json.dumps(protocol['root'])}"
                command.extend(["--config", patch])
            commands.append((command, cargo_dir, item))
    return commands


def dependency_install(
    project_root: Path,
    files: list[str],
    protocol: dict[str, Any],
    connection: dict[str, Any],
    run_dir: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    commands = dependency_commands(project_root, files, protocol, connection)
    manifests = []
    for _, _, item in commands:
        path = project_root / item
        manifests.append({"path": item, "sha256_before": sha256_file(path)})
    if not commands:
        return {"status": "not_applicable", "manifests": [], "commands": []}

    records = []
    for index, (argv, cwd, manifest) in enumerate(commands, start=1):
        tool = shutil.which(argv[0])
        if tool is None:
            raise StageFailure(
                "dependency_install",
                "DEPENDENCY_TOOL_MISSING",
                f"Dependency tool is unavailable: {argv[0]}",
                {"argv": argv, "manifest": manifest},
            )
        log = run_dir / f"dependency-{index:02d}.log"
        record = run_command([tool, *argv[1:]], cwd, log, env)
        record["manifest"] = manifest
        records.append(record)
        if record["exit_code"] != 0:
            raise StageFailure(
                "dependency_install",
                "DEPENDENCY_INSTALL_FAILED",
                f"Dependency install failed for {manifest}",
                record,
            )

    status = git_value(project_root, ["status", "--porcelain=v1"])
    if status:
        raise StageFailure(
            "dependency_install",
            "DEPENDENCY_INSTALL_SOURCE_DRIFT",
            "Dependency installation changed tracked candidate files",
            {"status": status},
        )
    for item in manifests:
        item["sha256_after"] = sha256_file(project_root / item["path"])
        if item["sha256_after"] != item["sha256_before"]:
            raise StageFailure(
                "dependency_install",
                "DEPENDENCY_LOCK_DRIFT",
                "Dependency installation changed a lockfile",
                item,
            )
    return {"status": "pass", "manifests": manifests, "commands": records}


def appsdk_identity() -> dict[str, Any]:
    binary = shutil.which("appsdk")
    if binary is None:
        raise StageFailure("appsdk", "APPSDK_BINARY_MISSING", "The appsdk executable is unavailable")
    path = Path(binary).resolve()
    return {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}


def appsdk_witness(
    project_root: Path,
    protocol: dict[str, Any],
    run_dir: Path,
    env: dict[str, str],
) -> tuple[dict[str, Any], StageFailure | None]:
    identity = appsdk_identity()
    witness: dict[str, Any] = {"binary": identity, "commands": []}

    verify_log = run_dir / "appsdk-verify.log"
    verify = run_command([identity["path"], "verify"], project_root, verify_log, env)
    witness["commands"].append(verify)
    first_failure: StageFailure | None = None
    if verify["exit_code"] != 0:
        first_failure = StageFailure(
            "appsdk_verify",
            "APPSDK_VERIFY_FAILED",
            "appsdk verify failed",
            verify,
        )

    admission_log = run_dir / "appsdk-verify-admission.log"
    admission = run_command(
        [identity["path"], "verify", "--admission"],
        project_root,
        admission_log,
        env,
    )
    witness["commands"].append(admission)
    if first_failure is None and admission["exit_code"] != 0:
        first_failure = StageFailure(
            "appsdk_verify_admission",
            "APPSDK_VERIFY_ADMISSION_FAILED",
            "appsdk verify --admission failed",
            admission,
        )

    witness["protocol_root"] = protocol["root"]
    witness["status"] = "blocked" if first_failure else "verified"
    return witness, first_failure


def artifact_identity(project_root: Path) -> dict[str, Any]:
    generated = project_root / "generated"
    project_manifest = generated / "project.compiled.json"
    manifests = []
    if project_manifest.is_file():
        manifests.append(project_manifest)
    manifests.extend(sorted(generated.glob("modules/*/module.compiled.json")))
    if not manifests:
        raise StageFailure(
            "artifact_output",
            "COMPILED_ARTIFACT_MISSING",
            "appsdk compile produced no compiled artifact manifest",
            {"expected": "generated/project.compiled.json or generated/modules/*/module.compiled.json"},
        )

    output: list[dict[str, Any]] = []
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StageFailure(
                "artifact_output",
                "COMPILED_ARTIFACT_MANIFEST_INVALID",
                f"Compiled artifact manifest is unreadable: {manifest_path}",
                {"error": str(exc)},
            ) from exc
        record: dict[str, Any] = {
            "manifest": relpath(manifest_path, project_root),
            "manifest_sha256": sha256_file(manifest_path),
            "artifact_hash": manifest.get("artifact_hash"),
            "module_id": manifest.get("module_id"),
            "outputs": [],
        }
        if not manifest.get("artifact_hash"):
            raise StageFailure(
                "artifact_output",
                "COMPILED_ARTIFACT_HASH_MISSING",
                f"Compiled artifact manifest has no artifact_hash: {manifest_path}",
                {"manifest": relpath(manifest_path, project_root)},
            )
        if manifest_path.name == "module.compiled.json":
            output_root = manifest_path.parent / "lib"
            declared_artifacts = manifest.get("artifacts")
            if not isinstance(declared_artifacts, list) or not declared_artifacts:
                raise StageFailure(
                    "artifact_output",
                    "COMPILED_ARTIFACT_OUTPUT_UNDECLARED",
                    f"Compiled module manifest declares no artifact outputs: {manifest_path}",
                    {"manifest": relpath(manifest_path, project_root)},
                )
            for artifact in declared_artifacts:
                declared_path = artifact.get("path")
                if not isinstance(declared_path, str) or not declared_path or Path(declared_path).is_absolute():
                    raise StageFailure(
                        "artifact_output",
                        "COMPILED_ARTIFACT_OUTPUT_PATH_INVALID",
                        f"Compiled artifact output path is not a relative file: {declared_path}",
                        {"manifest": relpath(manifest_path, project_root), "artifact": artifact},
                    )
                artifact_path = (output_root / declared_path).resolve()
                try:
                    artifact_path.relative_to(project_root.resolve())
                except ValueError as exc:
                    raise StageFailure(
                        "artifact_output",
                        "COMPILED_ARTIFACT_OUTPUT_OUTSIDE_PROJECT",
                        f"Compiled artifact output escapes the project root: {declared_path}",
                        {"manifest": relpath(manifest_path, project_root), "artifact": artifact},
                    ) from exc
                if not artifact_path.is_file():
                    raise StageFailure(
                        "artifact_output",
                        "COMPILED_ARTIFACT_OUTPUT_MISSING",
                        f"Compiled artifact output is missing: {artifact_path}",
                        {"manifest": relpath(manifest_path, project_root), "artifact": artifact},
                    )
                actual = sha256_file(artifact_path)
                expected = artifact.get("hash")
                if expected and expected != actual:
                    raise StageFailure(
                        "artifact_output",
                        "COMPILED_ARTIFACT_HASH_MISMATCH",
                        f"Compiled artifact output hash disagrees with its manifest: {artifact_path}",
                        {"expected": expected, "actual": actual},
                    )
                record["outputs"].append(
                    {"path": relpath(artifact_path, project_root), "sha256": actual}
                )
        output.append(record)
    return {"status": "bound", "manifests": output}


def next_action(failure: dict[str, Any]) -> str:
    stage = failure["stage"]
    if stage == "candidate_identity":
        return "Commit the candidate and rerun from its clean owner worktree."
    if stage == "protocol_binding":
        return "Provide a clean Obscura checkout and rerun with its protocol source root in OBSCURA_PROTOCOL_ROOT."
    if stage == "dependency_install":
        return "Repair the owning dependency manifest/tool or external protocol checkout, then rerun once after state changes."
    if stage.startswith("appsdk_verify"):
        return "Repair the first failing AppSDK contract gate, then rerun both verify commands once after state changes."
    if stage == "appsdk":
        return "Install or select the supported global AppSDK binary, then rerun the harness."
    if stage == "compile":
        return "Repair the owning AppSDK compile precondition, then rerun compile once after the bound state changes."
    if stage == "artifact_output":
        return "Inspect the single compile output and its declared artifact paths, then rerun after the owning fix."
    return "Fix the first failing gate and rerun only after its bound inputs change."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol-root",
        default=os.environ.get("OBSCURA_PROTOCOL_ROOT"),
        help="Obscura crate root or checkout root; defaults to OBSCURA_PROTOCOL_ROOT",
    )
    parser.add_argument(
        "--evidence-dir",
        default="/tmp/agentbrowser-appsdk-admission-harness-20260908",
        help="Directory for immutable command logs and the admission witness",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent
    evidence_root = Path(args.evidence_dir).expanduser().resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    run_dir: Path | None = None
    candidate: dict[str, Any] | None = None
    protocol: dict[str, Any] | None = None
    connection: dict[str, Any] | None = None
    dependencies: dict[str, Any] = {"status": "not_run", "manifests": [], "commands": []}
    app_sdk: dict[str, Any] = {"status": "not_run", "commands": [], "compile_invocations": 0}
    artifact: dict[str, Any] = {"status": "not_run", "manifests": []}
    failure: StageFailure | None = None

    try:
        candidate = candidate_identity(project_root)
        run_stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        run_dir = evidence_root / f"{candidate['commit'][:12]}-admission-{run_stamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        files = tracked_files(project_root)
        protocol = protocol_identity(project_root, args.protocol_root)
        connection = connection_binding(project_root, files)
        command_env = os.environ.copy()
        command_env["OBSCURA_PROTOCOL_ROOT"] = protocol["root"]
        dependencies = dependency_install(
            project_root,
            files,
            protocol,
            connection,
            run_dir,
            command_env,
        )
        app_sdk, gate_failure = appsdk_witness(project_root, protocol, run_dir, command_env)
        if gate_failure is not None:
            failure = gate_failure
        else:
            compile_log = run_dir / "appsdk-compile.log"
            app_sdk["compile_invocations"] = 1
            compile_result = run_command(
                [app_sdk["binary"]["path"], "compile"],
                project_root,
                compile_log,
                command_env,
            )
            app_sdk["commands"].append(compile_result)
            if compile_result["exit_code"] != 0:
                failure = StageFailure(
                    "compile",
                    "APPSDK_COMPILE_FAILED",
                    "appsdk compile failed; no retry was attempted",
                    compile_result,
                )
            else:
                app_sdk["status"] = "compiled"
                artifact = artifact_identity(project_root)
                after_candidate = candidate_identity(project_root)
                if after_candidate["commit"] != candidate["commit"] or after_candidate["tree"] != candidate["tree"]:
                    raise StageFailure(
                        "post_compile_integrity",
                        "CANDIDATE_IDENTITY_DRIFT",
                        "Candidate commit/tree changed during admission",
                        {"before": candidate, "after": after_candidate},
                    )
                after_protocol = protocol_identity(project_root, protocol["root"])
                if (
                    after_protocol["source_commit"] != protocol["source_commit"]
                    or after_protocol["source_tree"] != protocol["source_tree"]
                ):
                    raise StageFailure(
                        "post_compile_integrity",
                        "OBSCURA_PROTOCOL_IDENTITY_DRIFT",
                        "Obscura protocol commit/tree changed during admission",
                        {"before": protocol, "after": after_protocol},
                    )
    except StageFailure as exc:
        failure = failure or exc
    except OSError as exc:
        failure = StageFailure("evidence", "EVIDENCE_WRITE_FAILED", str(exc))

    if run_dir is None:
        run_stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        run_dir = evidence_root / f"unknown-admission-{run_stamp}"
        run_dir.mkdir(parents=True, exist_ok=True)

    witness: dict[str, Any] = {
        "schema_version": 1,
        "started_at": started,
        "finished_at": utc_now(),
        "result": "blocked" if failure else "pass",
        "project": candidate,
        "protocol": protocol,
        "connection_binding": connection,
        "dependency_install": dependencies,
        "appsdk": app_sdk,
        "artifact": artifact,
        "evidence_dir": str(run_dir),
        "lifecycle_records_written": False,
        "forbidden_mutations": [
            ".appsdk/records/**",
            ".appsdk/sdk.lock",
            "hash/freeze records",
            "Obscura protocol source",
        ],
    }
    if failure is not None:
        failure_dict = failure.as_dict()
        failure_dict["retry_allowed"] = False
        failure_dict["canonical_owner"] = {
            "candidate_identity": "AgentBrowser Git owner worktree",
            "protocol_binding": "Obscura checkout owner",
            "dependency_install": "declared dependency/tool owner",
            "appsdk_verify": "AppSDK project contract owner",
            "appsdk_verify_admission": "AppSDK project contract/evidence owner",
            "compile": "AppSDK compiler and project module owner",
        }.get(failure.stage, "the owner of the first failing gate")
        failure_dict["next_action"] = next_action(failure_dict)
        witness["first_failure"] = failure_dict

    witness_path = run_dir / "admission-witness.json"
    write_json(witness_path, witness)
    witness["witness_path"] = str(witness_path)
    print(json.dumps(witness, indent=2, sort_keys=True))
    return 1 if failure else 0


if __name__ == "__main__":
    sys.exit(main())
