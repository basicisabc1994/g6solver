# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The phase reading is total over what the runner reports (G6 0.3, review 1 of slice 2's follow-up, findings 1, 2 and 5;
classes ``failure_read_as_success`` and ``guard_covers_less_than_it_guards``, both met before).

``body_of`` accepted a call result without checking the other phases, so a call-only report, or one with a set-up or
teardown outcome pytest never gives, backed a pass or killed a control; ``phases_of`` read only ``test_outcomes``, so a
test the runner listed only as failed vanished beside a passing one; and a ``test_outcomes`` that was not a mapping
crashed the reader. Now the tests of a run are the union of every listing of the runner's report
(``patch_review.LISTINGS``), every phase is checked, and anything partial, unrecognised, contradictory or unreadable is
unknown: it never supports, never kills a control and never crashes.

The inventory lists, from the runner's own code (``repo_tests``: its plugin's report, how ``_progress`` passes it on, and
every field ``run``, ``run_before`` and ``run_at_base`` return), every field of the runner's report, each read by the
phase reading or named with why it is not; and the plants are made from the reading's own lists, so a listing, a phase
or an outcome added there is planted too.

Development fixtures only: G6's test runs are replaced by recorded results, so nothing is confined, and no model runs.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6
from g6.harness import patch_review as review
from g6.harness import requirement_evidence as evidence
from g6.workforce import repo_tests

CHECK, NODE, OTHER = "test_calc.py", "test_calc.py::test_add", "test_calc.py::test_more"
PASSED = {"setup": "passed", "call": "passed", "teardown": "passed"}
BODY_FAILED = {"setup": "passed", "call": "failed", "teardown": "passed"}


# --- review 1's findings 1, 2 and 5, brought in as this change's own --------------------------------------------------


@pytest.mark.parametrize("where,malformed", [
    ("base", {"setup": "never-seen", "call": "failed", "teardown": "passed"}),
    ("base", {"call": "failed"}),
    ("patch", {"setup": "passed", "call": "passed", "teardown": "never-seen"}),
    ("patch", {"call": "passed"}),
], ids=["base-unrecognised-setup", "base-call-only", "patch-unrecognised-teardown", "patch-call-only"])
def test_finding_1_unreadable_phase_is_unknown_and_cannot_kill_a_control(monkeypatch, where, malformed):
    node = "test_calc.py::test_add"
    passing = {"setup": "passed", "call": "passed", "teardown": "passed"}
    failing = {"setup": "passed", "call": "failed", "teardown": "passed"}

    def result(phases):
        return {"verdict": "failed" if phases["call"] == "failed" else "passed", "said": "", "output": "",
                "test_outcomes": {node: phases}, "failed_ids": [node] if phases["call"] == "failed" else [],
                "setup_errors": {}, "omitted_ids": [], "isolation": {"backend": "recorded"}}

    monkeypatch.setattr(review, "_execute_at_base", lambda *a, **k: result(malformed if where == "base" else failing))
    monkeypatch.setattr(review, "_execute", lambda *a, **k: result(malformed if where == "patch" else passing))
    req = review.Requirement(id="R1", text="add returns a sum", checks=[node])
    change = SimpleNamespace(files=("calc.py",), sources=("calc.py",), base_commit="0" * 40)
    verdicts = review._verdicts([node], result(malformed if where == "patch" else passing))
    runs = []
    deadline = time.monotonic() + 30
    review._at_base(change, [req], {node: node}, verdicts, deadline, [], runs, [], [])
    again = review._run_again(Path.cwd(), change, [req], {node: node}, verdicts, runs, deadline, "a" * 64)
    got = evidence.assess([review._requirement(req, verdicts, {node: node})], runs,
                          artifact="a" * 64, base_commit=change.base_commit,
                          check_sha={node: "c" * 64}, written=lambda _: False, environment={}, again=again)
    assert got["requirements"][0]["checked"] is False, "unreadable or partial phases were counted as evidence"


