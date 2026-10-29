# SPDX-License-Identifier: Apache-2.0
"""V10's secondary measures (PROTOCOL.md, version 2, "Secondary measures"), on fakes only."""
from __future__ import annotations

import math

import pytest
from patch_review_search import secondary
from patch_review_utility import stats


def act(kind, **fields):
    return {"activity": kind, **fields}


def report(*activities, adequacy=None):
    return {"assessment": {"completion": {"activities": list(activities), "test_adequacy": list(adequacy or [])}}}


def driver_row(patch, found):
    return {"patch": patch, "arms": {"T": {"report": found}}}


# --- template applicability (arm T), from its reports, before any label ---------------------------------------------

def test_template_applicability_gives_each_template_the_patches_it_was_applied_refused_or_not_chosen_on():
    first = report(act("proposer stage", stage="example.v1", result="replied"),
                   act("proposer stage", stage="relation.v1", result="replied"),
                   act("proposal", stage="relation.v1", requirement="R1", result="refused",
                       reason="its instance was refused: bindings.transformation: must be a dotted name"),
                   act("proposer stage", stage="scenarios", result="replied"),
                   act("template instance", check="g6p-example-1", template="example.v1", requirement="R1"),
                   act("proposal", stage="example.v1", requirement="R2", result="refused", reason="no binding given"))
    second = report(act("proposer stage", stage="example.v1", result="replied"),
                    act("proposer stage", stage="relation.v1", result="skipped", reason="the call cap was reached"))
    got = secondary.template_applicability([driver_row("p1", first), driver_row("p2", second),
                                            driver_row("p3", None)])
    example, relation = got["templates"]["example.v1"], got["templates"]["relation.v1"]
    assert example["applied"] == ["p1"] and example["refused"] == []
    assert example["refused_too"] == [{"patch": "p1", "reasons": ["no binding given"]}]
    assert example["not_chosen"] == [{"patch": "p2", "why": "its proposer stage replied with no instance of it"}]
    assert relation["applied"] == [] and relation["refused"] == [
        {"patch": "p1", "reasons": ["its instance was refused: bindings.transformation: must be a dotted name"]}]
    assert relation["not_chosen"] == [{"patch": "p2", "why": "its proposer stage was skipped: the call cap was reached"}]
    assert set(got["templates"]) == {"example.v1", "relation.v1"} and got["no_report"] == ["p3"]   # not "scenarios"


# --- the formal path (arm T) ------------------------------------------------------------------------------------------

def formal(template="state_invariant.v1", *, contract=None, verdict="violated", traces=(), correspondence=None,
           through=None):
    specification = {"template": template, "bindings_provenance": "model_hypothesis",
                     "contract": contract or {"vacuous": False, "conflicting": False, "undecided": False}}
    if through:
        specification.update(form="multi_step", through=through)
    return {"specification": specification,
            "model": {"operation": "check_invariant", "verdict": verdict, "traces": [list(t) for t in traces]},
            "correspondence": correspondence or {"result": "not_searched"}}


