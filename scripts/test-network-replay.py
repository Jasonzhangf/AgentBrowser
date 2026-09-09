#!/usr/bin/env python3
"""Focused regression for the network replay instrumentation result parser."""

from __future__ import annotations

import ast
import pathlib


SCRIPT = pathlib.Path(__file__).with_name("network-replay.py")
SOURCE = SCRIPT.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(SCRIPT))
HELPER = next(
    node
    for node in TREE.body
    if isinstance(node, ast.FunctionDef) and node.name == "instrumentation_passed"
)
STATIC_MODULE = ast.Module(
    body=[node for node in TREE.body if isinstance(node, (ast.Import, ast.ImportFrom))] + [HELPER],
    type_ignores=[],
)
NAMESPACE: dict[str, object] = {}
exec(compile(ast.fix_missing_locations(STATIC_MODULE), str(SCRIPT), "exec"), NAMESPACE)
instrumentation_passed = NAMESPACE["instrumentation_passed"]


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def successful_output() -> bytes:
    return (
        b"INSTRUMENTATION_STATUS: numtests=2\n"
        b"INSTRUMENTATION_STATUS_CODE: 0\n"
        b"INSTRUMENTATION_RESULT: stream=\n"
        b"Test results for InstrumentationTestRunner=..\n"
        b"Time: 22.899\n\n"
        b"OK (2 tests)\n\n"
        b"INSTRUMENTATION_CODE: -1\n"
    )


def main() -> int:
    output = successful_output()
    check(instrumentation_passed(output, 2), "current two-test instrumentation output must pass")
    check(
        not instrumentation_passed(output.replace(b"OK (2 tests)", b"OK (1 test)"), 2),
        "stale one-test summary must fail",
    )
    check(
        not instrumentation_passed(output.replace(b"OK (2 tests)", b"OK (2 test)"), 2),
        "malformed pluralization must fail",
    )
    check(
        not instrumentation_passed(output.replace(b"INSTRUMENTATION_CODE: -1", b"INSTRUMENTATION_CODE: 0"), 2),
        "non-success instrumentation code must fail",
    )
    check(
        not instrumentation_passed(output.replace(b"INSTRUMENTATION_CODE: -1\n", b""), 2),
        "missing completion code must fail",
    )
    check(
        not instrumentation_passed(
            b"diagnostic: OK (2 tests)\nINSTRUMENTATION_CODE: -1\n", 2
        ),
        "arbitrary text containing the summary must fail",
    )
    check(
        not instrumentation_passed(output + b"OK (2 tests)\n", 2),
        "duplicate summaries must fail",
    )
    check(
        not instrumentation_passed(
            b"OK (2 tests)\nINSTRUMENTATION_CODE: -1\n\xff", 2
        ),
        "invalid instrumentation output encoding must fail",
    )
    check(
        "if not instrumentation_passed(result.stdout, NETWORK_TEST_COUNT):" in SOURCE,
        "replay must use the strict parser and fixed test count",
    )
    print("Network replay parser focused tests: 8 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
