# SPDX-License-Identifier: Apache-2.0
"""A gated capability is never switched on without its recorded result (design 17.4: "a failed or inconclusive gate
stops advancement"): the view's admitted level in code equals what validation/workflow_value/decisions.json admits."""
from __future__ import annotations

import json
from pathlib import Path

from g6.harness.workflow import manifest as mf
from g6.harness.workflow import view

FOLDER = Path(__file__).resolve().parents[2] / "validation" / "workflow_value"


def _decisions() -> dict:
    return json.loads((FOLDER / "decisions.json").read_text(encoding="utf-8"))


def _entry(gate, row, decision="admit", simulated=False):
    return {"gate": gate, "row": row, "decision": decision, "simulated": simulated}


def test_admitted_level_equals_the_last_admitted_row_in_decisions_json():
    assert view.ADMITTED_LEVEL == mf.admitted_level(_decisions())
    # inventory over the levels: each level above the base is admitted by exactly one gate's row, in order
    above = view.LEVELS[view.LEVELS.index(mf.BASE_LEVEL) + 1:]
    assert tuple(level for _row, level in mf.ADMITS.values() if level != mf.BASE_LEVEL) == above
    assert all(row in mf.ROWS for row, _level in mf.ADMITS.values())


def test_only_a_real_admit_in_order_raises_the_level():
    g2, g3, g4 = _entry("G2", "self_friction"), _entry("G3", "diagnosis"), _entry("G4", "suggestions")
    assert mf.admitted_level({"entries": []}) == "narrow"
    assert mf.admitted_level({"entries": [g2]}) == "narrow"
    assert mf.admitted_level({"entries": [g2, g3]}) == "diagnosis"
    assert mf.admitted_level({"entries": [g2, g3, g4]}) == "suggestions"
    assert mf.admitted_level({"entries": [g3, g4]}) == "narrow"                         # G2 was skipped
    assert mf.admitted_level({"entries": [g2, g4]}) == "narrow"                         # G3 was skipped
    assert mf.admitted_level({"entries": [g2, _entry("G3", "diagnosis", simulated=True)]}) == "narrow"
    assert mf.admitted_level({"entries": [g2, g3, _entry("G3", "diagnosis", "repair")]}) == "narrow"   # the last run decides
    for held in ("hold", "repair"):
        assert mf.admitted_level({"entries": [g2, _entry("G3", "diagnosis", held), g4]}) == "narrow"


def test_tonights_entries_are_simulated_holds_bound_to_their_manifests():
    decisions = _decisions()
    assert decisions["base_level"] == "narrow" and decisions["entries"]
    for entry in decisions["entries"]:
        manifest = json.loads((FOLDER / "manifests" / f"{entry['gate']}.json").read_text(encoding="utf-8"))
        assert entry["manifest_sha256"] == mf.freeze(manifest)
        assert entry["simulated"] is True and entry["decision"] == "hold" and entry["status"] == "evidence_pending"
        assert entry["owed"] and entry["decision"] in mf.DECISIONS
        again = mf.decide(manifest, entry["manifest_sha256"], entry["results"])["rows"][entry["row"]]
        assert again["decision"] == entry["decision"] and again["why"] == entry["why"]
