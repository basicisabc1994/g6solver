# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Enforce exactly what the person confirmed (G6 0.3 design, 3.2 step 4 and section 4 step 5; slice 3, item 5).

The confirmed expectation compiles into its one example.v1 check, which the review's existing machinery runs on the
patch (``verification_templates.prepare``, then verification search's confined executor) as a check of
``person_confirmed`` provenance. Its result reaches the issuer like a stated check's: the run on the patch and the run
again are receipts, the run at the base commit where it failed is the control. The report shows the question, the
answer and the check's result apart from model results. With no question asked, every decided field is today's.

Development fixtures only: G6's test runs are replaced by recorded results, so nothing is confined and no model is
called.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys

import pytest

from g6.harness import confirmed_checks, patch_review, verification_search
from g6.harness.confirmed_expectation import ExpectationStandard, Question, confirm
from g6.harness.patch_review import review_patch
from g6.harness.verification_templates import instance_digest

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
ADD = "test_calc.py::test_add"
REQUIREMENTS = [{"id": "R1", "text": "add returns the sum of two positive integers", "checks": [ADD]}]
TASK = "Repair addition for positive integers."


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path.parent / (tmp_path.name + "_home")))
    (tmp_path / "calc.py").write_text(BAD)
    (tmp_path / "test_calc.py").write_text(TEST)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    (tmp_path / "calc.py").write_text(GOOD)
    standard(root=tmp_path)
    return tmp_path


