# SPDX-License-Identifier: Apache-2.0
"""The two kinds of test a repair meets, and how each is run in ``g6.core.sandbox``.

A **shown** test is one ``assert`` statement. The model that proposes a patch sees it, and its failures are fed back,
so it can only steer the repair. It runs whole beside the code in a bounded child process.

A **held-back** test compares one value with a literal: ``assert <expression> == <literal>`` (either side). The model
never sees it. Only its expression goes into the child, which returns the value it computed; the literal stays in
G6's process, where the two are compared (the value read back with ``ast.literal_eval``, never run). So code under test
can see which calls it is asked to make, but never their expected answers. The runner shares the patched code's
process: a patch that alters the runner can forge a pass. A test of any other form is refused as held-back.
"""
from __future__ import annotations

import ast
import json
import secrets
from dataclasses import dataclass
from typing import Any

from g6.code._types import CodeRefused
from g6.core.sandbox import run_script

BAD_TEST = "G6_E_HEAL_BAD_TEST"
SOLUTION = "solution.py"
CASES = "cases.json"
MAX_REPR = 2000
MAX_TEST_BYTES = 16_000
MAX_TEST_TOTAL_BYTES = 100_000
PROCESS_BLIND_SPOT = ("the checks ran in the patched code's own process; "
                      "a patch that alters the runner can forge a pass")


def check_bounds(*groups: tuple[str, ...]) -> None:
    """Bound each assertion and the combined shown/held-back input before parsing."""
    total = 0
    for tests in groups:
        for text in tests:
            size = len(text.encode("utf-8"))
            total += size
            if size > MAX_TEST_BYTES or total > MAX_TEST_TOTAL_BYTES:
                raise CodeRefused(BAD_TEST, f"each test is at most {MAX_TEST_BYTES} bytes and all tests together "
                                            f"at most {MAX_TEST_TOTAL_BYTES} bytes")


@dataclass(frozen=True)
class HeldBack:
    """A held-back test: the expression the child computes, and the literal it must equal (kept here)."""

    index: int
    expression: str
    expected: Any
    shape: str  # ast.dump of the expression, to find a held-back test that is also shown


def shown(tests: tuple[str, ...]) -> list[str]:
    """Each shown test as source, after checking it is one ``assert`` statement."""
    check_bounds(tests)
    out = []
    for index, text in enumerate(tests, 1):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError) as exc:
            raise CodeRefused(BAD_TEST, f"shown test {index} does not parse: {exc}") from None
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assert):
            raise CodeRefused(BAD_TEST, f"shown test {index} is not one assert statement")
        out.append(text.strip())
    return out


def _literal(node: ast.expr) -> tuple[bool, Any]:
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False, None


def held_back(tests: tuple[str, ...]) -> list[HeldBack]:
    """Each held-back test split into the expression to compute and the literal to compare it with."""
    check_bounds(tests)
    out = []
    for index, text in enumerate(tests, 1):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError) as exc:
            raise CodeRefused(BAD_TEST, f"held-back test {index} does not parse: {exc}") from None
        node = tree.body[0] if len(tree.body) == 1 and isinstance(tree.body[0], ast.Assert) else None
        test = node.test if node is not None else None
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
            raise CodeRefused(BAD_TEST, f"held-back test {index} must read assert <expression> == <literal>, so G6 "
                                        "can compare the value outside the code under test")
        left, right = test.left, test.comparators[0]
        right_ok, right_value = _literal(right)
        left_ok, left_value = _literal(left)
        if right_ok and not left_ok:
            expression, expected = left, right_value
        elif left_ok and not right_ok:
            expression, expected = right, left_value
        else:
            raise CodeRefused(BAD_TEST, f"held-back test {index} must compare one expression with one literal")
        out.append(HeldBack(index, ast.unparse(expression), expected, ast.dump(expression)))
    return out


def shapes_shown(tests: list[str]) -> set[str]:
    """The expressions the shown tests compare, to find a held-back test that is also shown."""
    found = set()
    for text in tests:
        test = ast.parse(text).body[0].test  # type: ignore[attr-defined]
        if isinstance(test, ast.Compare):
            found |= {ast.dump(test.left), *(ast.dump(c) for c in test.comparators)}
        found.add(ast.dump(test))
    return found


def expected_literals(tests: list[str]) -> list[Any]:
    """The literals the shown tests expect, to notice a patch that spells them out."""
    found = []
    for text in tests:
        test = ast.parse(text).body[0].test  # type: ignore[attr-defined]
        if isinstance(test, ast.Compare):
            for side in (test.left, *test.comparators):
                ok, value = _literal(side)
                if ok:
                    found.append(value)
    return found


