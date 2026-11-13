# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""One rule turns a run into observations, by phase (G6 0.3, slice 2's follow-up; review 4's finding 1; class
``failure_read_as_success``, met in every round of slice 2).

A set-up or teardown error at the base commit was folded into ``failed``, and the issuer's adapter read that as a killed
negative control: a control whose test body never ran, or passed, then backed a pass, and the report said "checked".
Now one function reads each test's outcome per phase from the runner's own report (``patch_review.phases_of``), and one
says what the phases show of the body (``body_of``): only a call phase that ran and failed is a failure of the body. A
set-up or teardown error is an error: the review's own verdict still counts it as ``failed`` (its status and outcome keep
their meaning), and the issuer is told it is unknown, never a kill and never a pass (``observation``,
``at_base_observations``). The inventory lists, from the code, every reader of a run's verdict in ``patch_review`` and
``requirement_evidence``, and each reads through the one rule.

Development fixtures only: G6's test runs are replaced by recorded results, so nothing is confined, and no model runs.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6
from g6.evidence import mapping as m
from g6.harness import patch_report, patch_review, requirement_evidence

NODE = "test_calc.py::test_add"


# --- review 4's finding 1, brought in as this change's own ------------------------------------------------------------


def _result(failure_phase=None):
    # This is the shape repo_tests._BOOT records for a fixture exception:
    # AttributeError goes into failed_ids; only FixtureLookupError uses setup_errors.
    phases = {"setup": "passed", "call": "passed", "teardown": "passed"}
    if failure_phase:
        phases[failure_phase] = "failed"
    if failure_phase == "setup":
        del phases["call"]
    passed, failed = int(failure_phase not in ("setup", "call")), int(bool(failure_phase))
    return {
        "verdict": "failed" if failure_phase else "passed", "said": "", "rootdir": "",
        "passed": passed, "failed": failed, "not_passed": 0,
        "total": passed + failed, "deselected": 0, "test_outcomes": {NODE: phases},
        "collected_ids": [NODE], "omitted_ids": [], "changed": [], "output": "",
        "seconds": 0.01, "python": sys.executable, "interpreter_source": "g6",
        "command": "pytest", "isolation": {"backend": "recorded-unit-result", "why": ""},
        "failed_ids": [NODE] if failure_phase else [], "setup_errors": {},
        "degraded": False, "receipt": "",
    }


@pytest.mark.parametrize("failure_phase", ["setup", "teardown"])
def test_finding_1_a_base_fixture_error_is_not_a_killed_control(monkeypatch, failure_phase):
    # R1's body either never ran, or explicitly passed at the base commit.
    # E.g. a fixture calls a new, unrelated calc.ready() API during setup/cleanup.
    monkeypatch.setattr(patch_review, "_execute", lambda *a, **kw: _result())
    baseline = _result(failure_phase)
    assert baseline["test_outcomes"][NODE].get("call", "not run") != "failed"
    req = patch_review.Requirement(id="R1", text="add returns the sum", checks=[NODE])
    change = SimpleNamespace(files=("calc.py",), sources=("calc.py",), base_commit="0" * 40)
    norm, verdicts = {NODE: NODE}, {NODE: {"verdict": "passed", "detail": "1 test passed"}}
    evaluated = [patch_review._requirement(req, verdicts, norm)]

    def assess(recorded):
        monkeypatch.setattr(patch_review, "_execute_at_base", lambda *a, **kw: recorded)
        runs, deadline = [], time.monotonic() + 60
        patch_review._at_base(change, [req], norm, verdicts, deadline, [], runs, [], [])
        again = patch_review._run_again(Path.cwd(), change, [req], norm, verdicts, runs, deadline, "a" * 64)
        return requirement_evidence.assess(
            evaluated, runs, artifact="a" * 64, base_commit=change.base_commit,
            check_sha={NODE: "c" * 64}, written=lambda _: False, environment={}, again=again)

    assert assess(_result("call"))["requirements"][0]["checked"] is True  # real test-body failure: positive control
    evidence = assess(baseline)
    found = evidence["requirements"][0]
    report = patch_report.render({"requirements": evaluated, "requirement_evidence": evidence,
                                 "completion": {"demonstrated": [{"requirement": "R1", "checks": [NODE]}]}})
    assert not found["checked"], (
        "a base-commit fixture error was promoted to a killed negative control although no test body failed",
        found, report,
    )


