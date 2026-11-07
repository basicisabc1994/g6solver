# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A stated check's control comes before the receipt it backs (G6 0.3, slice 2 follow-up).

The issuer counts a pass only once the check has been shown able to fail on the requirement. In a patch review the
control is G6's run of the check at the base commit, which comes after the stated checks' run on the patch, so that
pass can never be counted. The review now runs again on the patch, after the base-commit run, each stated check of a
requirement for new behaviour that passed on the patch and failed at the base commit: the order recorded is the order
that happened. That run changes no decided field; the issuer's reading of it is in ``assessment.requirement_evidence``.

- a new-behaviour check that fails at the base commit and passes on the patch is checked by the issuer;
- one that passes at the base commit too is not counted, and the report says why;
- a requirement to keep behaviour has no negative control in a review, so it is not counted, and the report says why;
- one that fails on the patch is not counted (the review runs nothing at the base commit after a defect);
- one that passes on the patch and then fails when run again is not counted, and the review's fields stay as they were.

Development fixtures only; G6's test runs are replaced by recorded results, so nothing here is confined and no model is
called.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from g6.evidence.mapping import id_of
from g6.harness import patch_review, requirement_evidence
from g6.harness.patch_review import review_patch

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
TEST = ("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_negative():\n    assert add(-2, -3) == -5\n")
ADD, NEGATIVE = "test_calc.py::test_add", "test_calc.py::test_negative"
SUM = [{"id": "R1", "text": "add returns the sum of two positive integers", "checks": [ADD]},
       {"id": "R2", "text": "add returns the sum of two negative integers", "checks": [NEGATIVE]}]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "calc.py").write_text(BAD)
    (tmp_path / "test_calc.py").write_text(TEST)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    (tmp_path / "calc.py").write_text(GOOD)
    return tmp_path


def result(passed=(), failed=()):
    outcomes = {**{node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in passed},
                **{node: {"setup": "passed", "call": "failed", "teardown": "passed"} for node in failed}}
    return {"verdict": "failed" if failed else "passed", "said": "", "passed": len(passed), "failed": len(failed),
            "not_passed": 0, "total": len(passed) + len(failed), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [], "output": "", "seconds": 0.25,
            "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": False, "receipt": ""}


class Runs:
    """Stands in for every test run of a review: each in-place run of ``ids`` gives the next of ``in_place[ids]``
    (the last one again once they run out), and the run at the base commit gives ``at_base``. Records the calls."""

    def __init__(self, monkeypatch, in_place, at_base=None):
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        given = {ids: list(found) if isinstance(found, list) else [found] for ids, found in in_place.items()}

        def execute(root, ids, *, timeout_s, change=None, roots=()):
            self.calls.append(("in_place", tuple(ids)))
            queue = given[tuple(ids)]
            return queue.pop(0) if len(queue) > 1 else queue[0]

        def execute_at_base(change, ids, *, timeout_s, roots=()):
            self.calls.append(("base", tuple(ids)))
            return at_base if at_base is not None else {"verdict": "not_run", "said": "no run was recorded"}

        monkeypatch.setattr(patch_review, "_execute", execute)
        monkeypatch.setattr(patch_review, "_execute_at_base", execute_at_base)


def review(repo, requirements=SUM):
    return review_patch(repo, task="Repair addition for positive and negative integers.", requirements=requirements)


def statements(got) -> dict[str, dict]:
    return {s["requirement"]: s for s in got["assessment"]["requirement_evidence"]["requirements"]}


def report_line(got, ident: str) -> str:
    return next(line for line in got["assessment"]["report"].splitlines() if f"`{ident}`" in line and "issuer" in line)


def test_a_new_behaviour_check_that_fails_at_base_and_passes_on_the_patch_is_checked_by_the_issuer(repo, monkeypatch):
    runs = Runs(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]), ("test_calc.py",): result([ADD, NEGATIVE])},
                at_base=result(failed=[ADD, NEGATIVE]))
    got = review(repo)
    for ident in ("R1", "R2"):
        found = statements(got)[ident]
        assert found["checked"] is True and found["support"] is True and found["gap"] == "", found
        assert "the evidence issuer: checked" in report_line(got, ident)
    assert got["assessment"]["requirement_evidence"]["state"] == "verified"   # the issuer's state, as a state
    assert got["completion_state"] == "qualified-draft"   # the review's own label is untouched
    # one run more than before: the two checks whose control came first, again on the patch, after the base run
    assert runs.calls == [("in_place", (ADD, NEGATIVE)), ("in_place", ("test_calc.py",)), ("base", (ADD, NEGATIVE)),
                          ("in_place", (ADD, NEGATIVE))]
    body = got["assessment"]["requirement_evidence"]["certificate"]
    # every run of each check on the patch, in the order it ran (repair 2): the stated checks' run, the run of the tests
    # that touch the changed modules (which collects both), then, after the run at the base commit, the run again
    assert [(r["receipt_id"], r["seq"]) for r in body["receipts"]] == [
        (id_of("stated-run", ADD), 2), (id_of("derived", ADD), 3), (id_of("again", ADD), 5),
        (id_of("stated-run", NEGATIVE), 2), (id_of("derived", NEGATIVE), 3), (id_of("again", NEGATIVE), 5)]
    assert {k["seq"] for c in body["checks"] for k in c["controls"]} == {4}   # the base run, before the run again


