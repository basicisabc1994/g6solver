# SPDX-License-Identifier: Apache-2.0
"""F9: summary reads only positions the producing component declares."""
import importlib
from types import SimpleNamespace

import pytest


def summary(component, op, output):
    module = importlib.import_module("g6.core.decision_summary")
    return module.summarise(component, op, output)


@pytest.mark.parametrize("verdict,expected", [("pass", True), ("report_consistent", None), ("revise", False),
                                            ("needs_review", False), ("block", False), ("new_word", None)])
def test_gate_declares_each_decision(verdict, expected):
    import g6.coding_agent_gate as gate
    out = summary(gate, "verify_answer", {"verdict": verdict, "evidence_status": "asserted"})
    assert out == {"may_proceed": expected, "declared": True, "evidence_status": "asserted",
                   "action_authorization": "not_requested"}


@pytest.mark.parametrize("decision,expected,authorization", [
    ("block", False, "refused"), ("BLOCK", False, "refused"),
    ("require_additional_information", False, "requires_person"), ("revise", False, "refused"),
    ("allow", True, "allowed"), ("require_confirmation", False, "requires_person")])
def test_action_decision_is_read_at_its_declared_position(decision, expected, authorization):
    import g6.action_gating as gate
    out = summary(gate, "evaluate", {"gate_decision": {"decision": decision}, "data": {"verdict": "pass"}})
    assert out["may_proceed"] is expected
    assert out["action_authorization"] == authorization
    assert out["evidence_status"] == "asserted"


def test_undeclared_component_and_data_with_a_decoy_decision_are_ignored():
    component = SimpleNamespace()
    assert summary(component, "evaluate", {"gate_decision": {"decision": "allow"}}) == {
        "may_proceed": None, "declared": False, "evidence_status": "asserted",
        "action_authorization": "not_requested"}
    import g6.coding_agent_gate as gate
    out = summary(gate, "verify_answer", {"data": {"verdict": "pass", "evidence_status": "observed_by_g6"}})
    assert out["may_proceed"] is None
    assert out["evidence_status"] == "asserted"


def test_non_decision_operation_does_not_inherit_a_declaration():
    import g6.coding_agent_gate as gate
    assert summary(gate, "help", {"verdict": "pass"})["declared"] is False


def test_bound_declarations_use_module_identity_and_own_declaration_wins(monkeypatch):
    from types import ModuleType
    import g6.action_gating as gate
    assert summary(ModuleType(gate.__name__), "evaluate", {"gate_decision": {"decision": "allow"}})["declared"] is False
    monkeypatch.setattr(gate, "__g6_decision__", {}, raising=False)
    assert summary(gate, "evaluate", {"gate_decision": {"decision": "allow"}})["declared"] is False


@pytest.mark.parametrize("module,op,output,expected", [
    ("g6.agents.agent_runtime", "decide", {"decision": {"verdict": "run"}}, True),
    ("g6.agents.agent_runtime", "decide", {"decision": {"verdict": "dry_run"}}, None),
    ("g6.agents.agent_runtime", "decide", {"decision": {"verdict": "refused"}}, False),
    ("g6.action_logic", "check_crisis_monitoring", {"result": {"severity": "review"}}, False),
    ("g6.epistemic_integrity", "adjudicate", {"overall_disposition": "BLOCK"}, False),
    ("g6.model_checking", "check_invariant", {"verdict": "no_violation_within_bound"}, True),
    ("g6.model_checking", "prove_invariant", {"verdict": "not_inductive"}, False),
    ("g6.model_checking", "sany_parse", {"verdict": "errors"}, False),
    ("g6.knowledge.knowledge_manifest", "verify_passage", {"outcome": "source_unavailable"}, False),
    ("g6.knowledge.knowledge_manifest", "verify_passage", {"outcome": "matches"}, True),
    ("g6.sources.ctx_library_mapper", "licence_check", {"results": [{"verdict": "compatible"}, {"verdict": "conditions"}]}, False),
    ("g6.sources.ctx_library_mapper", "licence_check", {"results": [{"verdict": "compatible"}, {"verdict": "unknown"}]}, None),
    ("g6.sources.ctx_library_mapper", "licence_check", {"results": []}, None),
    ("g6.grounding", "check_claim", {"check": {"status": "quoted"}}, True),
    ("g6.grounding", "check_claims", {"checks": [{"status": "quoted"}, {"status": "partial"}]}, False),
    ("g6.grounding", "coverage", {"coverage": {"claims": [{"status": "SUPPORTED"}, {"status": "ASSERTED"}]}}, False),
])
def test_remaining_components_declare_only_their_actual_positions(module, op, output, expected):
    out = summary(importlib.import_module(module), op, output)
    assert out["declared"] is True
    assert out["may_proceed"] is expected
    assert out["action_authorization"] == "not_requested"


def test_observed_gate_summary_preserves_status_without_promoting_it():
    import g6.coding_agent_gate as gate
    out = summary(gate, "verify_answer", {"verdict": "pass", "evidence_status": "observed_by_g6"})
    assert (out["may_proceed"], out["evidence_status"]) == (True, "observed_by_g6")
