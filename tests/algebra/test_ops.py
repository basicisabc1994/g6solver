# SPDX-License-Identifier: Apache-2.0
"""g6.algebra's SymPy ops (owner, 2026-10-20): known answers with oracles written here, computed by SymPy in G6's own
worker process and re-checked there; planted wrong SymPy answers refused end to end; the time limit holds on a
factorisation that does not end; a worker stopped during its re-check leaves the answer reported as not re-checked,
never as passed; and without SymPy every op refuses with the install hint, after G6's parser has had its say."""
from __future__ import annotations

import json
import sys
import time

import pytest

from g6.algebra import ops, runner
from g6.algebra.envelope import (
    CAS_UNAVAILABLE,
    CERTIFICATE_REFUTED,
    EXPONENT_TOO_LARGE,
    INPUT_INVALID,
    INSTALL_CAS,
    NO_ANSWER,
    NOT_INVERTIBLE,
    NOT_RECHECKED,
    RECHECKED,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    UNKNOWN_SYMBOL,
)
from g6.invariants import check_envelope

HAS_SYMPY = ops.sympy_installed()
needs_sympy = pytest.mark.skipif(not HAS_SYMPY, reason="SymPy is not installed (pip install 'g6[cas]')")
#: RSA-100: 100 digits, two 50-digit primes; SymPy does not factor it within any time limit G6 allows.
RSA_100 = int("15226050279225333605356183781326374297180681149613806886579084945801229632589528976540003506920061"
              "39")


def _drafted(result, code: str = RECHECKED):
    assert check_envelope(result.model_dump()) == [], check_envelope(result.model_dump())
    assert result.completion_state == "qualified-draft", (result.code, result.reason, result.recheck)
    assert result.warning_card["code"] == code, (result.warning_card, result.recheck)
    if code == RECHECKED:
        assert result.recheck["status"] == "passed" and result.recheck["methods"]
    assert result.engine == "SymPy" and result.engine_version and result.latex
    return result


def _blocked(result, code: str):
    assert check_envelope(result.model_dump()) == []
    assert result.completion_state == "blocked-escalated" and result.code == code, (result.code, result.reason)
    assert result.answer == "" and result.answers == []
    return result


# --- known answers, through the worker process ----------------------------------------------------------------------

@needs_sympy
def test_known_answers_of_calculus() -> None:
    assert _drafted(ops.differentiate("sin(x)^2", "x")).answer == "2*sin(x)*cos(x)"
    assert _drafted(ops.integrate("x*E^x", "x")).answer == "(x - 1)*exp(x)"
    definite = _drafted(ops.integrate("sin(x)", "x", lower=0, upper="pi"))
    assert definite.answer == "2" and "quadrature" in " ".join(definite.recheck["methods"])
    assert _drafted(ops.limit("sin(x)/x", "x", point=0)).answer == "1"
    assert _drafted(ops.series("exp(x)", "x", order=4)).answer == "1 + x + x**2/2 + x**3/6 + O(x**4)"


@needs_sympy
def test_known_answers_of_algebra() -> None:
    assert _drafted(ops.simplify("sin(x)^2 + cos(x)^2", symbols=["x"])).answer == "1"
    assert _drafted(ops.expand("(x + 1)^2", symbols=["x"])).answer == "x**2 + 2*x + 1"
    factored = _drafted(ops.factor("x^2 - 5*x + 6", symbols=["x"]))
    assert factored.answer in ("(x - 3)*(x - 2)", "(x - 2)*(x - 3)")
    roots = _drafted(ops.solve("x^2 - 5*x + 6 = 0", unknowns=["x"]))
    assert sorted(a["values"]["x"] for a in roots.answers) == ["2", "3"]
    assert roots.complete is True and "complete" in roots.completeness and "complete" in roots.warning_card["message"]
    system = _drafted(ops.solve(["x + y = 3", "x - y = 1"], unknowns=["x", "y"]))
    assert system.answers == [{"values": {"x": "2", "y": "1"}, "latex": {"x": "2", "y": "1"}}]