# --- one test's phases, and what they show of its body --------------------------------------------------------------

SETUP_ERROR = {"setup": "failed", "teardown": "passed"}
TEARDOWN_ERROR = {"setup": "passed", "call": "passed", "teardown": "failed"}
BODY_FAILED = {"setup": "passed", "call": "failed", "teardown": "passed"}
BOTH = {"setup": "passed", "call": "failed", "teardown": "failed"}
SKIPPED = {"setup": "skipped", "teardown": "passed"}
XFAILED = {"setup": "passed", "call": "skipped", "teardown": "passed"}   # pytest reports an xfail's call as skipped
PASSED = {"setup": "passed", "call": "passed", "teardown": "passed"}


def run(phases_by_node: dict, *, verdict: str | None = None, listed=None, rootdir: str = "") -> dict:
    """A recorded run with each node's phases; the runner lists every node with a failed phase among its failures."""
    failed = [node for node, phases in phases_by_node.items()
              if isinstance(phases, dict) and "failed" in phases.values()]
    passed = sum(isinstance(phases, dict) and phases.get("call") == "passed"
                 and all(state == "passed" for state in phases.values()) for phases in phases_by_node.values())
    skipped = sum(isinstance(phases, dict) and "skipped" in phases.values() and "failed" not in phases.values()
                  for phases in phases_by_node.values())
    return {"verdict": verdict or ("failed" if failed else "passed"), "said": "", "output": "",
            "test_outcomes": phases_by_node, "failed_ids": failed if listed is None else listed, "setup_errors": {},
            "omitted_ids": [], "collected_ids": sorted(phases_by_node), "rootdir": rootdir,
            "seconds": 0.01, "passed": passed, "failed": len(failed),
            "not_passed": skipped, "isolation": {"backend": "recorded-unit-result", "why": ""}}


@pytest.mark.parametrize(("phases", "shown"), [
    (BODY_FAILED, "failed"), (BOTH, "failed"), (SETUP_ERROR, patch_review.ERROR),
    (TEARDOWN_ERROR, patch_review.ERROR), (SKIPPED, "skipped"), (XFAILED, "skipped"), (PASSED, "passed"),
    ({"collection": "skipped"}, "skipped"), ({"setup": "passed"}, "not_run"), ({}, "not_run"),
], ids=["body-failed", "body-and-teardown", "setup-error", "teardown-error", "skip", "xfail", "passed",
        "module-skipped", "never-called", "nothing"])
def test_only_a_call_phase_that_ran_and_failed_is_a_failure_of_the_body(phases, shown):
    assert patch_review.body_of(phases) == shown


def test_a_failure_the_runner_names_in_no_phase_is_an_error_never_a_failure_of_the_body():
    ran = run({NODE: PASSED}, verdict="failed", listed=[NODE])   # the runner lists it, and no phase it reported failed
    assert patch_review.phases_of(ran)[NODE] == {**PASSED, "unnamed": "failed"}
    assert patch_review.body_of(patch_review.phases_of(ran)[NODE]) == patch_review.ERROR


def test_phases_that_do_not_read_as_a_mapping_are_none_never_a_pass():
    assert patch_review.phases_of(run({NODE: "passed"}, verdict="passed", listed=[]))[NODE] == {}
    assert patch_review.body_of(patch_review.phases_of(run({NODE: ["call"]}, verdict="passed", listed=[]))[NODE]) == (
        "not_run")
    listed = patch_review.phases_of(run({NODE: "garbled"}, verdict="failed", listed=[NODE]))[NODE]
    assert patch_review.body_of(listed) == patch_review.ERROR


