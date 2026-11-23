# SPDX-License-Identifier: Apache-2.0
"""Advisory guidance preserves lexical records and the existing reply envelope."""
import json

import pytest

from g6.core import semantic
from g6.core.llm import FakeLLM
from g6.grounding import GroundingBlock, GroundingInput
from g6.grounding.coverage import coverage
from g6.grounding.retrieval import TextRetriever
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

SOURCE = "You can return items within thirty days of purchasing them."
CLAIM = "Customers have a month to return the product"
CODE = "G6_E_GROUNDING_LEXICAL_ONLY"


def test_engine_guidance_keeps_the_lexical_coverage_record(monkeypatch):
    monkeypatch.setenv("G6_GROUNDING_ENGINE", "lexical")
    with semantic.request(llm=None):
        expected = coverage(CLAIM + ".", TextRetriever.from_texts({"policy": SOURCE})).to_dict()
    fake = FakeLLM(default='{"value":[]}')
    with semantic.request(llm=fake):
        out = GroundingBlock(llm=fake).infer(GroundingInput(
            op="coverage", text=CLAIM + ".", sources={"policy": SOURCE})).unwrap()
    assert out["coverage"] == expected
    assert out["coverage"]["claims"][0]["status"] == "UNVERIFIABLE"
    assert out["warning_card"]["code"] == "G6_E_GROUNDING_UNSUPPORTED"
    assert any(c["code"] == CODE for c in out["warning_card"]["warnings"])
    assert not fake.calls


@pytest.mark.parametrize("op", ["check_claim", "check_claims", "coverage"])
@pytest.mark.parametrize("claim", [SOURCE, CLAIM])
def test_lexical_warning_is_complete_before_the_mcp_door(monkeypatch, op, claim):
    monkeypatch.setenv("G6_GROUNDING_ENGINE", "lexical")
    params = {"op": op, "claim": claim, "text": claim, "sources": {"policy": SOURCE}}
    with semantic.request(llm=None):
        direct = GroundingBlock().infer(GroundingInput(**params)).unwrap()
        out = invoke_reply("grounding", op, json.dumps(params))
    card = direct["warning_card"]
    assert card["risks"] and "misses paraphrases" in " ".join(card["risks"])
    assert out["warning_card"] == out["result"]["warning_card"] == card
    assert out["completion_state"] == direct["completion_state"]
    assert out["evidence"] == direct.get("evidence", [])
    assert isinstance(out["request_id"], str) and out["request_id"]
    assert out["run_id"] == direct.get("run_id", "")
    assert out["ok"] is (direct["completion_state"] != "blocked-escalated")
    assert check_envelope(out) == []