@pytest.mark.parametrize("listing", ["failed_ids", "setup_errors"])
def test_finding_2_a_failed_test_with_missing_phases_is_not_lost_beside_a_passing_test(listing):
    check = "test_calc.py"
    good, missing = check + "::test_good", check + "::test_bad"
    ran = {"verdict": "failed", "said": "", "output": "",
           "test_outcomes": {good: {"setup": "passed", "call": "passed", "teardown": "passed"}},
           "failed_ids": [missing] if listing == "failed_ids" else [],
           "setup_errors": {missing: "fixture lookup failed"} if listing == "setup_errors" else {}, "omitted_ids": []}
    verdicts = review._verdicts([check], ran)
    req = review.Requirement(id="R1", text="calculation is correct", checks=[check])
    evaluated = [review._requirement(req, verdicts, {check: check})]
    got = evidence.assess(evaluated, [{"check": review.AT_BASE, "outcomes": {check: "failed"}}],
                          artifact="a" * 64, base_commit="0" * 40, check_sha={check: "c" * 64},
                          written=lambda _: False, environment={}, again={"verdicts": {check: "passed"}},
                          observed=[{"run": ["derived"], "verdicts": review._observed([check], ran)}])
    assert got["requirements"][0]["checked"] is False, "a failure with no phase report vanished beside a pass"


@pytest.mark.parametrize("unreadable", ["truncated report", ["call", "failed"], 1],
                         ids=["text", "list", "number"])
def test_finding_5_an_unreadable_phase_report_becomes_unknown_instead_of_crashing(unreadable):
    node = "test_calc.py::test_add"
    ran = {"verdict": "failed", "said": "", "output": "", "test_outcomes": unreadable,
           "failed_ids": [node], "setup_errors": {}, "omitted_ids": []}
    try:
        observed = review._observed([node], ran)
    except AttributeError as exc:
        raise AssertionError("an unreadable phase report crashed the evidence reader instead of yielding unknown") from exc
    assert observed == {node: review.ERROR}


# --- the plants: partial, unrecognised, wrong-typed and contradictory reports, at the base and on the patch ------------


def report(outcomes, **over) -> dict:
    """A run as the runner reports it: each test's phases, every test with a failed phase listed as failed, and every
    test it reports collected; ``over`` replaces any field."""
    named = outcomes if isinstance(outcomes, dict) else {}
    failed = [node for node, phases in named.items() if isinstance(phases, dict) and "failed" in phases.values()]
    found = {"verdict": "failed" if failed else "passed", "said": "", "output": "", "rootdir": "",
             "test_outcomes": outcomes, "failed_ids": failed, "setup_errors": {}, "collected_ids": sorted(named),
             "omitted_ids": [], "isolation": {"backend": "recorded-unit-result", "why": ""}}
    return {**found, **over}


def checked(monkeypatch, *, at_base: dict, on_patch: dict) -> bool:
    """Whether the issuer calls R1 checked when its one stated check ran ``on_patch``, then ``at_base`` (its control),
    then on the patch again, through the review's own steps."""
    monkeypatch.setattr(review, "_execute_at_base", lambda *a, **k: at_base)
    monkeypatch.setattr(review, "_execute", lambda *a, **k: on_patch)
    req = review.Requirement(id="R1", text="add returns a sum", checks=[CHECK])
    change = SimpleNamespace(files=("calc.py",), sources=("calc.py",), base_commit="0" * 40)
    norm = {CHECK: CHECK}
    verdicts = review._verdicts([CHECK], on_patch)
    runs: list[dict] = []
    deadline = time.monotonic() + 30
    review._at_base(change, [req], norm, verdicts, deadline, [], runs, [], [])
    again = review._run_again(Path.cwd(), change, [req], norm, verdicts, runs, deadline, "a" * 64)
    got = evidence.assess([review._requirement(req, verdicts, norm)], runs, artifact="a" * 64,
                          base_commit=change.base_commit, check_sha={CHECK: "c" * 64}, written=lambda _: False,
                          environment={}, again=again)
    return got["requirements"][0]["checked"]


GOOD_BASE, GOOD_PATCH = report({NODE: BODY_FAILED}), report({NODE: PASSED})
#: A container of another type than each listing's (``review.LISTINGS``), and an id that is not text inside the right
#: one: read from the reading's own list, so a listing added there is planted too.
WRONG = {dict: ["text", ["call", "failed"], 1, {1: PASSED}], list: [NODE, {NODE: "failed"}, 1, [1], (NODE,)]}
#: Outcomes pytest never gives a phase.
UNRECOGNISED = ["never-seen", "rerun", "PASSED", "", None, 0, ["passed"]]


