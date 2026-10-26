# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from g6.grounding import Passage, Retriever, TextRetriever, check_claim, check_claims, split_claims

POLICY = {
    "refunds.md": "Refunds are issued within 14 days of a return.\n\nStore credit never expires.",
    "shipping.md": "Orders over $50 ship free. Standard delivery takes 3 to 5 business days.",
    "ml.md": "Attention computes weighted sums over values.",
}


@pytest.fixture
def retriever():
    return TextRetriever.from_texts(POLICY)


def test_retriever_ranks_relevant_source_first(retriever):
    assert isinstance(retriever, Retriever)
    hits = retriever.search("how long do refunds take", 2)
    assert hits and hits[0].source == "refunds.md" and hits[0].score_scale == "bm25"
    assert retriever.search("zebra quantum") == []


def test_supported_claim_is_qualified_draft_never_verified(retriever):
    r = check_claim("Refunds are issued within 14 days", retriever)
    assert r.status == "quoted" and r.completion_state == "qualified-draft" and r.source == "refunds.md"
    r = check_claim("Refund issued within 14 days of return", retriever)
    assert r.status in ("quoted", "supported") and r.completion_state != "verified"


def test_grd_t01_negation_pair_is_a_conflict(retriever):
    """Audit 014 GRD-001: relevance must not certify a claim's negation."""
    r = check_claim("Attention never computes weighted sums over values", retriever)
    assert r.status == "conflict" and r.completion_state == "blocked-escalated"
    r = check_claim("Store credit expires", retriever)
    assert r.status == "conflict"


def test_wrong_number_is_a_conflict(retriever):
    r = check_claim("Refunds are issued within 30 days", retriever)
    assert r.status == "conflict" and "30" in r.conflicts[0]


def test_number_absent_from_source_is_not_support():
    r = check_claim("Revenue grew 40% last year", [Passage("Revenue grew strongly last year.", "a")])
    assert r.status == "partial" and "40%" in r.missing_terms


def test_invented_fact_is_not_found(retriever):
    r = check_claim("Customers receive a free umbrella with every order", retriever)
    assert r.status in ("not_found", "partial") and r.completion_state == "blocked-escalated"


def test_judge_can_only_make_it_stricter(retriever):
    assert check_claim("Refunds are issued within 14 days", retriever,
                       judge=lambda c, s: "contradicts").status == "conflict"
    assert check_claim("Refunds are issued within 14 days", retriever,
                       judge=lambda c, s: "neutral").status == "partial"
    r = check_claim("Customers receive a free umbrella", retriever, judge=lambda c, s: "entails")
    assert r.completion_state == "blocked-escalated"


def test_grd008_provenance_is_who_supplied_the_text_and_no_freshness_is_claimed(retriever):
    """Audit 014 GRD-008: provenance is never defaulted to a vetted corpus, and no freshness score is asserted."""
    hits = retriever.search("how long do refunds take", 3)
    assert hits and {h.provenance for h in hits} == {"caller_supplied"}
    assert not any("fresh" in key for key in check_claim("Refunds are issued within 14 days", retriever).to_dict())


def test_grd009_one_high_scoring_passage_sharing_one_term_is_not_support():
    """Audit 014 GRD-009: support is judged on one scale (claim terms in a source sentence), not on the retriever's
    raw score; one weak passage is not coverage, and the caller cannot pick a lower bar."""
    import inspect

    r = check_claim("Refund requests are approved by the finance team within a week",
                    [Passage("Refund policy.", "a", score=12.0)])
    assert r.status == "not_found" and r.completion_state == "blocked-escalated"
    assert "stakes" not in inspect.signature(check_claim).parameters


def test_split_and_check_many(retriever):
    claims = split_claims("# Summary\n- Orders over $50 ship free.\n- Delivery takes 3 to 5 business days.\nOk.")
    assert claims == ["Orders over $50 ship free", "Delivery takes 3 to 5 business days"]
    results = check_claims("Orders over $50 ship free. Delivery takes 10 business days.", retriever)
    assert [r.status for r in results][0] in ("quoted", "supported") and results[1].status == "conflict"


def test_from_folder(tmp_path):
    (tmp_path / "a.md").write_text("The warranty lasts two years.")
    (tmp_path / "skip.bin").write_bytes(b"\x00")
    r = TextRetriever.from_folder(tmp_path)
    assert len(r) == 1 and check_claim("The warranty lasts two years", r).status == "quoted"


def test_block_through_the_registry():
    from g6.core import discovery
    from g6.core.registry import get_registry

    reg = get_registry()
    ok = reg.invoke("grounding", "check_claim", {"claim": "Refunds are issued within 30 days", "sources": POLICY})
    out = ok.unwrap()
    assert out["check"]["status"] == "conflict" and out["completion_state"] == "blocked-escalated"
    many = reg.invoke("grounding", "check_claims", {"text": "Orders over $50 ship free.", "sources": POLICY}).unwrap()
    assert many["completion_state"] == "qualified-draft"
    assert reg.invoke("grounding", "check_claim", {"claim": "x y z"}).__class__.__name__ == "Failure"
    assert discovery.ops("grounding") == ["check_claim", "check_claims", "coverage"]
    assert discovery.describe("grounding")["maturity"] == "beta"
    assert discovery.describe("evidence")["maturity"] == "beta"


def test_a_quote_that_wraps_across_a_line_break_is_still_quoted():
    # found by G6's own premortem bundle on a real plan (2026-10-19): a sentence wrapped across two lines was split in
    # two, so a word-for-word quote of it came back "partial" and the bundle called it "not in your plan"
    plan = ("3. Then port the other 150 archive components (about 657k lines) in\nthe census's batch order, rebuilding "
            "each with real depth.\n4. A failure map.")
    claim = ("Then port the other 150 archive components (about 657k lines) in the census's batch order, rebuilding each "
             "with real depth.")
    assert check_claim(claim, [Passage(plan, "plan")]).status == "quoted"


def test_a_word_inside_a_longer_word_is_not_a_quote():
    assert check_claim("art", [Passage("We start at noon.", "s")]).status != "quoted"