def test_a_check_that_passes_at_the_base_commit_too_is_not_counted_and_the_report_says_why(repo, monkeypatch):
    runs = Runs(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]), ("test_calc.py",): result([ADD, NEGATIVE]),
                              (NEGATIVE,): result([NEGATIVE])},
                at_base=result([ADD], [NEGATIVE]))   # R1's check passes on the unpatched code too
    got = review(repo)
    assert statements(got)["R1"]["checked"] is False and statements(got)["R2"]["checked"] is True
    assert "passes at the base commit too" in statements(got)["R1"]["not_counted"]
    assert "not counted: its check passes at the base commit too" in report_line(got, "R1")
    assert runs.calls[-1] == ("in_place", (NEGATIVE,))   # only the check whose control was killed runs again


def test_a_requirement_to_keep_behaviour_has_no_negative_control_in_a_review_so_it_is_not_counted():
    # under the mapping table's stated row, its known-good reference is the base commit (a positive control); no run
    # of a review seeds a defect for its check, so nothing shows the check can fail, and nothing is invented for it
    keep = {"id": "K1", "text": "add keeps adding", "kind": "preserve", "provenance": "stated",
            "checks": [{"id": ADD, "provenance": "stated", "verdict": "passed"}]}
    runs = [{"tier": 2, "check": requirement_evidence.AT_BASE, "outcomes": {ADD: "passed"}}]
    got = requirement_evidence.assess([keep], runs, artifact="a" * 64, base_commit="0" * 40, check_sha={ADD: "c" * 64},
                                      written=lambda _check: False, environment={})
    found = got["requirements"][0]
    assert found["checked"] is False and found["support"] is False and found["refute"] is False
    assert "keeps behaviour" in found["not_counted"] and "no run seeded a defect" in found["not_counted"]
    assert [c["code"] for c in got["refused"][0]["codes"]] == ["G6_E_EVIDENCE_NO_NEGATIVE_CONTROL"]
    from g6.harness import patch_report

    line = patch_report.render({"outcome": "supported_within_scope", "reasons": [], "requirements": [],
                                "completion": {"demonstrated": [{"requirement": "K1", "kind": "preserve",
                                                                 "checks": [ADD]}]},
                                "requirement_evidence": got})
    assert "the evidence issuer: not counted: it keeps behaviour" in line


def test_a_check_that_fails_on_the_patch_is_not_counted_and_nothing_runs_at_the_base_commit_after(repo, monkeypatch):
    runs = Runs(monkeypatch, {(ADD, NEGATIVE): result([ADD], [NEGATIVE])})
    got = review(repo)
    assert got["assessment"]["outcome"] == "defect_detected"
    found = statements(got)["R2"]
    assert found["checked"] is False and found["refute"] is False   # a failure not shown able to fail is no refutation
    assert "failed on the patch, and the review runs nothing at the base commit after a problem" in found["not_counted"]
    assert "was not run at the base commit" in statements(got)["R1"]["not_counted"]   # its pass has no control either
    assert [kind for kind, _ in runs.calls] == ["in_place"]   # no run is added after a defect


def test_a_check_that_passes_then_fails_when_run_again_is_not_counted_and_the_review_is_as_it_was(repo, monkeypatch):
    flaky = [result([ADD, NEGATIVE]), result([ADD], [NEGATIVE])]   # NEGATIVE passes, then fails, on the same patch
    Runs(monkeypatch, {(ADD, NEGATIVE): flaky, ("test_calc.py",): result([ADD, NEGATIVE])},
         at_base=result(failed=[ADD, NEGATIVE]))
    got = review(repo)
    assert statements(got)["R1"]["checked"] is True
    found = statements(got)["R2"]
    assert found["checked"] is False and found["refute"] is False and "advisory_check_failed" in found["gap"]
    assert "run again on the patch after its control, its check failed" in found["not_counted"]
    # the review's own reading is the first run's, as before: the run again decides no field
    assert got["assessment"]["outcome"] == "supported_within_scope"
    assert [r["status"] for r in got["assessment"]["requirements"]] == ["passed_on_stated_checks"] * 2


def _decided(got) -> dict:
    assessment = got["assessment"]
    scope = {k: v for k, v in assessment["scope"].items() if k != "seconds"}
    return {"completion_state": got["completion_state"], "warning_card": got["warning_card"],
            "evidence": got["evidence"], "outcome": assessment["outcome"], "reasons": assessment["reasons"],
            "requirements": assessment["requirements"], "unknowns": assessment["unknowns"],
            "next_actions": assessment["next_actions"], "scope": scope, "completion": assessment["completion"],
            "merge": got["merge"], "gate": got["gate"]}


@pytest.mark.parametrize("again", [[result([ADD, NEGATIVE])], [result([ADD], [NEGATIVE])]], ids=["passes", "fails"])
def test_the_run_again_changes_no_decided_field(repo, monkeypatch, again):
    def reviewed(rerun: bool):
        Runs(monkeypatch, {(ADD, NEGATIVE): [result([ADD, NEGATIVE]), *again],
                           ("test_calc.py",): result([ADD, NEGATIVE])}, at_base=result(failed=[ADD, NEGATIVE]))
        if not rerun:
            monkeypatch.setattr(patch_review, "_run_again", lambda *args, **kwargs: {})
        return review(repo)

    with_it = reviewed(True)
    monkeypatch.undo()
    without = reviewed(False)
    assert _decided(with_it) == _decided(without)
    assert "again" in str(with_it["assessment"]["requirement_evidence"]) and "again" not in str(
        without["assessment"]["requirement_evidence"]["certificate"]["receipts"])
