# SPDX-License-Identifier: Apache-2.0
"""Arms 1, 3 and 4 (PROTOCOL.md, "Arms"), each ending in a disposition (``dispositions.py``). Arm 2 is
``self_review.py``. Every arm works on its own fresh tree: the base commit with the patch in the working tree.

**Arm 1, tests and continuous integration** (``tests_and_ci``). The repository's suite (the test paths its entry
freezes) runs three times at the base commit and three times on the patched tree, with the files of the tests the
patch adds or changes added on the patched tree, through the study's executor (confined in a study level). It accepts
when no test that passed in all three base runs fails on the patched tree, and every test in a test file the patch adds
or changes passes. "Fails" and "passes" are read as two of the three runs (pytest reports the test failed, or passed);
a run that could not run counts as failing every test. A test that passed in only some base runs is left out and
counted. A suite that cannot be collected or run in two of the three patched runs is not accepted (a defect found);
one that cannot run in any base run is a tool error, since there is no base to compare with. A base run killed at its
time limit is a tool error (``killed_at_time_limit``). When the base runs completed and two of the three patched runs
were killed at the limit, the patch caused it: a defect found, marked ``time_or_cap_reached`` (continuous integration
reports a time-out as a failure, and the label rule counts an over-time check as failing).

**Arm 3, G6's review** (``g6_review``). ``g6.harness.patch_review.review_patch`` on the tree, against ``HEAD``, with the
review contract, each requirement citing the checks given, no claim and no sources, and the arm's time limit. The row
and the acceptance rule are the development trial's (``validation/patch_review/run.py``): ``supported_within_scope``
with every requirement ``passed_on_stated_checks``. A refusal or an exception is a tool error or refusal; so is a report
that fails the A5 check (``report_check.py``). G6's model calls are read from its reply
(``assessment.completion.activities``) when it reports them, and are otherwise counted as zero; the source is recorded.

**Arm 4, the same checks without G6** (``direct_checks``). The deterministic checks G6's frozen review runs (Step 2,
frozen at commit b3c342b; the line numbers are ``src/g6/harness/patch_review.py``'s there), called directly, in G6's
order and with the same deadline, and accepted only when every check passes (``DIRECT_CHECKS``):

1. ``test_weakening``: ``g6.safety_engine.detect_test_weakening`` over every changed test-related file
   (``repo_tests.related_texts``: test files, ``conftest.py``, start-up files, any Python file under a test folder; a
   moved one compared with its old text), as at line 514 (review C2);
2. ``runner_configuration``: the changed runner files, as ``patch_review._runner_changes`` reads them (lines 179-213,
   called at 516). Its runner list is ``repo_tests.RUNNER_FILES`` (``pytest.toml`` and ``.pytest.toml`` included,
   review C1), shared, so it comes through as G6 has it;
3. ``ignored_inputs``: ``repo_tests.ignored_inputs`` finds no git-ignored file a run could load, and finished its scan
   (lines 519-527; bytecode, extension modules, links and plugin metadata, review C3, and the folders it does not
   enter, come through the shared function);
4. ``patch_changes_files``: the change against ``HEAD`` is not empty (line 528);
5. ``stated_checks``: one confined ``repo_tests.run`` of the cited checks that exist on the tree; every one passes
   (lines 531-551), each node id read against the root folder the run reports (``patch_review._in_repository``,
   lines 275-279; review I9);
6. ``tests_touching_changed_modules``: when everything before passed and time is left, one confined run of
   ``repo_tests.derive_tests``'s files not already run; it passes (lines 553-578). When it fails, the failing files
   (named against the run's root folder) are replayed at the base commit with ``repo_tests.run_before``, as
   ``patch_review._on_base`` does (lines 667-704), a moved file at its old path (review I6, inside ``run_before``),
   within the same budget; the replay is recorded and never turns a failure into a pass;
7. ``repository_unchanged``: ``repo_tests.snapshot`` is the same after the runs as before (line 582), compared by
   ``repo_tests._moved``, the comparison G6 itself uses from the pool-compatibility fix pass on (at b3c342b it is the
   same as comparing the two snapshots);
8. ``submodule_folders``: with the pool-compatibility change only (below), no submodule folder leaves the evidence
   short; at b3c342b it always passes, since that G6 refuses a repository holding one;
9. ``runs_on_the_reviewed_tree``: every in-place run began on the tree under review, its ``source_before`` the digest
   of the snapshot taken first (lines 469-470 and 584-588; review I4);
10. ``test_diff_shortcuts``: the coding gate's diff scan (``g6.coding_agent_gate.scan_diff``) finds nothing in the diff
   against the base of the test, test-runner and continuous-integration files only (``patch_review._test_diff`` over
   ``repo_tests.diff_text``, lines 409-421 and 589, read as integrity at 600-606; review I8). With no claim, the
   gate's other shortcut finding cannot occur.

Not mirrored: G6's run of the passing stated checks against the base commit's code (lines 579 and 631-664; review I8).
It can only add an unknown and a next action, never change G6's outcome, so it cannot change arm 3's acceptance.

**With the pool-compatibility change** (claude/pool-compat, a2161c7; feature-detected, so the same script runs at
b3c342b): the repository's import roots go first on the import path of every run (``import_roots``), and G6's guard
is mirrored: a tier 1 or tier 2 run that loaded a changed module from outside the tree, or cannot say what it loaded
(``repo_tests.shadowed``, applied where ``patch_review.py`` applies it at a2161c7, lines 569-584, 624 and 656), is no
evidence of the patch, so its check does not pass and the base replay is not run. The submodule folders git does not
look into are read as at the fix pass (8b5364c, lines 615-620 and 663-668): a changed gitlink entry
(``change_inside_submodule``), or a folder G6 could not hash before the runs or only after them
(``submodule_not_hashed``), fails ``submodule_folders`` (insufficient evidence, never an integrity finding: a folder
not hashed on either side is not a change). It is recorded after the runs, so, as in G6, it never stops them.

What arm 4 leaves out is G6's assessment logic: the outcome order, requirement statuses (a requirement with no check,
or a stated check that did not run, is G6's insufficient evidence; arm 4 has no rule for it), the unknowns (a test that
fails at the base too, a check that did not run to a result), and the receipts.

**Time limits of arms 3 and 4.** The study runs each patch's review in its own interpreter (``in_child``: ``python -I
child.py``, through ``g6.core.component_process.run_process`` inside a call window of the arm's time limit plus
``KILL_GRACE_S``), so a hang anywhere in the review, Python code included, cannot stall the study: when the window ends,
the child is killed with its whole process tree, and the arm has no reply, which is a tool error
(``killed_at_time_limit``). The child gets the study's environment without the research flag. Inside the child, the arm
runs in a window of the same length, which stops the processes G6 starts. An operator stop is never caught. A reply
that came after the time limit, or one whose own runs timed out or whose checks were cut short by its own deadline, is
the arm's reply: not accepted when late, its own outcome otherwise, and marked ``time_or_cap_reached`` unless accepted.
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import g6
from g6.coding_agent_gate import scan_diff
from g6.core import component_process
from g6.core.ledger import digest
from g6.harness import patch_review
from g6.safety_engine.weakening import detect_test_weakening
from g6.workforce import repo_tests
from patch_review.run import row
from patch_review_utility import containers, report_check
from patch_review_utility import contract as review_contract
from patch_review_utility.dispositions import ABSTAIN, ACCEPT, DEFECT, ERROR, G6_OUTCOMES, INTEGRITY, make
from patch_review_utility.executor import stop_container, unrunnable

RUNS = 3
DIRECT_CHECKS = ("test_weakening", "runner_configuration", "ignored_inputs", "patch_changes_files", "stated_checks",
                 "tests_touching_changed_modules", "repository_unchanged", "submodule_folders",
                 "runs_on_the_reviewed_tree", "test_diff_shortcuts")
#: How long past its time limit an arm's call window stays open, so G6's own deadline gives a reply first.
KILL_GRACE_S = 60
KILLED = "killed_at_time_limit"
#: G6's own codes for a review its deadline cut short (review I5, ``patch_review.py`` at b3c342b): the reason code of
#: a tier 1 run out of time (line 543), and a check's or run's verdict ``timed_out`` (lines 89 and 307). A check
#: never reached reads ``not_run`` (line 319), which other causes share, so it is read only through the reason code.
RUN_TIMED_OUT, TIMED_OUT = "run_timed_out", "timed_out"
#: How the direct checks say a run is no evidence of the patch: G6's guard (``changed_code_not_loaded``).
NOT_LOADED = "changed code not loaded"
MODEL_CALLS_NOT_REPORTED = ("not reported in G6's reply; its review at this commit makes no model call when no claim "
                            "and no sources are given, so counted as 0")


def _clock() -> tuple[float, float]:
    times = os.times()
    return time.monotonic(), times.user + times.system + times.children_user + times.children_system


def _cost(start: tuple[float, float], model_calls: int = 0) -> dict:
    now = _clock()
    return {"seconds": round(now[0] - start[0], 3), "processor_seconds": round(now[1] - start[1], 3),
            "model_calls": model_calls}


def _with_cost(disposition: dict, start: tuple[float, float]) -> dict:
    return {**disposition, "cost": _cost(start)}


def _killed(what: str) -> dict:
    return make(ERROR, f"{what} was stopped at its wall-clock limit, with no reply", error_kind=KILLED,
                time_or_cap=True)


# --- arm 1 --------------------------------------------------------------------------------------------------------------

def tests_and_ci(base_tree: Path, patched_tree: Path, *, suite: list[str], patch_tests: list[str], executor,
                 timeout_s: float) -> dict:
    start = _clock()
    base = [executor.run(base_tree, list(suite), timeout_s=timeout_s) for _ in range(RUNS)]
    targets = list(dict.fromkeys([*suite, *patch_tests]))
    patched = [executor.run(patched_tree, targets, timeout_s=timeout_s) for _ in range(RUNS)]
    described = executor.describe() if hasattr(executor, "describe") else {}
    trace = {"commands": [" ".join(map(str, described.get("command", []))) + f" ({len(targets)} target(s))"],
             "isolation": [{"backend": "study executor" if described.get("confined") else None,
                            "network": described.get("network")}]}
    found = {"base_passing": 0, "base_flaky": [], "regressions": [], "added_not_passing": [],
             "unrunnable": sum(unrunnable(r) for r in patched),
             "killed": {"base": sum(r["timed_out"] for r in base), "patched": sum(r["timed_out"] for r in patched)},
             "trace": trace}
    if found["killed"]["base"]:
        return {**found, "disposition": _with_cost(_killed("a suite run at the base commit"), start)}
    if any(unrunnable(r) for r in base):
        return {**found, "disposition": _with_cost(make(ERROR, "the suite cannot run at the base commit",
                                                        error_kind="tool_error"), start)}
    nodes = {node for r in base for node in r["outcomes"]}
    passing = {n for n in nodes if all(r["outcomes"].get(n) == "passed" for r in base)}
    found["base_passing"] = len(passing)
    found["base_flaky"] = sorted(n for n in nodes - passing if any(r["outcomes"].get(n) == "passed" for r in base))
    if found["killed"]["patched"] >= 2:   # the base completed, so the patch made the suite overrun its limit
        return {**found, "disposition": _with_cost(make(DEFECT, "the suite on the patched tree ran past its time limit "
                                                                "in two of three runs", time_or_cap=True), start)}
    if found["unrunnable"] >= 2:
        return {**found, "disposition": _with_cost(make(DEFECT, "the suite cannot be collected or run on the patched "
                                                                "tree"), start)}

    def count(node: str, outcome: str) -> int:
        return sum(1 for r in patched if (unrunnable(r) and outcome == "failed")
                   or (not unrunnable(r) and r["outcomes"].get(node) == outcome))

    found["regressions"] = sorted(n for n in passing if count(n, "failed") >= 2)
    added = sorted({n for r in patched for n in r["outcomes"] if n.partition("::")[0] in set(patch_tests)})
    found["added_not_passing"] = [n for n in added if count(n, "passed") < 2]
    if found["regressions"] or found["added_not_passing"]:
        detail = (f"{len(found['regressions'])} regression(s); {len(found['added_not_passing'])} added or changed "
                  "test(s) not passing")
        return {**found, "disposition": _with_cost(make(DEFECT, detail), start)}
    return {**found, "disposition": _with_cost(make(ACCEPT), start)}


# --- arm 3 --------------------------------------------------------------------------------------------------------------

def _task(requirements: list[dict]) -> str:
    return requirements[0]["text"] if requirements else "Review the patch against the review contract."


def _ran_out(reply: dict | None) -> bool:
    """Whether G6's reply says, in its own codes (``RUN_TIMED_OUT``, ``TIMED_OUT``), that its deadline cut the review
    short: the tier 1 run's reason code, a stated check's verdict, or a run's verdict. Its words are never read."""
    assessment = (reply or {}).get("assessment") or {}
    scope = assessment.get("scope") or {}
    return (any(reason.get("code") == RUN_TIMED_OUT for reason in assessment.get("reasons") or [])
            or any(check.get("verdict") == TIMED_OUT for req in assessment.get("requirements") or []
                   for check in req.get("checks") or [])
            or any(run.get("verdict") == TIMED_OUT for run in scope.get("checks_run") or []))