def test_the_formal_path_counts_come_from_arm_ts_activities_on_state_invariant_instances_only():
    reproduced = formal(traces=[[1, 2], [2]], correspondence={"result": "reproduced", "traces_reproduced": [0]})
    found = report(
        act("template instance", check="g6p-si-1", template="state_invariant.v1", formal=reproduced),
        act("template instance", check="g6p-si-1", template="state_invariant.v1", formal=reproduced),   # once
        act("template instance", check="g6p-si-2", template="state_invariant.v1", result="not_searched",
            formal=formal(contract={"vacuous": True, "conflicting": False, "undecided": False},
                          verdict="not_searched")),
        act("template instance", check="g6p-si-3", template="state_invariant.v1", result="not_searched",
            formal=formal(contract={"vacuous": False, "conflicting": True, "undecided": False},
                          verdict="not_searched")),
        act("template instance", check="g6p-ar-4", template="access_rule.v1",
            formal=formal("access_rule.v1", through="state_invariant.v1", traces=[[1]],
                          correspondence={"result": "not_reproduced", "reason": "model_only"})),
        act("template instance", check="g6p-si-5", template="state_invariant.v1",
            formal=formal(traces=[[3]], correspondence={"result": "not_replayable", "reason": "no such call"})),
        act("template instance", check="g6p-si-6", template="state_invariant.v1",
            formal=formal(verdict="no_violation_within_bound",
                          correspondence={"result": "not_replayed", "reason": "about the model only"})),
        act("symbolic search", instance="g6p-relation-7", template="relation.v1",       # not the formal path
            formal={"specification": {"template": "relation.v1"}, "correspondence": {"result": "not_searched"}}),
        # refusals: G6's structured code where its record has one, its own words (pinned below) where none
        act("model check", instance="g6p-si-11", template="state_invariant.v1", operation="check_invariant",
            verdict="refused", code="G6_E_MODEL_CHECK_INPUT_TOO_LARGE", reason="the model has 70 state variables"),
        act("model check", instance="g6p-si-12", template="state_invariant.v1", operation="check_invariant",
            verdict="refused", code="G6_E_MODEL_CHECK_UNSUPPORTED", reason="a term outside the grammar"),
        act("template instance", check="g6p-si-8", template="state_invariant.v1", requirement="R4", result="refused",
            reason="the model is over model_checking's limits: the model has 70 state variables; the limit is 64"),
        act("template instance", check="g6p-ar-9", template="access_rule.v1", requirement="R5", result="refused",
            reason="depth 60 is above the limit of 20 steps for a search whose traces are replayed"),
        act("template instance", check="g6p-si-10", template="state_invariant.v1", requirement="R6", result="refused",
            reason="x is a Real: the replay reads Int or Bool variables only"),
        act("template instance", check="g6p-ar-13", template="access_rule.v1", requirement="R7", result="refused",
            reason="not the formal path's refusal"),                                           # left out
        act("proposal", stage="state_invariant.v1", requirement="R3", result="refused",
            reason="its instance was refused: bindings.observe: must be a dotted name"))
    got = secondary.formal_path([driver_row("p1", found), driver_row("p2", report())])
    assert got["per_patch"]["p1"] == {"models_built": 6, "refused_over_limits": 3, "refused_other": 3, "vacuous": 1,
                                      "conflicting": 1, "undecided": 0, "searched": 4, "traces_found": 4,
                                      "traces_reproduced": 1, "model_only": 1, "not_replayable": 1,
                                      "not_replayed": 1}
    assert got["per_patch"]["p2"]["models_built"] == 0 and got["total"]["traces_found"] == 4
    assert [(r["instance"] or r["requirement"], r["over_limits"], r["read_from"]) for r in got["refusals"]] == [
        ("g6p-si-11", True, "code"), ("g6p-si-12", False, "code"), ("g6p-si-8", True, "reason text"),
        ("g6p-ar-9", True, "reason text"), ("g6p-si-10", False, "reason text"), ("R3", False, "reason text")]


@pytest.mark.parametrize("reason", [
    # G6's own words for a state model over the limits, as verification_templates._state_invariant writes them at the
    # launch commit the rehearsal ran (0c2a4f98e4); a change of wording must fail here, and freeze_check reads them too
    "the model is over model_checking's limits: the model has 70 state variables; the limit is 64",
    "depth 60 is above the limit of 20 steps for a search whose traces are replayed",
    "traces 9 is above the limit of 5 replayed traces",
    "the model has 60 state variables; with the 11 G6 adds to keep each trace's history (a step counter and one per "
    "step), that is over model_checking's limit of 64"])
def test_g6s_exact_words_for_a_model_over_the_limits_are_read_as_over_the_limits(reason):
    assert secondary.over_limits_by_reason(reason) is True


@pytest.mark.parametrize("reason", ["the model is above model_checking's limits: 70 variables",
                                    "depth 60 exceeds the limit of 20 steps", "traces 9 is above the limit of 5 traces",
                                    "too many state variables", "its instance was refused: List should have at most 8"])
def test_other_words_are_not_read_as_over_the_limits(reason):
    assert secondary.over_limits_by_reason(reason) is False


def test_the_over_limit_codes_are_g6s_model_checking_refusals():
    assert secondary.OVER_LIMIT_CODES == frozenset({"G6_E_MODEL_CHECK_INPUT_TOO_LARGE",
                                                    "G6_E_MODEL_CHECK_OUTPUT_TOO_LARGE",
                                                    "G6_E_MODEL_CHECK_TIME_LIMIT_TOO_LONG"})


# --- test adequacy (arm T) against the label --------------------------------------------------------------------------

def test_newcombes_interval_for_two_independent_proportions_is_method_10_of_his_1998_paper():
    low, high = secondary.newcombe_independent(56, 70, 48, 80)             # Newcombe (1998), example (a)
    assert low == pytest.approx(0.0524, abs=5e-5) and high == pytest.approx(0.3339, abs=5e-5)
    assert secondary.newcombe_independent(1, 0, 1, 3) is None               # a zero denominator: no interval


