# SPDX-License-Identifier: Apache-2.0
"""V10's analysis of the primary measures (PROTOCOL.md on the verification search branch, "Measures", "Analysis",
"The level", "What the results permit saying"). Fake records, fake labels and fake reruns only: no study data."""
from __future__ import annotations

import math

import pytest

from patch_review_search import analysis, record
from patch_review_utility.dispositions import ACCEPT, DEFECT, ERROR, make

OK, NO = make(ACCEPT), make(DEFECT, "a check failed")
SEED = 4242


def task(name, repository, label, *, tests=True, self_review=True, g6=True, direct=True, patch="non_empty",
         oracle=True, seconds=10.0):
    def disposition(accepted):
        return dict(OK if accepted else NO)
    return {"task_id": name, "repository": repository, "patch": patch, "label": label,
            "oracle_controls_passed": oracle,
            "arms": {"tests": disposition(tests), "self_review": disposition(self_review),
                     "g6": {**disposition(g6), "cost": {"seconds": seconds, "processor_seconds": 1.0,
                                                        "model_calls": 0}},
                     "direct_checks": disposition(direct)}}


def outputs(*, c=True, t=True, e=True, c_own=None, t_own=None, e_filtered=None, a_and_e=None, calls=2, cents=4.0,
            seconds=6.0, c_error="", search_stage=None):
    def disposition(accepted):
        return dict(OK if accepted else NO)
    c_disposition = make(ERROR, "x", error_kind=c_error) if c_error else disposition(c)

    def search(found, own):
        return {"disposition": found, "own": disposition(found["accepted"] if own is None else own),
                "meter": {"calls": calls, "characters": 100, "cents": cents}, "model_calls": calls,
                "seconds": seconds, "processor_seconds": 1.0, "findings": [], "search_stage": search_stage}
    return {"C": search(c_disposition, c_own), "T": search(disposition(t), t_own),
            "E": {"disposition": disposition(e), "e_filtered": disposition(e if e_filtered is None else e_filtered),
                  "a_and_e": disposition(e if a_and_e is None else a_and_e),
                  "meter": {"calls": 1, "characters": 50, "cents": 1.0}, "model_calls": 1, "seconds": 2.0,
                  "processor_seconds": 0.5, "findings": []}}


def finding(patch, arm, node, reproducible=True):
    return {"patch": patch, "arm": arm, "node": node,
            "result": {"runnable": True, "reproducible": reproducible, "patched": ["fail"] * 3, "base": ["pass"] * 3}}


def written(tmp_path, arms, reruns=(), excluded=()):
    path = tmp_path / "record.jsonl"
    kept = record.Record(path)
    for item in excluded:
        kept.add("excluded", item)
    for patch, found in arms.items():
        for arm in ("C", "T", "E"):
            kept.add("arm", {"patch": patch, "arm": arm, "output": found[arm]})
    for item in reruns:
        kept.add("rerun", item)
    return path, kept.final


def study(tmp_path, tasks, arms, reruns=(), excluded=(), reference=(), **kwargs):
    path, final = written(tmp_path, arms, reruns, excluded)
    return analysis.analyse(path, final, v9={"seed": SEED, "tasks": tasks}, reference=list(reference),
                            resamples=200, **kwargs)


TASKS = [task("d1", "r1", "defective", g6=False), task("d2", "r1", "defective"),
         task("d3", "r2", "defective", tests=False), task("d4", "r2", "defective", g6=False),
         task("c1", "r1", "correct"), task("c2", "r2", "correct", g6=False)]
ARMS = {"d1": outputs(c=False, t=False, e=False), "d2": outputs(c=True, t=False, e=True, e_filtered=False),
        "d3": outputs(c=False, t=False, e=False), "d4": outputs(c=False, t=False, e=False, c_error="time_out"),
        "c1": outputs(c=True, t=True, e=True, t_own=True), "c2": outputs(c=False, t=False, e=True, c_own=True,
                                                                        a_and_e=False)}


class Untouchable(dict):
    def __getitem__(self, key):
        raise AssertionError("read before the record was verified")

    get = __getitem__


def test_nothing_is_computed_unless_the_records_chain_holds_and_ends_at_the_committed_hash(tmp_path):
    path, final = written(tmp_path, ARMS)
    with pytest.raises(record.RecordRefused, match="committed"):
        analysis.analyse(path, "a" * 64, v9=Untouchable(), reference=[])
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0].replace('"accepted":false', '"accepted":true'), *lines[1:]]) + "\n",
                    encoding="utf-8")
    with pytest.raises(record.RecordRefused, match="chain"):
        analysis.analyse(path, final, v9=Untouchable(), reference=[])


