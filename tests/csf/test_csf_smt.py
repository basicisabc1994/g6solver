# SPDX-License-Identifier: Apache-2.0
"""g6.csf.smt with Z3 (audit 012: CSF-001, CSF-009), with survival controls and an independent oracle.

The archive cannot run here; each regression test says in its docstring what the archive did. Each ``test_null_*``
puts that behaviour back in memory (monkeypatching only) and shows the regression test fails.
"""
from __future__ import annotations

import dataclasses
import random

import pytest

z3 = pytest.importorskip("z3")

from g6.csf import BoundedAgent, ResourceTriple, reachability_bound  # noqa: E402 - after the importorskip
from g6.csf import smt  # noqa: E402
from g6.csf.ltl import (  # noqa: E402
    LTLAlways,
    LTLAnd,
    LTLAtom,
    LTLEventually,
    LTLNext,
    LTLNot,
    LTLOr,
    LTLRelease,
    LTLUntil,
    evaluate_ltl,
    ltl_to_str,
)
from g6.invariants import check_envelope  # noqa: E402


def _agent(states, triples, initial, unsafe, n):
    rows = [{"from": a, "to": b, "probability": p} for a, b, p in triples]
    return BoundedAgent(states=states, transitions=rows, initial_state=initial, unsafe_states=unsafe,
                        resources=ResourceTriple(N=n))


def _fails(test, *args) -> None:
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        test(*args)


def _chain():
    return _agent(["s0", "s1"], [("s0", "s1", 1.0), ("s1", "s1", 1.0)], "s0", [], 2)


def _branching(n: int = 1):
    return _agent(["s0", "s1", "bad"], [("s0", "s1", 0.6), ("s0", "bad", 0.4), ("s1", "s1", 1.0), ("bad", "bad", 1.0)],
                  "s0", ["bad"], n)


# --- archive behaviours -----------------------------------------------------------------------------------------------

_ENCODE = smt.agent_to_smt


def _archive_agent_to_smt(agent, property):
    """The archive's ``agent_to_smt`` (formal_bridge.py:107-211): the property argument is never read. What it encoded
    was the phi_A union bound, which for ``unsafe=[]`` can never exceed epsilon; here that is a property true on every
    path. The result still carries the caller's formula as its name (formal_bridge.py:346)."""
    unsafe = sorted(agent.unsafe_states)
    start = agent.initial_state
    stand_in = f"G({' & '.join(f'!{u}' for u in unsafe)})" if unsafe else f"{start} | !{start}"
    return dataclasses.replace(_ENCODE(agent, stand_in), property=smt._shown(property))


def _archive_argmax_support(agent, state):
    """The archive's SMV and Lean encodings (formal_bridge.py:80, 286): only the most probable successor."""
    return (max(agent.kernel[state], key=lambda o: o.probability).next_state,)


# --- survival controls ------------------------------------------------------------------------------------------------

def test_an_encodable_property_that_holds_is_still_checked() -> None:
    """A gate that refused or failed everything would fail this."""
    result = smt.check_z3(_branching(3), "G(s0 | s1 | bad) & F(s1 | bad) & (s0 U (s1 | bad))", request_id="q",
                          run_id="r")
    assert result.holds is True and result.code == "" and result.completion_state == "qualified-draft"
    envelope = result.to_envelope()
    assert check_envelope(envelope) == [] and envelope["warning_card"]["code"] == smt.SOLVER_DECISION
    assert envelope["request_id"] == "q" and envelope["run_id"] == "r"
    (evidence,) = envelope["evidence"]
    solver = evidence["solver"][0]
    assert solver["engine"].startswith("Z3") and solver["solver_version"]
    assert "(check-sat)" in solver["checks"][0]["query"]
    assert evidence["model"]["unsafe_states"] == ["bad"] and evidence["assumptions"] == list(smt.ASSUMPTIONS)


# --- CSF-001: the property is the encoding, or there is no check ------------------------------------------------------

def test_csf001_a_violated_property_is_never_reported_as_holding() -> None:
    """Archive: ``bridge_check_z3`` on s0 -> s1 -> s1, unsafe=[], ``formula_str="G(!s1)"`` returned verified=True,
    property_name="G(!s1)" and completion_state="verified", though the trace reaches s1 at step 1."""
    result = smt.check_z3(_chain(), "G(!s1)")
    assert result.holds is False and result.property == "G(!s1)"
    assert result.counterexample == ("s0", "s1", "s1") and result.counterexample_probability == 1.0
    assert result.code == smt.PROPERTY_VIOLATED and result.completion_state == "blocked-escalated"
    envelope = result.to_envelope()
    assert check_envelope(envelope) == [] and envelope["completion_state"] == "blocked-escalated"


def test_csf001_a_property_that_cannot_be_encoded_is_refused_not_replaced() -> None:
    """Archive: any formula, a typo included, was "checked" by an encoding that never read it."""
    for prop, code in (("G(!typo)", smt.PROPERTY_NOT_ENCODABLE), ("G(", smt.LTL_PARSE_ERROR),
                       (42, smt.PROPERTY_NOT_ENCODABLE)):
        result = smt.check_z3(_chain(), prop)
        assert result.holds is None and result.code == code and result.completion_state == "blocked-escalated"
        assert check_envelope(result.to_envelope()) == [] and result.solver_evidence == ()
    assert smt.check_z3(_chain(), "G(!typo)").property == "G(!typo)"


