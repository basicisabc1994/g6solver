# SPDX-License-Identifier: Apache-2.0
"""``corroboration`` and ``check_staleness`` (audit 014: GRD-010, GRD-017), with survival controls.

The archive cannot run here; each regression test says in its docstring what the archive did on the same input. Each
``test_null_*`` puts that archive behaviour back in memory (monkeypatching only) and shows the regression test fails.
"""
from __future__ import annotations

import inspect
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

import g6.grounding
from g6.grounding import (Passage, TextRetriever, check_claim, check_staleness, corroboration, lineage,
                          staleness)

CLAIM = "Refunds are issued within 14 days"
TODAY = date(2026, 10, 18)
_DOI = re.compile(r"\b10\.\d{4,9}/")


def _fails(test, *args) -> None:
    """The regression test ``test`` fails."""
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        test(*args)


# --- archive behaviours, for the null-instrument checks ---------------------------------------------------------------

def _archive_lineage(passage: Passage) -> str:
    """The archive's ``_independence_group`` (epistemic.py:54-59): the URL host, else the raw source string."""
    return urlparse(passage.source).netloc or passage.source.strip()


def _archive_stance(claim, passage, judge) -> str:
    """The archive's ``_to_record`` (epistemic.py:81): ``supports_or_opposes="supports"`` for every record."""
    return "supports"


def _archive_status(supporting, opposing) -> str:
    """The archive's grade -> status (epistemic.py:36-41, :73 and grade_evidence): a DOI made a lineage primary, and a
    primary lineage or two lineages read SUPPORTED."""
    if supporting and opposing:
        return "CONTESTED"
    primary = any(_DOI.search(p.source) for members in supporting.values() for p in members)
    if primary or len(supporting) >= 2:
        return "SUPPORTED"
    return "ASSERTED" if supporting else "UNVERIFIABLE"


_DECLARED = staleness._declared_date


def _archive_year_cap(passage: Passage):
    """The archive's plausible-year window (staleness.py:193): years after 2030 were not read."""
    found = _DECLARED(passage)
    return found if found is not None and 1900 <= found.year <= 2030 else None


def _archive_label_date(passage: Passage):
    """The archive's ``_extract_date_from_source`` (staleness.py:178-195): a year read out of the citation label."""
    s = passage.source
    m = re.search(r"(\d{4})\s*$", s) or re.search(r"\((\d{4})\)", s) or re.search(r",\s*(\d{4})", s)
    return date(int(m.group(1)), 1, 1) if m and 1900 <= int(m.group(1)) <= 2030 else None


# --- survival controls ------------------------------------------------------------------------------------------------

def test_two_independent_lineages_agreeing_still_read_as_corroborated() -> None:
    passages = [Passage("Refunds are issued within 14 days.", "https://shop.example.com/policy"),
                Passage("Refunds are issued within 14 days of purchase.", "https://www.consumer.example.org/guide")]
    r = corroboration(CLAIM, passages)
    assert (r.status, r.completion_state) == ("SUPPORTED", "qualified-draft")
    assert r.supporting_lineages == ("host:consumer.example.org", "host:shop.example.com")
    assert r.opposing_lineages == () and r.passages_considered == 2
    # a caller who knows two local files are independent can say so; by default they share the corpus lineage
    files = TextRetriever.from_texts({"regulator.md": "Refunds are issued within 14 days of a return.",
                                      "retailer.md": "Refunds are issued within 14 days, always."})
    assert corroboration(CLAIM, files, lineage=lambda p: p.source).status == "SUPPORTED"
    assert corroboration(CLAIM, files).status == "ASSERTED"


def test_a_passage_that_does_not_bear_on_the_claim_is_not_counted() -> None:
    r = corroboration(CLAIM, [Passage("Orders over $50 ship free.", "https://shop.example.com/shipping")])
    assert (r.status, r.completion_state, r.supporting_lineages, r.opposing_lineages) == (
        "UNVERIFIABLE", "blocked-escalated", (), ())
    assert corroboration(CLAIM, []).status == "UNVERIFIABLE"