def test_the_phases_are_read_relative_to_the_repository_s_top():
    ran = run({"test_calc.py::test_add": SETUP_ERROR}, rootdir="sub")
    assert patch_review.phases_of(ran) == {"sub/test_calc.py::test_add": {**SETUP_ERROR}}
    assert patch_review._verdicts(["sub/test_calc.py::test_add"], ran)["sub/test_calc.py::test_add"][
        "observation"] == patch_review.ERROR


@pytest.mark.parametrize(("phases", "verdict", "observed"), [
    (BODY_FAILED, "failed", "failed"), (BOTH, "failed", "failed"), (SETUP_ERROR, "failed", patch_review.ERROR),
    (TEARDOWN_ERROR, "failed", patch_review.ERROR), (SKIPPED, "skipped", "skipped"), (XFAILED, "skipped", "skipped"),
    (PASSED, "passed", "passed"),
], ids=["body-failed", "body-and-teardown", "setup-error", "teardown-error", "skip", "xfail", "passed"])
def test_the_review_s_verdict_keeps_its_meaning_and_the_issuer_is_told_the_observation(phases, verdict, observed):
    found = patch_review._verdicts([NODE], run({NODE: phases}))[NODE]
    assert found["verdict"] == verdict   # the review's status and outcome read this, as before
    assert patch_review.observation(found) == observed
    assert ("observation" in found) is (observed == patch_review.ERROR)   # named only where it differs


def test_a_check_whose_tests_fail_one_in_its_body_and_one_in_set_up_failed_in_its_body():
    ran = run({f"{NODE}[1]": BODY_FAILED, f"{NODE}[2]": SETUP_ERROR})
    assert patch_review.observation(patch_review._verdicts([NODE], ran)[NODE]) == "failed"


def test_a_file_that_failed_to_collect_is_an_error_never_a_failure_of_the_check():
    ran = {"verdict": "failed", "said": "", "output": "", "test_outcomes": {}, "failed_ids": [], "setup_errors": {},
           "omitted_ids": []}
    found = patch_review._verdicts([NODE], ran)[NODE]
    assert found["verdict"] == "failed" and patch_review.observation(found) == patch_review.ERROR


def test_an_observation_can_only_make_a_result_unknown():
    for value in (patch_review.ERROR, "passed", "failed", "", 0):
        for verdict in ("passed", "failed"):
            assert patch_review.observation({"verdict": verdict, "observation": value}) == patch_review.ERROR
    assert patch_review.observation({"verdict": "failed"}) == "failed"


def test_an_unreadable_observations_field_at_the_base_makes_every_check_there_unknown():
    runs = [{"check": patch_review.AT_BASE, "outcomes": {NODE: "failed", "b": "passed"}, "observations": ["x"]}]
    assert patch_review.at_base_observations(runs) == {NODE: patch_review.ERROR, "b": patch_review.ERROR}
    named = [{"check": patch_review.AT_BASE, "outcomes": {NODE: "failed", "b": "failed"},
              "observations": {NODE: patch_review.ERROR}}]
    assert patch_review.at_base_observations(named) == {NODE: patch_review.ERROR, "b": "failed"}
    assert patch_review.at_base_observations([]) == {}


# --- through the review: set-up and teardown errors, a skip and an xfail, at the base and on the patch ---------------

TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return root


def recorded(phases: dict) -> dict:
    found = run({NODE: phases})
    return {**found, "total": found["passed"] + found["failed"], "deselected": 0, "collected_ids": [NODE], "changed": [], "python": sys.executable,
            "interpreter_source": "g6", "command": "pytest", "degraded": False, "receipt": ""}


def review(monkeypatch, root: Path, *, on_patch: dict, at_base: dict) -> tuple[dict, list[str]]:
    calls: list[str] = []

    def execute(root, ids, *, timeout_s, change=None, roots=()):
        calls.append("patch")   # the stated checks' run, then the test file's derived run and the run again: passes
        return recorded(on_patch if calls.count("patch") == 1 else PASSED)

    def execute_at_base(change, ids, *, timeout_s, roots=()):
        calls.append("base")
        return recorded(at_base)

    monkeypatch.setattr(patch_review, "_execute", execute)
    monkeypatch.setattr(patch_review, "_execute_at_base", execute_at_base)
    reply = patch_review.review_patch(root, task="Fix addition", requirements=[
        {"id": "R1", "text": "add returns a sum", "checks": [NODE]}])
    return reply, calls


