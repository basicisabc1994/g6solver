# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Code correspondence on the restricted return fragment: a separate obligation, never supplied by acceptance."""
from __future__ import annotations

import pytest

z3 = pytest.importorskip("z3")

from g6.formal import z3_bridge  # noqa: E402
from g6.harness.formalisation.code_check import check_code  # noqa: E402
from tests.formalisation._cases import relation  # noqa: E402

PLUS_ONE = relation("(= result (+ x 1))")
RIGHT = "def f(x):\n    return x + 1\n"
WRONG = "def f(x):\n    return x + 2\n"


def running(source, name="f"):
    """The real code, run: the test's stand-in for the behaviour runner."""
    space: dict = {}
    exec(compile(source, "<code under check>", "exec"), space)   # noqa: S102 - the test's own three-line functions

    def run(call):
        try:
            return {"returns": space[name](*call["args"], **call["kwargs"])}
        except Exception as exc:   # noqa: BLE001 - the observation is the raised type's name
            return {"raises": type(exc).__name__}
    return run


def test_right_code_passes_with_its_reading_replayed_on_the_real_code():
    found = check_code(PLUS_ONE, RIGHT, "f", run=running(RIGHT))
    assert (found.result, found.replayed) == ("pass", "reproduced")
    assert found.query.reading == "entailed_under_assumptions" and found.query.query.quantifier == "forall"
    assert found.replays and all(one["agrees"] for one in found.replays)


def test_wrong_code_fails_with_a_reproduced_counterexample():
    found = check_code(PLUS_ONE, WRONG, "f", run=running(WRONG))
    assert (found.result, found.replayed) == ("fail", "reproduced") and found.query.reading == "counterexample"
    assert found.replays[0]["call"]["args"] == [found.query.witness["x"]]


def test_counterexample_dropped_when_not_reproduced():
    """The solver's counterexample is of the source as read; when the code that runs does not show it, it is dropped."""
    found = check_code(PLUS_ONE, WRONG, "f", run=running(RIGHT))
    assert (found.result, found.replayed) == ("not_established", "not_reproduced")
    assert "did not reproduce" in found.why


def test_true_model_result_with_code_that_runs_otherwise_is_not_established():
    """The relation holds of the source as read, and the function that runs is another one."""
    module = "OFFSET = 2\ndef f(x):\n    return x + 1\ndef g(x):\n    return x + OFFSET\nf = g\n"
    found = check_code(PLUS_ONE, RIGHT, "f", run=running(module))
    assert found.query.reading == "entailed_under_assumptions"
    assert (found.result, found.replayed) == ("not_established", "not_reproduced")
    assert "differs from the code that ran" in found.why


@pytest.mark.parametrize("source, names", [
    ("def f(x):\n    total = 0\n    for i in range(x):\n        total += 1\n    return total + 1\n", "f"),
    ("def f(x):\n    return abs(x) + 1\n", "f"),
    ("def f(x):\n    if x > 3:\n        return x + 1\n    return x + 1\n", "f"),
    ("def f(x):\n    return x + OFFSET\n", "f"),
    ("def f(x, y=0):\n    return x + 1\n", "parameters"),
    ("import functools\n@functools.cache\ndef f(x):\n    return x + 1\n", "decorated"),
    ("def f(x):\n    return x + 1\ndef f(x):\n    return x + 2\n", "defined 2 times"),
    ("x = 1\n", "no function f"), ("def f(x:\n", "does not parse"),
])
def test_unreadable_function_not_established(source, names):
    found = check_code(PLUS_ONE, source, "f", run=lambda call: {"returns": 0})
    assert (found.result, found.replayed, found.query) == ("not_established", "not_replayed", None)
    assert names in found.why


def test_bool_arithmetic_not_established():
    """Python's True == 1 is not SMT-LIB's: a bool used as a number is outside the fragment, never approximated."""
    flag = relation("(= result (ite p 1 0))", inputs=(("p", "Bool"),), domain=())
    for source in ("def f(p):\n    return p + 0\n", "def f(p):\n    return int(p)\n", "def f(p):\n    return p * 1\n"):
        found = check_code(flag, source, "f", run=running(source))
        assert found.result == "not_established" and found.query is None
    truth = relation("(= result (> x 0))", result_sort="Bool")
    number = "def f(x):\n    return x\n"
    assert "result is declared Bool" in check_code(truth, number, "f", run=running(number)).why


def test_entry_point_changed_after_acceptance():
    """The function was renamed, or its signature changed, after the rule was accepted: nothing is carried over."""
    renamed = "def compute(x):\n    return x + 1\n"
    found = check_code(PLUS_ONE, renamed, "f", run=running(renamed, "compute"))
    assert found.result == "not_established" and "no function f" in found.why
    assert "shop.f" in check_code(PLUS_ONE, RIGHT, "g", run=running(RIGHT)).why
    resigned = "def f(x, y):\n    return x + 1\n"
    assert "parameters" in check_code(PLUS_ONE, resigned, "f", run=lambda call: {"returns": 0}).why
    renamed_arg = "def f(n):\n    return n + 1\n"
    assert "parameters" in check_code(PLUS_ONE, renamed_arg, "f", run=lambda call: {"returns": 0}).why


@pytest.mark.parametrize("why", ["incomplete", "timeout"])
def test_undecided_is_not_pass(monkeypatch, why):
    monkeypatch.setattr(z3_bridge, "run_query", lambda query, timeout: ("unknown", {}, why))
    for source in (RIGHT, WRONG):
        found = check_code(PLUS_ONE, source, "f", run=running(source))
        assert (found.result, found.replayed) == ("not_established", "not_replayed")
        assert found.query is not None and not found.query.decided


def test_no_runner_is_a_model_result_only():
    found = check_code(PLUS_ONE, RIGHT, "f", run=None)
    assert (found.result, found.replayed) == ("not_established", "not_replayed") and "model result only" in found.why
    assert found.query.reading == "entailed_under_assumptions"
    def broken(call):
        raise OSError("no runner")
    assert check_code(PLUS_ONE, RIGHT, "f", run=broken).replayed == "not_replayed"
    assert check_code(PLUS_ONE, RIGHT, "f", run=lambda call: {"unavailable": "x"}).replayed == "not_replayable"


def test_empty_domain_is_not_established():
    empty = relation("(= result (+ x 1))", domain=("(> x 10)", "(< x 0)"))
    found = check_code(empty, WRONG, "f", run=running(WRONG))
    assert found.result == "not_established" and "domain" in found.why


def test_raise_guard_is_part_of_the_obligation():
    guarded = relation("(= result (+ x 1))", raises_when="(< x 0)", error="ValueError", domain=("(>= x (- 5))",))
    never_raises = "def f(x):\n    return x + 1\n"
    found = check_code(guarded, never_raises, "f", run=running(never_raises))
    assert (found.result, found.replayed) == ("fail", "reproduced") and found.query.witness["x"] < 0
    inside = relation("(= result (+ x 1))", raises_when="(< x 0)", error="ValueError")   # the domain never raises
    assert check_code(inside, never_raises, "f", run=running(never_raises)).result == "pass"


def test_unsupported_statement_is_not_established():
    import dataclasses

    odd = dataclasses.replace(PLUS_ONE, adapter="smt.other.v1")
    assert check_code(odd, RIGHT, "f", run=running(RIGHT)).result == "not_established"