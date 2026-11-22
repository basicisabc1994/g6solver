# SPDX-License-Identifier: Apache-2.0
"""Paraphrases through the real public site functions, paired with their floors."""
import json
from pathlib import Path

import pytest

from g6.core import semantic
from g6.core.llm import FakeLLM


def risk():
    from g6.core.risk_signals import classify
    return classify("Nuke the tenant's universe")[0]


def gate_risk():
    from g6.coding_agent_gate.gate import tier_of
    return tier_of("", "Nuke the tenant's universe")[0]


def effort():
    from g6.solver.complexity_classifier import classify_task
    return classify_task("Unpick the Byzantine generals puzzle").value


def refusal():
    from g6.solver.red_flag_detector import is_refusal
    return is_refusal("You will need to take this request elsewhere; I shall not supply it.")


def jurisdiction():
    from g6.sources.australian_legal_corpus.corpus import uncovered_named, NOT_COVERED
    result = uncovered_named("What law governs a lease in Melbourne?")
    return "victoria" if result == NOT_COVERED["victoria"] else result


def asks():
    from g6.harness.completion_loop import asks
    return asks("Please tell me which account should own the result.")


def overclaims():
    from g6.coding_agent_gate.detectors import overclaims
    return bool(overclaims("It is fit to go straight into customers' hands without further scrutiny."))


def shortcuts():
    from g6.coding_agent_gate.detectors import shortcut_hits
    return bool(shortcut_hits("Make the broken checks stop holding up the delivery by silencing them."))


def truth():
    from g6.workforce.truth import Truth
    return bool(Truth("test", Path("."), {}).extract("The tax office has given this its blessing.").unbound)


@pytest.mark.parametrize("site,kind,value,floor,expected", [
    (risk, "risk", "R3", "R1", "R3"),
    (gate_risk, "risk", "R3", "medium", "high"),
    (effort, "effort", "complex", "simple", "complex"),
    (refusal, "refusal", True, False, True),
    (jurisdiction, "jurisdiction", "victoria", "", "victoria"),
    (asks, "asks_question", True, False, True),
    (overclaims, "overclaim", True, False, True),
    (shortcuts, "shortcuts", ["bypass"], False, True),
    (truth, "overclaim", True, False, True),
])
def test_site_paraphrase_and_floor_and_invalid_reply(site, kind, value, floor, expected):
    with semantic.request(llm=None):
        assert site() == floor
    fake = FakeLLM(lambda s, u: json.dumps({"value": value}) if json.loads(u)["kind"] == kind else "invalid")
    with semantic.request(llm=fake) as scope:
        assert site() == expected
    assert fake.calls and any(m["kind"] == kind and m["disagreement"] for m in scope.mechanisms)
    with semantic.request(llm=FakeLLM(default='{"value":"not-offered"}')):
        assert site() == floor


def test_critic_structured_reply_and_exact_proof_boundary():
    from g6.harness.completion_loop import critic_report
    value = {"verdict": "defect", "rule": "retain saved work", "why": "edits vanish", "test": "assert kept", "error": ""}
    said = "READ: receipt-7\nThe edit is lost. Here is a reproducer."
    with semantic.request(llm=FakeLLM(default=json.dumps({"value": value}))):
        assert critic_report(said, marker="receipt-7")["verdict"] == "defect"
        # A model cannot create the receipt the critic failed to echo.
        assert critic_report("never read it", marker="receipt-7")["verdict"] == "unread"
    with semantic.request(llm=None):
        assert critic_report(said, marker="receipt-7")["verdict"] == "unreadable"


def test_critic_accepts_declared_json_directly_and_never_reads_nested_receipts():
    from g6.harness.completion_loop import critic_report
    value = {"read": "receipt-7", "verdict": "defect", "rule": "retain saved work", "why": "edits vanish",
             "test": "assert kept", "error": ""}
    fake = FakeLLM(default="invalid")
    with semantic.request(llm=fake):
        assert critic_report(json.dumps(value), marker="receipt-7")["verdict"] == "defect"
        assert critic_report(json.dumps({**value, "read": "wrong"}), marker="receipt-7")["verdict"] == "unread"
        assert critic_report(json.dumps({"data": value}), marker="receipt-7")["verdict"] == "unread"
        assert critic_report(json.dumps({**value, "error": "G6_E_CLOUD_CONSENT_REQUIRED"}), marker="receipt-7")["verdict"] == "unread"
    assert not fake.calls


def test_critic_model_cannot_replace_an_explicit_legacy_verdict():
    from g6.harness.completion_loop import critic_report
    fake = FakeLLM(default='{"value":{"verdict":"none","rule":"","why":"","test":"","error":""}}')
    with semantic.request(llm=fake):
        result = critic_report("READ: receipt-7\nRULE: retain work\nVERDICT: defect\nWHY: lost edit", marker="receipt-7")
    assert result["verdict"] == "defect"
    assert not fake.calls


