# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Every observation of a stated check on the patch reaches the issuer (G6 0.3, slice 2, repair 2; review 2's finding
5; class ``failure_read_as_success``, met in repair 1).

A derived test-file run could see a stated node fail after its first pass, while only the first verdict and the run
again reached the issuer: a pass, a fail and a pass gave it two passes and no caveat. Now every run of a stated check on
the patch adds its receipt, in the order it ran: the first run, each derived or tier-2 run that collects it, and the
run again. The inventory lists, from the code, every place the review runs tests, and each one that runs on the patch
is shown forwarding what it saw of a stated node.

Development fixtures only: G6's test runs are replaced by recorded results, so nothing is confined, and no model runs.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import g6
from g6.evidence import mapping as m
from g6.harness import patch_review
from g6.harness.patch_review import review_patch

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
TEST = ("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_extra():\n    assert add(0, 0) == 0\n")
NODE, EXTRA, FILE = "test_calc.py::test_add", "test_calc.py::test_extra", "test_calc.py"


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


def result(passed=(), failed=(), skipped=()):
    outcomes = {**{node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in passed},
                **{node: {"setup": "passed", "call": "failed", "teardown": "passed"} for node in failed},
                **{node: {"setup": "passed", "call": "skipped", "teardown": "passed"} for node in skipped}}
    return {"verdict": "failed" if failed else "passed", "said": "", "passed": len(passed), "failed": len(failed),
            "not_passed": len(skipped), "total": len(outcomes), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [], "output": "", "seconds": 0.25,
            "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": False, "receipt": ""}


class Runs:
    """Stands in for every test run of a review: each in-place run of ``ids`` gives the next of ``in_place[ids]``
    (the last again once they run out), a replay at the base commit and on the patch gives ``replay``, and the run at
    the base commit gives ``at_base``. Records the calls."""

    def __init__(self, monkeypatch, in_place, at_base=None, replay=None):
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        given = {ids: list(found) if isinstance(found, list) else [found] for ids, found in in_place.items()}

        def execute(root, ids, *, timeout_s, change=None, roots=()):
            if change is not None:
                self.calls.append(("replay", tuple(ids)))
                return dict(replay or {})
            self.calls.append(("in_place", tuple(ids)))
            queue = given[tuple(ids)]
            return queue.pop(0) if len(queue) > 1 else queue[0]

        def execute_at_base(change, ids, *, timeout_s, roots=()):
            self.calls.append(("base", tuple(ids)))
            return at_base if at_base is not None else {"verdict": "not_run", "said": "no run was recorded"}

        monkeypatch.setattr(patch_review, "_execute", execute)
        monkeypatch.setattr(patch_review, "_execute_at_base", execute_at_base)


def review(repo):
    return review_patch(repo, task="Fix addition", requirements=[{"id": "R1", "text": "add returns the sum",
                                                                  "checks": [NODE]}])


def statement(got) -> dict:
    return got["assessment"]["requirement_evidence"]["requirements"][0]


def receipts(got) -> list[tuple[str, str, int]]:
    body = got["assessment"]["requirement_evidence"]["certificate"]
    return [(r["receipt_id"], r["result"], r["seq"]) for r in body["receipts"]]


# --- review 2's finding 5, brought in as this change's own ------------------------------------------------------------


def test_finding_5_a_failure_of_the_stated_check_in_the_derived_run_is_kept_as_a_caveat(repo, monkeypatch):
    def reviewed(derived_passes: bool):
        not_replayed = {FILE: {"verdict": "not_run", "baseline": "failed", "patched": "not_run"}}
        runs = Runs(monkeypatch, {(NODE,): [result([NODE]), result([NODE])],
                                  (FILE,): result([NODE]) if derived_passes else result(failed=[NODE])},
                    at_base=result(failed=[NODE]), replay=not_replayed)
        return runs, review(repo)

    assert statement(reviewed(True)[1])["checked"] is True   # the positive control: every run passed
    runs, got = reviewed(False)
    assert runs.calls[:2] == [("in_place", (NODE,)), ("in_place", (FILE,))]
    assert any(run["verdict"] == "failed" for run in got["assessment"]["scope"]["checks_run"])
    found = statement(got)
    assert found["checked"] is False, (
        "the issuer was given only the passing runs of a check G6 also observed fail on the same patch", found)
    assert found["refute"] is False and "advisory_check_failed" in found["gap"]   # a caveat, never a refutation
    assert "did not (failed)" in found["not_counted"]
    assert receipts(got) == [(m.id_of("stated-run", NODE), "pass", 2), (m.id_of("derived", NODE), "fail", 3),
                             (m.id_of("again", NODE), "pass", 5)]


# --- each run on the patch, forwarded in the order it ran -----------------------------------------------------------