@needs_sympy
def test_known_answers_of_matrices_and_integers() -> None:
    assert _drafted(ops.determinant([[1, 2], [3, 4]])).answer == "-2"
    inverse = _drafted(ops.inverse([[1, 2], [3, 4]]))
    assert inverse.details["matrix"] == [["-2", "1"], ["3/2", "-1/2"]]
    eigen = _drafted(ops.eigenvalues([["2", "1"], ["1", "2"]]))
    assert {a["value"]: a["multiplicity"] for a in eigen.answers} == {"1": 1, "3": 1} and eigen.complete is True
    factors = _drafted(ops.factor_integer(360))
    assert factors.answers == [{"prime": "2", "power": 3}, {"prime": "3", "power": 2}, {"prime": "5", "power": 1}]
    assert factors.answer == "2^3 * 3^2 * 5"
    prime = _drafted(ops.is_prime(97))
    assert prime.answer == "True" and prime.details["sympy_deterministic"] is True and "2^64" in prime.completeness
    assert _drafted(ops.gcd([12, 18, 30])).answer == "6"


@needs_sympy
def test_answers_sympy_cannot_give_are_refused_with_their_own_codes() -> None:
    assert "no antiderivative in closed form" in _blocked(ops.integrate("exp(x)/log(x)", "x"), NO_ANSWER).reason
    singular = _blocked(ops.inverse([[1, 2], [2, 4]]), NOT_INVERTIBLE)
    assert "G6's own exact elimination in fractions also gives 0" in singular.reason
    assert _blocked(ops.solve("exp(x) = x", unknowns=["x"]), NO_ANSWER).reason.startswith("SymPy could not solve")
    assert _blocked(ops.limit("abs(x)/x", "x", point=0, symbols={"x": ["real"]}), NO_ANSWER)


@needs_sympy
def test_an_answer_the_recheck_cannot_decide_is_reported_as_not_rechecked() -> None:
    laurent = ops.series("1/sin(x)", "x", order=3)
    _drafted(laurent, NOT_RECHECKED)
    assert laurent.recheck["status"] == "undecided" and "Laurent" in laurent.warning_card["message"]
    assert "never as passed" in laurent.warning_card["message"]


@needs_sympy
def test_the_input_is_read_back_in_g6s_own_form() -> None:
    result = ops.differentiate("x**2-5*x+6", "x", symbols={"x": ["real"]})
    assert result.input == {"op": "differentiate", "symbols": {"x": ["real"]}, "expression": "x^2 - 5*x + 6",
                            "variable": "x", "order": 1}
    assert result.evidence[0]["input"] == result.input and result.evidence[0]["engine_ran"] is True


# --- planted wrong answers, end to end -------------------------------------------------------------------------------

@pytest.fixture
def in_process(monkeypatch: pytest.MonkeyPatch):
    """G6's worker run in this process (so a SymPy function can be replaced), with the same request it would read."""
    from g6.algebra import worker

    def call(request, *, seconds, memory_mb, cap, record):
        lines: list = []
        worker.handle(json.loads(json.dumps(request)), lines.append)
        return lines, ""

    monkeypatch.setattr(runner, "call_worker", call)
    return monkeypatch


@needs_sympy
@pytest.mark.parametrize(("name", "call", "wrong"), [
    ("integrate", lambda: ops.integrate("x*E^x", "x"), lambda s, x: x * s.exp(x)),
    ("diff", lambda: ops.differentiate("sin(x)^2", "x"), lambda s, x: 2 * s.sin(x)),
    ("simplify", lambda: ops.simplify("sin(x)^2 + cos(x)^2", symbols=["x"]), lambda s, x: s.Integer(2)),
    ("factor", lambda: ops.factor("x^2 - 5*x + 6", symbols=["x"]), lambda s, x: (x - 2) * (x + 3)),
    ("limit", lambda: ops.limit("sin(x)/x", "x", point=0), lambda s, x: s.Integer(0)),
    ("factorint", lambda: ops.factor_integer(360), lambda s, x: {2: 3, 3: 2, 7: 1}),
    ("isprime", lambda: ops.is_prime(561), lambda s, x: True),
    ("igcd", lambda: ops.gcd([12, 18, 30]), lambda s, x: 3),
])
def test_a_planted_wrong_sympy_answer_is_refuted_end_to_end(in_process, name, call, wrong) -> None:
    import sympy

    planted = wrong(sympy, sympy.Symbol("x"))
    in_process.setattr(sympy, name, lambda *a, **k: planted)
    result = _blocked(call(), CERTIFICATE_REFUTED)
    evidence = result.evidence[0]
    assert evidence["refuted_answer"]["answer"] and evidence["recheck"]["status"] == "refuted"
    assert evidence["recheck"]["failures"] and "re-check contradicts" in result.reason


