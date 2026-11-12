# SPDX-License-Identifier: Apache-2.0
"""The frozen decision manifest (design 17.5 "Before any run"): a missing field blocks the run, thresholds come from
the manifest and never from results, too little evidence holds, no start leaves a denominator, and a simulated run
never admits anything."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from g6.harness.workflow import manifest as mf

FOLDER = Path(__file__).resolve().parents[2] / "validation" / "workflow_value"
GATES = ("G2", "G3", "G4", "G5")


def _gate(name: str = "G2") -> dict:
    return json.loads((FOLDER / "manifests" / f"{name}.json").read_text(encoding="utf-8"))


def _results(count=10, total=20, *, starts=20, simulated=False, **measures) -> dict:
    row = {"used_or_would_use": {"count": count, "n": total}, "added_wall_seconds_median": {"value": 0.5, "n": 20},
           "added_wall_seconds_worst": {"value": 1.5, "n": 20}, "added_tool_calls": {"value": 0, "n": 20},
           "added_questions": {"value": 0, "n": 20}, "added_block_lines": {"value": 6, "n": 20},
           "contract_mismatches": {"value": 0, "n": 14}, **measures}
    return {"simulated": simulated, "starts": {"eligible": starts, "failed": 2, "abandoned": 1, "missing": 0, "censored": 0},
            "rows": {"self_friction": row}}


def _decision(results, manifest=None) -> str:
    manifest = manifest or _gate()
    return mf.decide(manifest, mf.freeze(manifest), results)["rows"]["self_friction"]["decision"]


@pytest.mark.parametrize("gate", GATES)
def test_each_gates_manifest_is_complete_and_names_its_research_values(gate):
    manifest = _gate(gate)
    assert mf.problems(manifest) == [] and len(mf.freeze(manifest)) == 64
    assert set(manifest["rows"]) <= set(mf.ROWS) and manifest["gate"] == gate
    for row in manifest["rows"].values():
        for threshold in row["thresholds"]:
            assert threshold["source"].startswith(f"S17.{gate}."), threshold["name"]
            assert not {"minutes", "saved", "faster"} & set(threshold["name"].split("_"))     # no time-saved bar


@pytest.mark.parametrize("field", mf.REQUIRED)
def test_each_missing_field_blocks_the_run(field):
    for empty in (None, "", [], {}):
        manifest = {**_gate(), field: empty}
        assert any(field in problem for problem in mf.problems(manifest)), field
        with pytest.raises(mf.ManifestRefused):
            mf.freeze(manifest)
    manifest = _gate()
    del manifest[field]
    assert mf.problems(manifest)


@pytest.mark.parametrize("field", mf.THRESHOLD_FIELDS)
def test_threshold_without_a_unit_or_sample_requirement_blocks(field):
    manifest = _gate()
    del manifest["rows"]["self_friction"]["thresholds"][0][field]
    assert any(f"has no {field}" in problem for problem in mf.problems(manifest))
    for bad in ({"sample_requirement": 0}, {"sample_requirement": 2.5}, {"uncertainty_rule": "by eye"},
                {"value": "S17.G2.useful_min"}, {"value": None}, {"role": "nice"}, {"direction": "sideways"}):
        manifest = _gate()
        manifest["rows"]["self_friction"]["thresholds"][0].update(bad)
        assert mf.problems(manifest), bad
    manifest = _gate()
    manifest["rows"]["self_friction"]["thresholds"][0]["value"] = None      # a name the research does not define
    assert any("S17.G2.useful_min is not defined" in problem for problem in mf.problems(manifest))


def test_zero_usefulness_minimum_blocks():
    for change in (lambda t: t.update(value=0), lambda t: t.update(role="burden"), lambda t: t.update(direction="at_most")):
        manifest = _gate()
        for threshold in manifest["rows"]["self_friction"]["thresholds"]:
            if threshold["role"] == "usefulness":
                change(threshold)
        assert any("refusing everything could pass" in problem for problem in mf.problems(manifest))
    for handling in mf.HANDLING:
        manifest = _gate()
        del manifest["handling"][handling]
        assert any(handling in problem for problem in mf.problems(manifest))
    manifest = _gate()
    manifest["rows"]["everything"] = manifest["rows"]["self_friction"]
    assert "unknown row: everything" in mf.problems(manifest)
    assert mf.problems({**_gate(), "rows": {"self_friction": {"decision": "d", "thresholds": []}}})


def test_results_under_a_changed_manifest_are_refused():
    manifest = _gate()
    frozen = mf.freeze(manifest)
    assert mf.decide(manifest, frozen, _results())["manifest_sha256"] == frozen
    edited = copy.deepcopy(manifest)
    edited["rows"]["self_friction"]["thresholds"][0]["value"] = 0.2      # a bar lowered after the results were seen
    with pytest.raises(mf.ManifestRefused, match="not the frozen one"):
        mf.decide(edited, frozen, _results())
    import inspect

    assert "threshold" not in " ".join(inspect.signature(mf.decide).parameters)   # no bar is read from results
    assert _decision({**_results(), "thresholds": {"used_or_would_use": 0.0}, "rows": {"self_friction": {
        **_results(count=2)["rows"]["self_friction"], "value": 0.0}}}) == "repair"


def test_the_bars_decide_admit_repair_or_hold():
    assert _decision(_results(count=10)) == "admit"                       # 10 of 20: 0.50, lower bound 0.30
    assert _decision(_results(count=9)) == "repair"                       # under the point bar
    assert _decision(_results(count=3)) == "repair"
    assert _decision(_results(added_questions={"value": 1, "n": 20})) == "repair"
    assert _decision(_results(added_block_lines={"value": 9, "n": 20})) == "repair"
    assert _decision(_results(contract_mismatches={"value": 1, "n": 14})) == "repair"


def test_too_little_evidence_is_hold_not_admit():
    assert _decision(_results(count=19, total=19, starts=19)) == "hold"   # every mark useful, one task short
    nothing = _results(count=0, total=0, starts=0)
    nothing["starts"].update(failed=0, abandoned=0)
    assert _decision(nothing) == "hold"
    missing = _results()
    del missing["rows"]["self_friction"]["added_tool_calls"]
    assert _decision(missing) == "hold"                                   # a bar not measured is not a pass
    assert _decision({"simulated": False, "starts": {"eligible": 20}, "rows": {}}) == "hold"


def test_a_simulated_run_can_fail_a_row_and_never_admits_one():
    assert _decision(_results(count=20, simulated=True)) == "hold"
    decided = mf.decide(_gate(), mf.freeze(_gate()), _results(count=20, simulated=True))["rows"]["self_friction"]
    assert decided["why"][0] == mf.SIMULATED and "evidence_pending" in mf.SIMULATED
    assert _decision(_results(count=20, simulated=True, added_block_lines={"value": 9, "n": 20})) == "repair"


def test_failed_and_censored_starts_stay_in_the_denominator():
    with pytest.raises(mf.ManifestRefused, match="every eligible start"):
        _decision(_results(count=10, total=17, starts=20))                # the three failed starts were left out
    with pytest.raises(mf.ManifestRefused):
        _decision({**_results(), "starts": {"eligible": 20, "failed": 15, "abandoned": 6}})
    with pytest.raises(mf.ManifestRefused):
        _decision({**_results(), "starts": {"failed": 1}})
    with pytest.raises(mf.ManifestRefused):
        _decision(_results(used_or_would_use={"count": 21, "n": 20}))


def test_tuning_cases_cannot_be_evaluation_cases():
    manifest = {**_gate(), "cases": ["case-1", "case-2", "case-3"], "tuned_on": ["case-9", "case-2"]}
    assert "a case used to tune a policy is among the evaluation cases" in mf.problems(manifest)
    assert mf.problems({**manifest, "tuned_on": ["case-9"]}) == []


def test_an_interval_bar_repairs_only_when_the_interval_excludes_it():
    manifest = _gate("G3")
    frozen = mf.freeze(manifest)

    def decision(wrong, agreement=16):
        row = {"right_and_useful": {"count": 14, "n": 20}, "useful_hypothesis_of_all_tasks": {"count": 14, "n": 40},
               "wrong_of_shown": {"count": wrong, "n": 20}, "reviewer_agreement": {"count": agreement, "n": 20},
               "added_wall_seconds_median": {"value": 0.1, "n": 40}, "added_wall_seconds_worst": {"value": 0.2, "n": 40},
               "added_tool_calls": {"value": 0, "n": 40}, "added_questions": {"value": 0, "n": 40},
               "added_block_lines": {"value": 2, "n": 40}, "contract_mismatches": {"value": 0, "n": 20}}
        results = {"simulated": False, "starts": {"eligible": 40}, "rows": {"diagnosis": row}}
        return mf.decide(manifest, frozen, results)["rows"]["diagnosis"]["decision"]
    assert decision(3) == "admit" and decision(5) == "hold" and decision(7) == "repair"     # 3, 4 to 6, 7 or more of 20
    assert decision(3, agreement=13) == "hold"                            # the reviewers disagree: neither way