def model_calls(reply: dict | None) -> dict:
    """G6's model calls as its reply reports them (each activity's ``model_calls``), and where the count came from.

    TODO(verification search): ``assessment.completion.activities`` and its per-activity ``model_calls`` are a guessed
    shape: verification search, which will add the field, does not exist yet. Replace this reader with its real fields
    when it lands; until then every reply at b3c342b lacks the field and the count is 0 (``MODEL_CALLS_NOT_REPORTED``)."""
    completion = ((reply or {}).get("assessment") or {}).get("completion")
    activities = completion.get("activities") if isinstance(completion, dict) else None
    if isinstance(activities, list):
        return {"count": sum(int(a.get("model_calls") or 0) for a in activities if isinstance(a, dict)),
                "source": "assessment.completion.activities"}
    return {"count": 0, "source": MODEL_CALLS_NOT_REPORTED}


def stated_check_verdicts(reply: dict | None) -> dict[str, str] | None:
    """Each stated check's verdict in G6's reply (``assessment.requirements[].checks``), by id: the checks whose
    provenance is ``stated``, or that carry none (G6's review before provenance was recorded). None when the reply
    has no assessment. V10's comparison of the search arms' stated-check results with arm 3's reads it."""
    requirements = ((reply or {}).get("assessment") or {}).get("requirements") if isinstance(reply, dict) else None
    if not isinstance(requirements, list):
        return None
    found: dict[str, str] = {}
    for req in requirements:
        for check in (req.get("checks") or []) if isinstance(req, dict) else []:
            if isinstance(check, dict) and check.get("id") and check.get("provenance", "stated") == "stated":
                found[check["id"]] = check.get("verdict")
    return found