@needs_sympy
def test_a_planted_wrong_answer_is_refuted_over_mcp(in_process) -> None:
    import sympy

    from g6.mcp import invoke_reply

    in_process.setattr(sympy, "integrate", lambda *a, **k: sympy.Integer(1))
    reply = invoke_reply("algebra", "integrate", json.dumps({"expression": "sin(x)", "variable": "x", "lower": 0,
                                                             "upper": "pi"}))
    assert check_envelope(reply) == [] and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == CERTIFICATE_REFUTED


# --- time limits ---------------------------------------------------------------------------------------------------

@needs_sympy
def test_the_time_limit_holds_on_a_factorisation_that_does_not_end() -> None:
    started = time.perf_counter()
    result = ops.factor_integer(RSA_100, time_limit=2)
    assert time.perf_counter() - started < 30
    _blocked(result, TIME_LIMIT)
    execution = result.evidence[0]["execution"]
    assert execution["cleanup_confirmed"] is True and execution["child_exited"] is True


def test_a_worker_stopped_during_its_recheck_leaves_the_answer_not_rechecked() -> None:
    lines = [{"stage": "start", "sympy_version": "1.14.0"},
             {"stage": "answer", "answer": "1", "latex": "1"},
             {"stage": "partial", "recheck": {"status": "undecided", "methods": ["G6's own evaluation"],
                                              "why": "SymPy's simplify of the difference had not finished"}}]
    state = ops.read_lines({}, lines, "time", 5)
    assert state["recheck"]["status"] == "undecided" and "time limit of 5 s" in state["recheck"]["why"]
    passed_partial = [*lines[:2], {"stage": "partial", "recheck": {"status": "passed"}}]
    assert ops.read_lines({}, passed_partial, "time", 5)["recheck"]["status"] == "undecided"
    with pytest.raises(Exception) as caught:
        ops.read_lines({}, lines[:1], "time", 5)
    assert caught.value.code == TIME_LIMIT


@pytest.mark.parametrize(("limit", "code"), [(0, INPUT_INVALID), (-1, INPUT_INVALID), (121, TIME_LIMIT_TOO_LONG),
                                             (True, INPUT_INVALID)])
def test_time_limits_are_checked_and_capped(limit, code) -> None:
    _blocked(ops.simplify("x", symbols=["x"], time_limit=limit), code)


# --- without SymPy -------------------------------------------------------------------------------------------------

@pytest.fixture
def no_sympy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [m for m in sys.modules if m == "sympy" or m.startswith("sympy.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "sympy", None)


@pytest.mark.parametrize("call", [
    lambda: ops.simplify("x", symbols=["x"]), lambda: ops.expand("x", symbols=["x"]),
    lambda: ops.factor("x", symbols=["x"]), lambda: ops.differentiate("x", "x"), lambda: ops.integrate("x", "x"),
    lambda: ops.solve("x = 1", unknowns=["x"]), lambda: ops.limit("x", "x"), lambda: ops.series("x", "x"),
    lambda: ops.determinant([[1]]), lambda: ops.inverse([[1]]), lambda: ops.eigenvalues([[1]]),
    lambda: ops.factor_integer(6), lambda: ops.is_prime(7), lambda: ops.gcd([4, 6]),
])
def test_without_sympy_every_op_refuses_with_the_install_hint(no_sympy, call) -> None:
    result = _blocked(call(), CAS_UNAVAILABLE)
    assert INSTALL_CAS in result.reason and INSTALL_CAS in result.warning_card["suggested_action"]
    assert result.evidence[0]["engine_ran"] is False


def test_without_sympy_the_parser_still_refuses_first(no_sympy) -> None:
    _blocked(ops.simplify("2^(10^10)"), EXPONENT_TOO_LARGE)
    found = _blocked(ops.simplify("x + y", symbols=["x"]), UNKNOWN_SYMBOL)
    assert found.refusals[0]["column"] == 5 and found.refusals[0]["field"] == "expression"


def test_without_sympy_the_worker_refuses_too(no_sympy) -> None:
    from g6.algebra import worker

    lines: list = []
    worker.handle({"op": "simplify", "params": {"expression": "x", "symbols": ["x"]}}, lines.append)
    assert lines[0]["stage"] == "refusal" and lines[0]["code"] == CAS_UNAVAILABLE and lines[-1]["stage"] == "done"