def test_a_dated_source_within_its_window_is_not_stale() -> None:
    r = check_staleness([Passage("x", "a", published_at="2026-03-01"),
                         Passage("x", "b", published_at="2026"),
                         Passage("x", "c", published_at="2026-10-18T09:30:00+10:00"),
                         Passage("x", "d", published_at="2024-10-18"),
                         Passage("x", "e", published_at="2019-05")], today=TODAY)
    assert [a.status for a in r.sources] == ["fresh", "fresh", "fresh", "stale", "expired"]
    assert [a.freshness for a in r.sources] == [1.0, 1.0, 1.0, 0.75, 0.0]
    assert r.sources[2].age_days == 1          # 09:30 in UTC+10 is the 17th in UTC
    fresh = check_staleness([Passage("x", "a", published_at="2026-03-01")], today=TODAY)
    assert fresh.all_fresh and fresh.counts == {"fresh": 1, "stale": 0, "expired": 0, "unknown": 0}
    # the thresholds are the caller's, never a window the source declares for itself
    assert check_staleness([Passage("x", "a", published_at="2026-03-01")], today=TODAY,
                           warn_days=30).sources[0].status == "stale"
    with pytest.raises(ValueError):
        check_staleness([], warn_days=10, expire_days=5)


# --- GRD-010: support is counted in lineages, opposition is counted, and the report says it is a proxy ---------------

def test_grd010_two_hits_from_different_files_of_one_repo_corpus_are_not_supported(tmp_path: Path) -> None:
    """Archive: the independence group was the URL host, else the raw source string (epistemic.py:54-59), so two
    files of one repository corpus were two lineages, and two lineages read SUPPORTED."""
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "refunds.md").write_text("Refunds are issued within 14 days of a return.", encoding="utf-8")
    (tmp_path / "docs" / "faq.md").write_text("Refunds are issued within 14 days, as the policy says.",
                                              encoding="utf-8")
    r = corroboration(CLAIM, TextRetriever.from_folder(tmp_path))
    assert r.status != "SUPPORTED"
    assert (r.status, r.completion_state) == ("ASSERTED", "blocked-escalated")
    assert r.supporting_lineages == ("corpus:caller_supplied",) and r.evidence["passages_supporting"] == 2


def test_grd010_a_doi_alone_is_not_primary_evidence() -> None:
    """Archive: any DOI made a source primary (epistemic.py:73), and one primary lineage read SUPPORTED."""
    one = [Passage("Attention computes weighted sums over values.", "https://doi.org/10.48550/arXiv.1706.03762")]
    r = corroboration("Attention computes weighted sums over values", one)
    assert (r.status, r.completion_state) == ("ASSERTED", "blocked-escalated")
    two = [*one, Passage("Attention computes weighted sums over values.", "doi:10.1038/nature14539")]
    assert corroboration("Attention computes weighted sums over values", two).supporting_lineages == ("host:doi.org",)


def test_grd010_a_contradicting_source_makes_it_contested() -> None:
    """Archive: every record was stamped ``supports`` (epistemic.py:81), so CONTESTED could not be reached and these
    three sources read SUPPORTED."""
    passages = [Passage("Refunds are issued within 14 days.", "https://shop.example.com/policy"),
                Passage("Refunds are issued within 14 days of purchase.", "https://consumer.example.org/guide"),
                Passage("Refunds are issued within 30 days.", "https://forum.example.net/t/1")]
    r = corroboration(CLAIM, passages)
    assert (r.status, r.completion_state) == ("CONTESTED", "blocked-escalated")
    assert r.opposing_lineages == ("host:forum.example.net",)
    assert r.supporting_lineages == ("host:consumer.example.org", "host:shop.example.com")
    assert r.evidence["passages_opposing"] == 1
    assert corroboration("Store credit expires",
                         [Passage("Store credit never expires.", "https://shop.example.com/credit")]).status == "CONTESTED"
    # a judge can only add opposition
    assert corroboration(CLAIM, passages[:2], judge=lambda c, s: "contradicts").status == "CONTESTED"


def test_grd010_the_report_says_it_is_a_lexical_proxy_and_never_verified() -> None:
    r = corroboration(CLAIM, [Passage("Refunds are issued within 14 days.", "https://shop.example.com/policy"),
                              Passage("Refunds are issued within 14 days.", "https://consumer.example.org/guide")])
    assert r.evidence["method"] == "lexical_retrieval_proxy" and r.evidence["oracle_category"] == "HEURISTIC_REVIEW"
    assert r.evidence["not_established"].startswith("truth;")
    assert r.evidence["sources_by_lineage"] == {"host:consumer.example.org": ["https://consumer.example.org/guide"],
                                                "host:shop.example.com": ["https://shop.example.com/policy"]}
    assert r.to_dict()["supporting_lineages"] == ["host:consumer.example.org", "host:shop.example.com"]
    contested = corroboration(CLAIM, [Passage("Refunds are issued within 30 days.", "https://forum.example.net/t")])
    states = {report.completion_state for report in (r, contested, corroboration(CLAIM, []))}
    assert states == {"qualified-draft", "blocked-escalated"} and set(lineage.STATUSES) == {
        "SUPPORTED", "ASSERTED", "CONTESTED", "UNVERIFIABLE"}


