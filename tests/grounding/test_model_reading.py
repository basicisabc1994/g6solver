# SPDX-License-Identifier: Apache-2.0
"""Advisory meaning cannot change grounding's deterministic result or its gates."""
import json
import subprocess
import sys
from types import ModuleType

import pytest

from g6.core import semantic
from g6.core.llm import FakeLLM
from g6.grounding import GroundingBlock, GroundingInput

CLAIM = "Customers have a month to return the product"
POLICY = "You can return items within thirty days of purchasing them."


def reply(reading="supports", quote=POLICY):
    return json.dumps({"value": [reading, quote]})


def run(raw, *, claim=CLAIM, sources=None, **kw):
    fake = FakeLLM(default=raw)
    with semantic.request(llm=fake):
        result = GroundingBlock().infer(GroundingInput(
            claim=claim, sources=sources or {"policy": POLICY}, judge="model", **kw)).value
    return result, fake


def test_month_paraphrase_is_still_blocked_beside_model_support():
    out, fake = run(reply())
    check = out["check"]
    assert check["status"] == "not_found"
    assert out["completion_state"] == check["completion_state"] == "blocked-escalated"
    assert out["warning_card"]["code"] == "G6_E_GROUNDING_NOT_FOUND"
    reading = check["model_reading"]
    assert reading["reading"] == "supports" and reading["quote_found"] is True
    assert reading["quote"] == POLICY and reading["lexical_on_quote"]["status"] == "not_found"
    assert len(fake.calls) == 1
    body = json.loads(fake.calls[0][1])
    assert body["kind"] == "grounding" and body["combine"] == "choose"
    assert {c["id"] for c in body["candidates"]} == {"supports", "contradicts", "unclear", POLICY}


@pytest.mark.parametrize("quote", ["Returns are allowed for a month.", POLICY.lower(), POLICY[:-1],
                                   "return items within thirty days"])
def test_fabricated_or_non_sentence_quote_is_unclear(quote):
    out, _ = run(reply(quote=quote))
    reading = out["check"]["model_reading"]
    assert reading["reading"] == "unclear" and reading["quote_found"] is False
    assert reading["lexical_on_quote"] is None


def test_quote_matching_normalises_only_whitespace():
    out, _ = run(reply(quote=POLICY.replace(" ", "\n\t")),
                 sources={"policy": POLICY.replace(" ", "  ")})
    assert out["check"]["model_reading"]["quote_found"] is True


def test_semicolon_is_not_permission_to_clip_a_source_sentence():
    source = "Returns are allowed; customers have thirty days."
    out, _ = run(reply(quote=source), sources={"policy": source})
    assert out["check"]["model_reading"]["quote_found"] is True
    clipped, _ = run(reply(quote="customers have thirty days."), sources={"policy": source})
    assert clipped["check"]["model_reading"]["quote_found"] is False


@pytest.mark.parametrize("claim,source", [
    ("Customers can return products within 60 days", "Customers can return products within 30 days."),
    ("Customers cannot return products", "Customers can return products."),
    ("The limit is 60 days", "The limit is thirty days."),
    ("An unrelated 60 widgets claim", "The policy allows 30 days."),
])
def test_numeric_and_negation_controls_veto_model_support(claim, source):
    out, _ = run(reply(quote=source), claim=claim, sources={"policy": source})
    reading = out["check"]["model_reading"]
    assert reading["quote_found"] is True
    assert reading["reading"] == "unclear" and reading["reason"] == "NUMBER_OR_NEGATION"


@pytest.mark.parametrize("raw", ["bad json", "null", '[]', '{"value":["supports"]}',
                                 '{"value":["certain", "x"]}', '{"value":["supports",4]}',
                                 '{"value":["unclear"],"status":"supported"}',
                                 '{"value":["supports","x","x"]}'])
def test_malformed_reply_abstains_without_exception(raw):
    out, _ = run(raw)
    assert out["check"]["status"] == "not_found"
    assert out["check"]["model_reading"]["reading"] == "unclear"


def test_model_contradiction_never_demotes_lexical_support():
    out, _ = run(reply("contradicts"), claim=POLICY)
    assert out["check"]["status"] == "quoted" and out["completion_state"] == "qualified-draft"
    assert out["check"]["model_reading"]["reading"] == "contradicts"


def test_many_claims_share_budget_and_keep_execution_identity():
    fake = FakeLLM(default=reply())
    text = CLAIM + ". A second claim about an umbrella."
    with semantic.request(llm=fake, budget=1):
        out = GroundingBlock().infer(GroundingInput(
            op="check_claims", text=text, sources={"policy": POLICY}, judge="model")).value
    assert len(fake.calls) == 1 and len(out["checks"]) == 2
    assert [c["model_reading"]["reading"] for c in out["checks"]] == ["supports", "unclear"]
    assert {c["execution_id"] for c in out["checks"]} == {out["execution_id"]}