def defects_reproduced(reply: dict | None) -> dict | None:
    """The defects G6's reply reproduced (``assessment.completion.defects_reproduced``), with their reproductions;
    None when the reply has no completion section (G6's review before verification search). V10 reruns arm 3's."""
    completion = ((reply or {}).get("assessment") or {}).get("completion") if isinstance(reply, dict) else None
    found = completion.get("defects_reproduced") if isinstance(completion, dict) else None
    return found if isinstance(found, dict) else None


def g6_review(tree: Path, requirements: list[dict], checks: list[str], *, timeout_s: int,
              review=patch_review.review_patch, kill_after_s: float | None = None,
              import_roots: list[str] | tuple = ()) -> dict:
    start = _clock()
    if import_roots and "import_roots" not in inspect.signature(review).parameters:
        said = ("this repository needs its import roots (" + ", ".join(import_roots) + "), and the frozen G6's "
                "review takes no import_roots, so it would test the installed code, not the patch")
        return {"disposition": _with_cost(make(ERROR, said, error_kind="tool_error"), start),
                "trace": {"commands": [], "isolation": []}, "model_calls": model_calls(None)}
    rooted = {"import_roots": list(import_roots)} if import_roots else {}
    cited = review_contract.cite(requirements, checks)
    reply, error, killed = None, "", False
    try:
        with component_process.window(timeout_s + KILL_GRACE_S if kill_after_s is None else kill_after_s):
            reply = review(tree, task=_task(requirements), requirements=cited, timeout_s=timeout_s, **rooted)
    except component_process.ExecutionInterrupted as exc:
        if exc.stopped:
            raise   # an operator stop ends the study, never one arm's result
        killed = True
    except component_process.ContainmentFailure:
        raise
    except Exception as exc:  # a tool error is counted, never dropped
        error = f"{type(exc).__name__}: {exc}"
    calls = model_calls(reply)
    cost = _cost(start, calls["count"])
    found = row(Path(tree).name, None, reply, cost["seconds"], error=error)
    a5 = report_check.check(reply, [r["id"] for r in cited]) if found["g6_outcome"] else None
    ran_out = _ran_out(reply)
    if killed:
        disposition = _killed("G6's review")
    elif found["tool_error"]:
        disposition = make(ERROR, found["tool_error"], error_kind="tool_error")
    elif found["refused"]:
        disposition = make(ERROR, str((reply or {}).get("error", ""))[:300], error_kind="refusal")
    elif not a5["complete"]:
        disposition = make(ERROR, "the report lacks: " + ", ".join(a5["missing"]), error_kind="a5_report_incomplete")
    elif cost["seconds"] > timeout_s:
        disposition = make(ABSTAIN, "the review replied after its time limit", time_or_cap=True)
    elif found["g6_accept"]:
        disposition = make(ACCEPT)
    elif found["g6_outcome"] in G6_OUTCOMES:
        disposition = make(G6_OUTCOMES[found["g6_outcome"]], "; ".join(found["reason_codes"])[:300],
                           time_or_cap=ran_out)
    else:
        disposition = make(ERROR, f"unknown outcome {found['g6_outcome']!r}", error_kind="tool_error")
    trace = {"commands": [run["command"] for run in found["checks_run"]],
             "isolation": [found["isolation"]] if found["isolation"] else []}
    return {"disposition": {**disposition, "cost": cost}, "row": found, "a5": a5, "trace": trace,
            "model_calls": calls, "stated_checks": stated_check_verdicts(reply),
            "defects_reproduced": defects_reproduced(reply)}


