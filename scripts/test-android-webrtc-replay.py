#!/usr/bin/env python3
"""Focused regression for the Android WebRTC replay instrumentation contract."""

from __future__ import annotations

import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent
SCRIPT_PATH = ROOT / "android-webrtc-replay.py"
spec = importlib.util.spec_from_file_location("android_webrtc_replay_tests", SCRIPT_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load {SCRIPT_PATH}")
replay = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = replay
spec.loader.exec_module(replay)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def instrumentation_output(summary: bytes = b"OK (3 tests)", *, result_marker: bool = True) -> bytes:
    lines: list[bytes] = []
    for index, (test_class, test_name) in enumerate(replay.WEBRTC_TEST_ENTRIES, start=1):
        for status, stream in ((1, b""), (0, b".")):
            lines.extend([
                f"INSTRUMENTATION_STATUS: class={test_class}\n".encode(),
                f"INSTRUMENTATION_STATUS: current={index}\n".encode(),
                b"INSTRUMENTATION_STATUS: id=InstrumentationTestRunner\n",
                b"INSTRUMENTATION_STATUS: numtests=3\n",
                b"INSTRUMENTATION_STATUS: stream=" + stream + b"\n",
                f"INSTRUMENTATION_STATUS: test={test_name}\n".encode(),
                f"INSTRUMENTATION_STATUS_CODE: {status}\n".encode(),
            ])
    if result_marker:
        lines.extend([
            b"INSTRUMENTATION_RESULT: stream=\n",
            b"Test results for InstrumentationTestRunner=...\n",
            b"Time: 32.132\n\n",
        ])
    lines.extend([summary + b"\n\n", b"INSTRUMENTATION_CODE: -1\n"])
    return b"".join(lines)


def main() -> int:
    passed = replay.validate_instrumentation_output(instrumentation_output())
    check(passed.ok and passed.status == "passed", "current three-test output must pass")
    check(passed.observed.test_count == 3, "typed result must retain the observed three-test count")
    check(
        passed.observed.tests == (
            "testTypedWebRtcReachesNativeMedia",
            "testTypedWebRtcReachesNativeMedia",
            "testImeCompositionCancel",
            "testImeCompositionCancel",
            "testRealNetworkControlAndFrames",
            "testRealNetworkControlAndFrames",
        ),
        "typed result must retain every fixed WebRTC and network test name",
    )
    two_tests = instrumentation_output().replace(
        b"INSTRUMENTATION_STATUS: numtests=3", b"INSTRUMENTATION_STATUS: numtests=2"
    ).replace(b"OK (3 tests)", b"OK (2 tests)")
    two_test_result = replay.validate_instrumentation_output(two_tests)
    check(not two_test_result.ok and two_test_result.error.code == "TEST_COUNT_MISMATCH", "stale two-test output must fail")
    wrong_summary = replay.validate_instrumentation_output(instrumentation_output(b"OK (3 test)"))
    check(not wrong_summary.ok and wrong_summary.error.code == "SUMMARY_INVALID", "wrong summary must fail")
    missing_marker = replay.validate_instrumentation_output(instrumentation_output(result_marker=False))
    check(not missing_marker.ok and missing_marker.error.code == "RESULT_MARKER_MISSING", "missing result marker must fail")
    wrong_code = replay.validate_instrumentation_output(
        instrumentation_output().replace(b"INSTRUMENTATION_CODE: -1", b"INSTRUMENTATION_CODE: 0")
    )
    check(not wrong_code.ok and wrong_code.error.code == "INSTRUMENTATION_CODE_INVALID", "wrong completion code must fail")
    wrong_entry = replay.validate_instrumentation_output(
        instrumentation_output().replace(b"testTypedWebRtcReachesNativeMedia", b"testUnexpected")
    )
    check(not wrong_entry.ok and wrong_entry.error.code == "TEST_ENTRY_MISMATCH", "wrong test name must fail")
    print("Android WebRTC replay parser focused tests: 6 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