def _wrong_containers(failing: bool) -> dict[str, dict]:
    base = {NODE: BODY_FAILED if failing else PASSED}
    found = {}
    for key, kind in review.LISTINGS.items():
        for n, value in enumerate(WRONG[kind]):
            found[f"{key}-{n}"] = report(base, **{key: value}, **({} if failing else {"verdict": "passed"}))
    for n, value in enumerate([1, ["sub"], {"sub": 1}]):
        found[f"rootdir-{n}"] = report(base, rootdir=value, **({} if failing else {"verdict": "passed"}))
    for n, value in enumerate(["passed", ["call"], None, 1]):
        found[f"phases-{n}"] = report({NODE: value}, failed_ids=[NODE] if failing else [],
                                      verdict="failed" if failing else "passed")
    return found


def _phase_by_phase(failing: bool) -> dict[str, dict]:
    """Each phase of the reading's own list (``review.PHASES``) left out, given an outcome pytest never gives, and
    joined by a phase pytest never names."""
    whole = BODY_FAILED if failing else PASSED
    found = {}
    for phase in review.PHASES:
        found[f"without-{phase}"] = report({NODE: {k: v for k, v in whole.items() if k != phase}},
                                           failed_ids=[NODE] if failing else [], verdict="failed" if failing else "passed")
        for n, value in enumerate(UNRECOGNISED):
            phases = {**whole, phase: value}
            found[f"{phase}-{n}"] = report({NODE: phases}, failed_ids=[NODE] if failing else [],
                                           verdict="failed" if failing else "passed")
    found["extra-phase"] = report({NODE: {**whole, "rerun": "passed"}}, failed_ids=[NODE] if failing else [],
                                  verdict="failed" if failing else "passed")
    found["with-collection"] = report({NODE: {**whole, "collection": "skipped"}}, failed_ids=[NODE] if failing else [],
                                      verdict="failed" if failing else "passed")
    return found


#: At the base commit: a report that is anything but a whole, recognised and consistent failure of the body, named
#: here; the plants made from the reading's own lists are below. None may kill the control.
AT_BASE = {
    "call-only": report({NODE: {"call": "failed"}}),
    "unrecognised-setup": report({NODE: {**BODY_FAILED, "setup": "never-seen"}}),
    "no-teardown": report({NODE: {"setup": "passed", "call": "failed"}}),
    "unlisted-body-failure": report({NODE: BODY_FAILED}, failed_ids=[]),
    "setup-failed-call-failed": report({NODE: {"setup": "failed", "call": "failed", "teardown": "passed"}}),
    "setup-skipped-call-failed": report({NODE: {"setup": "skipped", "call": "failed", "teardown": "passed"}}),
    "listed-in-setup-errors-only": report({}, verdict="failed", setup_errors={NODE: "fixture 'db' not found"}),
    "listed-in-failed-ids-only": report({}, verdict="failed", failed_ids=[NODE]),
    "phases-not-a-mapping": report({NODE: "failed"}, failed_ids=[NODE], verdict="failed"),
    "outcomes-not-a-mapping": report(["call", "failed"], failed_ids=[NODE], verdict="failed"),
    "failed-ids-as-text": report({NODE: BODY_FAILED}, failed_ids=NODE),
}
#: On the patch: a report that is anything but a whole, recognised and consistent pass of every test of the check,
#: named here; the plants made from the reading's own lists are below. None may support it.
ON_PATCH = {
    "call-only": report({NODE: {"call": "passed"}}),
    "never-called": report({NODE: {"setup": "passed"}}),
    "unrecognised-teardown": report({NODE: {**PASSED, "teardown": "never-seen"}}),
    "listed-as-failed": report({NODE: PASSED}, failed_ids=[NODE], verdict="passed"),
    "in-setup-errors": report({NODE: PASSED}, setup_errors={NODE: "fixture 'db' not found"}, verdict="passed"),
    "setup-skipped-call-passed": report({NODE: {"setup": "skipped", "call": "passed", "teardown": "passed"}}),
    "sibling-only-in-failed-ids": report({NODE: PASSED}, failed_ids=[OTHER], verdict="passed"),
    "sibling-only-in-setup-errors": report({NODE: PASSED}, setup_errors={OTHER: "fixture 'db' not found"},
                                           verdict="passed"),
    "sibling-collected-never-reported": report({NODE: PASSED}, collected_ids=[NODE, OTHER]),
    "sibling-with-no-phases": report({NODE: PASSED, OTHER: {}}),
    "collected-ids-as-a-number": report({NODE: PASSED}, collected_ids=1),
    "rootdir-not-text": report({NODE: PASSED}, rootdir=1),
}