def _evidence(reply: dict) -> dict:
    return reply["assessment"]["requirement_evidence"]


def test_a_body_failure_at_the_base_commit_kills_the_control(repo, monkeypatch):   # the positive control below
    reply, calls = review(monkeypatch, repo, on_patch=PASSED, at_base=BODY_FAILED)
    assert calls == ["patch", "patch", "base", "patch"]   # the stated run, the derived run, the base, the run again
    assert _evidence(reply)["requirements"][0]["checked"] is True
    assert [k["killed"] for k in _evidence(reply)["certificate"]["checks"][0]["controls"]] == [True]


@pytest.mark.parametrize("at_base", [SETUP_ERROR, TEARDOWN_ERROR, SKIPPED, XFAILED],
                         ids=["setup-error", "teardown-error", "skip", "xfail"])
def test_nothing_but_a_body_failure_at_the_base_commit_kills_a_control(repo, monkeypatch, at_base):
    reply, calls = review(monkeypatch, repo, on_patch=PASSED, at_base=at_base)
    assert calls == ["patch", "patch", "base"], "a check whose body did not fail at the base commit is not run again"
    evidence = _evidence(reply)
    assert evidence["requirements"][0]["checked"] is False
    assert all(not check["controls"] for check in evidence["certificate"]["checks"])
    assert [r["result"] for r in evidence["certificate"]["receipts"]] == []   # its pass is refused, and withholds
    base = next(r for r in reply["assessment"]["scope"]["checks_run"] if r["check"] == patch_review.AT_BASE)
    errors = at_base in (SETUP_ERROR, TEARDOWN_ERROR)
    assert base["outcomes"] == {NODE: "failed" if errors else "skipped"}   # the review's own verdict, as before
    assert base.get("observations", {}) == ({NODE: patch_review.ERROR} if errors else {})
    report = reply["assessment"]["report"]
    assert "fail at the base commit" not in report and "fails at the base commit" not in report


@pytest.mark.parametrize(("on_patch", "status"), [(SETUP_ERROR, "failed"), (TEARDOWN_ERROR, "failed"),
                                                  (SKIPPED, "not_established"), (XFAILED, "not_established")],
                         ids=["setup-error", "teardown-error", "skip", "xfail"])
def test_nothing_but_a_body_pass_on_the_patch_is_a_pass_and_an_error_there_is_unknown(repo, monkeypatch, on_patch,
                                                                                     status):
    reply, _calls = review(monkeypatch, repo, on_patch=on_patch, at_base=BODY_FAILED)
    assert reply["assessment"]["requirements"][0]["status"] == status   # the review's own reading, as before
    if status == "failed":
        assert reply["assessment"]["outcome"] == patch_review.DEFECT
    evidence = _evidence(reply)
    assert evidence["requirements"][0]["checked"] is False
    receipts = [(r["receipt_id"], r["result"]) for r in evidence["certificate"]["receipts"]]
    assert receipts and all(result == "unknown" for _, result in receipts), receipts   # never a pass, never a fail
    assert evidence["requirements"][0]["caveats"]


def test_a_body_failure_on_the_patch_is_a_failure(repo, monkeypatch):
    reply, _calls = review(monkeypatch, repo, on_patch=BODY_FAILED, at_base=BODY_FAILED)
    receipts = [r["result"] for r in _evidence(reply)["certificate"]["receipts"]]
    assert receipts == ["fail"] and reply["assessment"]["requirements"][0]["status"] == "failed"