def test_the_exact_interval_is_clopper_pearsons_two_sided_95_percent_one():
    low, high = analysis.exact(0, 10)
    assert low == 0.0 and math.isclose(high, 1 - 0.025 ** (1 / 10), abs_tol=1e-6)
    low, high = analysis.exact(10, 10)
    assert high == 1.0 and math.isclose(low, 0.025 ** (1 / 10), abs_tol=1e-6)
    low, high = analysis.exact(3, 10)
    assert math.isclose(low, 0.0667, abs_tol=1e-4) and math.isclose(high, 0.6525, abs_tol=1e-4)


def test_false_and_correct_acceptance_per_arm_with_their_intervals_and_per_repository(tmp_path):
    got = study(tmp_path, TASKS, ARMS)
    rates = got["rates"]
    assert rates["A"]["false_acceptance"]["count"] == 2 and rates["A"]["false_acceptance"]["of"] == 4
    assert rates["C"]["false_acceptance"]["count"] == 1 and rates["T"]["false_acceptance"]["count"] == 0
    assert rates["E"]["false_acceptance"]["count"] == 1 and rates["E"]["correct_acceptance"]["count"] == 2
    assert rates["C"]["correct_acceptance"]["count"] == 1 and rates["T"]["correct_acceptance"]["count"] == 1
    assert rates["T"]["false_acceptance"]["interval"] == list(analysis.exact(0, 4))
    assert rates["A"]["per_repository"]["r1"]["false_acceptance"]["count"] == 1
    assert rates["A"]["per_repository"]["r2"]["correct_acceptance"] == {"count": 0, "of": 1, "rate": 0.0,
                                                                      "interval": list(analysis.exact(0, 1))}
    assert got["counts"]["defective"] == 4 and got["counts"]["correct"] == 2 and got["counts"]["repositories"] == [
        "r1", "r2"]


def test_a_zero_denominator_gives_a_count_and_no_interval(tmp_path):
    got = study(tmp_path, [t for t in TASKS if t["label"] == "defective"], {k: v for k, v in ARMS.items()
                                                                           if k.startswith("d")})
    assert got["rates"]["T"]["correct_acceptance"] == {"count": 0, "of": 0, "rate": None, "interval": None}


def test_e_filtered_a_and_e_and_each_search_arms_own_run_acceptance_come_from_the_record(tmp_path):
    rates = study(tmp_path, TASKS, ARMS)["rates"]
    assert rates["E_filtered"]["false_acceptance"]["count"] == 0 and rates["E"]["false_acceptance"]["count"] == 1
    assert rates["A_and_E"]["correct_acceptance"]["count"] == 1
    assert rates["C_own"]["correct_acceptance"]["count"] == 2 and rates["T_own"]["correct_acceptance"]["count"] == 1


def test_actionable_discovery_counts_reproducible_findings_on_defective_patches_and_reference_compatibility(tmp_path):
    reruns = [finding("d1", "T", "a.py::t1"), finding("d1", "T", "a.py::t2"), finding("d3", "T", "b.py::t", False),
              finding("d3", "C", "b.py::u"), finding("c1", "T", "c.py::t"), finding("d4", "A", "d.py::t"),
              finding("d4", "E", "e.py::t")]
    reference = [{"patch": "d1", "arm": "T", "node": "a.py::t1", "result": {"compatible": True,
                                                                            "not_comparable": False}},
                 {"patch": "d1", "arm": "T", "node": "a.py::t2", "result": {"compatible": False,
                                                                            "not_comparable": True}}]
    got = study(tmp_path, TASKS, ARMS, reruns, reference=reference)["discovery"]
    assert (got["T"]["count"], got["T"]["of"]) == (1, 4) and got["T"]["actionable_findings"] == 2
    assert got["T"]["compatible_with_reference"] == 1 and got["T"]["not_comparable"] == 1
    assert got["C"]["count"] == 1 and got["A"]["count"] == 1 and got["E"]["count"] == 1
    assert got["T"]["findings_per_patch"] == {"patches_with_findings": 3, "mean": round(4 / 6, 3), "most": 2}
    assert got["2"] == {"count": 0, "of": 4, "rate": 0.0, "interval": list(analysis.exact(0, 4)),
                        "note": "the agent's own review gives no finding (PROTOCOL.md, Findings)"}
    assert "not computed" in got["1"]["note"] and "not computed" in got["4"]["note"]


