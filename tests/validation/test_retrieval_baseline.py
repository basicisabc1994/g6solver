# SPDX-License-Identifier: Apache-2.0
"""Row 03's lexical comparison: the baseline harness gives the same numbers on every run of the frozen labels, both
arms see the same files, and the result meets the protocol's lexical thresholds on the development labels."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "context_engine"))

import retrieval_baseline  # noqa: E402

LABELS = ROOT / "tests" / "knowledge" / "labels" / "retrieval.v1.json"
PROTOCOL = ROOT / "docs" / "measurements" / "context-engine" / "03-retrieval.protocol.md"


def thresholds() -> dict:
    text = PROTOCOL.read_text(encoding="utf-8")
    return json.loads(text[text.index("```json") + 7:text.rindex("```")])["thresholds"]


def test_baseline_scores_are_reproducible_on_the_frozen_labels(tmp_path):
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    first = retrieval_baseline.run(labels, tmp_path / "one")
    second = retrieval_baseline.run(labels, tmp_path / "two")
    assert first == second, "a fixed corpus, fixed labels and fixed tie-breaks give the same result"
    assert first["fts5"]["queries"] == first["substring"]["queries"] == len(labels["queries"]) == 53
    assert first["fts5"]["files_seen"] == first["substring"]["files_seen"] == 49
    limits = thresholds()
    gap = first["fts5"]["recall_at_10"] - first["substring"]["recall_at_10"]
    assert gap >= limits["fts5_recall_at_10_minus_substring_recall_at_10_min"]
    assert first["fts5"]["unsupported_citations"] == limits["fts5_unsupported_citations"] == 0
    assert first["substring"]["unsupported_citations"] == 0
    assert retrieval_baseline.admitted(first) == (True, [])
    assert "not independently written" in first["labelled_by"], "a development-cohort result says what its labels are"


def test_the_admission_rule_fails_a_weaker_engine_and_an_unsupported_citation():
    good = {"fts5": {"recall_at_10": 0.9, "unsupported_citations": 0, "files_seen": 49},
            "substring": {"recall_at_10": 0.9, "unsupported_citations": 0, "files_seen": 49}}
    assert retrieval_baseline.admitted(good)[0] is True
    for change in ({"recall_at_10": 0.8}, {"unsupported_citations": 1}, {"files_seen": 48}):
        worse = {**good, "fts5": {**good["fts5"], **change}}
        met, why = retrieval_baseline.admitted(worse)
        assert met is False and len(why) == 1


def test_the_substring_scan_ranks_by_distinct_words_then_occurrences_then_path():
    files = {"b.md": "alpha beta", "a.md": "alpha alpha", "c.md": "gamma", "d.md": "alpha beta beta"}
    assert retrieval_baseline.substring_scan(files, "alpha beta", 10) == ["d.md", "b.md", "a.md"]
    assert retrieval_baseline.substring_scan(files, "delta", 10) == []
    assert retrieval_baseline.substring_scan(files, "alpha beta", 1) == ["d.md"]


def test_the_label_file_is_the_frozen_one():
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    assert labels["version"] == "retrieval.v1" and labels["unit"] == "file"
    assert len({q["id"] for q in labels["queries"]}) == 53
    assert sum(1 for q in labels["queries"] if not q["eligible_files"]) == 3
    assert len(hashlib.sha256(LABELS.read_bytes()).hexdigest()) == 64