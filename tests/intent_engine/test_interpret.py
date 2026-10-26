# SPDX-License-Identifier: Apache-2.0
from g6.core.llm import FakeLLM, NoModel
from g6.intent_engine import IntentContract, IntentElicitor


def test_missing_fields_raise_posture_without_magic_keywords():
    # Removing interpret.py's missing-field floor makes this fail (USER_INTENT-01, D1).
    from g6.intent_engine import interpret
    contract = IntentContract(goal="Export selected invoices")
    result = interpret("Export selected invoices", contract)
    assert result["posture"] == "clarify" and result["requires_confirmation"]
    assert "success_criteria" in result["missing_fields"]
    assert result["contract"] == contract.model_dump()


def test_model_cannot_lower_floor_or_fill_contract():
    # Removing apply_intent_floor's max severity makes this fail.
    from g6.intent_engine import interpret
    contract = IntentContract(goal="Export selected invoices")
    result = interpret(contract.goal, contract, FakeLLM(default='{"posture":"endorsed","questions":[]}'))
    assert result["posture"] == "clarify"
    assert result["contract"]["success_criteria"] == []


def test_stated_preferences_and_risks_survive_reelicitation():
    # Removing elicitor.py's declared_risks copy makes this fail (USER_INTENT-02).
    from g6.intent_engine import interpret
    contract = IntentContract(goal="Export invoices", constraints=["British spelling"], declared_risks=["data loss"])
    again = IntentElicitor().elicit(contract.goal, prior=contract).contract
    assert again.constraints == ["British spelling"] and again.declared_risks == ["data loss"]
    assert interpret(contract.goal, again)["posture"] == "clarify"


FILLED = {"why": "customers need their invoices", "success_criteria": ["a CSV of the selected invoices"],
          "failure_criteria": ["an invoice nobody selected is exported"], "non_goals": ["PDF export"],
          "risk_tolerance": "low"}


def test_a_request_that_is_not_the_contracts_goal_is_not_endorsed():
    # Removing interpret's request-against-goal floor makes this fail (any filled contract endorsed any request).
    from g6.intent_engine import interpret
    contract = IntentContract(goal="Export selected invoices", **FILLED)
    assert interpret("export  selected invoices", contract)["posture"] == "endorsed"   # the same goal, normalised
    result = interpret("Delete all customer records", contract)
    assert result["posture"] == "clarify" and result["requires_confirmation"]
    assert result["completion_state"] == "qualified-draft" and any("goal" in q for q in result["questions"])


def test_a_replacement_the_elicitor_recorded_lets_the_contract_stand():
    # Removing interpret's recorded-replacement exception makes this fail.
    from g6.intent_engine import interpret
    session = IntentElicitor().elicit("Delete all customer records", answers={"goal": "Export selected invoices",
                                                                              **FILLED})
    assert interpret("Delete all customer records", session.contract)["posture"] == "endorsed"
    assert interpret("Archive the old invoices", session.contract)["posture"] == "clarify"


def test_model_dependency_failure_blocks():
    # Removing interpret.py's model-failure branch makes this fail.
    from g6.intent_engine import interpret
    result = interpret("Export invoices", IntentContract(goal="Export invoices"), NoModel())
    assert result["posture"] == "block" and result["completion_state"] == "blocked-escalated"
    assert result["warning_card"]["code"] == "G6_E_INTENT_REVIEW_FAILED"


def test_elicitor_dependency_failure_blocks(monkeypatch):
    # Removing interpret.py's structural-floor failure branch makes this fail.
    from g6.intent_engine import interpret
    monkeypatch.setattr(IntentElicitor, "_questions_for", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    result = interpret("Export invoices", IntentContract(goal="Export invoices"))
    assert result["posture"] == "block"
