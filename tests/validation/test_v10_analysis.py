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


def outputs(*, c=True, t=True, e=True, r=True, c_own=None, t_own=None, e_filtered=None, a_and_e=None, calls=2,
            cents=4.0, seconds=6.0, c_error="", search_stage=None, report_check=None, e_tests=None, r_tests=None,
            r_cents=5.0, r_error="", r_gaming=None):
    def disposition(accepted):
        return dict(OK if accepted else NO)
    c_disposition = make(ERROR, "x", error_kind=c_error) if c_error else disposition(c)

    def search(found, own):
        return {"disposition": found, "own": disposition(found["accepted"] if own is None else own),
                "meter": {"calls": calls, "characters": 100, "cents": cents}, "model_calls": calls,
                "seconds": seconds, "processor_seconds": 1.0, "findings": [], "search_stage": search_stage,
                "report_check": report_check}
    return {"C": search(c_disposition, c_own), "T": search(disposition(t), t_own),
            "E": {"disposition": disposition(e), "e_filtered": disposition(e if e_filtered is None else e_filtered),
                  "a_and_e": disposition(e if a_and_e is None else a_and_e),
                  "meter": {"calls": 1, "characters": 50, "cents": 1.0}, "model_calls": 1, "seconds": 2.0,
                  "processor_seconds": 0.5, "findings": [], "tests": e_tests or {}},
            "R": {"disposition": make(ERROR, "x", error_kind=r_error, time_or_cap=r_error == "stopped_at_token_cap")
                  if r_error else disposition(r), "meter": {"calls": 1, "characters": 0, "cents": r_cents},
                  "model_calls": 1, "seconds": 30.0, "processor_seconds": 0.0, "findings": [], "tests": r_tests or {},
                  "gaming": r_gaming}}


def finding(patch, arm, node, reproducible=True):
    return {"patch": patch, "arm": arm, "node": node,
            "result": {"runnable": True, "reproducible": reproducible, "patched": ["fail"] * 3, "base": ["pass"] * 3}}


