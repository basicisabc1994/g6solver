# SPDX-License-Identifier: Apache-2.0
"""K7 (audit 2026-10-23, csf): ``g6.csf`` declared ``improvised_risky_behaviour`` ("an agent improvising in a known risky
situation") as guarded, on the words "an approved cap only tightens epsilon; it never selects a verifier or runs
actions". Nothing in the package watches what an agent does in a risky situation, and no test fails if anything stopped
catching it: ``tighten`` only returns a smaller epsilon (``test_merge_epsilon_policy.py``). It declares what its code
and tests back: ``unanalysed_hazard`` and ``model_reality_gap``."""
from __future__ import annotations

import json
from pathlib import Path

import g6.csf


def test_csf_declares_only_the_failure_modes_its_tests_catch() -> None:
    # fails if csf/__init__.py's __g6_mitigates__ or its skill claim improvised_risky_behaviour again without a check
    # (and a prove-it-fails test) that catches an agent improvising in a known risky situation
    assert g6.csf.__g6_mitigates__ == ("unanalysed_hazard", "model_reality_gap")
    skill = (Path(g6.csf.__file__).parent / "skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "improvised_risky_behaviour" not in skill


def test_the_site_catalogue_says_csf_strategy_is_a_cap_that_only_tightens() -> None:
    # fails if web/data/component_catalogue.json's csf_strategy summary again says g6.csf registers, approves or runs
    # named safety strategies: what merged is EpsilonPolicy and tighten (csf/policy.py), a cap a person approves
    catalogue = Path(__file__).resolve().parents[2] / "web" / "data" / "component_catalogue.json"
    rows = json.loads(catalogue.read_text(encoding="utf-8"))["components"]
    summary = next(r["summary"] for r in rows if r["name"] == "csf_strategy")
    assert "only ever lower" in summary and "no registry of named strategies" in summary
    assert "register, approve and run" not in summary