def test_adequacy_is_a_table_against_the_label_with_a_row_for_no_mutation_run_and_the_difference_in_defective_share():
    def analysed(name, label, entries):
        return {"id": name, "label": label, "repository": "r", "outputs": {"T": {"report": report(adequacy=entries)}}}
    survived = [{"requirement": "R1", "checks": ["t"], "killed": 2, "survived": 1, "survivors": []},
                {"requirement": "R2", "result": "not_run", "reason": "no stated check names it"}]
    killed = [{"requirement": "R1", "checks": ["t"], "killed": 3, "survived": 0, "survivors": []}]
    not_run = [{"requirement": "R1", "result": "not_run", "reason": "no stated check names it"}]
    nothing_ran = [{"requirement": "R1", "checks": ["t"], "killed": 0, "survived": 0, "survivors": []}]
    rows = [analysed("d1", "defective", survived), analysed("d2", "defective", survived),
            analysed("c1", "correct", survived), analysed("d3", "defective", killed),
            analysed("c2", "correct", killed), analysed("c3", "correct", killed), analysed("c4", "correct", not_run),
            analysed("d4", "defective", nothing_ran), {"id": "c5", "label": "correct", "repository": "r", "outputs": {}}]
    got = secondary.adequacy(rows)
    assert got["table"] == {"survived": {"defective": 2, "correct": 1}, "none_survived": {"defective": 1, "correct": 2},
                            "no_mutation_run": {"defective": 1, "correct": 2}}
    assert got["patches"]["d4"] == "no_mutation_run" and got["patches"]["c5"] == "no_mutation_run"
    found = got["difference"]
    assert (found["with"]["count"], found["with"]["of"], found["without"]["count"], found["without"]["of"]) == (2, 3,
                                                                                                               1, 3)
    assert found["difference"] == pytest.approx(1 / 3)
    assert found["newcombe"] == pytest.approx(list(secondary.newcombe_independent(2, 3, 1, 3)))
    assert "changes no outcome" in got["note"]


# --- the reason codes, with the bootstrap over tasks and all their findings ---------------------------------------------

def finding(code, *, reproducible=True, compatible=None, not_comparable=False):
    return {"node": "a.py::t", "code": code, "result": {"reproducible": reproducible},
            "reference": None if compatible is None else {"compatible": compatible, "not_comparable": not_comparable}}


def test_the_share_interval_resamples_the_same_task_sets_as_v9s_bootstrap():
    tasks = [("r1", 1, 1), ("r1", 0, 1), ("r2", 1, 1), ("r2", 1, 1), ("r2", 0, 1)]
    pairs = [(repo, bool(x), False) for repo, x, _ in tasks]
    got = secondary.share_interval(tasks, seed=11, resamples=400)
    assert got["interval"] == pytest.approx(list(stats.stratified_bootstrap(pairs, seed=11, resamples=400,
                                                                            confidence=0.95)))
    assert secondary.share_interval([("r1", 0, 0)], seed=11, resamples=10) == {"interval": None, "undefined": 10}


def test_each_reason_code_gives_its_findings_and_their_shares_each_with_its_bootstrap_interval():
    def analysed(name, repository, label, found):
        return {"id": name, "repository": repository, "label": label, "findings": {"A": [], "C": [], "T": found}}
    rows = [analysed("d1", "r1", "defective", [finding("unconfirmed_counterexample"),
                                              finding("unconfirmed_counterexample", reproducible=False)]),
            analysed("c1", "r1", "correct", [finding("unconfirmed_counterexample", compatible=True)]),
            analysed("d2", "r2", "defective", [finding("defect_detected", compatible=False, not_comparable=True),
                                              {**finding("x"), "code": None}]),
            analysed("c2", "r2", "correct", [])]
    got = secondary.reason_codes(rows, seed=7, resamples=300)
    counter = got["T"]["unconfirmed_counterexample"]
    assert counter["findings"] == 3 and counter["on_defective"]["share"] == pytest.approx(2 / 3)
    assert counter["reproducible"]["share"] == pytest.approx(2 / 3)
    assert counter["compatible_with_reference"]["share"] == pytest.approx(1 / 3)
    assert counter["not_comparable"]["share"] == 0.0
    low, high = counter["on_defective"]["interval"]
    assert 0.0 <= low <= 2 / 3 <= high <= 1.0
    assert got["T"]["defect_detected"]["not_comparable"]["share"] == 1.0
    assert got["T"][secondary.NO_CODE]["findings"] == 1                   # a rerun recorded without its code
    assert got["C"] == {} and got["findings_per_patch"]["T"] == {"mean": 1.25, "most": 2}


# --- similarity to the hidden tests -----------------------------------------------------------------------------------

HIDDEN = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n    assert add(-1, 1) == 0\n"
SPEC = {"measure": "token_containment.v1", "min_run": 8, "threshold": 0.5}


