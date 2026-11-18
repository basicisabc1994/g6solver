# SPDX-License-Identifier: Apache-2.0
import json
from types import SimpleNamespace

import pytest

from g6.core import semantic
from g6.core.llm import FakeLLM


def test_feasibility_paraphrase_can_only_raise():
    from g6.intent_engine.schema import IntentContract
    from g6.reliability_pipeline.feasibility import deterministic_classify
    contract = IntentContract(goal="Nuke the tenant's universe", success_criteria=["Tenant is gone"])
    with semantic.request(llm=None):
        assert deterministic_classify(contract).classification == "clear"
    with semantic.request(llm=FakeLLM(lambda s, u: json.dumps({"value": "risky" if json.loads(u)["kind"] == "feasibility"
                                                          else "R3"}))):
        assert deterministic_classify(contract).classification == "risky"
    with semantic.request(llm=FakeLLM(default='{"value":"clear"}')):
        assert deterministic_classify(IntentContract(goal="delete production data", success_criteria=["done"])).classification == "risky"


def test_feasibility_reader_receives_the_declared_attachment_fact():
    from g6.intent_engine.schema import IntentContract
    from g6.reliability_pipeline.feasibility import deterministic_classify
    seen = []

    def respond(system, user):
        body = json.loads(user)
        seen.append(json.loads(body["text"]))
        return json.dumps({"value": "R0" if body["kind"] == "risk" else "clear"})

    contract = IntentContract(goal="Use the attached document", success_criteria=["summarised"])
    with semantic.request(llm=FakeLLM(respond)):
        assert deterministic_classify(contract, context_attached=True).classification == "clear"
    assert seen and all(item.get("context_attached") is True for item in seen)


def test_failure_mode_relevance_paraphrase_and_closed_set():
    from g6.reliability_pipeline.failure_modes import relevant, SKILLS
    text = "An encore of a previously handled notification must leave the ledger alone"
    with semantic.request(llm=None):
        assert not relevant(text)
    with semantic.request(llm=FakeLLM(default=json.dumps({"value": [SKILLS[0].failure_id]}))):
        assert relevant(text) == (SKILLS[0],)
    with semantic.request(llm=FakeLLM(default='{"value":["invented"]}')):
        assert not relevant(text)


def test_plan_concerns_are_add_only():
    from g6.reliability_pipeline.plan import Plan, lexical_tripwire
    plan = Plan(goal="Nuke the tenant's universe")
    with semantic.request(llm=None):
        assert not lexical_tripwire(plan)
    with semantic.request(llm=FakeLLM(default='{"value":["unsafe_action"]}')):
        assert lexical_tripwire(plan)[0].category == "unsafe_action"
    with semantic.request(llm=FakeLLM(default='{"value":[]}')):
        assert lexical_tripwire(Plan(goal="delete production data"))


def test_literal_level_paraphrase_keeps_explicit_answer():
    from g6.intent_engine.elicitor import IntentElicitor
    assumptions = []
    args = ("I do this professionally every day", ("beginner", "intermediate", "expert"), "unknown", "expertise", assumptions)
    with semantic.request(llm=None):
        assert IntentElicitor._coerce_literal(*args) == "unknown"
    with semantic.request(llm=FakeLLM(default='{"value":"expert"}')):
        assert IntentElicitor._coerce_literal(*args) == "expert"
    with semantic.request(llm=FakeLLM(default='{"value":"invented"}')):
        assert IntentElicitor._coerce_literal(*args) == "unknown"


def test_grounding_retrieval_can_find_without_shared_tokens():
    from g6.grounding.retrieval import TextRetriever
    retriever = TextRetriever([("a", "Felines nap."), ("b", "Dogs bark.")])
    with semantic.request(llm=None):
        assert retriever.search("sleepy cats") == []
    with semantic.request(llm=FakeLLM(default='{"value":["0"]}')):
        assert retriever.search("sleepy cats")[0].source == "a"
    with semantic.request(llm=FakeLLM(default='{"value":["outside"]}')):
        assert retriever.search("sleepy cats") == []


def test_exemplar_rank_by_meaning_and_no_self_example():
    from g6.metaharness.exemplars import Bank
    bank = Bank([SimpleNamespace(input="Dogs bark.", expected="dog"), SimpleNamespace(input="Felines nap.", expected="cat")])
    with semantic.request(llm=None):
        assert bank.nearest("sleepy cats", 1)[0][1] == "dog"
    with semantic.request(llm=FakeLLM(default='{"value":["1"]}')):
        assert bank.nearest("sleepy cats", 1)[0][1] == "cat"
        assert all(text != "Felines nap." for text, _ in bank.nearest("Felines nap.", 2))