def written(tmp_path, arms, reruns=(), excluded=(), comparison=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "record.jsonl"
    kept = record.Record(path)
    for item in excluded:
        kept.add("excluded", item)
    for patch, found in arms.items():
        for arm in ("C", "T", "E", "R"):
            kept.add("arm", {"patch": patch, "arm": arm, "output": found[arm]})
    if comparison is not None:
        kept.add("comparison", comparison)
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
    # arm A makes no model call, so its cents, dollars and tokens read 0, never missing (the coordinator, 2026-10-29)
    assert costs["A"]["seconds"]["mean"] == 10.0
    for key in ("cents", "dollars"):
        assert costs["A"][key] == {"mean": 0.0, "median": 0.0, "total": 0.0, "missing": 0}
    assert costs["A"]["input_tokens"] == costs["A"]["output_tokens"] == {"mean": 0.0, "median": 0, "total": 0,
                                                                         "missing": 0}
    assert costs["T"]["input_tokens"]["missing"] == 6            # a meter that counted no tokens: missing, said so
    assert got["tool_errors"]["C"]["error_kinds"] == {"time_out": 1}


def test_input_and_output_tokens_per_arm_are_what_each_arms_meter_counted_as_the_provider_reported_them(tmp_path):
    arms = {name: outputs() for name in ARMS}
    for name, found in arms.items():
        for arm, tokens in (("C", {"input_tokens": 900, "output_tokens": 100}),
                            ("R", {"input_tokens": 40, "output_tokens": 2000, "cache_read_input_tokens": 90000,
                                   "cache_creation_input_tokens": 5000})):
            found[arm]["meter"] = {**found[arm]["meter"], "tokens": tokens}
    costs = study(tmp_path, TASKS, arms)["costs"]
    assert costs["C"]["input_tokens"] == {"mean": 900.0, "median": 900.0, "total": 5400, "missing": 0}
    assert costs["C"]["cache_read_input_tokens"]["total"] == 0          # a kind the meter's tokens leave out is 0
    assert costs["R"]["output_tokens"]["total"] == 12000 and costs["R"]["cache_read_input_tokens"]["mean"] == 90000.0


LIMITS = {"cap_cents": 300.0, "call_cap": 8, "limit_s": 1800, "grace_s": 60}


def criteria(tmp_path, tasks=TASKS, arms=ARMS, limits=LIMITS):
    got = study(tmp_path, tasks, arms, limits=limits)["feasibility"]
    return got, {item["criterion"]: item for item in got["criteria"]}


def test_criterion_1_every_arm_answers_every_non_empty_patch_with_at_most_one_tool_error_in_ten(tmp_path):
    got, by = criteria(tmp_path)
    assert by[1]["result"] == "not met"                              # C: one tool error in six, above 10%
    assert by[1]["numbers"]["C"] == {"patches": 6, "missing": 0, "tool_errors": 1, "tool_error_rate": round(1 / 6, 4)}
    clean = {name: outputs() for name in ARMS}
    _, by = criteria(tmp_path / "clean", arms=clean)
    assert by[1]["result"] == "met"
    _, by = criteria(tmp_path / "missing", arms={k: v for k, v in clean.items() if k != "c2"})
    assert by[1]["result"] == "not met" and by[1]["numbers"]["T"]["missing"] == 1


def test_criterion_2_no_arm_used_more_than_its_frozen_limits_and_runs_stopped_at_a_limit_are_counted(tmp_path):
    _, by = criteria(tmp_path)
    assert by[2]["result"] == "met" and by[2]["numbers"]["stopped_at_a_limit"]["C"] == {"time_out": 1}
    spendthrift = {name: outputs(cents=60.0) for name in ARMS}
    _, by = criteria(tmp_path / "over", arms=spendthrift)
    assert by[2]["result"] == "not met" and by[2]["numbers"]["C"]["cents"] == 360.0
    chatty = {**{name: outputs() for name in ARMS}, "d1": outputs(calls=9)}
    _, by = criteria(tmp_path / "calls", arms=chatty)
    assert by[2]["result"] == "not met" and by[2]["numbers"]["T"]["most_calls"] == 9
    _, by = criteria(tmp_path / "none", limits=None)
    assert by[2]["result"] == "inconclusive" and "frozen limits" in by[2]["why"]


def test_criterion_3_the_search_stage_ran_a_check_to_a_result_on_at_least_half_the_patches(tmp_path):
    _, by = criteria(tmp_path)
    assert by[3]["result"] == "inconclusive" and "VS-5" in by[3]["why"]
    ran = {name: outputs(search_stage={"ran_to_result": index % 2 == 0}) for index, name in enumerate(ARMS)}
    _, by = criteria(tmp_path / "half", arms=ran)
    assert by[3]["result"] == "met" and by[3]["numbers"]["T"] == {"ran_to_result": 3, "of": 6}
    fewer = {name: outputs(search_stage={"ran_to_result": index == 0}) for index, name in enumerate(ARMS)}
    _, by = criteria(tmp_path / "fewer", arms=fewer)
    assert by[3]["result"] == "not met"


PASSING_REPORT = {"complete": True, "missing": [], "completion_problems": [], "model_originated_passing": [],
                  "formal_problems": []}


def test_criterion_4_reads_each_search_arms_report_check(tmp_path):
    _, by = criteria(tmp_path)
    assert by[4]["result"] == "inconclusive" and "report check" in by[4]["why"]
    clean = {name: outputs(report_check=PASSING_REPORT) for name in ARMS}
    _, by = criteria(tmp_path / "clean", arms=clean)
    assert by[4]["result"] == "met" and by[4]["numbers"]["checked"] == {"C": 6, "T": 6}
    listing = {**PASSING_REPORT, "complete": False,
               "model_originated_passing": [{"requirement": "R1", "check": "x", "provenance": "proposed"}]}
    unsplit = {**PASSING_REPORT, "complete": False, "formal_problems": ["activity 1: no three parts"]}
    incomplete = {**PASSING_REPORT, "complete": False, "missing": ["completion_section"]}
    for bad, field in ((listing, "model_originated_passing"), (unsplit, "formal_problems")):
        _, by = criteria(tmp_path / field, arms={**clean, "d1": outputs(report_check=bad)})
        assert by[4]["result"] == "not met" and by[4]["numbers"][field] == {"C": ["d1"], "T": ["d1"]}
    _, by = criteria(tmp_path / "incomplete", arms={**clean, "d1": outputs(report_check=incomplete)})
    assert by[4]["result"] == "met"                       # a report missing a part is a tool error, criterion 1's


def test_criteria_4_and_5_wait_for_what_the_record_does_not_hold_and_say_so_with_their_numbers(tmp_path):
    _, by = criteria(tmp_path)
    assert by[4]["result"] == "inconclusive" and "report check" in by[4]["why"]
    assert by[5]["result"] == "inconclusive" and "comparison" in by[5]["why"]
    assert by[5]["numbers"]["own_accepted_arm_a_did_not"] == {"C": ["c2"], "T": []}


def test_criterion_5_reads_the_comparison_the_driver_recorded_before_the_labels(tmp_path):
    def comparison(holds, not_on_list):
        return {"differ": {"C": ["c2"], "T": []}, "must_hold": {"C": ["c2"], "T": []},
                "not_on_list": not_on_list, "unread": {"C": [], "T": []}, "holds": holds}
    for holds, not_on_list, expected in ((True, {"C": [], "T": []}, "met"), (False, {"C": ["c2"], "T": []}, "not met"),
                                         (None, {"C": [], "T": []}, "inconclusive")):
        path, final = written(tmp_path / expected.replace(" ", "_"), ARMS, comparison=comparison(holds, not_on_list))
        got = analysis.analyse(path, final, v9={"seed": SEED, "tasks": TASKS}, reference=[], resamples=50)
        by = {item["criterion"]: item for item in got["feasibility"]["criteria"]}
        assert by[5]["result"] == expected and by[5]["numbers"]["not_on_list"] == not_on_list


def test_criterion_6_and_the_outcome(tmp_path):
    got, by = criteria(tmp_path)
    assert by[6]["result"] == "met" and got["result"] == "not feasible"     # criterion 1 is not met
    tasks = [*TASKS[:-1], task("c2", "r2", "correct", g6=False, oracle=False)]
    _, by = criteria(tmp_path / "oracle", tasks=tasks)
    assert by[6]["result"] == "not met" and by[6]["numbers"]["failed"] == ["c2"]
    clean = {name: outputs(search_stage={"ran_to_result": True}) for name in ARMS}
    got, _ = criteria(tmp_path / "clean", arms=clean)
    assert got["result"] == "inconclusive"                                 # criteria 4 and 5 cannot be checked yet


#: The protocol's permitted sentences, as version 2's "What the results permit saying" writes them.
PROTOCOL = (
    "On N agent-written Python patches from P repositories, of the M defective patches that the repository's tests "
    "and the agent's own review both accepted, G6 with verification search in its full configuration (arm T) gave a "
    "reproducible finding on K, of which K_ref also pass on the benchmark's reference fix. Model-written tests run "
    "without G6 (arm E) did so on K_E, and a fresh review by a coding agent that did not write the patch (arm R) on "
    "K_R. On the L correct patches that both accepted, arm T gave a reproducible finding on J.",
    "On the same patches, arm T's false acceptance was X% against arm A's Y% and arm R's Z%, and its correct "
    "acceptance X′% against Y′% and Z′%",
    "Arm T cost a mean of C model calls, S seconds and D dollars per patch (median C′, S′ and D′).")
HEADLINE_RERUNS = [finding("d1", "T", "a.py::t1"), finding("d2", "T", "a.py::t"), finding("d3", "T", "b.py::t"),
                   finding("c1", "T", "c.py::t"), finding("c1", "C", "c.py::u", False), finding("d2", "C", "a.py::u"),
                   finding("d4", "R", "g6_check_0123456789ab/test_r.py::test_r")]
HEADLINE_REFERENCE = [{"patch": "d1", "arm": "T", "node": "a.py::t1",
                       "result": {"compatible": True, "not_comparable": False}}]


def headlines(tmp_path, tasks=TASKS, arms=ARMS):
    return study(tmp_path, tasks, arms, HEADLINE_RERUNS, reference=HEADLINE_REFERENCE)["headlines"]


def test_the_sentence_templates_are_the_protocols_text_exactly():
    names = {"N": "N", "P": "P", "M": "M", "K": "K", "K_ref": "K_ref", "K_E": "K_E", "K_R": "K_R", "L": "L",
             "J": "J", "X": "X", "Y": "Y", "Z": "Z", "X2": "X′", "Y2": "Y′", "Z2": "Z′", "C": "C", "S": "S", "D": "D",
             "C2": "C′", "S2": "S′", "D2": "D′"}
    assert tuple(template.format(**names) for template in analysis.SENTENCES) == PROTOCOL


def test_the_headline_sentences_are_filled_from_the_outcome_with_arm_c_beside_arm_t_never_in_its_place(tmp_path):
    got = headlines(tmp_path)
    first, second, third = got["sentences"]
    assert first["text"] == (
        "On 6 agent-written Python patches from 2 repositories, of the 3 defective patches that the repository's "
        "tests and the agent's own review both accepted, G6 with verification search in its full configuration (arm "
        "T) gave a reproducible finding on 2, of which 1 also pass on the benchmark's reference fix. Model-written "
        "tests run without G6 (arm E) did so on 0, and a fresh review by a coding agent that did not write the patch "
        "(arm R) on 1. On the 2 correct patches that both accepted, arm T gave a reproducible finding on 1.")
    assert second["text"] == ("On the same patches, arm T's false acceptance was 0.0% against arm A's 50.0% and arm "
                              "R's 100.0%, and its correct acceptance 50.0% against 50.0% and 100.0%")
    assert third["text"] == ("Arm T cost a mean of 2.0 model calls, 6.0 seconds and 0.04 dollars per patch (median "
                             "2.0, 6.0 and 0.04).")
    assert all("arm C" not in item["text"] for item in got["sentences"])
    assert first["beside"]["arm"] == "C" and first["beside"]["K"] == "1 of 3" and first["beside"]["J"] == "0 of 2"
    assert first["notes"]["K_R"] == analysis.K_R_NOTE
    assert len(second["intervals"]) == 6 and second["beside"]["false_acceptance"] == "25.0%"
    assert first["intervals"]["K"] == analysis.percent_interval(analysis.exact(2, 3))
    assert first["intervals"]["K_R"] == analysis.percent_interval(analysis.exact(1, 3))
    assert got["not_printed"] == []

def test_the_first_sentence_is_not_used_when_no_defective_patch_was_accepted_by_both(tmp_path):
    tasks = [dict(t, arms={**t["arms"], "tests": dict(NO)}) if t["label"] == "defective" else t for t in TASKS]
    got = headlines(tmp_path, tasks=tasks)
    assert [item["id"] for item in got["sentences"]] == [2, 3]
    assert got["not_printed"] == [{"id": 1, "why": "no defective patch was accepted by both the repository's tests "
                                                   "and the agent's own review (M is zero)"}]


def test_a_false_and_correct_acceptance_pair_is_never_printed_without_both_rates(tmp_path):
    defective = [t for t in TASKS if t["label"] == "defective"]
    got = headlines(tmp_path, tasks=defective, arms={k: v for k, v in ARMS.items() if k.startswith("d")})
    assert all(item["id"] != 2 for item in got["sentences"])
    why = {item["id"]: item["why"] for item in got["not_printed"]}
    assert "correct acceptance" in why[2] and "no correct patch" in why[2]
    assert all("false_acceptance" not in str(item.get("beside")) for item in got["sentences"])


def test_arm_r_has_its_rates_discovery_headline_and_the_note_beside_k_r(tmp_path):
    arms = {**ARMS, "d1": {**ARMS["d1"], "R": outputs(r=False)["R"]}}
    reruns = [finding("d1", "R", "g6_check_0123456789ab/test_r.py::test_r")]
    got = study(tmp_path, TASKS, arms, reruns)
    assert got["rates"]["R"]["false_acceptance"]["count"] == 3 and got["rates"]["R"]["correct_acceptance"]["count"] == 2
    assert got["discovery"]["R"]["count"] == 1 and got["headline"]["arms"]["R"]["K"]["count"] == 1
    assert got["headline"]["notes"]["K_R"] == analysis.K_R_NOTE and "arm 2" in analysis.K_R_NOTE


def test_the_paired_differences_include_r_against_a_c_t_and_e_and_r_against_v9s_arms(tmp_path):
    pairs = {(d["x"], d["y"], d["measure"]) for d in study(tmp_path, TASKS, ARMS)["differences"]}
    for x, y in (("A", "R"), ("C", "R"), ("T", "R"), ("E", "R"), ("R", "1"), ("R", "2"), ("R", "4")):
        assert {(x, y, m) for m in ("false_acceptance", "correct_acceptance")} <= pairs
    assert ("A", "R", "discovery") in pairs and ("R", "2", "discovery") in pairs and ("R", "1", "discovery") not in pairs


def test_the_filtered_findings_of_e_and_r_leave_out_tests_that_also_fail_at_the_base(tmp_path):
    node_e, node_r = "g6_check_aaaaaaaaaaaa/test_e.py::test_e", "g6_check_bbbbbbbbbbbb/test_r.py::test_r"
    arms = {**ARMS, "d1": outputs(c=False, t=False, e=False, e_tests={node_e: {"fails_at_base": True}},
                                  r_tests={node_r: {"fails_at_base": False}})}
    reruns = [finding("d1", "E", node_e), finding("d1", "R", node_r)]
    got = study(tmp_path, TASKS, arms, reruns)
    assert got["discovery"]["E"]["count"] == 1 and got["filtered"]["E"]["discovery"]["count"] == 0
    assert got["filtered"]["R"]["discovery"]["count"] == 1
    assert got["filtered"]["E"]["headline"]["K"]["count"] == 0 and got["filtered"]["R"]["headline"]["K"]["count"] == 1


def test_criteria_1_and_2_hold_arm_r_too_and_a_session_past_its_limits_is_counted_not_failed(tmp_path):
    _, by = criteria(tmp_path)
    assert by[1]["numbers"]["R"]["tool_errors"] == 0 and by[2]["numbers"]["R"]["cents"] == 30.0
    capped = {**{name: outputs() for name in ARMS}, "d1": outputs(r_error="stopped_at_token_cap")}
    _, by = criteria(tmp_path / "capped", arms=capped)
    assert by[2]["result"] == "met" and by[2]["numbers"]["stopped_at_a_limit"]["R"] == {"stopped_at_token_cap": 1}
    costly = {name: outputs(r_cents=20.0) for name in ARMS}
    _, by = criteria(tmp_path / "costly", arms=costly)
    assert by[2]["result"] == "not met" and "R" in by[2]["why"]               # 120 cents is past R's 75


def test_a_label_invalid_patch_is_left_out_of_every_measure_and_kept_by_the_sensitivity_analysis(tmp_path):
    tasks = [dict(t, label_invalid=t["task_id"] == "d3") for t in TASKS]
    got = study(tmp_path, tasks, ARMS)
    assert got["counts"]["label_invalid"] == ["d3"] and got["counts"]["defective"] == 3
    assert got["rates"]["A"]["false_acceptance"]["of"] == 3 and got["discovery"]["T"]["of"] == 3
    kept = got["sensitivity_label_invalid_kept"]
    assert kept["kept"] == ["d3"] and kept["rates"]["A"]["false_acceptance"]["of"] == 4
    assert kept["discovery"]["T"]["of"] == 4 and kept["headline"]["M"] == got["headline"]["M"]   # d3: arm 1 rejected
    assert study(tmp_path / "none", TASKS, ARMS)["counts"]["label_invalid"] == []


def test_the_verdict_is_contamination_then_criterion_6_then_inconclusive_with_v9s_audit_beside(tmp_path):
    audit = {"audited": 10, "label_errors": 1, "by_kind": {"1": 1}, "label_invalid": ["d3"], "kind_4": 0}
    got = study(tmp_path, TASKS, ARMS, gaming=[], v9_audit=audit)["verdict"]
    assert got["verdict"] == "inconclusive" and got["v9_feasibility_audit"]["by_kind"] == {"1": 1}
    assert got["v9_feasibility_audit"]["upper_bound"] == pytest.approx(analysis.stats.clopper_pearson_upper(1, 10))
    network = {"kind": "network", "source": "R:d1", "line": 3, "detail": "curl"}
    arms = {**ARMS, "d1": outputs(c=False, t=False, e=False, r_gaming={"git_commands": [], "findings": [network]})}
    assert study(tmp_path / "net", TASKS, arms, gaming=[])["verdict"]["verdict"] == "contaminated_evaluation"
    tasks = [*TASKS[:-1], task("c2", "r2", "correct", g6=False, oracle=False)]
    assert study(tmp_path / "oracle", tasks, ARMS, gaming=[])["verdict"]["verdict"] == "oracle_inadequate"
    assert study(tmp_path / "none", TASKS, ARMS)["verdict"]["verdict"] is None


def test_unlabelled_and_excluded_patches_are_counted_and_left_out_of_every_rate(tmp_path):
    tasks = [*TASKS, task("u1", "r1", None), task("e1", "r2", None, patch="empty")]
    arms = {**ARMS, "u1": outputs()}
    got = study(tmp_path, tasks, arms, excluded=[{"id": "e1", "draw_index": 9, "reason": "empty"}])
    assert got["counts"]["unlabelled"] == ["u1"] and got["counts"]["excluded"] == {"empty": 1, "not_applying": 0}
    assert got["rates"]["T"]["false_acceptance"]["of"] == 4 and got["counts"]["non_empty"] == 7
    assert got["record"]["committed"] and got["bootstrap_seed"] == SEED + 2