def test_the_headline_counts_k_k_ref_and_j_over_m_and_l(tmp_path):
    reruns = [finding("d1", "T", "a.py::t1"), finding("d2", "T", "a.py::t"), finding("d3", "T", "b.py::t"),
              finding("c1", "T", "c.py::t"), finding("c1", "C", "c.py::u", False)]
    reference = [{"patch": "d1", "arm": "T", "node": "a.py::t1", "result": {"compatible": True,
                                                                            "not_comparable": False}}]
    got = study(tmp_path, TASKS, ARMS, reruns, reference=reference)["headline"]
    assert got["M"] == 3 and got["L"] == 2                   # d3 was not accepted by the tests
    assert got["arms"]["T"]["K"]["count"] == 2 and got["arms"]["T"]["K_ref"]["count"] == 1
    assert got["arms"]["T"]["J"]["count"] == 1 and got["arms"]["C"]["J"]["count"] == 0
    assert got["arms"]["T"]["K"]["interval"] == list(analysis.exact(2, 3))


def test_stratifying_the_bootstrap_by_repository_changes_the_interval_from_a_pooled_one():
    rows = [{"repository": "r1", "x": True, "y": False}] * 10 + [{"repository": "r2", "x": False, "y": False}] * 10
    stratified = analysis.paired(rows, lambda r: r["x"], lambda r: r["y"], seed=SEED, resamples=500)
    pooled = analysis.paired([dict(r, repository="all") for r in rows], lambda r: r["x"], lambda r: r["y"],
                             seed=SEED, resamples=500)
    assert stratified["bootstrap"] == [0.5, 0.5] and pooled["bootstrap"] != stratified["bootstrap"]
    assert stratified["newcombe"] == pooled["newcombe"] and stratified["difference"] == 0.5


def test_the_paired_differences_are_those_the_protocol_lists_and_one_without_discordant_pairs_is_not_read(tmp_path):
    got = study(tmp_path, TASKS, ARMS)["differences"]
    pairs = {(d["x"], d["y"], d["measure"]) for d in got}
    for x, y in (("C", "A"), ("T", "A"), ("C", "E"), ("T", "E"), ("T", "C"), ("C", "1"), ("T", "2"), ("E", "4")):
        assert {(x, y, m) for m in ("false_acceptance", "correct_acceptance")} <= pairs
    assert ("T", "A", "discovery") in pairs and ("T", "1", "discovery") not in pairs
    same = next(d for d in got if (d["x"], d["y"], d["measure"]) == ("T", "C", "false_acceptance"))
    assert same["discordant"] == {"x_only": 0, "y_only": 1} and same["interpreted"] is True
    none = next(d for d in got if (d["x"], d["y"], d["measure"]) == ("T", "A", "correct_acceptance"))
    assert none["discordant"] == {"x_only": 0, "y_only": 0} and none["interpreted"] is False


def test_costs_per_arm_and_tool_errors_by_kind(tmp_path):
    got = study(tmp_path, TASKS, ARMS)
    costs = got["costs"]
    assert costs["T"]["model_calls"] == {"mean": 2.0, "median": 2.0, "total": 12, "missing": 0}
    assert costs["T"]["cents"]["total"] == 24.0 and costs["T"]["dollars"]["mean"] == 0.04
    assert costs["A"]["seconds"]["mean"] == 10.0 and costs["A"]["cents"]["missing"] == 6
    assert got["tool_errors"]["C"]["error_kinds"] == {"time_out": 1}


def test_unlabelled_and_excluded_patches_are_counted_and_left_out_of_every_rate(tmp_path):
    tasks = [*TASKS, task("u1", "r1", None), task("e1", "r2", None, patch="empty")]
    arms = {**ARMS, "u1": outputs()}
    got = study(tmp_path, tasks, arms, excluded=[{"id": "e1", "draw_index": 9, "reason": "empty"}])
    assert got["counts"]["unlabelled"] == ["u1"] and got["counts"]["excluded"] == {"empty": 1, "not_applying": 0}
    assert got["rates"]["T"]["false_acceptance"]["of"] == 4 and got["counts"]["non_empty"] == 7
    assert got["record"]["committed"] and got["bootstrap_seed"] == SEED + 2