def made_from_the_reading(failing: bool) -> dict[str, dict]:
    """The plants made from the reading's own lists (``LISTINGS``, ``PHASES``): each listing as a wrong container, each
    phase left out, given an outcome pytest never gives, or joined by a phase pytest never names."""
    return {**_wrong_containers(failing), **_phase_by_phase(failing)}


def unclassified(monkeypatch, plants: dict[str, dict], *, at_base: bool) -> list[str]:
    """Each plant the reading does not read as unknown: one that kills the control at the base (``at_base``) or
    supports on the patch, or crashes the reading."""
    found = []
    for name, ran in plants.items():
        try:
            if checked(monkeypatch, at_base=ran if at_base else GOOD_BASE, on_patch=GOOD_PATCH if at_base else ran):
                found.append(name)
        except Exception as exc:   # a crash is a member the reading cannot classify
            found.append(f"{name}: {type(exc).__name__}: {exc}")
    return found


def test_the_positive_control_a_whole_failure_at_the_base_and_a_whole_pass_on_the_patch_is_checked(monkeypatch):
    assert checked(monkeypatch, at_base=GOOD_BASE, on_patch=GOOD_PATCH) is True


@pytest.mark.parametrize("case", sorted(AT_BASE))
def test_nothing_but_a_whole_failure_of_the_body_at_the_base_kills_the_control(monkeypatch, case):
    assert unclassified(monkeypatch, {case: AT_BASE[case]}, at_base=True) == []


@pytest.mark.parametrize("case", sorted(ON_PATCH))
def test_nothing_but_a_whole_pass_of_every_test_on_the_patch_supports(monkeypatch, case):
    assert unclassified(monkeypatch, {case: ON_PATCH[case]}, at_base=False) == []


def test_no_plant_made_from_the_reading_s_own_lists_kills_a_control_at_the_base(monkeypatch):
    plants = made_from_the_reading(failing=True)
    assert len(plants) > 40 and unclassified(monkeypatch, plants, at_base=True) == []


def test_no_plant_made_from_the_reading_s_own_lists_supports_on_the_patch(monkeypatch):
    plants = made_from_the_reading(failing=False)
    assert len(plants) > 40 and unclassified(monkeypatch, plants, at_base=False) == []


def test_no_plant_crashes_the_reading_or_reads_as_a_body_failure_or_a_pass():
    wrong = []
    for where, plants in (("base", {**AT_BASE, **made_from_the_reading(failing=True)}),
                          ("patch", {**ON_PATCH, **made_from_the_reading(failing=False)})):
        for name, ran in plants.items():
            try:
                found = review._verdicts([CHECK], ran)[CHECK]
                observed = review._observed([CHECK], ran)
                bodies = [review.body_of(phases) for phases in review.phases_of(ran).values()]
            except Exception as exc:
                wrong.append(f"{where}:{name}: {type(exc).__name__}")
                continue
            if where == "base" and (review.observation(found) == review.FAILED or observed.get(CHECK) == review.FAILED):
                wrong.append(f"{where}:{name}: a body failure ({bodies})")
            if where == "patch" and (found["verdict"] == "passed" or observed.get(CHECK) == "passed"):
                wrong.append(f"{where}:{name}: a pass ({bodies})")
    assert wrong == []


def test_a_failure_the_runner_lists_keeps_the_review_s_verdict_failed_whatever_else_the_report_holds():
    # the review's own status reads its verdict: a listed failure is never turned into a pass or into "did not run"
    for value in ("text", 1, {NODE: "x"}):
        ran = report({NODE: PASSED}, failed_ids=[NODE], collected_ids=value)
        assert review._verdicts([CHECK], ran)[CHECK]["verdict"] == review.FAILED
    assert review._verdicts([CHECK], report({}, verdict="failed", failed_ids=[NODE]))[CHECK]["verdict"] == review.FAILED