# --- arm 4 --------------------------------------------------------------------------------------------------------------

class _Unconfined(repo_tests.RepoRefused):
    pass


def _confined(ran: dict) -> dict:
    if "isolation" in ran and not (ran["isolation"] or {}).get("backend"):
        raise _Unconfined("G6_E_ISOLATION_UNAVAILABLE", "no operating-system boundary confined the run")
    return ran


def _covers(check: str, node: str) -> bool:
    check = check.rstrip("/")
    return node == check or node.startswith((check + "::", check + "[", check + "/"))


def _passes(ran: dict, ids: list[str]) -> tuple[bool, str, str]:
    """Whether one run passed every id: (passed, category when not, detail). Node ids are read against the root folder
    the run reports, as checks name them (``patch_review._in_repository``; review I9)."""
    verdict = ran.get("verdict")
    if verdict in patch_review.UNTRUSTED:
        return False, INTEGRITY, f"the run's results are not believed: {verdict}"
    full = patch_review._in_repository(ran)
    failed = {full(node) for node in [*(ran.get("failed_ids") or []), *(ran.get("setup_errors") or {})]}
    outcomes = {full(node): phases for node, phases in (ran.get("test_outcomes") or {}).items()}
    bad, unrun = [], []
    for check in ids:
        nodes = {node: phases for node, phases in outcomes.items() if _covers(check, node)}
        if any(node in failed or "failed" in phases.values() for node, phases in nodes.items()):
            bad.append(check)
        elif not nodes or any(phases.get("call") != "passed" or "skipped" in phases.values()
                              for phases in nodes.values()):
            unrun.append(check)
    if bad or verdict == repo_tests.FAILED:
        return False, DEFECT, "failed: " + ", ".join(bad or [f"the run ({verdict})"])
    if unrun or verdict != repo_tests.PASSED or ran.get("degraded"):
        return False, ABSTAIN, f"did not run to a pass ({verdict}): " + ", ".join(unrun)
    return True, "", f"{len(ids)} passed"


