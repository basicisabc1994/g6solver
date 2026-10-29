# SPDX-License-Identifier: Apache-2.0
"""V10's study runner: the driver's inputs from V9's results, arm A's findings among them, read from V9's recorded
arm-3 replies (``assessment.completion.defects_reproduced``). The reply used here was recorded on a development
fixture (``fixtures/v10_arm3``: ``record_reply.py``, G6 at the verification search commit the reply names), never on
task data. The fixture's tree is rebuilt here; nothing calls a model."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from patch_review_search import analysis, driver, record, reruns, runner
from patch_review_utility import executor
from patch_review_utility.dispositions import ACCEPT, make

HERE = Path(__file__).resolve().parent / "fixtures" / "v10_arm3"
sys.path.insert(0, str(HERE))
import record_reply  # noqa: E402

RECORDED = json.loads((HERE / "reply.json").read_text(encoding="utf-8"))
ARM_3 = RECORDED["arm_3"]
OK = make(ACCEPT)


def tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
    return root


@pytest.fixture
def trees(tmp_path):
    base = tree(tmp_path / "base", record_reply.FILES)
    patched = tree(tmp_path / "patched", {**record_reply.FILES, **record_reply.PATCH})
    return base, patched


def task(name="t1", *, patch="non_empty", arm_3=ARM_3, label="defective"):
    return {"task_id": name, "repository": "fixture/calc", "patch": patch, "label": label,
            "oracle_controls_passed": True,
            "contract": [{"id": "R1", "text": record_reply.STATEMENT, "source": "statement"}],
            "arms": {"tests": dict(OK), "self_review": dict(OK), "g6": arm_3["disposition"], "direct_checks": dict(OK)},
            "details": {"g6": {"row": arm_3["row"], "stated_checks": arm_3["stated_checks"],
                               "defects_reproduced": arm_3["defects_reproduced"]}}}


def test_the_recorded_reply_comes_from_a_development_fixture_and_g6_with_verification_search():
    assert ARM_3["row"]["g6_outcome"] == "defect_detected" and len(RECORDED["g6_commit"]) == 40
    assert ARM_3["defects_reproduced"]["stated_and_repository_checks"][0]["check"] == record_reply.CHECK


def test_arm_as_findings_are_its_failed_stated_checks_with_the_patched_trees_text(trees):
    _, patched = trees
    found = runner.arm_a_findings(task(), patched)
    assert found == [{"file": "tests/test_calc.py", "text": record_reply.FILES["tests/test_calc.py"],
                      "node": record_reply.CHECK, "check": record_reply.CHECK, "code": "check_failed"}]
    assert reruns.problem(found[0]) == ""


def test_a_finding_whose_file_is_not_on_the_patched_tree_is_kept_and_cannot_be_rerun(trees, tmp_path):
    found = runner.arm_a_findings(task(), tmp_path / "elsewhere")
    assert found[0]["text"] is None and reruns.problem(found[0])


def test_a_regression_of_the_repositorys_own_tests_is_one_of_arm_as_findings(trees):
    _, patched = trees
    derived = json.loads(json.dumps(ARM_3))
    derived["defects_reproduced"]["stated_and_repository_checks"][0].update(provenance="derived",
                                                                            code="derived_test_regressed")
    found = runner.arm_a_findings(task(arm_3=derived), patched)
    assert [(f["node"], f["code"]) for f in found] == [(record_reply.CHECK, "derived_test_regressed")]
    assert found[0]["text"] == record_reply.FILES["tests/test_calc.py"]


def test_a_search_arms_report_also_gives_its_proposed_checks_findings_but_never_a_model_only_trace(trees):
    _, patched = trees
    defects = json.loads(json.dumps(ARM_3["defects_reproduced"]))
    repro = {"file": "tests/g6_check_0123456789ab/test_x.py", "text": "def test_x():\n    assert 0\n",
             "node": "tests/g6_check_0123456789ab/test_x.py::test_x"}
    defects["proposed_checks"] = [
        {"check": "p1", "code": "unconfirmed_counterexample", "reproduction": repro},
        {"check": "p2", "code": "unconfirmed_failure", "reproduction": repro,
         "formal": {"correspondence": {"result": "not_reproduced"}}},
        {"check": "p3", "code": "fails_on_base_too", "reproduction": repro}]
    found = runner.findings(defects, patched, search_arm=True)
    assert [f["check"] for f in found] == [record_reply.CHECK, "p1"] and found[1]["text"] == repro["text"]
    assert [f["check"] for f in runner.findings(defects, patched)] == [record_reply.CHECK]


def test_other_defects_and_a_reply_without_a_completion_section_give_no_arm_a_finding(trees):
    _, patched = trees
    other = json.loads(json.dumps(ARM_3))
    other["defects_reproduced"]["stated_and_repository_checks"][0].update(code="run_not_trusted")
    assert runner.arm_a_findings(task(arm_3=other), patched) == []
    assert runner.arm_a_findings(task(arm_3={**ARM_3, "defects_reproduced": None}), patched) == []


def test_the_driver_gets_every_non_empty_task_in_draw_order_and_every_other_one_counted(trees):
    _, patched = trees
    v9 = {"seed": 1, "tasks": [task("t0"), task("t1", patch="empty"), task("t2"), task("t3", patch="not_applying")]}
    patches, excluded = runner.driver_inputs(v9, lambda name: patched)
    assert [(p["id"], p["draw_index"]) for p in patches] == [("t0", 0), ("t2", 2)]
    assert excluded == [{"id": "t1", "draw_index": 1, "reason": "empty"},
                        {"id": "t3", "draw_index": 3, "reason": "not_applying"}]
    first = patches[0]
    assert first["arm_a"] == ARM_3["disposition"] and first["contract_ids"] == ["R1"]
    assert first["arm_a_stated_checks"] == ARM_3["stated_checks"] and first["arm_a_findings"][0]["node"] == \
        record_reply.CHECK


def test_end_to_end_arm_as_discovery_k_k_ref_and_j_read_real_values(trees, tmp_path):
    base, patched = trees
    v9 = {"seed": 7, "tasks": [task("t0")]}
    patches, excluded = runner.driver_inputs(v9, lambda name: patched)

    def search(patch, *, meter, **kwargs):
        return {"own": dict(OK), "model_calls": 0}

    def e_arm(patch, *, meter, **kwargs):
        return {"disposition": dict(OK), "findings": [], "calls": {"due": 1, "made": 0}}

    def rerun(patch, arm, reproduction):
        return reruns.before_labels(executor.Executor(), patched, base, reproduction, timeout_s=60)

    path = tmp_path / "record.jsonl"
    got = driver.study(patches, arm_c=search, arm_t=search, arm_e=e_arm, arm_r=e_arm, spending=driver.Spending(300.0),
                       record=record.Record(path), rerun=rerun, excluded=excluded)
    (result,) = got["reruns"]
    assert result["arm"] == "A" and result["result"]["reproducible"] is True
    assert result["result"]["patched"] == ["fail"] * 3 and result["result"]["base"] == ["pass"] * 3
    reference = [{"patch": "t0", "arm": "A", "node": record_reply.CHECK,
                  "result": reruns.on_reference_fix(executor.Executor(), base, patches[0]["arm_a_findings"][0],
                                                    timeout_s=60)}]
    found = analysis.analyse(path, got["record"], v9=v9, reference=reference, resamples=50)
    assert found["discovery"]["A"]["count"] == 1 and found["discovery"]["A"]["actionable_findings"] == 1
    assert found["discovery"]["A"]["compatible_with_reference"] == 1
    head = found["headline"]["arms"]["A"]
    assert (head["K"]["count"], head["K_ref"]["count"], head["J"]["count"]) == (1, 1, 0)
