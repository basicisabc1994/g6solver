# SPDX-License-Identifier: Apache-2.0
"""g6.formal's block (owner, 2026-10-20): every op reachable through the registry and ``g6_invoke``, each answer inside
the library envelope and never ``verified``; unknown ops, unknown fields and bad parameters refused with stable codes;
no op that runs code, so none is sensitive."""
from __future__ import annotations

import importlib.util
import json
import sys

import pytest

from g6.core import discovery
from g6.core.registry import get_registry
from g6.formal.envelope import SOLVER_UNAVAILABLE
from g6.formal.formal_block import FormalBlock
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

HAS_Z3 = importlib.util.find_spec("z3") is not None
needs_z3 = pytest.mark.skipif(not HAS_Z3, reason="z3-solver is not installed (pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver')")
STATES = {"qualified-draft", "blocked-escalated"}

Z3_CALLS = {
    "check_contract": {"properties": ["(> x 0)", "(< x 10)"], "premises": ["(= x 4)"], "sorts": {"x": "Int"}},
    "check_sat": {"formula": "(> x 2)", "sorts": {"x": "Int"}, "assertions": ["(< x 4)"]},
    "check_validity": {"premises": ["(> x 1)"], "formula": "(> x 0)", "sorts": {"x": "Int"}},
    "check_entailment": {"formula": "(and p q)", "other": "(or p r)", "sorts": {"p": "Bool", "q": "Bool", "r": "Bool"}},
    "find_counterexample": {"formula": "(> (* x x) x)", "premises": ["(> x -5)", "(< x 5)"], "sorts": {"x": "Int"}},
    "optimise": {"objectives": [["minimise", "(+ x y)"]], "assertions": ["(>= x 2)", "(>= y 3)"],
                 "sorts": {"x": "Int", "y": "Int"}, "bounds": {"x": [None, 10]}},
    "unsat_core": {"named": {"big": "(> x 5)", "small": "(< x 3)", "other": "(> y 0)"},
                   "sorts": {"x": "Int", "y": "Int"}},
    "simplify": {"formula": "(+ x 1 1)", "sorts": {"x": "Int"}},
    "eliminate_quantifiers": {"formula": "(exists ((z Int)) (= x (* 2 z)))", "sorts": {"x": "Int"}},
    "count_models": {"formula": "(or p q)", "sorts": {"p": "Bool", "q": "Bool"}, "cap": 10},
    "check_equivalence": {"formula": "(> x 0)", "other": "(>= x 1)", "sorts": {"x": "Real"}},
}
PURE_CALLS = {
    "check_propositional": {"formula": "A & ~B", "assertions": ["B | C"]},
    "belnap_eval": {"formula": "p & q", "values": {"p": "T", "q": "B"}},
    "belnap_table": {"formula": "p -> q"},
    "modal_eval": {"formula": "p", "worlds": {"a": {"p": "T"}, "b": {"p": "N"}}, "access": [["a", "b"], ["b", "a"]],
                   "world": "a", "modality": "possibly", "modal_frame": "deontic"},
    "frame_check": {"worlds": {"a": {}, "b": {}}, "access": [["a", "b"]], "modal_frame": "deontic"},
}
EXPECTED = {
    "check_contract": ("qualified-draft", {"value": "T"}),
    "check_sat": ("qualified-draft", {"satisfiable": True, "model": {"x": 3}}),
    "check_validity": ("qualified-draft", {"valid": True}),
    "check_entailment": ("qualified-draft", {"valid": True}),
    "find_counterexample": ("qualified-draft", {"valid": False}),
    "optimise": ("qualified-draft", {"optimal": True, "model": {"x": 2, "y": 3}}),
    "unsat_core": ("qualified-draft", {"core": ["big", "small"], "minimal": True}),
    "simplify": ("qualified-draft", {"result_term": "(+ 2 x)", "equivalent": True}),
    "eliminate_quantifiers": ("qualified-draft", {"result_term": "(= 0 (mod x 2))", "quantifier_free": True}),
    "count_models": ("qualified-draft", {"count": 3, "exact": True}),
    "check_equivalence": ("qualified-draft", {"valid": False}),
    "check_propositional": ("qualified-draft", {"satisfiable": True}),
    "belnap_eval": ("blocked-escalated", {"value": "B"}),
    "belnap_table": ("qualified-draft", {}),
    "modal_eval": ("blocked-escalated", {"value": "N"}),       # no evidence either way blocks; the value is kept
    "frame_check": ("blocked-escalated", {}),
}
CALLS = [pytest.param(op, params, marks=needs_z3) for op, params in Z3_CALLS.items()] + list(PURE_CALLS.items())


