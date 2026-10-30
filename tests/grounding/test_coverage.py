# SPDX-License-Identifier: Apache-2.0
"""g6.grounding.coverage (merged from the archive's knowledge_quality): how much of a draft rests on more than one
independent source, counted per claim with the lineage rule g6.grounding already uses, never from retrieval scores or
a table of host prestige; caller-supplied source weights are recorded as the caller's claim and change nothing."""
from __future__ import annotations

import json

import pytest

import g6.grounding
from g6.core.failure_modes import FailureModeRegistry
from g6.grounding import Passage, TextRetriever, check_claims, coverage
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

REFUND = "Refunds are issued within 14 days of a return."
GIFT = "Gift cards are not refundable."
DRAFT = f"{REFUND} {GIFT}"
ONE_HOST = {"https://www.SHOP.example/help": f"{REFUND} {GIFT}", "http://shop.example/faq": f"{REFUND} {GIFT}"}


# -- D1: a draft every sentence of which reads supported can still rest on one source ------------------------------------

def test_claims_backed_by_one_host_are_thin_however_many_pages_say_them() -> None:
    sources = TextRetriever.from_texts(ONE_HOST)
    assert [c.status for c in check_claims(DRAFT, sources)] == ["quoted", "quoted"]   # each sentence looks fine
    report = coverage(DRAFT, sources)
    assert [c["status"] for c in report.claims] == ["ASSERTED", "ASSERTED"]
    assert (report.covered, report.single_lineage, report.covered_share) == (0, 2, 0.0)
    assert report.completion_state == "blocked-escalated"
    assert report.warning_card["code"] == "G6_E_GROUNDING_THIN_SOURCES"
    widened = coverage(DRAFT, TextRetriever.from_texts({**ONE_HOST, "https://consumer-rights.example/refunds": REFUND}))
    assert [c["status"] for c in widened.claims] == ["SUPPORTED", "ASSERTED"] and widened.covered == 1
    assert widened.claims[0]["lineages"] == ["host:consumer-rights.example", "host:shop.example"]
    assert widened.completion_state == "blocked-escalated"


def test_every_claim_on_two_lineages_is_a_qualified_draft_never_verified() -> None:
    sources = TextRetriever.from_texts({"https://shop.example/help": DRAFT, "https://consumer-rights.example/a": DRAFT})
    report = coverage(DRAFT, sources)
    assert report.covered == 2 and report.covered_share == 1.0 and report.completion_state == "qualified-draft"
    assert report.warning_card is None and report.lineages == ["host:consumer-rights.example", "host:shop.example"]
    assert "truth" in report.evidence["not_established"]


def test_a_contradicting_source_makes_the_claim_contested_and_names_the_worst_code() -> None:
    sources = TextRetriever.from_texts({"https://shop.example/help": DRAFT, "https://consumer-rights.example/a": DRAFT,
                                        "https://forum.example/t/1": "Refunds are issued within 30 days of a return."})
    report = coverage(DRAFT, sources)
    assert report.claims[0]["status"] == "CONTESTED" and report.contested == 1
    assert report.claims[0]["opposing_lineages"] == ["host:forum.example"]
    assert report.warning_card["code"] == "G6_E_GROUNDING_CONTESTED"


def test_a_claim_with_no_source_is_unsupported() -> None:
    report = coverage(f"{REFUND} Shipping is free to every country.", TextRetriever.from_texts(ONE_HOST))
    assert [c["status"] for c in report.claims] == ["ASSERTED", "UNVERIFIABLE"] and report.unsupported == 1
    assert report.warning_card["code"] == "G6_E_GROUNDING_UNSUPPORTED"


# -- KNOWLEDGE_QUALITY-01: not a fixed threshold on retrieval scores -----------------------------------------------------

class _Scored:
    """A retriever whose scores sit on whatever scale its backend uses."""

    def __init__(self, scale: float) -> None:
        self.scale = scale

    def search(self, query: str, top_k: int = 3) -> list[Passage]:
        return [Passage(REFUND, "https://shop.example/help", score=0.01 * self.scale),
                Passage(REFUND, "https://consumer-rights.example/a", score=0.02 * self.scale)][:top_k]


@pytest.mark.parametrize("scale", [1.0, 1000.0])
def test_coverage_does_not_depend_on_the_scale_of_retrieval_scores(scale: float) -> None:
    report = coverage(REFUND, _Scored(scale))
    assert [c["status"] for c in report.claims] == ["SUPPORTED"] and report.completion_state == "qualified-draft"


# -- KNOWLEDGE_QUALITY-02 and G3: no prestige table, and a caller's weights are its claim -------------------------------