def test_similarity_is_the_share_of_a_checks_tokens_in_long_runs_the_hidden_tests_hold_in_order():
    copied = HIDDEN.replace("test_add", "test_mine")
    unrelated = "import os\n\n\ndef test_env():\n    assert os.sep in ('/', '\\\\')\n"
    assert secondary.similarity(copied, [HIDDEN], SPEC) == pytest.approx(22 / 28)   # all but its first 5 tokens
    assert secondary.similarity(unrelated, [HIDDEN], SPEC) < 0.2
    assert secondary.similarity("# a comment only\n", [HIDDEN], SPEC) == 0.0
    assert secondary.similarity(copied, [], SPEC) == 0.0
    assert secondary.similarity("def f(:\n    pass ((\n", [HIDDEN], SPEC) == 0.0     # untokenizable: read as words
    for broken in ({**SPEC, "measure": "another"}, {**SPEC, "threshold": 0}, {**SPEC, "threshold": 1.5},
                   {**SPEC, "min_run": 0}, {"measure": "token_containment.v1"}):
        with pytest.raises(ValueError):
            secondary.check_similarity(broken)


def test_the_hidden_tests_are_a_contracts_hidden_test_files_and_the_lines_its_test_patch_adds():
    contract = {"groups": [
        {"origin": "hidden_tests", "overlay": {"files": {"tests/test_h.py": HIDDEN},
                                               "patch": "diff --git a/tests/test_p.py b/tests/test_p.py\n"
                                                        "--- a/tests/test_p.py\n+++ b/tests/test_p.py\n"
                                                        "@@ -1 +1,2 @@\n x = 1\n+def test_p():\n+    assert x\n"}},
        {"origin": "withheld_specification", "overlay": {"files": {"spec.py": "secret"}, "patch": ""}}]}
    assert secondary.hidden_texts(contract) == [HIDDEN, "def test_p():\n    assert x\n"]


def test_a_search_arms_every_proposed_check_is_measured_from_the_texts_it_kept_findings_or_not():
    # the coordinator's ruling on item 81: C and T keep the text of every proposed check they ran, and all are measured
    copied = HIDDEN.replace("test_add", "test_mine")
    template_text = "TEMPLATE = 'example.v1'\nBINDINGS = {'function': 'calc.add'}\n"
    outputs = {"p1": {"C": {"checks": [
        {"check": "g6p-check-1", "placed": "g6_check_a/test_c.py", "text": copied, "ran": True},
        {"check": "g6p-example-2", "placed": "g6_check_b/test_g6_example.py", "text": template_text, "ran": True}],
        "findings": [{"check": "g6p-check-1", "node": "g6_check_a/test_c.py", "file": "g6_check_a/test_c.py",
                      "text": copied, "code": "unconfirmed_failure"}]}}}
    got = secondary.measure_similarity(outputs, {"p1": [HIDDEN]}, SPEC)
    measured = got["patches"]["p1"]["C"]
    assert [(c["check"], c["above"], c["findings"]) for c in measured] == [
        ("g6p-check-1", True, ["g6_check_a/test_c.py"]), ("g6p-example-2", False, [])]
    assert got["above"] == [["p1", "C", "g6_check_a/test_c.py"]] and got["patches"]["p1"]["T"] == []


def test_similarity_is_measured_on_every_model_written_check_the_record_holds_and_flags_those_above_the_threshold():
    copied = HIDDEN.replace("test_add", "test_mine")
    outputs = {"p1": {
        "C": {"findings": [{"file": "g6_check_a/test_c.py", "node": "g6_check_a/test_c.py", "text": copied,
                            "code": "unconfirmed_failure"},
                           {"file": "tests/test_calc.py", "node": "tests/test_calc.py::test_zero", "text": HIDDEN,
                            "code": "defect_detected"}]},                       # a stated check: not model-written
        "T": {"findings": []},
        "E": {"kept": [{"placed": "g6_check_b/test_e.py", "text": "import os\n\n\ndef test_e():\n    assert os\n"}],
              "findings": [{"node": "g6_check_b/test_e.py::test_e", "file": "g6_check_b/test_e.py"}]},
        "R": {"kept": [{"placed": "g6_check_c/test_r.py", "text": copied}],
              "findings": [{"node": "g6_check_c/test_r.py::test_mine", "file": "g6_check_c/test_r.py"}]}}}
    got = secondary.measure_similarity(outputs, {"p1": [HIDDEN]}, SPEC)
    assert [c["file"] for c in got["patches"]["p1"]["C"]] == ["g6_check_a/test_c.py"]
    assert got["patches"]["p1"]["R"][0]["above"] is True and got["patches"]["p1"]["E"][0]["above"] is False
    assert sorted(got["above"]) == [["p1", "C", "g6_check_a/test_c.py"], ["p1", "R", "g6_check_c/test_r.py::test_mine"]]
    assert got["spec"] == SPEC and math.isclose(got["patches"]["p1"]["E"][0]["similarity"],
                                                secondary.similarity(outputs["p1"]["E"]["kept"][0]["text"], [HIDDEN],
                                                                     SPEC))