def test_null_csf001(monkeypatch) -> None:
    monkeypatch.setattr(smt, "agent_to_smt", _archive_agent_to_smt)
    _fails(test_csf001_a_violated_property_is_never_reported_as_holding)
    _fails(test_csf001_a_property_that_cannot_be_encoded_is_refused_not_replaced)


# --- CSF-009: every positive-probability branch is encoded ------------------------------------------------------------

def test_csf009_a_low_probability_branch_into_an_unsafe_state_is_encoded() -> None:
    """Archive: SMV and Lean kept only the most probable successor, so for s0 -> s1 (0.6) | bad (0.4) the Lean theorem
    asserted ``transition .s0 = .s1`` and could prove safety while the one-step risk was 0.4."""
    result = smt.check_z3(_branching(), "G(!bad)")
    assert result.holds is False and result.counterexample == ("s0", "bad")
    assert result.counterexample_probability == pytest.approx(0.4)


def test_null_csf009(monkeypatch) -> None:
    monkeypatch.setattr(smt, "_support", _archive_argmax_support)
    _fails(test_csf009_a_low_probability_branch_into_an_unsafe_state_is_encoded)


# --- an independent oracle: enumerate every path ----------------------------------------------------------------------

def _random_formula(rng: random.Random, atoms: list[str], depth: int):
    if depth == 0 or rng.random() < 0.3:
        return LTLAtom(rng.choice(atoms))
    kind = rng.choice([LTLNot, LTLNext, LTLAlways, LTLEventually, LTLAnd, LTLOr, LTLUntil, LTLRelease])
    if kind in (LTLNot, LTLNext, LTLAlways, LTLEventually):
        return kind(_random_formula(rng, atoms, depth - 1))
    return kind(_random_formula(rng, atoms, depth - 1), _random_formula(rng, atoms, depth - 1))


def _random_agent(rng: random.Random):
    n = rng.randint(1, 4)
    states = [f"s{i}" for i in range(n)]
    triples = []
    for s in states:
        succ = rng.sample(states, rng.randint(1, n))
        triples += [(s, d, 1.0 / len(succ)) for d in succ]
    return _agent(states, triples, rng.choice(states), [s for s in states if rng.random() < 0.4], rng.randint(1, 3))


def _paths(agent):
    paths = [(agent.initial_state,)]
    for _ in range(agent.resources.N):
        paths = [path + (o.next_state,) for path in paths for o in agent.kernel[path[-1]]]
    return paths


def test_z3_agrees_with_path_enumeration_on_random_models_and_formulas() -> None:
    """The encoding against the evaluator: on 80 random models and formulas, ``holds`` is exactly "evaluate_ltl is
    true on every path", and a counterexample is one of the failing paths."""
    rng = random.Random(11)
    for _ in range(80):
        agent = _random_agent(rng)
        formula = _random_formula(rng, list(agent.states), 3)
        failing = {p for p in _paths(agent) if not evaluate_ltl(formula, [{s: True} for s in p])}
        result = smt.check_z3(agent, formula)
        assert result.code in ("", smt.PROPERTY_VIOLATED), (result.code, result.reason)
        assert result.holds is (not failing), (ltl_to_str(formula), agent.describe())
        if failing:
            assert result.counterexample in failing


def test_never_unsafe_holds_exactly_when_the_reachability_bound_is_zero() -> None:
    """The two backends of g6.csf agree: G(no unsafe state) holds on the model iff the union bound is 0."""
    rng = random.Random(4)
    checked = 0
    while checked < 40:
        agent = _random_agent(rng)
        if not agent.unsafe_states:
            continue
        never = "G(" + " & ".join(f"!{u}" for u in sorted(agent.unsafe_states)) + ")"
        assert smt.check_z3(agent, never).holds is (reachability_bound(agent, 1.0).vub == 0.0)
        checked += 1


def test_a_counterexample_the_evaluator_does_not_confirm_is_not_reported(monkeypatch) -> None:
    """The re-read of Z3's model: a "counterexample" on which the property holds is no decision, not a violation."""
    real = smt.check_sat
    monkeypatch.setattr(smt, "check_sat", lambda goal, *args, **kwargs: real("true", *args, **kwargs))
    result = smt.check_z3(_chain(), "F(s1)")
    assert result.holds is None and result.code == smt.ENCODING_MISMATCH
    assert result.completion_state == "blocked-escalated" and check_envelope(result.to_envelope()) == []


def test_solver_refusals_pass_through_as_blocked() -> None:
    result = smt.check_z3(_chain(), "G(!s1)", timeout=0)
    assert result.holds is None and result.code == "G6_E_FORMAL_INPUT_INVALID"
    assert result.completion_state == "blocked-escalated" and check_envelope(result.to_envelope()) == []
    refused = smt.check_z3("agent", "G(!s1)")  # type: ignore[arg-type]
    assert refused.code == "G6_E_CSF_MODEL_INVALID" and refused.holds is None