def test_no_host_counts_for_more_than_another_and_a_callers_weights_change_nothing() -> None:
    famous = {"https://en.wikipedia.org/wiki/Refund": REFUND}
    alone = coverage(REFUND, TextRetriever.from_texts(famous))
    weighted = coverage(REFUND, TextRetriever.from_texts(famous), weights={"https://en.wikipedia.org/wiki/Refund": 100})
    assert alone.claims[0]["status"] == weighted.claims[0]["status"] == "ASSERTED"
    assert alone.completion_state == weighted.completion_state == "blocked-escalated"
    assert weighted.claims[0]["caller_weights"] == {"https://en.wikipedia.org/wiki/Refund": 100.0}
    assert "caller's claim" in weighted.evidence["caller_weights"]
    paired = coverage(REFUND, TextRetriever.from_texts({**famous, "https://a-small-blog.example/p": REFUND}))
    assert paired.claims[0]["status"] == "SUPPORTED"   # a blog and an encyclopaedia are two lineages, equally
    with pytest.raises(ValueError, match="weight"):
        coverage(REFUND, TextRetriever.from_texts(famous), weights={"x": float("inf")})


def test_files_handed_in_together_are_one_lineage() -> None:
    report = coverage(REFUND, TextRetriever.from_texts({"policy.md": REFUND, "faq.md": REFUND}))
    assert report.claims[0]["status"] == "ASSERTED" and report.claims[0]["lineages"] == ["corpus:caller_supplied"]


# -- the block op ------------------------------------------------------------------------------------------------------

def test_coverage_is_an_op_of_the_block_and_round_trips_over_mcp() -> None:
    reply = invoke_reply("grounding", "coverage", json.dumps({"text": DRAFT, "sources": ONE_HOST,
                                                              "weights": {"http://shop.example/faq": 2}}))
    assert not reply["ok"] and check_envelope(reply) == [] and reply["completion_state"] == "blocked-escalated"
    result = reply["result"]
    assert result["op"] == "coverage" and result["warning_card"]["code"] == "G6_E_GROUNDING_THIN_SOURCES"
    assert result["coverage"]["single_lineage"] == 2 and "coverage" in invoke_reply("grounding", "ops")["result"]["ops"]
    refused = invoke_reply("grounding", "coverage", json.dumps({"text": DRAFT, "sources": ONE_HOST, "authority": 1}))
    assert refused["ok"] is False and refused["error"].startswith("[G6_E_INPUT_VALIDATION]")
    empty = invoke_reply("grounding", "coverage", json.dumps({"text": "   ", "sources": ONE_HOST}))
    assert empty["ok"] is False and empty["error"].startswith("[G6_E_GROUNDING_NO_CHECKABLE_SENTENCE]")


# -- the work is bounded before any retrieval, and a weight is a finite number ----------------------------------------

class _Counting:
    """A retriever that counts its searches and says how many passages it holds."""

    def __init__(self, size: int = 2) -> None:
        self.calls, self._size = 0, size

    def __len__(self) -> int:
        return self._size

    def search(self, query: str, top_k: int = 3) -> list[Passage]:
        self.calls += 1
        return [Passage(REFUND, "https://shop.example/help"), Passage(REFUND, "https://consumer-rights.example/a")]


def test_an_oversized_coverage_check_is_refused_before_any_retrieval() -> None:
    cases = [
        (REFUND + " " + "Refunds are issued quickly to every customer. " * 20_000, _Counting(), "text"),   # ~900 KB
        (" ".join(f"Claim number {i} is here." for i in range(1_000)), _Counting(), "claims"),          # 1,000 claims
        (REFUND, _Counting(size=100_000), "passages"),                                                   # 100,000 held
    ]
    for text, sources, what in cases:
        report = coverage(text, sources)
        assert sources.calls == 0, f"{what}: {sources.calls} retrieval call(s) before any refusal"
        assert report.completion_state == "blocked-escalated" and report.claims == ()
        assert report.warning_card["code"] in ("G6_E_GROUNDING_TEXT_TOO_LARGE", "G6_E_GROUNDING_SOURCES_TOO_LARGE")
        assert "at most" in report.warning_card["message"]
    fine = _Counting()
    assert coverage(REFUND, fine).completion_state == "qualified-draft" and fine.calls == 1   # within the caps


def test_a_weight_that_is_not_a_finite_number_is_an_input_error_over_mcp() -> None:
    params = json.dumps({"text": DRAFT, "sources": ONE_HOST, "weights": {"a": float("inf")}})
    assert "Infinity" in params
    reply = invoke_reply("grounding", "coverage", params)
    assert reply["ok"] is False and reply["error"].startswith("[G6_E_INPUT_VALIDATION]")
    many = json.dumps({"text": DRAFT, "sources": ONE_HOST, "weights": {f"s{i}": 1 for i in range(5_000)}})
    assert invoke_reply("grounding", "coverage", many)["error"].startswith("[G6_E_INPUT_VALIDATION]")


def test_d2_grounding_declares_weak_sources_and_no_longer_plans_it() -> None:
    assert "weak_sources" in g6.grounding.__g6_mitigates__
    assert "weak_sources" not in FailureModeRegistry.planned().get("g6.grounding", ())