def _not_loaded(change, ran: dict) -> str:
    """Why a run that ran is no evidence of the patch, as G6's guard reads it (``repo_tests.shadowed``: a changed
    module loaded from outside the tree, an installed copy, or a run that cannot say what it loaded); "" when it is
    evidence, or when this G6 has no such check (before the pool-compatibility change)."""
    shadowed = getattr(repo_tests, "shadowed", None)
    if shadowed is None or ran.get("verdict") not in repo_tests.RAN:
        return ""
    found = shadowed(change.sources, ran)
    if found is None:
        return f"{NOT_LOADED}: the run cannot say which code it loaded"
    return "; ".join(f"{NOT_LOADED}: {rel} is changed, but the run loaded {where['module']} from {where['file']}"
                     for rel, where in found.items())


def _submodule_folders(change, before: dict, final: dict) -> tuple[bool, str]:
    """Whether the submodule folders git does not look into leave the evidence whole, as G6 reads them at the
    pool-compatibility fix pass (8b5364c): no changed gitlink entry (``change_inside_submodule``), and no folder G6
    could not hash, before the runs or only after them (``submodule_not_hashed``). (True, "") when this G6 reads no
    such folder (before the pool-compatibility change, which refuses a repository holding one)."""
    not_hashed = getattr(repo_tests, "submodules_not_hashed", None)
    short = [f"{rel}: the patch changes this submodule's entry" for rel in getattr(change, "submodule_changes", ())]
    if not_hashed is not None:
        earlier = not_hashed(before)
        short += [f"{rel}: G6 could not hash this submodule folder ({why})" for rel, why in earlier.items()]
        short += [f"{rel}: G6 could not hash this submodule folder after the runs ({why})"
                  for rel, why in not_hashed(final).items() if rel not in earlier]
    return not short, "; ".join(short)