def test_the_issuer_s_adapter_reads_a_hand_made_base_record_by_the_one_rule():
    req = {"id": "R1", "text": "t", "kind": "requirement", "provenance": "stated",
           "checks": [{"id": NODE, "provenance": "stated", "verdict": "passed"}]}
    common = {"artifact": "a" * 64, "base_commit": "0" * 40, "check_sha": {NODE: "c" * 64},
              "written": lambda _check: False, "environment": {}, "again": {"verdicts": {NODE: "passed"}}}
    killed = [{"check": requirement_evidence.AT_BASE, "outcomes": {NODE: "failed"}}]
    assert requirement_evidence.assess([req], killed, **common)["requirements"][0]["checked"] is True
    errored = [{**killed[0], "observations": {NODE: patch_review.ERROR}}]
    found = requirement_evidence.assess([req], errored, **common)["requirements"][0]
    assert found["checked"] is False and "(error)" in found["not_counted"]
    first = {**req, "checks": [{"id": NODE, "provenance": "stated", "verdict": "failed",
                                "observation": patch_review.ERROR}]}
    facts, artefact, records = requirement_evidence.records_for([first], killed, **{
        k: v for k, v in common.items() if k != "again"})
    assert [r.result for r in records] == ["unknown"]   # an error on the patch is no failure of the check
    assert "did not run to a pass on the patch (error)" in requirement_evidence.assess([first], killed, **common)[
        "requirements"][0]["not_counted"]
    assert m.issue_records(facts, artefact, records).certificate.predicates[0].caveats   # and still withholds


# --- the inventory: every reader of a run's verdict in the review and its issuer adapter, from the code ---------------

HARNESS = Path(g6.__file__).resolve().parent / "harness"
#: The keys a run's verdict is read by: the runner's per-test report, a check's verdict entry, the run at the base
#: commit's record of each check, and the other runs' and the run again's verdicts.
KEYS = frozenset({"test_outcomes", "failed_ids", "setup_errors", "verdict", "outcomes", "observations",
                  "observation", "verdicts"})
ONE_RULE = "the one rule itself"
REVIEW = "the review's own verdict (status, outcome, reasons): an error counts as failed there, as it always has"
RUN = "a run's own verdict (did it run, can it be believed), never a check's"
CHANNEL = "a channel that holds observations the one rule made (observation): it reads no verdict of its own"
MESSAGE = "the ids of the tests that failed, for a message; it decides nothing"
#: Every function that reads one of ``KEYS``, with what it is.
READERS = {
    ("patch_review.py", "observation"): ONE_RULE + ": what a check's verdict entry tells the issuer",
    ("patch_review.py", "at_base_observations"): ONE_RULE + ": what the run at the base commit tells the issuer",
    ("patch_review.py", "_verdicts"): ONE_RULE + ": each check's verdict by phase, and its observation",
    ("patch_review.py", "_at_base"): (REVIEW + " for which checks run at the base commit and its unknowns; the record "
                                      "it writes holds the review's verdict and the one rule's observations"),
    ("patch_review.py", "_record"): RUN,
    ("patch_review.py", "_untrusted"): RUN,
    ("patch_review.py", "_loaded_elsewhere"): RUN,
    ("patch_review.py", "_run_again"): RUN + "; each check through observation and at_base_observations",
    ("patch_review.py", "_on_base"): RUN + "; each stated check it observed through _observed; " + MESSAGE,
    ("patch_review.py", "_assess"): REVIEW + "; " + RUN,
    ("patch_review.py", "_requirement"): REVIEW,
    ("patch_review.py", "_reply"): RUN + " (the envelope's evidence summary)",
    ("patch_review.py", "_structural_check"): "a structural check's verdict, read from the files: no run",
    ("patch_review.py", "unusable"): RUN,
    ("patch_review.py", "infer"): RUN + " (the coding gate's test runs, as exit codes)",
    ("patch_review.py", "_gate"): "the coding gate's own verdict: no run's",
    ("patch_review.py", "_not_loaded"): ("a check's verdict entry at the base commit (_verdicts'), made not_run where "
                                         "its file could not load there: it names no observation, so never a kill"),
    ("requirement_evidence.py", "observed_runs"): CHANNEL,
    ("requirement_evidence.py", "records_for"): CHANNEL + " (the run again's)",
    ("requirement_evidence.py", "_not_counted"): CHANNEL + " (the other runs' and the run again's)",
}
#: The functions of the issuer's adapter allowed to read a channel; it reads no verdict, outcome or report itself.
CHANNELS = frozenset({"verdicts"})


