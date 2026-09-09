#!/usr/bin/env python3
"""Focused checks for the M1 combined replay's validation and evidence rules."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import math
import pathlib
import sys
import tempfile
from typing import Any, Callable


ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER_PATH = ROOT / "scripts" / "m1-dual-client-runner.py"
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("agentbrowser_m1_dual_runner_tests", RUNNER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load {RUNNER_PATH}")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def raises(expected: type[BaseException], function: Callable[[], Any], message: str) -> None:
    try:
        function()
    except expected:
        return
    raise AssertionError(message)


def arguments(*values: str):
    return runner.parser().parse_args(values)


def test_argument_validation() -> None:
    raises(
        ValueError,
        lambda: runner.build_arguments(arguments("--run-id", "bad id", "--dry-run")),
        "run IDs with whitespace must be rejected",
    )
    raises(
        ValueError,
        lambda: runner.build_arguments(arguments("--bind-ip", "192.0.2.10", "--dry-run")),
        "non-loopback bind addresses must be rejected",
    )
    raises(
        ValueError,
        lambda: runner.build_arguments(arguments("--android-serial", "device with-space")),
        "real runs must reject serials containing whitespace",
    )
    raises(
        ValueError,
        lambda: runner.build_arguments(arguments("--timeout", "-1", "--dry-run")),
        "negative timeout must be rejected",
    )
    for option in ("--timeout", "--poll", "--android-test-timeout"):
        for value in ("nan", "inf", "-inf"):
            raises(ValueError, lambda option=option, value=value: runner.build_arguments(arguments(f"{option}={value}", "--dry-run")), f"{option} {value} must be rejected")
    raises(
        ValueError,
        lambda: runner.build_arguments(arguments("--android-test-class", "com.example.Other", "--dry-run")),
        "instrumentation class must be fixed",
    )
    raises(
        ValueError,
        lambda: runner.build_arguments(arguments("--android-test-component", "com.example.OtherRunner", "--dry-run")),
        "instrumentation component must be fixed",
    )
    relative = runner.build_arguments(
        arguments("--dry-run", "--fixture-bin", "fixture", "--run-id", "relative-path"),
        cwd=pathlib.Path("/tmp/m1-relative-cwd"),
    )
    check(relative.fixture_bin == pathlib.Path("/tmp/m1-relative-cwd/fixture").resolve(), "relative executable paths must bind to cwd")


def test_environment_identity() -> None:
    responses = {
        ("get-state",): (0, "device\n"),
        ("shell", "getprop", "ro.product.model"): (0, "Test phone\n"),
        ("shell", "getprop", "ro.build.version.sdk"): (0, "36\n"),
        ("shell", "getprop", "ro.product.cpu.abi"): (0, "arm64-v8a\n"),
    }

    def fake_adb(command):
        return responses[tuple(command)]

    value = runner.environment_identity("serial-1", fake_adb)
    android = value["android"]
    check(android["availability"] == "known", "fake device must be recognized as available")
    check(android["model"] == "Test phone" and android["sdk"] == "36", "ADB identity fields must be recorded")
    check(android["abi"] == "arm64-v8a" and android["errors"] == [], "complete ADB identity must have no errors")


def test_adb_reverse_mapping() -> None:
    output = "host-19 tcp:52510 tcp:52510\nhost-19 tcp:52511 tcp:52511\n"
    check(
        runner.adb_reverse_mappings(output, 52510) == ["host-19 tcp:52510 tcp:52510"],
        "reverse listing must identify the selected device-side port",
    )
    check(runner.adb_reverse_mappings(output.encode(), 52512) == [], "unrelated reverse ports must not be reported")
    check(runner.adb_reverse_mappings("52510\n", 52510) == [], "adb command output is not a reverse mapping row")
    check(runner.adb_reverse_mappings("host-11 tcp:52510 tcp:525100\n", 52510) == ["host-11 tcp:52510 tcp:525100"], "device-side mapping parser must retain changed host socket for ownership checks")
    check(runner.exact_adb_reverse_mapping(["host-11 tcp:52510 tcp:525100"], "host-11", 52510) is None, "cleanup must reject a changed host socket")
    check(runner.exact_adb_reverse_mapping(["host-11 tcp:52510 tcp:52510"], "host-11", 52510) == "host-11 tcp:52510 tcp:52510", "cleanup must accept only the exact owned mapping")


def test_typed_evidence() -> None:
    check(runner.boolean_evidence(True, ["test"], "missing")["status"] == "known", "explicit true must be known")
    check(runner.boolean_evidence(False, ["test"], "missing")["value"] is False, "explicit false must remain false")
    check(runner.boolean_evidence(None, ["test"], "missing")["status"] == "unknown", "missing bool must remain unknown")
    check(runner.combined_status({"one": runner.known(True, ["test"]), "two": runner.known(True, ["test"])}) == "proved", "known true dependencies must prove")
    check(runner.combined_status({"one": runner.known(True, ["test"]), "two": runner.unknown("missing")}) == "unknown", "missing dependency must block proof")


def test_acknowledged_active_frames() -> None:
    class Bridge:
        active_generation = 7
        acked_tickets = {(7, 2), (6, 9)}
        frames = [
            {"generation": 6, "ticket": 9},
            {"generation": 7, "ticket": 1},
            {"generation": 7, "ticket": 2},
        ]
    check(runner.acknowledged_active_frames(Bridge()) == [{"generation": 7, "ticket": 2}], "only active-generation ACKed frames may support Mac evidence")

    class BoolFrameBridge:
        active_generation = 1
        acked_tickets = {(1, 2)}
        frames = [{"generation": True, "ticket": 2}]

    check(runner.acknowledged_active_frames(BoolFrameBridge()) == [], "boolean protocol integers must not project frame evidence")


def test_transport_frames_are_separate_from_display_evidence() -> None:
    class Bridge:
        active_generation = 4
        acked_tickets = {(4, 2)}
        frames = [
            {"generation": 3, "ticket": 8},
            {"generation": 4, "ticket": 1},
            {"generation": 4, "ticket": 2},
        ]

    check(
        runner.active_frame_headers(Bridge()) == [
            {"generation": 4, "ticket": 1},
            {"generation": 4, "ticket": 2},
        ],
        "Mac evidence must retain active frame headers even before transport acknowledgement",
    )
    check(
        runner.acknowledged_active_frames(Bridge()) == [{"generation": 4, "ticket": 2}],
        "transport acknowledgement must remain a subset of active frame headers",
    )
    dual_source = pathlib.Path(runner.__file__).read_text(encoding="utf-8")
    direct_source = pathlib.Path(runner._DIRECT.__file__).read_text(encoding="utf-8")
    check(
        'value.get("renderedFrames", 0)' not in dual_source + direct_source,
        "renderedFrames must not be used as direct-runner display evidence",
    )
    check(
        "h264_frame_displayed" not in direct_source,
        "bridge transport ACK must not create a displayed-frame proof",
    )


def test_combined_side_evidence_does_not_promote_bridge_ack_to_display() -> None:
    class Bridge:
        active_generation = 4
        acked_tickets = {(4, 2)}
        frames = [{"generation": 4, "ticket": 2, "session_id": "session-1", "visible_width": 347, "visible_height": 580, "coded_width": 352, "coded_height": 592, "viewport_revision": 3, "document_revision": 5}]

    combined = runner.CombinedRunner.__new__(runner.CombinedRunner)
    combined.host_statuses = [{"value": {}}]
    combined.android_result = {}
    combined.bridge = Bridge()
    combined.mac_operations = []
    combined.first_failure = None
    combined.evidence = {
        "sides": {},
        "cross_side": {},
        "required_claims": {},
        "unknown": [],
        "stages": {"mac_reconnect": {"result": "passed"}},
    }
    combined._write_json = lambda name, value: None
    combined.build_side_evidence()
    mac = combined.evidence["sides"]["mac"]
    check(mac["frame_ack"]["status"] == "unknown", "bridge ACK must not be reported as native display evidence")
    check(mac["frame_transport_ack"]["status"] == "known", "bridge ACK must remain available as transport evidence")
    check(mac["display"]["status"] == "unknown", "combined runner must expose missing native display evidence")


def test_strict_protocol_integers_and_epochs() -> None:
    check(runner.nonnegative_integer(0), "zero must be accepted as a generation integer")
    check(runner.positive_integer(3), "positive frames must be accepted as a ticket integer")
    check(not runner.nonnegative_integer(True), "true must not be accepted as a JSON integer")
    check(not runner.positive_integer(True), "true must not be accepted as a positive JSON integer")
    check(not runner.nonnegative_integer("1") and not runner.positive_integer(None), "string and missing values must not be accepted as JSON integers")
    check(runner.control_epoch(3) == 3, "valid control epoch must normalize")
    check(runner.control_epoch(True) is None, "boolean control epochs must be rejected before control commands")
    check(runner.control_epoch(-1) is None and runner.control_epoch("1") is None, "negative or malformed control epochs must be rejected")


def test_candidate_identity_drift_fails_closed() -> None:
    combined = runner.CombinedRunner.__new__(runner.CombinedRunner)
    combined.worktree = runner.ROOT
    combined.first_failure = None
    combined.evidence = {
        "failures": [],
        "candidate": {
            "branch": "codex/m1-dual-control-hardening-20260909",
            "commit": "abc",
            "tree": "def",
            "status_before": [],
        },
        "stages": {
            "cleanup": {"result": "running", "started_at": None, "finished_at": None, "details": {}, "first_failure": None},
        },
    }
    original = runner.candidate_git_probes

    def drift(cwd):
        probes = original(cwd)
        probes["commit"]["value"] = "drift-commit"
        return probes

    try:
        runner.candidate_git_probes = drift
        check(not combined._verify_candidate_identity_after(), "teardown identity drift must fail closed")
    finally:
        runner.candidate_git_probes = original
    check(combined.evidence["candidate"]["identity_drift_after"].get("commit") == {"before": "abc", "after": "drift-commit"}, "teardown must record the exact drifted identity fields")
    check(combined.first_failure is not None and combined.first_failure["code"] == "CANDIDATE_IDENTITY_DRIFT", "teardown drift must become a structured failure")


def test_device_pairing_uses_resolved_adb() -> None:
    with tempfile.TemporaryDirectory(prefix="m1-fake-adb-") as raw:
        fake_adb = pathlib.Path(raw) / "adb"
        fake_adb.write_text("#!/bin/sh\n", encoding="utf-8")
        fake_adb.chmod(0o755)
        combined = runner.CombinedRunner.__new__(runner.CombinedRunner)
        combined.adb_path = fake_adb.resolve(strict=False)
        command = combined.device_pairing_command("install", pathlib.Path("/tmp/an-fixture"))
    check(str(combined.adb_path) in command, "device pairing must receive the exact resolved ADB path")
    check(command[2:6] == ["--adb", str(combined.adb_path), "install", "/tmp/an-fixture"], "device pairing command must pass the resolved ADB executable explicitly")


def test_git_probe_failure_is_distinct() -> None:
    check(runner.git_value(pathlib.Path("/definitely/missing/worktree"), "status") is None, "Git probe failure must remain distinguishable from clean output")


def test_schema_and_comparison() -> None:
    schema = runner.schema_document()
    required = set(schema["required"])
    check({"schema", "run_id", "candidate", "environment", "fixture", "sides", "cross_side", "required_claims", "result", "cleanup"} <= required, "schema must require the evidence roots")
    check(schema["properties"]["result"]["enum"] == ["pending", "dry_run", "pass", "partial", "failed"], "schema result enum must include dry-run")
    mac_schema = schema["properties"]["sides"]["properties"]["mac"]["properties"]
    check("framed bridge transport" in mac_schema["frame_transport_ack"]["description"], "schema must distinguish bridge transport ACK from display evidence")

    known = lambda value: runner.known(value, ["test"])
    missing = runner.compare_field("session_id", {"host": {"session_id": known("s")}, "android": {}, "mac": {"session_id": known("s")}})
    check(missing["status"] == "unknown" and missing["missing_sides"] == ["android"], "missing side must remain unknown")
    mismatch = runner.compare_field("session_id", {"host": {"session_id": known("one")}, "android": {"session_id": known("two")}, "mac": {"session_id": known("one")}})
    check(mismatch["status"] == "failed", "different side values must fail")
    equal = runner.compare_field("session_id", {"host": {"session_id": known("same")}, "android": {"session_id": known("same")}, "mac": {"session_id": known("same")}})
    check(equal["status"] == "proved", "equal complete side values must be proved")
    check(runner.integer_pair([391.0, 845]) == [391, 845], "integer-valued protocol floats must normalize")
    check(runner.integer_pair([391.5, 845]) is None, "fractional dimensions must not be rounded")
    check(runner.combined_status({"one": runner.unknown("missing"), "two": {"status": "proved"}}) == "unknown", "dependent unknown must remain unknown")
    check(runner.combined_status({"one": runner.failed("different"), "two": {"status": "proved"}}) == "failed", "dependent failure must remain failed")


def test_result_classification() -> None:
    check(runner.classify_result(None, {"claim": runner.unknown("not emitted")}) == "partial", "unknown claim must not become pass")
    check(runner.classify_result(None, {"claim": runner.failed("different")}) == "failed", "failed claim must fail result")
    check(runner.classify_result({"code": "FIRST"}, {"claim": {"status": "proved"}}) == "failed", "first failure must dominate claims")
    check(runner.classify_result(None, {"claim": {"status": "proved"}}) == "pass", "complete proved claims may pass")


def test_write_exclusive() -> None:
    with tempfile.TemporaryDirectory(prefix="m1-dual-write-") as raw:
        root = pathlib.Path(raw)
        text_path = root / "nested" / "text.txt"
        binary_path = root / "nested" / "bytes.bin"
        runner.write_exclusive(text_path, "中文\n")
        runner.write_exclusive(binary_path, b"\x00\xff")
        check(text_path.read_text(encoding="utf-8") == "中文\n", "text evidence must preserve UTF-8")
        check(binary_path.read_bytes() == b"\x00\xff", "binary evidence must preserve bytes")


def test_schema_dry_run_switch() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = runner.main(["--print-schema", "--dry-run"])
    check(code == 2, "schema and dry-run must reject the ambiguous invocation")
    check("mutually exclusive" in stderr.getvalue(), "ambiguous invocation must explain the rejection")


def test_dry_run() -> None:
    with tempfile.TemporaryDirectory(prefix="m1-dual-dry-run-") as raw:
        evidence_dir = pathlib.Path(raw) / "evidence"
        args = runner.build_arguments(
            arguments(
                "--dry-run",
                "--run-id",
                "dry-run-test",
                "--evidence-dir",
                str(evidence_dir),
            )
        )
        combined = runner.CombinedRunner(args)
        code = combined.dry_run()
        check(code == 0, "dry-run must exit zero after validating its invocation")
        evidence = json.loads((evidence_dir / "evidence.json").read_text(encoding="utf-8"))
        check(evidence["result"] == "dry_run", "dry-run evidence must use dry_run result")
        check(evidence["result"] != "pass" and evidence["unknown"], "dry-run must expose unknown runtime claims")


def test_instrumentation_summary_validation() -> None:
    def output(test_count: int, summary: str | None, class_name: str = runner.DEFAULT_ANDROID_CLASS, failure: bool = False) -> bytes:
        lines: list[str] = []
        tests = ["testImeCompositionCancel"]
        if test_count > 1:
            tests.append("testRealNetworkControlAndFrames")
        for current, test_name in enumerate(tests, 1):
            for status in (1, -2 if failure else 0):
                lines.extend(
                    [
                        f"INSTRUMENTATION_STATUS: class={class_name}",
                        f"INSTRUMENTATION_STATUS: current={current}",
                        f"INSTRUMENTATION_STATUS: numtests={test_count}",
                        f"INSTRUMENTATION_STATUS: test={test_name}",
                        f"INSTRUMENTATION_STATUS_CODE: {status}",
                    ]
                )
        lines.extend(["INSTRUMENTATION_RESULT: stream="])
        if failure:
            lines.append("FAILURES!!!")
        if summary is not None:
            lines.append(summary)
        lines.append("INSTRUMENTATION_CODE: -1")
        return ("\n".join(lines) + "\n").encode()

    one_test = runner.parse_instrumentation_result(output(1, "OK (1 test)"))
    check(one_test.status == "failed", "one-test summary must fail the fixed two-test contract")
    check(one_test.error.code == "TEST_COUNT_MISMATCH", "one-test failure must preserve a structured count error")

    two_tests = runner.parse_instrumentation_result(output(2, "OK (2 tests)"))
    check(two_tests.status == "passed", "the complete two-test summary must pass")
    check(two_tests.observed.test_count == 2, "the passed result must retain the observed test count")

    wrong_completion_code = runner.parse_instrumentation_result(output(2, "OK (2 tests)").replace(b"INSTRUMENTATION_CODE: -1", b"INSTRUMENTATION_CODE: 0"))
    check(wrong_completion_code.status == "failed", "an unexpected instrumentation completion code must fail")
    check(wrong_completion_code.error.code == "INSTRUMENTATION_CODE_INVALID", "unexpected completion code must preserve a structured error")

    failed_output = runner.parse_instrumentation_result(output(2, "FAILURES!!!", failure=True))
    check(failed_output.status == "failed", "instrumentation failure output must fail")
    check(failed_output.error.code == "INSTRUMENTATION_FAILURE", "instrumentation failure must preserve its structured error")

    missing_summary = runner.parse_instrumentation_result(output(2, None))
    check(missing_summary.status == "failed", "missing summary must fail closed")
    check(missing_summary.error.code == "SUMMARY_MISSING", "missing summary must preserve a structured error")

    wrong_entry = runner.parse_instrumentation_result(output(2, "OK (2 tests)", "com.example.Other"))
    check(wrong_entry.status == "failed", "an unexpected instrumentation class must fail")
    check(wrong_entry.error.code == "TEST_ENTRY_MISMATCH", "unexpected class must preserve a structured entry error")

    check(
        "parse_instrumentation_result(output)" in pathlib.Path(runner.__file__).read_text(encoding="utf-8"),
        "runner must consume the shared parser",
    )
    malformed = runner.parse_instrumentation_result(output(2, "OK (2 tests)").replace(
        b"INSTRUMENTATION_STATUS_CODE: 0",
        b"INSTRUMENTATION_STATUS_CODE: -2",
        1,
    ))
    check(malformed.error.code == "INSTRUMENTATION_FAILURE", "runner must expose shared typed failure")


def main() -> int:
    tests = (
        test_argument_validation,
        test_environment_identity,
        test_adb_reverse_mapping,
        test_typed_evidence,
        test_acknowledged_active_frames,
        test_transport_frames_are_separate_from_display_evidence,
        test_combined_side_evidence_does_not_promote_bridge_ack_to_display,
        test_strict_protocol_integers_and_epochs,
        test_candidate_identity_drift_fails_closed,
        test_device_pairing_uses_resolved_adb,
        test_git_probe_failure_is_distinct,
        test_schema_and_comparison,
        test_result_classification,
        test_write_exclusive,
        test_schema_dry_run_switch,
        test_dry_run,
        test_instrumentation_summary_validation,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"M1 dual runner focused tests: {len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