def test_a_check_none_of_whose_tests_ran_keeps_the_verdict_it_had():
    # collected and never reached: the run's own reading of a check that never started stands (review I5)
    never = report({OTHER: PASSED}, collected_ids=[NODE, OTHER], verdict="timed_out")
    assert review._verdicts(["test_calc.py::test_add"], never)["test_calc.py::test_add"]["verdict"] == "not_run"
    started = report({NODE: {"setup": "passed"}}, collected_ids=[NODE, OTHER], verdict="timed_out")
    assert review._verdicts([CHECK], started)[CHECK]["verdict"] == "timed_out"


# --- the inventory: every field of the runner's report, from the runner's own code ------------------------------------

SRC = Path(g6.__file__).resolve().parent
REPO_TESTS = SRC / "workforce" / "repo_tests.py"
PATCH_REVIEW = SRC / "harness" / "patch_review.py"
#: The functions that make up the phase reading.
READING = ("_listing", "_failed_ids", "phases_of", "_whole", "body_of", "_verdicts", "_observed", "_in_repository",
           "_not_found", "_collection_errors")
#: Every field of the runner's report the phase reading reads, with what it takes from it.
READ = {
    "test_outcomes": "each test's outcome by phase (phases_of)",
    "failed_ids": "the tests the runner lists as failed: each is a test of the run (phases_of)",
    "setup_errors": "the tests whose set-up failed on a fixture pytest could not find: each is a test of the run",
    "omitted_ids": "the tests collected and left out of the session (deselected): never a pass (_verdicts)",
    "collected_ids": ("the tests collected: one of a check that started that reported no phase and was not left out "
                      "never finished (_verdicts)"),
    "rootdir": "the folder pytest named every id relative to (_in_repository); one that is not text makes all unknown",
    "verdict": "the run's verdict, which G6 decides outside the run from its exit code and counts (repo_tests._judge)",
    "said": "G6's own words for the run's verdict, in a check's detail",
    "output": "captured text retained for display; never a source of an outcome",
    "not_found_ids": "missing selections recorded from pytest's collection objects, never its console text",
    "collection_stopped": "the producer's final collection-stage flag, for the released diagnostic verdict",
}
COUNT = "a count: the run's verdict (repo_tests._judge) reads it; a count names no test"
ANNOTATION = ("why a failure happened, by the failure's id: a test's is also in failed_ids or setup_errors (the "
              "plugin lists every failed report in one of the two), and a file's is one pytest could not collect, "
              "which the output names; read as a test it would turn a file that could not collect into a failed test")
HOW = "how the run was made, never what a test did"
COPY = "what the run's copy left out or the run loaded from elsewhere: _loaded_elsewhere and _record read it"
RECEIPT = "the run's test receipt, or why it has none: the coding gate reads it"
#: Every field of the runner's report the phase reading does not read, with why.
NOT_READ = {
    "passed": COUNT, "failed": COUNT, "not_passed": COUNT, "total": COUNT, "deselected": COUNT,
    "degraded": COUNT + " (a summary of the counts)", "completed": COUNT + " (a summary of the counts)",
    "failure_messages": ANNOTATION, "os_errors": ANNOTATION,
    "missing_modules": "module names from exception objects, read by dependency guidance",
    "probe_observations": "the producer's bounded G6 payload, read by verification_search",
    "python_sources": "Python names from the copy's pre-run snapshot, read by shared loaded_from ownership",
    "changed": "the tracked files the run changed: its verdict says so (changed_tracked_files, never believed)",
    "seconds": HOW, "python": HOW, "interpreter_source": HOW, "command": HOW, "import_path": HOW, "own_config": HOW,
    "repo_env": HOW, "env_set_by_g6": HOW,
    "isolation": "the boundary the run had: _confined refuses a run none confined, before it is read",
    "source_before": "the snapshot the run began on: _run_again reads it, to refuse a run of another tree",
    "loaded_outside": COPY, "loaded_complete": COPY, "submodules_not_hashed": COPY, "links_left_out": COPY,
    "links_materialised": COPY, "generated_copied": COPY, "generated_left_out": COPY,
    "receipt": RECEIPT, "receipt_withheld": RECEIPT, "receipt_error": RECEIPT, "ignored_inputs": RECEIPT,
    "ignored_loadable": RECEIPT,
    "baseline": "a replay's verdict at the base commit (run_before): _on_base reads it; it names no test",
    "patched": "a replay's verdict on the patch when it did not match its tests (run_before): _on_base reads it",
}
#: The fields of the plugin's own report (``_BOOT``'s ``_Counted.write``) that ``_progress`` reads as counts, never
#: passing them on.
PLUGIN_COUNTS = {"collected": COUNT, "deselected": COUNT, "started": COUNT, "finished": COUNT}