def test_null_grd010_regressions_fail_on_the_archive_proxy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lineage, "_lineage", _archive_lineage)
    _fails(test_grd010_two_hits_from_different_files_of_one_repo_corpus_are_not_supported, tmp_path)
    monkeypatch.undo()
    monkeypatch.setattr(lineage, "_status", _archive_status)
    _fails(test_grd010_a_doi_alone_is_not_primary_evidence)
    monkeypatch.undo()
    monkeypatch.setattr(lineage, "_stance", _archive_stance)
    _fails(test_grd010_a_contradicting_source_makes_it_contested)


# --- GRD-017: dates from the source's own metadata, no year cap, never assumed fresh ----------------------------------

def test_grd017_a_source_dated_after_2030_is_read() -> None:
    """Archive: years outside 1900-2030 were not read (staleness.py:193), so this source was unknown-age."""
    a = check_staleness([Passage("x", "report", published_at="2031-06-01")], today=date(2032, 1, 10)).sources[0]
    assert (a.status, a.age_days, a.freshness) == ("fresh", 223, 1.0)


def test_grd017_the_date_comes_from_the_sources_metadata_not_its_label() -> None:
    """Archive: the year was read out of the citation label (staleness.py:178-195), so "Smith et al., 2015" counted
    as a 2015 source; a label is a name the caller chose."""
    r = check_staleness([Passage("x", "Smith et al., 2015")], today=TODAY)
    a = r.sources[0]
    assert (a.status, a.freshness, a.age_days, a.reason) == ("unknown", None, None, "no published_at")
    assert r.all_fresh is False
    dated = check_staleness([Passage("x", "Smith et al., 2015", published_at="2015-03-02")], today=TODAY).sources[0]
    assert (dated.status, dated.freshness) == ("expired", 0.0)


def test_grd017_an_undated_unreadable_or_future_source_is_never_assumed_fresh() -> None:
    r = check_staleness([Passage("x", "a"), Passage("x", "b", published_at="last spring"),
                         Passage("x", "c", published_at="2040-01-01")], today=TODAY)
    assert [(a.status, a.freshness) for a in r.sources] == [("unknown", None)] * 3
    assert [a.reason for a in r.sources] == ["no published_at", "published_at is not an ISO 8601 date",
                                             "published_at is after today"]
    assert r.counts["unknown"] == 3 and r.all_fresh is False and check_staleness([], today=TODAY).all_fresh is False
    assert r.to_dict()["sources"][0]["freshness"] is None
    # with no ``today`` it uses the current UTC date
    now = datetime.now(timezone.utc).date().isoformat()
    assert check_staleness([Passage("x", "a", published_at=now)]).sources[0].status == "fresh"


def test_null_grd017_regressions_fail_on_the_archive_dating(monkeypatch) -> None:
    monkeypatch.setattr(staleness, "_declared_date", _archive_year_cap)
    _fails(test_grd017_a_source_dated_after_2030_is_read)
    monkeypatch.setattr(staleness, "_declared_date", _archive_label_date)
    _fails(test_grd017_the_date_comes_from_the_sources_metadata_not_its_label)


def test_grd017_check_claim_is_unchanged_and_staleness_is_not_wired_into_it() -> None:
    """Study V1 measured ``check_claim``; a source's date must not move it."""
    text = "Refunds are issued within 14 days."
    assert check_claim(CLAIM, [Passage(text, "a")]) == check_claim(CLAIM, [Passage(text, "a", published_at="1990")])
    assert list(inspect.signature(check_claim).parameters) == ["claim", "sources", "top_k", "judge"]


def test_grd017_the_remote_block_and_skill_server_are_not_in_the_library() -> None:
    """The remote-block and skill-MCP parts of GRD-017 have no code here; they are recorded as not ported."""
    assert not hasattr(g6.grounding, "RemoteGroundingBlock")
    package = Path(g6.grounding.__file__).parent
    assert not (package / "remote_grounding.py").exists()
    # the archive's skill server was not ported; skill/ holds only the component's SKILL.md (package data, FW-01)
    assert sorted(p.name for p in (package / "skill").iterdir() if p.name != "__pycache__") == ["SKILL.md"]
    offered, _, not_ported = g6.grounding.__doc__.partition("Not ported from the archive")
    assert "remote grounding block" in not_ported and "skill MCP" in not_ported and "GRD-017" in not_ported