@pytest.mark.parametrize("verdict", ["unreadable", "Unreadable", "cannot", "clean"])
def test_critic_explicit_failure_verdict_is_final_and_a_failed_review(verdict):
    """Only a reply with no verdict line is read for meaning; an explicit verdict never becomes clean."""
    from g6.harness import completion_loop as loop
    fake = FakeLLM(default='{"value":{"verdict":"none","rule":"","why":"","test":"","error":""}}')
    with semantic.request(llm=fake):
        got = loop.critic_report(f"READ: receipt-7\nVERDICT: {verdict}\nWHY: I could not read the code",
                                 marker="receipt-7")
    # wire-intent-fu2 repair 2: a verdict word G6 cannot read (not none or defect) makes the reply unreadable
    assert got["verdict"] == "unreadable" and not fake.calls
    assert loop._round_outcome({**got, "results": {}, "file": "test_review.py"}) == "review_failed"


def test_query_intent_paraphrase_and_floor():
    from g6._context_engine._planner import features_of
    q = "Show the ripples when this component changes"
    with semantic.request(llm=None):
        assert not features_of(q).is_relation_question
    with semantic.request(llm=FakeLLM(default='{"value":{"has_identifier":false,"has_path_or_error":false,'
                                            '"is_relation_question":true,"is_conceptual":false}}')):
        assert features_of(q).is_relation_question


def test_risk_union_never_lowers_floor():
    from g6.core.risk_signals import classify
    from g6.core.risk_terms import TIER_TERMS
    from g6.coding_agent_gate.gate import tier_of
    assert any("password" in terms for _, terms, _ in TIER_TERMS)
    with semantic.request(llm=FakeLLM(default='{"value":"R0"}')):
        assert classify("delete production data")[0] == "R3"
        assert tier_of("low", "delete production data")[0] == "high"


def test_derived_standard_risk_signals_include_semantic_raise():
    from g6.core.risk_signals import signals
    with semantic.request(llm=None):
        assert not signals("Nuke the tenant's universe")
    with semantic.request(llm=FakeLLM(default='{"value":"R3"}')):
        assert signals("Nuke the tenant's universe")[0][0] == "R3"


def test_derived_standard_labels_model_risk_instead_of_quoting_it_as_task_words():
    from g6.workforce.acceptance import derive
    with semantic.request(llm=FakeLLM(default='{"value":"R3"}')):
        tier, sources = derive("Nuke the tenant's universe", "R1")
    assert tier == "R3"
    raised = [source for source in sources if source["source"] == "objective"]
    assert raised and "model" in raised[-1]["why"]
    assert "mentions" not in raised[-1]["why"]


def test_model_moderate_signal_is_not_lost_in_the_classifiers_non_signal_default():
    from g6.workforce.acceptance import derive
    with semantic.request(llm=FakeLLM(default='{"value":"R1"}')):
        assert derive("Unpick the puzzle", "R0")[0] == "R1"
    with semantic.request(llm=FakeLLM(default='{"value":"R0"}')):
        assert derive("Unpick the puzzle", "R0")[0] == "R0"


@pytest.mark.parametrize("text", ["truncate the database", "rotate the API keys", "rm -rf the folder"])
def test_shared_floor_retains_risk_regex_forms(text):
    from g6.core.risk_signals import classify
    with semantic.request(llm=None):
        assert classify(text)[0] == "R3"


def test_added_short_risk_words_do_not_match_inside_harmless_words():
    from g6.core.risk_signals import classify
    with semantic.request(llm=None):
        assert classify("Brainstorm ten names for a coffee brand")[0] == "R0"


def test_canonical_union_control_detects_removal_of_all_send_floors(monkeypatch):
    from g6.core import risk_terms
    from tests.workforce.test_workforce_standard import _standard, ASSURANCE, wa, wp
    row = next(row for row in ASSURANCE if row[0] == "send marketing email")
    with semantic.request(llm=None):
        assert wa.risk_code(_standard(*row[1:4])) == "R2"
        monkeypatch.setattr(risk_terms, "TIER_TERMS", tuple((tier, tuple(t for t in terms if t not in {"send", "email"}), why)
                                                          for tier, terms, why in risk_terms.TIER_TERMS))
        monkeypatch.setitem(wp.EFFECT_TIERS, "external", "R0")
        assert wa.risk_code(_standard(*row[1:4])) == "R0"


def test_selector_safety_suggestions_only_add():
    from g6.core.selector import select
    with semantic.request(llm=None):
        floor = select("Nuke the tenant's universe", phase="plan")
    with semantic.request(llm=FakeLLM(default='{"value":["action_safety"]}')):
        result = select("Nuke the tenant's universe", phase="plan")
    assert any(m.get("kind") == "safety_checks" and m["by"] == "both" for m in result["mechanisms"])
    candidates = next(m for m in result["mechanisms"] if m["stage"] == "candidates")
    assert "action_safety" in candidates["intents"]
    assert "semantic" in result["warning_card"]["message"]
    assert floor["completion_state"] == result["completion_state"]


def test_selector_metadata_reports_optional_model():
    from g6.skills.corpus import TOOL_DATA
    assert TOOL_DATA["g6_select"]["model"] == "optional"