def _checks(root: Path, requirements: list[dict], checks: list[str], *, deadline: float, run, run_before,
            add, done: list[dict], trace: dict, rooted: dict) -> None:
    """The ten checks in order (the module docstring gives each one's line in G6's review), each recorded with
    ``add`` into ``done``; runs go through ``run`` and ``run_before``."""
    in_place: list[dict] = []

    def left() -> int:
        return max(1, int(deadline - time.monotonic()))

    def execute(ids: list[str]) -> dict:
        trace["commands"].append("pytest " + " ".join(ids))
        ran = _confined(run(root, ids, timeout_s=left(), **rooted))
        trace["isolation"].append(ran.get("isolation") or {})
        trace["verdicts"].append(ran.get("verdict"))
        in_place.append(ran)
        return ran

    root = repo_tests.checked_repo(root)
    before = repo_tests.snapshot(root)
    artifact = digest(before)                                                     # lines 469-470
    change = repo_tests.change(root, "HEAD")
    weakened = list(detect_test_weakening(*repo_tests.related_texts(change)).reasons)   # line 514
    add("test_weakening", not weakened, "; ".join(weakened), INTEGRITY)
    runner = patch_review._runner_changes(change)                                 # line 516
    add("runner_configuration", not runner, "; ".join(runner), INTEGRITY)
    scan = repo_tests.ignored_inputs(root)                                        # lines 519-527
    add("ignored_inputs", not scan.loadable and scan.complete,
        ", ".join(scan.loadable[:10]) or ("" if scan.complete else "the scan stopped at its limit"), INTEGRITY)
    add("patch_changes_files", bool(change.files), "" if change.files else "no change against HEAD", ABSTAIN)
    ids = list(dict.fromkeys(repo_tests.inside(root, c, "test") for c in checks)) if requirements else []
    present = [i for i in ids if (root / i.partition("::")[0]).exists()]          # lines 531-551
    if present:
        first = execute(present)
        elsewhere = _not_loaded(change, first)
        passed, category, detail = (False, ABSTAIN, elsewhere) if elsewhere else _passes(first, present)
        missing = [i for i in ids if i not in present]
        if passed and missing:
            passed, category, detail = False, ABSTAIN, "not on the tree: " + ", ".join(missing)
        add("stated_checks", passed, detail, category)
    else:
        add("stated_checks", not ids, "no cited check is on the tree" if ids else "no check was cited", ABSTAIN)
    if not all(c["passed"] for c in done):                                        # lines 553-578
        add("tests_touching_changed_modules", None, "not run: an earlier check did not pass")
    elif deadline - time.monotonic() < 1:
        add("tests_touching_changed_modules", False, "not run: the arm's time limit was reached", ABSTAIN,
            out_of_time=True)
    else:
        derived = [rel for rel in repo_tests.derive_tests(change)[0] if rel not in present]
        if not derived:
            add("tests_touching_changed_modules", True, "no other test file names a changed module")
        else:
            second = execute(derived)
            elsewhere = _not_loaded(change, second)
            passed, category, detail = (False, ABSTAIN, elsewhere) if elsewhere else _passes(second, derived)
            if (not elsewhere and second.get("verdict") in (repo_tests.FAILED, "collection_error")
                    and deadline - time.monotonic() >= 1):
                full = patch_review._in_repository(second)                        # lines 667-704
                failing = sorted({full(rel) for rel in {n.partition("::")[0] for n in [
                    *(second.get("failed_ids") or []), *(second.get("setup_errors") or {})]}
                    | patch_review._collection_errors(second.get("output") or "")} & set(derived))
                if failing:
                    texts = {rel: (root / rel).read_text(encoding="utf-8", errors="replace") for rel in failing}
                    replays = run_before(replace(change, before=texts), timeout_s=left(), **rooted)
                    detail += "; at the base commit: " + ", ".join(
                        f"{rel} {(replays.get(rel) or {}).get('verdict')}" for rel in failing)
            add("tests_touching_changed_modules", passed, detail, category)
    final = repo_tests.snapshot(root)
    unchanged = not repo_tests._moved(before, final)                              # line 582
    add("repository_unchanged", unchanged, "" if unchanged else "the working tree changed during the checks",
        INTEGRITY)
    add("submodule_folders", *_submodule_folders(change, before, final), ABSTAIN)
    elsewhere = [ran["source_before"] for ran in in_place
                 if ran.get("source_before") and ran["source_before"] != artifact]   # lines 584-588
    add("runs_on_the_reviewed_tree", not elsewhere,
        f"{len(elsewhere)} run(s) began on a working tree other than the one under review" if elsewhere else "",
        INTEGRITY)
    hits = scan_diff(patch_review._test_diff(repo_tests.diff_text(change)))       # lines 589 and 600-606
    add("test_diff_shortcuts", not hits, ", ".join(hits), INTEGRITY)


