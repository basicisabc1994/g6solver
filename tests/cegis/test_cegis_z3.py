# SPDX-License-Identifier: Apache-2.0
"""g6.cegis with a real Z3 (audit 027 §11, CG-008): survival controls, a real timeout, and the translation of a
candidate checked against Python itself, run out of process. Skipped without z3-solver (``pip install 'g6[formal]'``).
"""
from __future__ import annotations

import pytest

z3 = pytest.importorskip("z3")

from g6.cegis import (  # noqa: E402 - after the importorskip
    Counterexample,
    Inconclusive,
    InputDomain,
    NoCounterexample,
    Sketch,
    SubprocessRunner,
    Z3Verifier,
    cegis,
)
from g6.invariants import check_envelope  # noqa: E402

LINEAR = "def f(x):\n    return x * ?? + ??\n"
X = InputDomain.ints(["x"], -10, 10)
CLAMP = Sketch.from_src("def f(x):\n    return ?? if x > ?? else x\n")
CLAMP_SPEC = "(= result (ite (> x 20) 20 x))"


def _smt(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value) if value >= 0 else f"(- {-value})"


# --- survival controls ------------------------------------------------------------------------------------------------

def test_a_correct_candidate_gets_a_qualified_draft_no_counterexample_within_the_encoded_domain() -> None:
    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X,
                   verifier=Z3Verifier("(= result (+ (* 3 x) 1))"))
    assert result.status == "no_counterexample_in_domain" and result.control == {0: 3, 1: 1}
    assert result.completion_state == "qualified-draft"
    said = result.boundary["verifier"]
    assert said["outcome"] == "no_counterexample" and said["degraded"] is False and said["calls"] == 1
    evidence = said["evidence"]
    assert evidence["encoded_domain"] == {"x": {"sort": "Int", "range": [-10, 10]}}
    assert evidence["candidate_term"] == "(+ (* x 3) 1)" and evidence["result_sort"] == "Int"
    (solver,) = evidence["solver"]
    assert solver["solver_version"] == z3.get_version_string() and solver["checks"][0]["result"] == "unsat"
    assert "(check-sat)" in solver["checks"][0]["query"]
    envelope = result.to_envelope(request_id="q", run_id="r")
    assert check_envelope(envelope) == [] and envelope["completion_state"] == "qualified-draft"
    assert envelope["warning_card"]["code"] == "G6_E_CEGIS_DOMAIN_BOUNDED"
    assert "not a proof a person has inspected" in envelope["warning_card"]["message"]


def test_z3_returns_the_real_counterexample_in_the_domain() -> None:
    """``20 if x > 5 else x`` is wrong exactly on 6..19 of [-1000, 1000]; Z3 must name one of those inputs."""
    domain = InputDomain.ints(["x"], -1000, 1000, max_inputs=3, seed=1)
    outcome = Z3Verifier(CLAMP_SPEC).find_counterexample(CLAMP, {0: 20, 1: 5}, domain)
    assert isinstance(outcome, Counterexample) and 6 <= outcome.inputs["x"] <= 19
    assert isinstance(Z3Verifier(CLAMP_SPEC).find_counterexample(CLAMP, {0: 20, 1: 20}, domain), NoCounterexample)


def test_z3_finds_what_three_samples_missed_and_the_loop_uses_it() -> None:
    domain = InputDomain.ints(["x"], -1000, 1000, max_inputs=3, seed=1)
    sampled = cegis(CLAMP, lambda x: min(x, 20), domain=domain)
    assert sampled.status == "no_counterexample_in_domain"  # three samples, and the program is wrong:
    assert isinstance(Z3Verifier(CLAMP_SPEC).find_counterexample(CLAMP, sampled.control, domain), Counterexample)
    checked = cegis(CLAMP, lambda x: min(x, 20), domain=domain, verifier=Z3Verifier(CLAMP_SPEC))
    assert checked.status == "no_counterexample_in_domain" and checked.control[0] == 20 and checked.control[1] in (19, 20)
    assert "counterexample" in [step.get("verifier") for step in checked.trace]
    assert checked.boundary["verifier"]["evidence"]["covers"] == "every input of the domain, not only the sampled ones"


# --- CG-008 with the real solver --------------------------------------------------------------------------------------