def test_formal_is_registered_with_a_block_and_no_sensitive_op() -> None:
    registry = get_registry()
    assert registry.get_class("formal") is FormalBlock
    info = discovery.describe("formal")
    assert info["block"] == "FormalBlock" and info["maturity"] == "beta" and info["sensitive_ops"] == []
    assert sorted(info["ops"]) == sorted([*Z3_CALLS, *PURE_CALLS])
    assert info["summary"].endswith(".") and len(info["summary"]) <= 170


@pytest.mark.parametrize(("op", "params"), CALLS)
def test_every_op_returns_a_valid_envelope_through_the_registry(op: str, params: dict) -> None:
    result = get_registry().invoke("formal", op, {**params, "request_id": "req-1", "run_id": "run-1"},
                                   block_sensitive=True)
    assert result.__class__.__name__ == "Success", result
    payload = result.unwrap()
    assert check_envelope(payload) == [] and payload["completion_state"] in STATES
    assert (payload["component"], payload["op"]) == ("formal", op)
    assert (payload["request_id"], payload["run_id"]) == ("req-1", "run-1") and payload["evidence"]
    state, fields = EXPECTED[op]
    assert payload["completion_state"] == state, (payload["code"], payload["reason"])
    for key, value in fields.items():
        assert payload[key] == value, key


@pytest.mark.parametrize(("op", "params"), CALLS)
def test_every_op_keeps_its_state_through_g6_invoke(op: str, params: dict) -> None:
    reply = invoke_reply("formal", op, json.dumps(params))
    assert check_envelope(reply) == [] and "result" in reply
    assert reply["completion_state"] == EXPECTED[op][0] != "verified"
    assert reply["ok"] is (reply["completion_state"] != "blocked-escalated")
    assert reply["warning_card"]["code"] == reply["result"]["warning_card"]["code"]


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer(op: str) -> None:
    payload = get_registry().invoke("formal", op).unwrap()
    listed = payload[op]
    assert len(listed) == len(Z3_CALLS) + len(PURE_CALLS)
    reply = invoke_reply("formal", op)
    assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"


@pytest.mark.parametrize(("op", "params"), [
    ("run_code", {"code": "import os"}),
    ("interpolate", {"formula": "p"}),
    ("check_sat", {"formula": "(> x 0)", "backend": "prolog"}),
    ("check_sat", {"formula": "(> x 0)", "solver": "lean"}),
    ("check_sat", {"formula": "(> x 0)", "sorts": "x Int"}),
    ("count_models", {"formula": "p", "sorts": {"p": "Bool"}, "cap": 0}),
    ("optimise", {"objectives": "minimise x"}),
    ("modal_eval", {"formula": "p", "modality": "always"}),
])
def test_unknown_ops_unknown_fields_and_bad_parameters_are_refused(op: str, params: dict) -> None:
    result = get_registry().invoke("formal", op, params, block_sensitive=True)
    assert result.__class__.__name__ == "Failure"
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION] [FORMAL_ERROR]")
    reply = invoke_reply("formal", op, json.dumps(params))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and check_envelope(reply) == []


def test_a_term_the_grammar_refuses_is_a_labelled_refusal_not_an_error() -> None:
    payload = get_registry().invoke("formal", "check_sat", {"formula": "(set-option :x y)"}).unwrap()
    assert payload["completion_state"] == "blocked-escalated" and payload["code"] == "G6_E_FORMAL_PARSE_ERROR"


@pytest.mark.parametrize("op", list(Z3_CALLS))
def test_without_z3_every_z3_op_is_blocked_unavailable(op: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "z3", None)  # ``import z3`` now raises ImportError
    payload = get_registry().invoke("formal", op, Z3_CALLS[op], block_sensitive=True).unwrap()
    assert payload["completion_state"] == "blocked-escalated" and payload["code"] == SOLVER_UNAVAILABLE
    assert "g6[formal]" in payload["reason"] and check_envelope(payload) == []