def test_a_replay_that_ran_the_stated_check_on_the_patch_adds_what_it_saw_in_the_order_it_ran(repo, monkeypatch):
    # the derived run fails on another test of the file; its replay on the patch, in a copy, skips the stated one
    Runs(monkeypatch, {(NODE,): result([NODE]), (FILE,): result([NODE], failed=[EXTRA])},
         at_base=result(failed=[NODE]),
         replay={FILE: {**result([EXTRA], skipped=[NODE]), "baseline": "passed"}})
    got = review(repo)
    assert receipts(got) == [(m.id_of("stated-run", NODE), "pass", 2), (m.id_of("derived", NODE), "pass", 3),
                             (m.id_of("replay", FILE, NODE), "unknown", 4), (m.id_of("again", NODE), "pass", 6)]
    controls = got["assessment"]["requirement_evidence"]["certificate"]["checks"][0]["controls"]
    assert [k["seq"] for k in controls] == [5]   # the run at the base commit, after every run before it
    assert statement(got)["checked"] is False and "check_result_unknown" in statement(got)["gap"]
    observed = got["assessment"]["requirement_evidence"]["observed"]
    assert observed == [{"run": ["derived"], "verdicts": {NODE: "passed"}},
                        {"run": ["replay", FILE], "verdicts": {NODE: "skipped"}}]


def test_a_replay_on_the_patch_that_kept_no_per_test_result_is_an_unknown_never_dropped(repo, monkeypatch):
    # its tests on the patch differ from the base commit's, so the replay kept no per-test result: it ran, and passed
    unkept = {FILE: {"verdict": "not_run", "said": "the tests differ", "baseline": "passed", "patched": "passed"}}
    Runs(monkeypatch, {(NODE,): result([NODE]), (FILE,): result([NODE], failed=[EXTRA])},
         at_base=result(failed=[NODE]), replay=unkept)
    got = review(repo)
    assert (m.id_of("replay", FILE, NODE), "unknown", 4) in receipts(got)
    assert statement(got)["checked"] is False and patch_review.NOT_READ in statement(got)["not_counted"]


def test_a_run_that_did_not_collect_the_stated_check_adds_nothing_for_it(repo, monkeypatch):
    Runs(monkeypatch, {(NODE,): result([NODE]), (FILE,): result([EXTRA])}, at_base=result(failed=[NODE]))
    got = review(repo)
    assert receipts(got) == [(m.id_of("stated-run", NODE), "pass", 2), (m.id_of("again", NODE), "pass", 4)]
    assert statement(got)["checked"] is True


def test_a_derived_run_of_code_from_outside_the_tree_is_an_unknown_for_the_stated_check(repo, monkeypatch):
    derived = result([NODE])
    Runs(monkeypatch, {(NODE,): result([NODE]), (FILE,): derived}, at_base=result(failed=[NODE]))

    def shadowed(sources, ran):   # every run loaded the tree's code but the derived one, which ran an installed copy
        return {"calc.py": {"module": "calc", "file": "elsewhere/calc.py"}} if ran is derived else {}

    monkeypatch.setattr(patch_review.repo_tests, "shadowed", shadowed)
    got = review(repo)
    assert (m.id_of("derived", NODE), "unknown", 3) in receipts(got)
    assert statement(got)["checked"] is False


def test_forwarding_what_each_run_saw_changes_no_decided_field(repo, monkeypatch):
    def reviewed(forward: bool):
        Runs(monkeypatch, {(NODE,): result([NODE]), (FILE,): result(failed=[NODE])}, at_base=result(failed=[NODE]),
             replay={FILE: {"verdict": "not_run", "baseline": "failed"}})
        if not forward:
            monkeypatch.setattr(patch_review, "_observed", lambda checks, ran: {})
        return review(repo)

    with_it = reviewed(True)
    monkeypatch.undo()
    without = reviewed(False)
    for key in ("completion_state", "warning_card", "evidence", "merge", "gate"):
        assert with_it[key] == without[key], key
    for key in ("outcome", "reasons", "requirements", "unknowns", "next_actions", "completion"):
        assert with_it["assessment"][key] == without["assessment"][key], key
    assert statement(with_it)["checked"] is False and statement(without)["checked"] is True


# --- the inventory: every place the review runs tests, from the code ------------------------------------------------

