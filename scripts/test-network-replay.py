#!/usr/bin/env python3
"""Focused regression for the network replay instrumentation result parser."""

from __future__ import annotations

import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent
PARSER_PATH = ROOT / "instrumentation_result.py"
spec = importlib.util.spec_from_file_location("instrumentation_result_tests", PARSER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load {PARSER_PATH}")
parser = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = parser
spec.loader.exec_module(parser)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def successful_output() -> bytes:
    return (
        b"INSTRUMENTATION_STATUS: class=com.agentbrowser.probe.NetworkDeviceTest\n"
        b"INSTRUMENTATION_STATUS: current=1\n"
        b"INSTRUMENTATION_STATUS: id=InstrumentationTestRunner\n"
        b"INSTRUMENTATION_STATUS: numtests=2\n"
        b"INSTRUMENTATION_STATUS: stream=\n"
        b"com.agentbrowser.probe.NetworkDeviceTest:\n"
        b"INSTRUMENTATION_STATUS: test=testImeCompositionCancel\n"
        b"INSTRUMENTATION_STATUS_CODE: 1\n"
        b"INSTRUMENTATION_STATUS: class=com.agentbrowser.probe.NetworkDeviceTest\n"
        b"INSTRUMENTATION_STATUS: current=1\n"
        b"INSTRUMENTATION_STATUS: id=InstrumentationTestRunner\n"
        b"INSTRUMENTATION_STATUS: numtests=2\n"
        b"INSTRUMENTATION_STATUS: stream=.\n"
        b"INSTRUMENTATION_STATUS: test=testImeCompositionCancel\n"
        b"INSTRUMENTATION_STATUS_CODE: 0\n"
        b"INSTRUMENTATION_STATUS: class=com.agentbrowser.probe.NetworkDeviceTest\n"
        b"INSTRUMENTATION_STATUS: current=2\n"
        b"INSTRUMENTATION_STATUS: id=InstrumentationTestRunner\n"
        b"INSTRUMENTATION_STATUS: numtests=2\n"
        b"INSTRUMENTATION_STATUS: stream=\n"
        b"INSTRUMENTATION_STATUS: test=testRealNetworkControlAndFrames\n"
        b"INSTRUMENTATION_STATUS_CODE: 1\n"
        b"INSTRUMENTATION_STATUS: class=com.agentbrowser.probe.NetworkDeviceTest\n"
        b"INSTRUMENTATION_STATUS: current=2\n"
        b"INSTRUMENTATION_STATUS: id=InstrumentationTestRunner\n"
        b"INSTRUMENTATION_STATUS: numtests=2\n"
        b"INSTRUMENTATION_STATUS: stream=.\n"
        b"INSTRUMENTATION_STATUS: test=testRealNetworkControlAndFrames\n"
        b"INSTRUMENTATION_STATUS_CODE: 0\n"
        b"INSTRUMENTATION_RESULT: stream=\n"
        b"Test results for InstrumentationTestRunner=..\n"
        b"Time: 22.899\n\n"
        b"OK (2 tests)\n\n"
        b"INSTRUMENTATION_CODE: -1\n"
    )


def main() -> int:
    output = successful_output()
    result = parser.parse_instrumentation_result(output)
    check(result.ok and result.status == "passed", "current two-test instrumentation output must pass")
    check(result.observed.test_count == 2, "typed result must retain the observed test count")
    check(result.observed.status_codes == (1, 0, 1, 0), "typed result must retain start and completion status codes")
    check(
        not parser.parse_instrumentation_result(output.replace(b"OK (2 tests)", b"OK (1 test)")).ok,
        "stale one-test summary must fail",
    )
    check(
        not parser.parse_instrumentation_result(output.replace(b"OK (2 tests)", b"OK (2 test)")).ok,
        "malformed pluralization must fail",
    )
    check(
        not parser.parse_instrumentation_result(output.replace(b"INSTRUMENTATION_CODE: -1", b"INSTRUMENTATION_CODE: 0")).ok,
        "non-success instrumentation code must fail",
    )
    check(
        not parser.parse_instrumentation_result(output.replace(b"INSTRUMENTATION_CODE: -1\n", b"")).ok,
        "missing completion code must fail",
    )
    check(
        not parser.parse_instrumentation_result(
            b"diagnostic: OK (2 tests)\nINSTRUMENTATION_CODE: -1\n"
        ).ok,
        "arbitrary text containing the summary must fail",
    )
    check(
        parser.parse_instrumentation_result(output + b"OK (2 tests)\n").error.code == "SUMMARY_MULTIPLE",
        "duplicate summaries must fail with a typed error",
    )
    check(
        parser.parse_instrumentation_result(output.replace(
            b"INSTRUMENTATION_RESULT: stream=\n",
            b"INSTRUMENTATION_RESULT: stream=\nINSTRUMENTATION_RESULT: stream=\n",
        )).error.code == "RESULT_MARKER_MULTIPLE",
        "duplicate result markers must fail with a typed error",
    )
    check(
        parser.parse_instrumentation_result(output.replace(
            b"INSTRUMENTATION_RESULT: stream=\n",
            b"",
        )).error.code == "RESULT_MARKER_MISSING",
        "missing result marker must fail with a typed error",
    )
    check(
        parser.parse_instrumentation_result(output.replace(
            b"INSTRUMENTATION_CODE: -1\n",
            b"",
        )).error.code == "INSTRUMENTATION_CODE_MISSING",
        "missing instrumentation code must fail with a typed error",
    )
    check(
        parser.parse_instrumentation_result(output.replace(
            b"INSTRUMENTATION_CODE: -1\n",
            b"INSTRUMENTATION_CODE: -1\nINSTRUMENTATION_CODE: -1\n",
        )).error.code == "INSTRUMENTATION_CODE_MULTIPLE",
        "duplicate instrumentation code must fail with a typed error",
    )
    check(
        parser.parse_instrumentation_result(output.replace(
            b"INSTRUMENTATION_RESULT: stream=\n"
            b"Test results for InstrumentationTestRunner=..\n"
            b"Time: 22.899\n\n"
            b"OK (2 tests)\n\n",
            b"OK (2 tests)\n"
            b"INSTRUMENTATION_RESULT: stream=\n",
        )).error.code == "MARKER_ORDER_INVALID",
        "out-of-order result and summary markers must fail with a typed error",
    )
    check(
        parser.parse_instrumentation_result(output.replace(
            b"INSTRUMENTATION_STATUS_CODE: 0\n",
            b"INSTRUMENTATION_STATUS_CODE: -2\n",
            1,
        )).error.code == "INSTRUMENTATION_FAILURE",
        "negative status code must fail with a typed error",
    )
    check(
        parser.parse_instrumentation_result(output.replace(
            b"INSTRUMENTATION_STATUS_CODE: 1\n",
            b"INSTRUMENTATION_STATUS_CODE: 2\n",
            1,
        )).error.code == "TEST_COUNT_MISMATCH",
        "unexpected positive status code must fail with a typed error",
    )
    check(
        parser.parse_instrumentation_result(output.replace(
            b"INSTRUMENTATION_RESULT: stream=\n",
            b"INSTRUMENTATION_RESULT: stream=\nFAILURES!!!\n",
        )).error.code == "INSTRUMENTATION_FAILURE",
        "FAILURES marker must fail with a typed error",
    )
    check(
        parser.parse_instrumentation_result(output.replace(
            b"com.agentbrowser.probe.NetworkDeviceTest",
            b"com.example.Other",
            1,
        )).error.code == "TEST_ENTRY_MISMATCH",
        "wrong instrumentation entry must fail with a typed error",
    )
    check(
        parser.parse_instrumentation_result(output.replace(
            b"INSTRUMENTATION_STATUS: numtests=2",
            b"INSTRUMENTATION_STATUS: numtests=1",
            1,
        )).error.code == "TEST_COUNT_MISMATCH",
        "wrong instrumentation count must fail with a typed error",
    )
    check(
        not parser.parse_instrumentation_result(b"OK (2 tests)\nINSTRUMENTATION_CODE: -1\n\xff").ok,
        "invalid instrumentation output encoding must fail",
    )
    check(
        parser.parse_instrumentation_result("not bytes").error.code == "OUTPUT_TYPE_INVALID",
        "non-byte instrumentation output must fail with a typed error",
    )
    check(
        "parse_instrumentation_result(result.stdout)" in (ROOT / "network-replay.py").read_text(encoding="utf-8"),
        "replay must use the strict parser and fixed test count",
    )
    check(
        "EXPECTED_TEST_CLASS" in (ROOT / "network-replay.py").read_text(encoding="utf-8"),
        "replay instrumentation entry must use the parser contract",
    )
    replay_source = (ROOT / "network-replay.py").read_text(encoding="utf-8")
    pairing_remove = replay_source.index('device-pairing.py", "remove"')
    fixture_quit = replay_source.index('fixture.stdin.write(b"quit\\n")')
    fixture_wait = replay_source.index("fixture.wait(timeout=10)")
    fixture_exit_check = replay_source.index('raise RuntimeError(f"Fixture failed: {fixture.returncode}")')
    check(
        fixture_quit < fixture_wait < fixture_exit_check < pairing_remove,
        "fixture CloseSession/Closed completion must precede pairing removal",
    )
    print("Network replay parser and teardown focused tests: 24 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