def direct_checks(tree: Path, requirements: list[dict], checks: list[str], *, timeout_s: int, run=repo_tests.run,
                  run_before=repo_tests.run_before, kill_after_s: float | None = None,
                  import_roots: list[str] | tuple = ()) -> dict:
    start = _clock()
    done: list[dict] = []
    trace: dict = {"commands": [], "isolation": [], "verdicts": []}

    def add(name: str, passed: bool | None, detail: str = "", category: str = "", *, out_of_time: bool = False) -> None:
        done.append({"name": name, "passed": passed, "detail": detail[:500], "category": "" if passed else category,
                     "out_of_time": out_of_time})

    def ended(disposition: dict) -> dict:
        return {"checks": done, "trace": {"commands": trace["commands"], "isolation": trace["isolation"]},
                "disposition": _with_cost(disposition, start)}

    try:
        with component_process.window(timeout_s + KILL_GRACE_S if kill_after_s is None else kill_after_s):
            _checks(Path(tree), requirements, checks, deadline=start[0] + timeout_s, run=run, run_before=run_before,
                    add=add, done=done, trace=trace,
                    rooted={"roots": tuple(import_roots)} if import_roots else {})
    except component_process.ExecutionInterrupted as exc:
        if exc.stopped:
            raise
        return ended(_killed("the direct checks"))
    except component_process.ContainmentFailure:
        raise
    except repo_tests.RepoRefused as exc:
        return ended(make(ERROR, f"[{exc.code}] {exc}"[:300], error_kind="refusal"))
    except (OSError, ValueError, TypeError) as exc:
        return ended(make(ERROR, f"{type(exc).__name__}: {exc}"[:300], error_kind="tool_error"))
    failing = [c for c in done if c["passed"] is False]
    ran_out = TIMED_OUT in trace["verdicts"] or any(c["out_of_time"] for c in failing)
    cost = _cost(start)
    if not failing and cost["seconds"] > timeout_s:
        disposition = make(ABSTAIN, "the checks finished after their time limit", time_or_cap=True)
    elif not failing:
        disposition = make(ACCEPT)
    else:
        kinds = {c["category"] for c in failing}
        category = INTEGRITY if INTEGRITY in kinds else DEFECT if DEFECT in kinds else ABSTAIN
        disposition = make(category, "; ".join(f"{c['name']}: {c['detail']}" for c in failing)[:500],
                           time_or_cap=ran_out)
    return {"checks": done, "trace": {"commands": trace["commands"], "isolation": trace["isolation"]},
            "disposition": {**disposition, "cost": cost}}


