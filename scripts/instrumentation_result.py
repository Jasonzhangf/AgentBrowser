"""Strict parser for the Android NetworkDeviceTest instrumentation contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Optional


EXPECTED_TEST_CLASS = "com.agentbrowser.probe.NetworkDeviceTest"
EXPECTED_TEST_COUNT = 2
EXPECTED_INSTRUMENTATION_CODE = -1

_CLASS = re.compile(r"^INSTRUMENTATION_STATUS: class=([^\r\n]+)\r?$")
_NUMTESTS = re.compile(r"^INSTRUMENTATION_STATUS: numtests=([0-9]+)\r?$")
_TEST = re.compile(r"^INSTRUMENTATION_STATUS: test=([^\r\n]+)\r?$")
_STATUS_CODE = re.compile(r"^INSTRUMENTATION_STATUS_CODE: (-?[0-9]+)\r?$")
_RESULT = re.compile(r"^INSTRUMENTATION_RESULT: stream=.*\r?$")
_SUMMARY = re.compile(r"^OK \(([0-9]+) (test|tests)\)\r?$")
_INSTRUMENTATION_CODE = re.compile(r"^INSTRUMENTATION_CODE: (-?[0-9]+)\r?$")


@dataclass(frozen=True)
class InstrumentationError:
    code: str
    message: str


@dataclass(frozen=True)
class InstrumentationExpectation:
    test_class: str
    test_count: int
    summary: str
    instrumentation_code: int


@dataclass(frozen=True)
class InstrumentationObservation:
    classes: tuple[str, ...]
    numtests: tuple[int, ...]
    tests: tuple[str, ...]
    test_count: Optional[int]
    summary: Optional[str]
    summary_markers: int
    status_codes: tuple[int, ...]
    completed_tests: int
    result_markers: int
    instrumentation_codes: tuple[int, ...]
    failure_markers: tuple[str, ...]


@dataclass(frozen=True)
class InstrumentationResult:
    status: str
    ok: bool
    error: Optional[InstrumentationError]
    expected: InstrumentationExpectation
    observed: InstrumentationObservation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _expected() -> InstrumentationExpectation:
    return InstrumentationExpectation(
        test_class=EXPECTED_TEST_CLASS,
        test_count=EXPECTED_TEST_COUNT,
        summary=f"OK ({EXPECTED_TEST_COUNT} tests)",
        instrumentation_code=EXPECTED_INSTRUMENTATION_CODE,
    )


def _observation(lines: list[str]) -> InstrumentationObservation:
    class_values: list[str] = []
    numtests_values: list[int] = []
    test_values: list[str] = []
    status_codes: list[int] = []
    summary: Optional[str] = None
    summary_count: Optional[int] = None
    summary_markers = 0
    result_markers = 0
    instrumentation_codes: list[int] = []
    failure_markers: list[str] = []

    for line in lines:
        match = _CLASS.fullmatch(line)
        if match:
            class_values.append(match.group(1))
            continue
        match = _NUMTESTS.fullmatch(line)
        if match:
            numtests_values.append(int(match.group(1)))
            continue
        match = _TEST.fullmatch(line)
        if match:
            test_values.append(match.group(1))
            continue
        match = _STATUS_CODE.fullmatch(line)
        if match:
            status_codes.append(int(match.group(1)))
            continue
        if _RESULT.fullmatch(line):
            result_markers += 1
            continue
        match = _SUMMARY.fullmatch(line)
        if match:
            summary_markers += 1
            summary_count = int(match.group(1))
            summary = match.group(0).rstrip("\r")
            continue
        match = _INSTRUMENTATION_CODE.fullmatch(line)
        if match:
            instrumentation_codes.append(int(match.group(1)))
            continue
        if line == "FAILURES!!!":
            failure_markers.append(line)

    return InstrumentationObservation(
        classes=tuple(class_values),
        numtests=tuple(numtests_values),
        tests=tuple(test_values),
        test_count=summary_count,
        summary=summary,
        summary_markers=summary_markers,
        status_codes=tuple(status_codes),
        completed_tests=status_codes.count(0),
        result_markers=result_markers,
        instrumentation_codes=tuple(instrumentation_codes),
        failure_markers=tuple(failure_markers),
    )


def _failed(
    expected: InstrumentationExpectation,
    observed: InstrumentationObservation,
    code: str,
    message: str,
) -> InstrumentationResult:
    return InstrumentationResult(
        status="failed",
        ok=False,
        error=InstrumentationError(code=code, message=message),
        expected=expected,
        observed=observed,
    )


def parse_instrumentation_result(output: bytes) -> InstrumentationResult:
    """Parse one complete NetworkDeviceTest result, failing closed."""

    expected = _expected()
    if not isinstance(output, bytes):
        return _failed(expected, _observation([]), "OUTPUT_TYPE_INVALID", "instrumentation output must be bytes")
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        return _failed(expected, _observation([]), "OUTPUT_ENCODING_INVALID", "instrumentation output is not valid UTF-8")

    lines = text.splitlines()
    observed = _observation(lines)
    if observed.failure_markers:
        return _failed(expected, observed, "INSTRUMENTATION_FAILURE", "instrumentation reported a failing test")
    if any(code < 0 for code in observed.status_codes):
        return _failed(expected, observed, "INSTRUMENTATION_FAILURE", "instrumentation reported a negative status code")
    if observed.result_markers != 1:
        code = "RESULT_MARKER_MISSING" if observed.result_markers == 0 else "RESULT_MARKER_MULTIPLE"
        return _failed(expected, observed, code, "instrumentation output must contain exactly one result marker")
    if observed.summary_markers != 1:
        code = "SUMMARY_MISSING" if observed.summary_markers == 0 else "SUMMARY_MULTIPLE"
        return _failed(expected, observed, code, "instrumentation output must contain exactly one success summary")
    if len(observed.instrumentation_codes) != 1:
        code = "INSTRUMENTATION_CODE_MISSING" if not observed.instrumentation_codes else "INSTRUMENTATION_CODE_MULTIPLE"
        return _failed(expected, observed, code, "instrumentation output must contain exactly one instrumentation code")
    if observed.test_count is None:
        return _failed(expected, observed, "SUMMARY_MISSING", "instrumentation output has no exact success summary")
    if observed.instrumentation_codes[0] != expected.instrumentation_code:
        return _failed(expected, observed, "INSTRUMENTATION_CODE_INVALID", "instrumentation output did not report the expected completion code")

    if (
        len(observed.classes) != expected.test_count * 2
        or len(observed.numtests) != expected.test_count * 2
        or len(observed.tests) != expected.test_count * 2
        or len(observed.status_codes) != expected.test_count * 2
        or any(value != expected.test_count for value in observed.numtests)
        or len(set(observed.tests)) != expected.test_count
        or observed.tests[::2] != observed.tests[1::2]
        or observed.status_codes != tuple([1, 0] * expected.test_count)
    ):
        return _failed(expected, observed, "TEST_COUNT_MISMATCH", "instrumentation numtests does not match the expected test count")
    if observed.test_count != expected.test_count:
        return _failed(expected, observed, "TEST_COUNT_MISMATCH", "instrumentation test count does not match the expected test count")
    if any(value != expected.test_class for value in observed.classes):
        return _failed(expected, observed, "TEST_ENTRY_MISMATCH", "instrumentation output does not identify the expected NetworkDeviceTest entry")
    if observed.summary != expected.summary:
        return _failed(expected, observed, "SUMMARY_INVALID", "instrumentation success summary is not the expected summary")
    if observed.completed_tests != expected.test_count:
        return _failed(expected, observed, "TEST_COMPLETION_MISSING", "instrumentation did not report completion for every expected test")

    marker_kinds: list[str] = []
    for line in lines:
        if _CLASS.fullmatch(line):
            marker_kinds.append("class")
        elif _NUMTESTS.fullmatch(line):
            marker_kinds.append("numtests")
        elif _TEST.fullmatch(line):
            marker_kinds.append("test")
        elif _STATUS_CODE.fullmatch(line):
            marker_kinds.append("status_code")
        elif _RESULT.fullmatch(line):
            marker_kinds.append("result")
        elif _SUMMARY.fullmatch(line):
            marker_kinds.append("summary")
        elif _INSTRUMENTATION_CODE.fullmatch(line):
            marker_kinds.append("instrumentation_code")
    expected_prefix = ["class", "numtests", "test", "status_code"] * (expected.test_count * 2)
    if marker_kinds != expected_prefix + ["result", "summary", "instrumentation_code"]:
        return _failed(expected, observed, "MARKER_ORDER_INVALID", "instrumentation markers are out of order")

    return InstrumentationResult(
        status="passed",
        ok=True,
        error=None,
        expected=expected,
        observed=observed,
    )