def _functions(tree: ast.AST) -> dict[str, ast.FunctionDef]:
    return {f.name: f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}


def _keys(node: ast.Dict) -> set[str]:
    return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _counted() -> ast.ClassDef:
    return next(n for n in ast.walk(ast.parse(repo_tests._BOOT)) if isinstance(n, ast.ClassDef) and n.name == "_Counted")


def plugin_fields() -> dict[str, ast.expr]:
    """Each field the runner's plugin writes to its report, with the value it writes."""
    write = _functions(_counted())["write"]
    dump = next(n for n in ast.walk(write) if isinstance(n, ast.Call) and ast.unparse(n.func) == "json.dump")
    return {k.value: v for k, v in zip(dump.args[0].keys, dump.args[0].values, strict=True)}


def passed_on() -> dict[str, str]:
    """Each field of the plugin's report ``_progress`` passes on, by the field of the result it becomes."""
    progress = _functions(ast.parse(REPO_TESTS.read_text(encoding="utf-8")))["_progress"]
    returned = next(n.value for n in ast.walk(progress) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)
                    and len(n.value.keys) > 2)
    found = {}
    for key, value in zip(returned.keys, returned.values, strict=True):
        for node in ast.walk(value):
            if (isinstance(node, ast.Call) and ast.unparse(node.func) == "found.get" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                found[node.args[0].value] = key.value
    return found


def producer_fields() -> set[str]:
    """Every field the runner's report holds, from its code: the result ``_run`` builds (``result``) and every field it
    adds to one, the copy's report spread into it (``repository_copy``), and the entries ``run_before`` and
    ``run_at_base`` make themselves."""
    tree = ast.parse(REPO_TESTS.read_text(encoding="utf-8"))
    functions = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}
    run = functions["_run"]
    result = next(f for f in ast.walk(run) if isinstance(f, ast.FunctionDef) and f.name == "result")
    found = _keys(next(n.value for n in ast.walk(result) if isinstance(n, ast.Return)))
    for node in ast.walk(run):
        if isinstance(node, ast.Dict) and any(k is None and (ast.unparse(v) == "found" or ast.unparse(v).startswith(
                "result(")) for k, v in zip(node.keys, node.values, strict=True)):
            found |= _keys(node)
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript)
                and ast.unparse(node.targets[0].value) == "found" and isinstance(node.targets[0].slice, ast.Constant)):
            found.add(node.targets[0].slice.value)
    for name in ("run_before", "run_at_base", *(name for name in ("_not_run_report",) if name in functions)):
        found |= {key for node in ast.walk(functions[name]) if isinstance(node, ast.Dict) for key in _keys(node)
                  if "verdict" in _keys(node)}
    # The shared early-exit builder adds the replay comparison diagnostics.
    if "_not_run_report" in functions:
        found |= {node.targets[0].slice.value for node in ast.walk(functions["_not_run_report"])
                  if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)}
    update = next(n for n in ast.walk(functions["repository_copy"]) if isinstance(n, ast.Call)
                  and ast.unparse(n.func) == "report.update")
    found |= {n.elts[0].value for n in ast.walk(update) if isinstance(n, ast.Tuple) and len(n.elts) == 2
              and isinstance(n.elts[0], ast.Constant) and isinstance(n.elts[0].value, str)}
    return found