def test_cg008_a_real_z3_timeout_is_degraded_never_a_pass() -> None:
    """x^3 - y^3 = 33 has no integer solution, and Z3 cannot settle that in 50 ms."""
    domain = InputDomain.ints(["x", "y"], -100_000, 100_000)
    sketch = Sketch.from_src("def f(x, y):\n    return x * x * x - y * y * y\n")
    outcome = Z3Verifier("(not (= result 33))", timeout_s=0.05).find_counterexample(sketch, {}, domain)
    assert isinstance(outcome, Inconclusive) and outcome.degraded is True
    assert outcome.degradation_reason in ("z3_timeout", "z3_unknown")
    assert outcome.evidence["solver"][0]["checks"][0]["result"] == "unknown"


# --- what Z3 does not decide for the oracle ---------------------------------------------------------------------------

def test_a_spec_the_oracle_disagrees_with_is_never_fed_back() -> None:
    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X,
                   verifier=Z3Verifier("(= result (+ (* 3 x) 2))"))
    assert result.status == "inconclusive" and result.reason.startswith("counterexample_unconfirmed")
    assert result.completion_state == "blocked-escalated" and check_envelope(result.to_envelope()) == []


def test_a_spec_naming_an_undeclared_symbol_is_refused() -> None:
    outcome = Z3Verifier("(= result y)").find_counterexample(Sketch.from_src(LINEAR), {0: 3, 1: 1}, X)
    assert isinstance(outcome, Inconclusive) and outcome.degradation_reason == "z3_refused"
    assert "unknown constant y" in outcome.reason


# --- the translation agrees with Python -------------------------------------------------------------------------------

EXPRESSIONS = [
    "x // 3", "x % 3", "-x // 4", "(x - y) % 5", "x * y - 7", "-(x + 3) * 2", "+x", "x if x > y else y",
    "x < y <= 3", "x == y", "x != y", "not (x > 0) or y < 0", "(x >= 0) == (y >= 0)", "x > 0 and y > 0 and x != y",
]
POINTS = [{"x": x, "y": y} for x in (-7, -1, 0, 5) for y in (-3, 2)]


@pytest.mark.parametrize("expression", EXPRESSIONS)
def test_the_translation_agrees_with_python_at_every_point(expression: str) -> None:
    """Python's value is computed by running the candidate in the child process; Z3 must agree with it exactly, and
    must find the counterexample when the spec is the negation."""
    sketch = Sketch.from_src(f"def f(x, y):\n    return {expression}\n")
    row = SubprocessRunner().evaluate(sketch, [{}], POINTS)[0]
    for point, got in zip(POINTS, row, strict=True):
        value = _smt(got["ok"])
        domain = InputDomain.of({"x": [point["x"]], "y": [point["y"]]})
        agree = Z3Verifier(f"(= result {value})").find_counterexample(sketch, {}, domain)
        assert isinstance(agree, NoCounterexample), (expression, point, got, agree)
        differ = Z3Verifier(f"(not (= result {value}))").find_counterexample(sketch, {}, domain)
        assert isinstance(differ, Counterexample) and differ.inputs == point, (expression, point, differ)


def test_python_ints_do_not_wrap_around() -> None:
    """The archive's Z3 leg used 64-bit bit-vectors, on which 3_000_000 ** 3 wraps; Python's int and SMT-LIB Int do
    not."""
    sketch = Sketch.from_src("def f(x):\n    return x * x * x\n")
    domain = InputDomain.of({"x": [3_000_000]})
    exact, wrapped = 3_000_000 ** 3, (3_000_000 ** 3) % 2 ** 64
    assert isinstance(Z3Verifier(f"(= result {exact})").find_counterexample(sketch, {}, domain), NoCounterexample)
    assert isinstance(Z3Verifier(f"(= result {wrapped})").find_counterexample(sketch, {}, domain), Counterexample)


def test_bool_holes_and_scattered_domains_are_encoded() -> None:
    sketch = Sketch.from_src("def f(x, b):\n    return x if ??b else -x\n")
    domain = InputDomain.of({"x": [-4, 0, 9], "b": [True, False]})
    outcome = Z3Verifier("(= result (- x))").find_counterexample(sketch, {0: False}, domain)
    assert isinstance(outcome, NoCounterexample)
    assert outcome.evidence["encoded_domain"] == {"x": {"sort": "Int", "values": [-4, 0, 9]},
                                                  "b": {"sort": "Bool", "values": [False, True]}}
    wrong = Z3Verifier("(= result (- x))").find_counterexample(sketch, {0: True}, domain)
    assert isinstance(wrong, Counterexample) and wrong.inputs["x"] in (-4, 9) and wrong.inputs["b"] in (True, False)