def test_registry_and_mcp_use_the_declared_guarded_model_door(monkeypatch):
    import g6.grounding as grounding
    from g6.core.registry import get_registry
    from g6.mcp import invoke_reply

    assert grounding.__g6_model__ == "llm"
    fake = FakeLLM(default=reply())
    params = {"claim": CLAIM, "sources": {"policy": POLICY}, "judge": "model"}
    out = get_registry().invoke("grounding", "check_claim", params, llm=fake).unwrap()
    assert out["check"]["model_reading"]["reading"] == "supports"
    monkeypatch.setattr("g6.core.door.person_model", lambda: fake)
    out = invoke_reply("grounding", "check_claim", json.dumps(params))
    assert out["ok"] is False and out["completion_state"] == "blocked-escalated"
    assert out["result"]["check"]["model_reading"]["reading"] == "supports"


def test_cloud_without_grant_abstains_before_send(monkeypatch):
    from g6.core.consent_callers import Destination

    fake = FakeLLM(default=reply())
    monkeypatch.setattr(semantic, "destination", lambda caller: Destination("cloud:unknown", False))
    with semantic.request(llm=fake):
        out = GroundingBlock().infer(GroundingInput(
            claim=CLAIM, sources={"policy": POLICY}, judge="model")).value
    assert not fake.calls
    assert out["check"]["model_reading"]["reading"] == "unclear"
    assert out["check"]["model_reading"]["reason"] == "consent not given"


def test_disabled_and_missing_models_abstain(monkeypatch):
    fake = FakeLLM(default=reply())
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    out, _ = run(reply())
    assert out["check"]["model_reading"]["reading"] == "unclear"
    monkeypatch.delenv("G6_DISABLE_LLM")
    with semantic.request(llm=None):
        out = GroundingBlock(llm=fake).infer(GroundingInput(
            claim=CLAIM, sources={"policy": POLICY}, judge="model")).value
    assert not fake.calls and out["check"]["model_reading"]["reading"] == "unclear"


@pytest.mark.parametrize("op", ["check_claim", "check_claims", "coverage", "help", "ops"])
def test_default_reply_bytes_match_c5782df_even_with_ambient_model(op, monkeypatch):
    # Execute only the two historical modules needed for a real differential;
    # no checkout, source edit, or baseline approximation.
    old_retrieval = ModuleType("grounding_lexical_reference")
    monkeypatch.setitem(sys.modules, old_retrieval.__name__, old_retrieval)
    def before(path):
        return subprocess.run(["git", "show", "c5782df:" + path], check=True,
                              capture_output=True, encoding="utf-8").stdout
    exec(compile(before("src/g6/grounding/retrieval.py"), "<reference-retrieval>", "exec"), old_retrieval.__dict__)
    block_module = ModuleType("grounding_block_reference")
    monkeypatch.setitem(sys.modules, block_module.__name__, block_module)
    old_block = block_module.__dict__
    source = before("src/g6/grounding/grounding_block.py").replace(
        "from g6.grounding.retrieval import TextRetriever", "from grounding_lexical_reference import TextRetriever")
    exec(compile(source, "<reference-block>", "exec"), old_block)
    params = {"op": op, "claim": CLAIM, "text": CLAIM + ".", "sources": {"policy": POLICY}}
    expected = old_block["GroundingBlock"]().infer(old_block["GroundingInput"](**params)).value
    fake = FakeLLM(default='{"value":[]}')  # would erase retrieved evidence if it reached retrieval
    with semantic.request(llm=fake):
        actual = GroundingBlock().infer(GroundingInput(**params)).value
        explicit = GroundingBlock().infer(GroundingInput(**params, judge="lexical")).value
    assert json.dumps(actual) == json.dumps(expected) == json.dumps(explicit)
    assert not fake.calls


def test_coverage_remains_lexical_with_model_selected():
    fake = FakeLLM(default=reply())
    data = {"op": "coverage", "text": POLICY, "sources": {"policy": POLICY}}
    with semantic.request(llm=fake):
        expected = GroundingBlock().infer(GroundingInput(**data)).value
        actual = GroundingBlock().infer(GroundingInput(**data, judge="model")).value
    assert actual == expected and not fake.calls


@pytest.mark.parametrize("judge", [None, True, 1, "MODEL", "unknown"])
def test_judge_is_strictly_validated(judge):
    from g6.core.registry import get_registry
    out = get_registry().invoke("grounding", "check_claim", {"claim": CLAIM, "sources": {"p": POLICY}, "judge": judge})
    assert out.__class__.__name__ == "Failure" and "G6_E_INPUT_VALIDATION" in str(out.failure())