HARNESS = Path(g6.__file__).resolve().parent / "harness"
#: The functions a test run of a review goes through.
RUNNERS = frozenset({"_execute", "_execute_at_base", "run", "run_before", "run_at_base", "_run"})
FORWARDED = "on the patch: what it saw of each stated check it collected reaches the issuer"
#: Every call that runs tests, in the review and the modules it calls: on the patch and forwarded, or not on the patch.
RUN_SITES = {
    ("patch_review.py", "_assess", "_execute(root, present, timeout_s=max(1, _left(deadline)), **_rooted(roots))"): (
        FORWARDED + ": the stated checks' first run, each one's first receipt (_requirement's verdicts)"),
    ("patch_review.py", "_assess", "_execute(root, derived, timeout_s=max(1, _left(deadline)), **_rooted(roots))"): (
        FORWARDED + ": the tests that touch the changed modules (_observed, the derived run)"),
    ("patch_review.py", "_on_base",
     "_execute(root, failing, timeout_s=max(1, _left(deadline)), change=change, **_rooted(roots))"): (
        FORWARDED + ": each failing derived file at the base commit, then on the patch in a copy (_observed, a "
                    "replay; NOT_READ when it kept no per-test result)"),
    ("patch_review.py", "_run_again", "_execute(root, ids, timeout_s=max(1, _left(deadline)), **_rooted(roots))"): (
        FORWARDED + ": the run again, after the run at the base commit (again)"),
    ("patch_review.py", "_at_base",
     "_execute_at_base(change, passing, timeout_s=max(1, _left(deadline)), **_rooted(roots))"): (
        "at the base commit, not on the patch: each check's negative control"),
    ("patch_review.py", "_execute", "repo_tests.run_before(replace(change, before=texts), roots=roots, "
                                    "timeout_s=timeout_s, require_isolation=True)"): "the runner of the replays above",
    ("patch_review.py", "_execute", "repo_tests.run(root, ids, roots=roots, timeout_s=timeout_s, receipt=True, "
                                    "require_isolation=True)"): "the runner of the in-place runs above",
    ("patch_review.py", "_execute_at_base", "repo_tests.run_at_base(change, ids, roots=roots, timeout_s=timeout_s, "
                                            "require_isolation=True)"): "the runner of the run at the base commit",
    ("verification_search.py", "_run_check", "repo_tests.run_at_base(change, [node], roots=roots, "
                                             "timeout_s=timeout_s, overlay=overlay, require_isolation=True)"): (
        "a proposed check at the base commit, not on the patch"),
    ("verification_search.py", "_run_check", "repo_tests._run(copy, [node], roots=roots, timeout_s=timeout_s, "
                                             "network=False, copied=True, python=repo_tests.interpreter(change.repo), "
                                             "scratch_dir=copy.parent, deadline=deadline, require_isolation=True, "
                                             "links=report)"): (
        "a proposed check's own code overlaid in a copy: never a stated check's bytes, so no stated node"),
    ("verification_search.py", "_run_mutant", "repo_tests._run(copy, ids, roots=roots, timeout_s=timeout_s, "
                                              "network=False, mutant=(rel, source), copied=True, "
                                              "python=repo_tests.interpreter(change.repo), scratch_dir=copy.parent, "
                                              "deadline=deadline, require_isolation=True, links=report)"): (
        "the stated checks on a mutant of the patch, not on the patch: test adequacy, reported only"),
}


def run_sites() -> set[tuple[str, str, str]]:
    found = set()
    for path in sorted(HARNESS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
                if name in RUNNERS and (owner == "repo_tests" or (not owner and name.startswith("_execute"))):
                    found.add((path.name, function.name, ast.unparse(node)))
    return found


def test_every_place_the_review_runs_tests_is_listed_and_every_listed_one_is_still_there():
    found = run_sites()
    assert sorted(found - set(RUN_SITES)) == [], "a run the inventory does not list: forward it, or say why not"
    assert sorted(set(RUN_SITES) - found) == [], "listed runs the scan no longer finds: remove them"


def test_each_run_on_the_patch_reads_what_it_saw_of_the_stated_checks():
    tree = ast.parse((HARNESS / "patch_review.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    def calls(function: str, name: str) -> list[str]:
        return [ast.unparse(node) for node in ast.walk(functions[function]) if isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == name]

    assert "_observed(stated_ids, second)" in calls("_assess", "_observed")   # the derived run
    assert any(call.startswith("_observed(mine, out)") for call in calls("_on_base", "_observed"))   # each replay
    assert "_verdicts(present, first)" in calls("_assess", "_verdicts")      # the first run
    assert "_verdicts(ids, ran)" in calls("_run_again", "_verdicts")          # the run again
    reply = calls("_assess", "_reply")[0]
    assert "'observed': observed" in reply and "'again': again" in reply     # and all of it reaches the issuer


def test_the_scan_finds_a_planted_run():
    planted = ast.parse("def probe(root, ids):\n    return repo_tests.run(root, ids)\n")
    names = {(f.name, ast.unparse(c)) for f in ast.walk(planted) if isinstance(f, ast.FunctionDef)
             for c in ast.walk(f) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") in RUNNERS
             and getattr(c.func.value, "id", "") == "repo_tests"}
    assert names == {("probe", "repo_tests.run(root, ids)")}
