# SPDX-License-Identifier: Apache-2.0
"""Row 07's paired comparison: every arm has the same information and the same budget, the scores are the same on
every run of the frozen labels, an arm that cannot run is pending and never empty, and a development result admits
nothing."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "context_engine"))

import planner_pairs  # noqa: E402

LABELS = ROOT / "tests" / "knowledge" / "labels" / "retrieval.v1.json"
PROTOCOL = ROOT / "docs" / "measurements" / "context-engine" / "07-planner.protocol.md"


def thresholds() -> dict:
    text = PROTOCOL.read_text(encoding="utf-8")
    return json.loads(text[text.index("```json") + 7:text.rindex("```")])["thresholds"]


def test_every_arm_has_equal_information_and_budget_and_the_scores_repeat(tmp_path):
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    first = planner_pairs.run(labels, tmp_path / "one")
    second = planner_pairs.run(labels, tmp_path / "two")
    ran = ("exact", "fixed_fusion", "planner")
    assert first["arms"] == second["arms"], "a fixed corpus, fixed labels and fixed rules give the same scores"
    arms = [first["arms"][name] for name in ran]
    assert all(arm["information"] == arms[0]["information"] and arm["budget"] == arms[0]["budget"] for arm in arms)
    assert arms[0]["information"] == {"generations": [1], "files_seen": 49, "sources": 1, "destination": "local"}
    assert arms[0]["budget"] == {"top_k": 10, "searches": 6, "model_calls": 0, "tokens": 8000}
    assert all(arm["queries"] == len(labels["queries"]) == 53 and arm["model_calls"] == 0 and arm["not_intact"] == 0
               for arm in arms)
    limits = thresholds()
    planner, fusion = first["arms"]["planner"], first["arms"]["fixed_fusion"]
    assert planner["recall_at_10"] - fusion["recall_at_10"] >= limits["planner_recall_at_10_minus_fixed_fusion_recall_at_10_min"]
    assert planner["searches"] - fusion["searches"] <= limits["planner_searches_minus_fixed_fusion_searches_max"]
    assert set(first["seconds"]) == set(ran), "time is reported apart from the scores"
    # an arm that cannot run here is pending, never a result of zero
    for name in ("dense", "graph"):
        assert first["arms"][name] == {"state": planner_pairs.PENDING[name]}
        assert first["arms"][name]["state"].startswith("evidence_pending")
    met, why = planner_pairs.admitted(first)
    assert met is False and any("development" in reason for reason in why), "a development cohort admits nothing"


def test_the_comparison_is_refused_when_an_arm_saw_other_information(tmp_path, monkeypatch):
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    labels = {**labels, "queries": labels["queries"][:2]}
    from g6.knowledge import _engine as engine
    from g6.knowledge import _planner
    from g6.knowledge._scope import access

    real = _planner.run

    def and_a_file_changes(acc, query, needs, **kw):
        out = real(acc, query, needs, **kw)
        note = sorted((tmp_path / "one" / "project" / "knowledge" / "docs").glob("*.md"))[0]
        note.write_text(note.read_text(encoding="utf-8") + "\nchanged during the comparison\n", encoding="utf-8")
        engine.refresh(access(acc.binding, "refresh", "local"))
        return out

    monkeypatch.setattr(_planner, "run", and_a_file_changes)
    with pytest.raises(SystemExit, match="same information and budget"):
        planner_pairs.run(labels, tmp_path / "one")