_LOAD = '''
import json, sys
MARK = {mark!r}
with open("cases.json", encoding="utf-8") as handle:
    cases = json.load(handle)
namespace = {{"__name__": "solution"}}
try:
    with open("solution.py", encoding="utf-8") as handle:
        exec(compile(handle.read(), "solution.py", "exec"), namespace)
except BaseException as exc:
    print(MARK + json.dumps({{"loaded": False, "error": (type(exc).__name__ + ": " + str(exc))[:300]}}))
    raise SystemExit(3)
'''
_SHOWN = _LOAD + '''
results = []
for index, source in enumerate(cases):
    try:
        exec(compile(source, "<shown test %d>" % (index + 1), "exec"), namespace)
        results.append([index, "passed", ""])
    except BaseException as exc:
        results.append([index, "failed", (type(exc).__name__ + ": " + str(exc))[:300]])
print(MARK + json.dumps({{"loaded": True, "results": results}}))
'''
_HELD = _LOAD + '''
def unsupported(value):
    kind = type(value)
    if any(kind is literal for literal in (type(None), bool, int, float, str, bytes)):
        return ""
    if kind is list or kind is tuple or kind is set:
        for item in value:
            bad = unsupported(item)
            if bad:
                return bad
        return ""
    if kind is dict:
        for key, item in value.items():
            bad = unsupported(key) or unsupported(item)
            if bad:
                return bad
        return ""
    return kind.__name__
results = []
for index, source in enumerate(cases):
    try:
        value = eval(compile(source, "<case %d>" % (index + 1), "eval"), namespace)
        bad = unsupported(value)
        if bad:
            results.append([index, "unsupported", bad])
        else:
            results.append([index, "value", repr(value)[:{limit}]])
    except BaseException as exc:
        results.append([index, "raised", type(exc).__name__])
print(MARK + json.dumps({{"loaded": True, "results": results}}))
'''


def _run(template: str, code: str, cases: list[str], timeout_s: float) -> dict[str, Any]:
    mark = f"G6-CASES-{secrets.token_hex(8)}:"
    ran = run_script(template.format(mark=mark, limit=MAX_REPR), {SOLUTION: code, CASES: json.dumps(cases)},
                     timeout_s=timeout_s, mem_mb=512)
    report = None
    for line in reversed(ran.stdout.splitlines()):
        if line.startswith(mark):
            try:
                report = json.loads(line[len(mark):])
            except json.JSONDecodeError:
                report = None
            break
    limits = {"status": ran.status, "seconds": round(ran.seconds, 3), **ran.limits}
    if not isinstance(report, dict):
        why = "it timed out" if ran.status == "timeout" else "it reported nothing (it may have exited early)"
        return {"loaded": False, "error": why, "results": [], "run": limits}
    return {"loaded": bool(report.get("loaded")), "error": str(report.get("error", ""))[:300],
            "results": report.get("results") if isinstance(report.get("results"), list) else [], "run": limits}


def run_shown(code: str, tests: list[str], timeout_s: float) -> dict[str, Any]:
    """Each shown test's outcome on ``code``, from a bounded child process."""
    found = _run(_SHOWN, code, tests, timeout_s)
    outcomes = {r[0]: (r[1], r[2]) for r in found["results"] if isinstance(r, list) and len(r) == 3}
    found["failures"] = [{"test": tests[i], "said": outcomes.get(i, ("missing", "no outcome"))[1] or "no outcome"}
                         for i in range(len(tests)) if outcomes.get(i, ("missing", ""))[0] != "passed"]
    found["passed"] = len(tests) - len(found["failures"]) if found["loaded"] else 0
    return found


def judge(code: str, tests: list[HeldBack], timeout_s: float) -> dict[str, Any]:
    """The held-back tests judged here: the child computes each expression, and G6 compares the value it read back
    with the literal, which never left this process."""
    found = _run(_HELD, code, [t.expression for t in tests], timeout_s)
    computed = {r[0]: (r[1], r[2]) for r in found["results"] if isinstance(r, list) and len(r) == 3}
    verdicts = []
    for position, test in enumerate(tests):
        kind, said = computed.get(position, ("missing", ""))
        if kind != "value":
            verdicts.append({"held_back": test.index, "passed": False,
                             "why": (f"unsupported value type: {said}" if kind == "unsupported"
                                     else f"the expression raised {said}" if kind == "raised"
                                     else "no value came back")})
            continue
        readable, value = (False, None)
        if len(said) < MAX_REPR:
            try:
                readable, value = True, ast.literal_eval(said)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                readable = False
        if not readable:
            verdicts.append({"held_back": test.index, "passed": False,
                             "why": "the value is not a literal, so it cannot be compared outside the code"})
            continue
        equal = bool(value == test.expected)  # both are plain literals here, so == runs no code under test
        verdicts.append({"held_back": test.index, "passed": equal,
                         "why": "" if equal else "the value differs from the expected one"})
    return {"loaded": found["loaded"], "error": found["error"], "verdicts": verdicts, "run": found["run"]}


__all__ = ["BAD_TEST", "HeldBack", "expected_literals", "held_back", "judge", "run_shown", "shapes_shown", "shown"]