def read_by_the_reading() -> set[str]:
    """Every field of a run the phase reading's functions read: by a constant key (``ran["k"]``, ``ran.get("k")``,
    ``_listing(ran, "k")``), or every listing at once (``for key in LISTINGS``)."""
    functions = _functions(ast.parse(PATCH_REVIEW.read_text(encoding="utf-8")))
    where = {"ran.get": 0, "_listing": 1}   # the argument that names the field
    found = set()
    for name in READING:
        for node in ast.walk(functions[name]):
            if (isinstance(node, ast.Subscript) and ast.unparse(node.value) == "ran"
                    and isinstance(node.slice, ast.Constant)):
                found.add(node.slice.value)
            elif isinstance(node, ast.Call) and ast.unparse(node.func) in where:
                key = node.args[where[ast.unparse(node.func)]]
                if isinstance(key, ast.Constant):
                    found.add(key.value)
            elif isinstance(node, ast.comprehension) and ast.unparse(node.iter) == "LISTINGS":
                found |= set(review.LISTINGS)
            elif isinstance(node, ast.Call) and ast.unparse(node.func) == "repo_tests.read_report":
                # A read of the validated form reads the producer's required
                # evidence fields, rather than keeping another raw reader here.
                found |= {key for key, field in repo_tests.REPORT_FIELDS.items() if field.required}
            elif (isinstance(node, ast.Call) and ast.unparse(node.func) == "report.data.get"
                  and node.args and isinstance(node.args[0], ast.Constant)):
                found.add(node.args[0].value)
    return found


def test_every_field_of_the_runner_s_report_is_read_or_named_with_why_it_is_not():
    found = producer_fields()
    assert sorted(found - set(READ) - set(NOT_READ)) == [], "a field of the runner's report the inventory does not name"
    assert sorted((set(READ) | set(NOT_READ)) - found) == [], "named fields the runner no longer gives: remove them"
    assert set(READ).isdisjoint(NOT_READ)
    read = read_by_the_reading()
    assert sorted(set(READ) - read) == [], "a field named as read that the phase reading does not read"
    assert sorted((read & found) - set(READ)) == [], "a field the phase reading reads that the inventory does not name"


def test_every_field_of_the_runner_s_plugin_reaches_the_report_or_is_a_count():
    fields, on = plugin_fields(), passed_on()
    for name in fields:
        assert name in on or name in PLUGIN_COUNTS, f"the plugin writes {name!r}, and nothing says what reads it"
        assert name not in on or on[name] in READ or on[name] in NOT_READ, name
    assert set(PLUGIN_COUNTS) <= set(fields)


def test_every_listing_of_tests_the_runner_s_plugin_writes_is_read_as_one_or_an_annotation_of_a_failure():
    """A field the plugin fills with tests' ids (``self.x.add(report.nodeid)``, ``self.x[report.nodeid] = ...``) is a
    listing: the reading's union holds it (``LISTINGS``), or it annotates a failure another listing holds."""
    counted, fields, on = _counted(), plugin_fields(), passed_on()
    by_ids = set()
    for node in ast.walk(counted):
        holder = (node.func.value if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) else
                  node.value if isinstance(node, ast.Subscript) else None)
        if (isinstance(holder, ast.Attribute) and ast.unparse(holder.value) == "self"
                and (".nodeid" in ast.unparse(node) or holder.attr == "not_found_ids"
                     and isinstance(node, ast.Call) and node.func.attr == "add")):
            by_ids.add(holder.attr)
    listings = {name for name, value in fields.items()
                if any(isinstance(n, ast.Attribute) and ast.unparse(n.value) == "self" and n.attr in by_ids
                       for n in ast.walk(value)) and not ast.unparse(value).startswith("len(")}
    assert listings, "the scan found no listing: it no longer reads the plugin"
    became = {on.get(name, name) for name in listings}
    assert sorted(became - set(review.LISTINGS) - {k for k, why in NOT_READ.items() if why == ANNOTATION}) == []
    assert set(review.LISTINGS) <= became, "a listing the reading names that the runner's plugin does not write"


def test_the_scans_find_a_planted_field_and_a_planted_read():
    planted = ast.parse("def result():\n    return {'verdict': 1, 'retried_ids': []}\n")
    assert "retried_ids" in _keys(next(n.value for n in ast.walk(planted) if isinstance(n, ast.Return)))
    read = ast.parse("def phases_of(ran):\n    return ran.get('retried_ids'), _listing(ran, 'flaky_ids')\n")
    keys = {n.args[0 if ast.unparse(n.func) == "ran.get" else 1].value for n in ast.walk(read)
            if isinstance(n, ast.Call) and ast.unparse(n.func) in ("ran.get", "_listing")}
    assert keys == {"retried_ids", "flaky_ids"}