def result(passed=(), failed=()):
    outcomes = {**{n: {"setup": "passed", "call": "passed", "teardown": "passed"} for n in passed},
                **{n: {"setup": "passed", "call": "failed", "teardown": "passed"} for n in failed}}
    return {"verdict": "failed" if failed else "passed", "said": "", "passed": len(passed), "failed": len(failed),
            "not_passed": 0, "total": len(outcomes), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [], "output": "", "seconds": 0.25,
            "python": sys.executable, "interpreter_source": "g6",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": False, "receipt": ""}


def stated(monkeypatch):
    """The stated check passes on the patch, fails at the base commit, and passes again: supported, as today."""
    monkeypatch.setattr(patch_review, "_execute", lambda root, ids, **k: result([ADD]) if ids == [ADD] else
                        result([ADD]))
    monkeypatch.setattr(patch_review, "_execute_at_base", lambda change, ids, **k: result(failed=[ADD]))


def compiled(monkeypatch, *verdicts):
    """The compiled check's runs, in order (on the patch, at the base commit, again on the patch): each "passed" or
    "failed"; what each run was given is recorded."""
    given, left = [], list(verdicts)

    def run_check(change, placed, source, node, *, at_base, timeout_s, roots=(), artefact=None):
        # artefact: the review's snapshot, which the real executor checks each copy on the patch against
        # This historical flag describes a receipt on the patch. The base control now also receives the snapshot
        # so its projected starting copy can be checked, but is not itself a receipt on the patch's bytes.
        given.append({"at_base": at_base, "node": node, "source": source,
                      "bound": artefact is not None and not at_base})
        test = f"{node}::test_example"
        if left.pop(0) == "passed":
            return {**result([test]), "source_before": "d" * 64}
        return {**result(failed=[test]), "source_before": "d" * 64,
                "failure_messages": {test: "E   Failed: case 0 returned -1, expected 5"}}

    monkeypatch.setattr(verification_search, "_run_check", run_check)
    return given


def standard(function="calc.add", option="A", *, root=None) -> ExpectationStandard:
    shown = Question(requirement_id="R1", function=function, args=[2, 3],
                     outcomes={"A": {"returns": 5}, "B": {"returns": -1}}, scope="this one case: this input, this "
                     "outcome", cited="Repair addition for positive integers.", baseline_sha256="b" * 64)
    one = confirm(shown, option, answered_by="person:Ada", answered_at="2026-11-09T00:00:00Z")
    if root is not None:
        from g6.harness import behaviour_question as bq, question_store as qs
        from g6.harness.executable_scenario import Scenario, save

        scenario = Scenario.resolve(root, shown)
        save(shown, scenario)
        candidate = bq.Candidate.make(source='caller', requirement_id=shown.requirement_id,
            function=shown.function, args=shown.args, kwargs=shown.kwargs, outcomes=shown.outcomes,
            scope=shown.scope, cited=shown.cited, evidence=['calc.py: add'])
        store = qs.TaskQuestions(qs.task_identity(root, TASK))
        store.ask(shown, None, candidate)
        store.answer(one.answer, scenario=scenario)
    return ExpectationStandard().with_expectation(one)


def review(repo, **extra):
    return review_patch(repo, task=TASK, requirements=REQUIREMENTS, **extra)


def decided(reply: dict) -> dict:
    """The decided fields, as the off-path test reads them (``tests/harness/test_patch_review_off_path.py``)."""
    assessment = reply.get("assessment") or {}
    return {"ok": reply.get("ok"), "completion_state": reply.get("completion_state"),
            "warning_code": (reply.get("warning_card") or {}).get("code"), "outcome": assessment.get("outcome"),
            "requirements": [[r["id"], r["status"]] for r in assessment.get("requirements", [])],
            "reasons": [[r["stage"], r["code"]] for r in assessment.get("reasons", [])],
            "merge": (reply.get("merge") or {}).get("status"), "unknowns": len(assessment.get("unknowns", []))}


def _timeless(report: str) -> str:
    """The report without its timings, which no two runs share."""
    return re.sub(r"[0-9.]+ seconds", "N seconds", report)


def test_with_no_question_asked_every_decided_field_is_today_s(repo, monkeypatch):
    stated(monkeypatch)
    given = compiled(monkeypatch)
    today = review(repo)
    for extra in ({"confirmed": None}, {"confirmed": ExpectationStandard()}):
        got = review(repo, **extra)
        assert decided(got) == decided(today) and set(got["assessment"]) == set(today["assessment"])
        assert _timeless(got["assessment"]["report"]) == _timeless(today["assessment"]["report"])
        assert got["assessment"]["requirement_evidence"]["requirements"] == \
            today["assessment"]["requirement_evidence"]["requirements"]
    assert given == [] and "confirmed_expectations" not in today["assessment"]


def test_the_confirmed_case_is_its_one_example_v1_check_run_on_the_patch_and_counted_by_the_issuer(repo, monkeypatch):
    stated(monkeypatch)
    given = compiled(monkeypatch, "passed", "failed", "passed")
    held = standard()
    [one] = held.expectations
    got = review(repo, confirmed=held)
    assert decided(got) == decided(review(repo))   # a case that holds changes no decided field
    assert [g["at_base"] for g in given[:3]] == [False, True, False]
    assert [g["bound"] for g in given[:3]] == [True, False, True]   # each run on the patch, bound to the artefact
    assert "'cases': [{'args': [2, 3], 'kwargs': {}, 'expect': 'returns', 'value': 5}]" in given[0]["source"]
    assert "TEMPLATE = 'example.v1'" in given[0]["source"]
    held_out = got["assessment"]["confirmed_expectations"]
    [shown] = held_out["expectations"]
    assert shown["question"]["function"] == "calc.add" and shown["answer"]["option"] == "A"
    assert shown["answer"]["answered_by"] == "person:Ada" and "not authenticated" in shown["answer"]["identity"]
    check = shown["check"]
    assert check["result"] == "passed" and check["provenance"] == "person_confirmed"
    assert check["check_sha256"] == one.check_sha256 == instance_digest(one.instance)
    evidence = held_out["evidence"]
    [statement] = evidence["expectations"]
    assert statement["expectation"] == one.expectation_id and statement["checked"] is True
    [issued] = evidence["certificate"]["checks"]
    assert issued["kind"] == "independent_executable" and issued["author"] == "person:Ada"
    assert len(evidence["certificate"]["receipts"]) == 2   # the run on the patch, and the run again after the control
    assert evidence["standard_sha256"] == held.sha256


def test_a_confirmed_case_that_fails_on_the_patch_is_a_reproduced_defect(repo, monkeypatch):
    stated(monkeypatch)
    compiled(monkeypatch, "failed", "failed")
    got = review(repo, confirmed=standard())
    assessment = got["assessment"]
    assert assessment["outcome"] == patch_review.DEFECT
    assert ["tier_1", patch_review.CONFIRMED_FAILED] in [[r["stage"], r["code"]] for r in assessment["reasons"]]
    [found] = [d for d in assessment["completion"]["defects_reproduced"]["stated_and_repository_checks"]
               if d["code"] == patch_review.CONFIRMED_FAILED]
    assert found["provenance"] == "person_confirmed" and "BINDINGS" in found["reproduction"]["text"]
    assert found["reproduction"]["caution"] == confirmed_checks.CAUTION
    report = assessment["report"]
    assert "A case the person confirmed fails" in report.split("## Not established")[0]
    [statement] = assessment["confirmed_expectations"]["evidence"]["expectations"]
    assert statement["checked"] is False and statement["refute"] is False and "did not pass" in statement["not_counted"]


def test_a_confirmed_case_the_base_commit_already_has_is_enforced_and_not_counted(repo, monkeypatch):
    stated(monkeypatch)
    compiled(monkeypatch, "passed", "passed")
    got = review(repo, confirmed=standard())
    assert got["assessment"]["outcome"] == patch_review.SUPPORTED
    [statement] = got["assessment"]["confirmed_expectations"]["evidence"]["expectations"]
    assert statement["checked"] is False and "passes at the base commit too" in statement["not_counted"]


def test_a_confirmed_case_g6_could_not_apply_leaves_the_evidence_short(repo, monkeypatch):
    stated(monkeypatch)
    given = compiled(monkeypatch)
    got = review(repo, confirmed=standard(function="calc.missing"))
    assessment = got["assessment"]
    assert given == [] and assessment["outcome"] == patch_review.INSUFFICIENT
    assert ["evidence", patch_review.CONFIRMED_NOT_RUN] in [[r["stage"], r["code"]] for r in assessment["reasons"]]
    [shown] = assessment["confirmed_expectations"]["expectations"]
    assert shown["check"]["result"] == confirmed_checks.NOT_RUN and "could not be applied" in shown["check"]["detail"]


def test_with_an_integrity_problem_the_confirmed_case_is_not_run_and_nothing_is_counted(repo, monkeypatch):
    stated(monkeypatch)
    given = compiled(monkeypatch)
    (repo / "conftest.py").write_text("import pytest\n")
    got = review(repo, confirmed=standard())
    assessment = got["assessment"]
    assert assessment["outcome"] == patch_review.INTEGRITY and given == []
    [shown] = assessment["confirmed_expectations"]["expectations"]
    assert shown["check"]["detail"] == patch_review.INTEGRITY_SKIP
    assert assessment["confirmed_expectations"]["evidence"]["certificate"]["receipts"] == []


def test_the_report_shows_the_question_the_answer_and_the_result_apart_from_model_results(repo, monkeypatch):
    stated(monkeypatch)
    compiled(monkeypatch, "passed", "failed", "passed")
    report = review(repo, confirmed=standard())["assessment"]["report"]
    part = report.split("## What the person confirmed", 1)[1].split("##", 1)[0]
    [line] = [one for one in part.splitlines() if one.startswith("- ")]
    for said in ("calc.add(2, 3)", "the person chose `A`", "returns 5", "person:Ada",
                 "recorded as given, not authenticated", "its check passes on the patch", "the evidence issuer"):
        assert said in line, said
    assert "proposed" not in part and "model" not in part
    assert report.index("## Demonstrated on the stated checks") < report.index("## What the person confirmed") < \
        report.index("## For you to decide")


def test_confirmed_is_the_standard_of_confirmed_expectations_and_nothing_else(repo, monkeypatch):
    stated(monkeypatch)
    for wrong in ({"answer": "A"}, [standard().expectations[0]], "A"):
        got = review(repo, confirmed=wrong)
        assert got["completion_state"] == "blocked-escalated" and "ExpectationStandard" in got["error"]


def test_the_compiled_check_is_g6_s_text_with_the_person_s_case_and_nothing_the_patch_wrote(repo, monkeypatch):
    stated(monkeypatch)
    given = compiled(monkeypatch, "passed", "failed", "passed")
    [one] = standard().expectations
    got = review(repo, confirmed=standard())
    [shown] = got["assessment"]["confirmed_expectations"]["expectations"]
    assert shown["check"]["source_sha256"] == hashlib.sha256(given[0]["source"].encode("utf-8")).hexdigest()
    assert one.instance.provenance == "person_confirmed" and one.instance.template == "example.v1"
    assert "calc.py" not in given[0]["source"].split("BINDINGS =", 1)[0]   # the fixed text names no file of the patch