def test_compression_chooses_existing_sentence_and_preserves_offsets():
    from g6.context.compress import compress
    from g6.context.packet import ContextItem
    item = ContextItem(key="a", section="repository", value="Cats cats cats. Take insulin before dinner.")
    with semantic.request(llm=FakeLLM(default='{"value":["1"]}')):
        result = compress(item, 10)
    assert result.value == "Take insulin before dinner."
    assert result.metadata["source_offsets"] == [(16, 43)]
    assert result.trust == "untrusted"


def test_context_fusion_can_add_conflict_without_relaxing_floor():
    from g6.context.fuse import fuse
    from g6.context.packet import ContextItem
    a = ContextItem(key="a", section="repository", value="The client service cache is always always always always approved", source="a")
    b = ContextItem(key="b", section="repository", value="The client service cache is always always always always forbidden", source="b")
    with semantic.request(llm=None):
        assert len(fuse([[a, b]], 1000)) == 1
    with semantic.request(llm=FakeLLM(default='{"value":true}')):
        assert len(fuse([[a, b]], 1000)) == 2


def test_debate_interpretation_uses_shared_reader():
    from g6.debate.agreement import judge_agreement
    from g6.debate.debate import Transcript, Exchange
    transcript = Transcript("May we continue?", exchanges=(Exchange("a", "a", 1, "Go ahead."), Exchange("b", "b", 1, "Proceed.")))
    llm = FakeLLM(default='{"value":{"agreement_level":"strong","dominant_position":"Continue",'
                          '"dissenting_views":[],"rationale":"Same decision"}}')
    with semantic.request(llm=llm) as scope:
        result = judge_agreement(transcript, llm)
    assert result.level == "strong" and result.floor.level == "none"
    assert any(m["kind"] == "agreement" for m in scope.mechanisms)


def test_cloud_consent_is_checked_without_poisoning_reply(monkeypatch):
    from g6.core import cloud_consent as cloud
    from g6.core.llm import OpenAICompatibleCaller
    monkeypatch.setattr(cloud, "is_granted", lambda *a, **k: False)
    caller = OpenAICompatibleCaller(base_url="https://example.invalid/v1", model="fake", api_key="fake")
    monkeypatch.setattr("g6.core.llm._post_json", lambda *a, **k: pytest.fail("unconsented send"))
    with cloud.collect_sources() as capture, semantic.request(llm=caller):
        result = semantic.decide("effort", "hello", "simple", ("simple", "complex"))
    assert result.by == "floor" and "consent" in result.reason
    assert not capture.refused_provider


def test_public_safety_reports_include_semantic_mechanisms():
    from g6.coding_agent_gate.gate import preflight
    from g6.intent_engine.schema import IntentContract
    from g6.reliability_pipeline.feasibility import deterministic_assess
    from g6.reliability_pipeline.plan import Plan, critique
    with semantic.request(llm=None):
        gate = preflight("update the documentation").to_dict()
        feasibility = deterministic_assess(IntentContract(goal="update the documentation", success_criteria=["readable"]))
        plan = critique(Plan(goal="update the documentation for all callers"))
    assert gate["mechanisms"]
    assert feasibility.model_dump()["mechanisms"]
    assert plan.to_envelope()["mechanisms"]


def test_engine_fusion_adds_caution_for_opposed_paraphrases():
    from tests.knowledge.test_fuse import passage, hits, report
    from g6._context_engine._fuse import fuse
    left, right = passage("Access is sanctioned", "left"), passage("Entry is forbidden", "right")
    results = [(report("sqlite"), hits([left, right]), 1)]
    with semantic.request(llm=None):
        assert not fuse(results, top_k=2).cautions
    with semantic.request(llm=FakeLLM(default='{"value":true}')):
        reading = fuse(results, top_k=2)
        assert reading.cautions[0]["kind"] == "semantic"
        assert set(reading.cautions[0]["passages"]) == {left.id, right.id}


def test_mcp_component_reply_reports_readings_and_shares_one_budget(monkeypatch):
    from g6.core import door
    from g6.mcp import invoke_reply

    def component(*args, **kwargs):
        first = semantic.decide("effort", "first", "simple", ("simple", "complex"))
        second = semantic.decide("risk", "second", "R1", ("R0", "R1", "R2", "R3"))
        return {"ok": True, "result": [first.value, second.value], "completion_state": "qualified-draft"}

    monkeypatch.setattr(door, "invoke_checked", component)
    with semantic.request(llm=FakeLLM(default='{"value":"complex"}'), budget=1):
        reply = invoke_reply("synthetic", "synthetic", registry=object())
    assert reply["result"] == ["complex", "R1"]
    assert [m["by"] for m in reply["mechanisms"]] == ["model", "floor"]