def readers() -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = {}
    for name in ("patch_review.py", "requirement_evidence.py"):
        tree = ast.parse((HARNESS / name).read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue
            keys = set()
            for node in ast.walk(function):
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                    keys.add(node.slice.value)
                elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                      and node.args and isinstance(node.args[0], ast.Constant)):
                    receiver = node.func.value
                    if (isinstance(receiver, ast.Attribute) and receiver.attr == "data"
                            and isinstance(receiver.value, ast.Call)
                            and ast.unparse(receiver.value.func) == "repo_tests.read_report"):
                        continue  # typed detached fields, never a raw report access
                    keys.add(node.args[0].value)
                elif (isinstance(node, ast.Call) and ast.unparse(node.func) == "_listing" and len(node.args) > 1
                      and isinstance(node.args[1], ast.Constant)):   # the one reader of a listing (_listing)
                    keys.add(node.args[1].value)
            if keys & KEYS:
                found[(name, function.name)] = keys & KEYS
    return found


def test_every_reader_of_a_run_s_verdict_is_listed_and_every_listed_one_is_still_there():
    found = readers()
    assert sorted(set(found) - set(READERS)) == [], "a reader of a run's verdict the inventory does not list"
    assert sorted(set(READERS) - set(found)) == [], "listed readers the scan no longer finds: remove them"


def test_the_runner_s_report_is_read_only_by_the_one_rule():
    found = readers()
    report = {where for where, keys in found.items() if keys & {"test_outcomes", "failed_ids", "setup_errors"}}
    # The producer owns the raw reader; these adapters consume its detached form.
    assert report == set()
    on_base = ast.unparse(next(f for f in ast.walk(ast.parse((HARNESS / "patch_review.py").read_text(encoding="utf-8")))
                               if isinstance(f, ast.FunctionDef) and f.name == "_on_base"))
    assert "repo_tests.read_report(out).data.get('failed_ids', ())" in on_base
    assert "sorted(_failed_ids(out))" not in on_base and "out.get('failed_ids'" not in on_base


def test_the_issuer_s_adapter_reads_no_verdict_but_through_the_one_rule():
    found = readers()
    mine = {where: keys for where, keys in found.items() if where[0] == "requirement_evidence.py"}
    assert all(keys <= CHANNELS for keys in mine.values()), mine
    tree = ast.parse((HARNESS / "requirement_evidence.py").read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                and node.module == "g6.harness.patch_review" for alias in node.names}
    assert {"observation", "at_base_observations"} <= imported


def test_every_evidence_reader_in_the_review_reads_through_the_one_rule():
    tree = ast.parse((HARNESS / "patch_review.py").read_text(encoding="utf-8"))
    functions = {f.name: ast.unparse(f) for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}
    assert "observation(found)" in functions["_observed"]
    assert "at_base_observations(runs)" in functions["_run_again"] and "observation(found)" in functions["_run_again"]
    assert "at_base_observations(runs)" in functions["_self_checked"]
    assert "repo_tests.read_report(ran)" in functions["_verdicts"]
    assert "report.phases" in functions["_verdicts"] and "body_of(phases)" in functions["_verdicts"]
    assert "'observation'" in functions["_at_base"]   # the record of the run at the base commit keeps it
    for name in ("_observed", "_run_again"):
        assert "['verdict']" not in functions[name] and ".get('verdict')" not in functions[name].replace(
            "ran.get('verdict')", ""), name


def test_the_scan_finds_a_planted_reader():
    planted = ast.parse("def killed(runs, check):\n    return runs[0]['outcomes'][check] == 'failed'\n")
    keys = {node.slice.value for node in ast.walk(planted) if isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)}
    assert "outcomes" in keys & KEYS