# --- arms 3 and 4 in their own process --------------------------------------------------------------------------------

#: The two arms the study runs in a child process, by the names its jobs use.
CHILD_ARMS = {"g6": g6_review, "direct_checks": direct_checks}
CHILD = Path(__file__).with_name("child.py")
#: The folders a child imports from: this study's (validation/) and G6's (the folder holding the ``g6`` package).
ROOTS = (str(Path(__file__).resolve().parents[1]), str(Path(g6.__file__).resolve().parents[1]))
RESEARCH_FLAG = "G6_ECHA_RESEARCH"


def run_job(job: dict) -> dict:
    """In the child: the arm the job names (one of ``CHILD_ARMS``; a ``module:function`` only in tests) on its inputs."""
    arm = job["arm"]
    if arm in CHILD_ARMS:
        review = CHILD_ARMS[arm]
    else:
        module, _, name = arm.partition(":")
        review = getattr(importlib.import_module(module), name)
    rooted = {"import_roots": job["import_roots"]} if job.get("import_roots") else {}
    return review(Path(job["tree"]), job["requirements"], job["checks"], timeout_s=job["timeout_s"], **rooted)


def _fill(word: str, filled: dict[str, str]) -> str:
    for key, value in filled.items():
        word = word.replace(key, value)
    return word


def _no_reply(disposition: dict, start: tuple[float, float], child: dict) -> dict:
    return {"disposition": _with_cost(disposition, start), "trace": {"commands": [], "isolation": []},
            "child": child}


def in_child(arm: str, tree: Path, requirements: list[dict], checks: list[str], *, timeout_s: int,
             kill_after_s: float | None = None, roots: list[str | Path] | tuple = (),
             command: list[str] | None = None, import_roots: list[str] | tuple = ()) -> dict:
    """``arm``'s review of ``tree`` in a fresh isolated interpreter, killed with its process tree at the limit. With
    ``command`` (``containers.g6_command``), the interpreter is G6's toolchain inside the task's image: the change is
    staged beside the job, the child reviews the image's ``/app``, and a container past its limit is stopped by name."""
    start = _clock()
    limit = timeout_s + KILL_GRACE_S if kill_after_s is None else kill_after_s
    with tempfile.TemporaryDirectory(prefix="v9-arm-") as folder:
        job, reply, log = Path(folder) / "job.json", Path(folder) / "reply.json", Path(folder) / "child.log"
        name = f"v9-{os.urandom(8).hex()}"
        if command is None:
            target, imports = str(tree), [*map(str, roots), *ROOTS]
            argv = [sys.executable, "-I", str(CHILD), str(job), str(reply)]
        else:
            filled = {"{container}": name, "{job}": folder, "{base}": containers.stage(tree, folder)}
            target, imports = containers.REPOSITORY, [containers.STUDY_MOUNT]
            argv = [_fill(word, filled) for word in command]
        job.write_text(json.dumps({"arm": arm, "tree": target, "requirements": requirements, "checks": checks,
                                   "timeout_s": timeout_s, "roots": imports,
                                   "import_roots": list(import_roots)}), encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if key != RESEARCH_FLAG}
        try:
            with open(log, "wb") as sink, component_process.window(limit):
                code = component_process.run_process(argv, env=env, stdout=sink, stderr=subprocess.STDOUT)
        except component_process.ExecutionInterrupted as exc:
            if exc.stopped:
                raise
            stopped = stop_container(argv, name) if command is not None else None
            return _no_reply(_killed(f"the {arm} review"), start, {"killed": True, "limit_s": limit,
                                                                   "container_stopped": stopped})
        said = log.read_text(encoding="utf-8", errors="replace")[-600:]
        if not reply.is_file():
            return _no_reply(make(ERROR, f"the review's process ended with exit code {code} and no reply: {said}",
                                  error_kind="tool_error"), start, {"exit_code": code, "limit_s": limit})
        found = json.loads(reply.read_text(encoding="utf-8"))
    return {**found, "child": {"exit_code": code, "limit_s": limit, "seconds": _cost(start)["seconds"]}}
