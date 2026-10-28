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
one that cannot run in any base run is a tool error, since there is no base to compare with. A suite run killed at its
time limit has no reply: when a base run, or two of the three patched runs, were killed, the arm is a tool error
(``killed_at_time_limit``).

**Arm 3, G6's review** (``g6_review``). ``g6.harness.patch_review.review_patch`` on the tree, against ``HEAD``, with the
review contract, each requirement citing the checks given, no claim and no sources, and the arm's time limit. The row
and the acceptance rule are the development trial's (``validation/patch_review/run.py``): ``supported_within_scope``
with every requirement ``passed_on_stated_checks``. A refusal or an exception is a tool error or refusal; so is a report
that fails the A5 check (``report_check.py``). G6's model calls are read from its reply
(``assessment.completion.activities``) when it reports them, and are otherwise counted as zero; the source is recorded.

**Arm 4, the same checks without G6** (``direct_checks``). The deterministic checks G6's frozen review runs, called
directly, in G6's order and with the same deadline, and accepted only when every check passes (``DIRECT_CHECKS``):

1. ``test_weakening``: ``g6.workforce.verify.weakened_tests`` over each changed test file's text before and after (a
   moved file compared with its old text);
2. ``runner_configuration``: the changed runner files, as ``g6.harness.patch_review._runner_changes`` reads them;
3. ``ignored_inputs``: ``g6.workforce.repo_tests.ignored_inputs`` finds no git-ignored file a run could load, and
   finished its scan;
4. ``patch_changes_files``: the change against ``HEAD`` is not empty;
5. ``stated_checks``: one confined ``repo_tests.run`` of the cited checks that exist on the tree; every one passes;
6. ``tests_touching_changed_modules``: when everything before passed and time is left, one confined run of
   ``repo_tests.derive_tests``'s files not already run; it passes. When it fails, the failing files are replayed at the
   base commit (``repo_tests.run_before``), as G6 does, within the same budget; the replay is recorded and never turns a
   failure into a pass;
7. ``repository_unchanged``: ``repo_tests.snapshot`` is the same after the runs as before.

What arm 4 leaves out is G6's assessment logic: the outcome order, requirement statuses (a requirement with no check,
or a stated check that did not run, is G6's insufficient evidence; arm 4 has no rule for it), the unknowns (a test that
fails at the base too, a check that did not run to a result), the receipts and the coding gate. The gate cannot change
G6's outcome with this arm's inputs (no claim, and no diff reaches it in this commit), so arm 4 has no counterpart.

**Time limits of arms 3 and 4.** Each arm runs inside a G6 call window (``g6.core.component_process.window``) of its
time limit plus ``KILL_GRACE_S``: every child process G6 starts is killed when the window ends, and the arm then has no
reply, which is a tool error (``killed_at_time_limit``). An operator stop is never caught. A reply that came after the
time limit, or one whose own runs timed out or whose checks were cut short by its own deadline, is the arm's reply:
not accepted when late, its own outcome otherwise, and marked ``time_or_cap_reached`` unless accepted.
"""
from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

from g6.core import component_process
from g6.harness import patch_review
from g6.workforce import repo_tests, verify
from patch_review.run import row
from patch_review_utility import contract as review_contract
from patch_review_utility import report_check
from patch_review_utility.dispositions import ABSTAIN, ACCEPT, DEFECT, ERROR, G6_OUTCOMES, INTEGRITY, make
from patch_review_utility.executor import unrunnable

RUNS = 3
DIRECT_CHECKS = ("test_weakening", "runner_configuration", "ignored_inputs", "patch_changes_files", "stated_checks",
                 "tests_touching_changed_modules", "repository_unchanged")
#: How long past its time limit an arm's call window stays open, so G6's own deadline gives a reply first.
KILL_GRACE_S = 60
KILLED = "killed_at_time_limit"
#: G6's words for a check its own deadline cut short (``patch_review.py``), read from its reply.
OUT_OF_TIME = "time budget"
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
    if found["killed"]["patched"] >= 2:
        return {**found, "disposition": _with_cost(_killed("the suite on the patched tree"), start)}
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
    """Whether G6's reply says its own deadline cut a check short: a run that timed out, or a check skipped or left
    unknown because the time budget was spent."""
    assessment = (reply or {}).get("assessment") or {}
    scope = assessment.get("scope") or {}
    return (any(run.get("verdict") == "timed_out" for run in scope.get("checks_run") or [])
            or any(OUT_OF_TIME in str(skip.get("reason", "")) for skip in scope.get("checks_skipped") or [])
            or any(OUT_OF_TIME in str(unknown) for unknown in assessment.get("unknowns") or []))


def model_calls(reply: dict | None) -> dict:
    """G6's model calls as its reply reports them (each activity's ``model_calls``), and where the count came from."""
    completion = ((reply or {}).get("assessment") or {}).get("completion")
    activities = completion.get("activities") if isinstance(completion, dict) else None
    if isinstance(activities, list):
        return {"count": sum(int(a.get("model_calls") or 0) for a in activities if isinstance(a, dict)),
                "source": "assessment.completion.activities"}
    return {"count": 0, "source": MODEL_CALLS_NOT_REPORTED}


def g6_review(tree: Path, requirements: list[dict], checks: list[str], *, timeout_s: int,
              review=patch_review.review_patch, kill_after_s: float | None = None) -> dict:
    start = _clock()
    cited = review_contract.cite(requirements, checks)
    reply, error, killed = None, "", False
    try:
        with component_process.window(timeout_s + KILL_GRACE_S if kill_after_s is None else kill_after_s):
            reply = review(tree, task=_task(requirements), requirements=cited, timeout_s=timeout_s)
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
            "model_calls": calls}


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
    """Whether one run passed every id: (passed, category when not, detail)."""
    verdict = ran.get("verdict")
    if verdict in patch_review.UNTRUSTED:
        return False, INTEGRITY, f"the run's results are not believed: {verdict}"
    failed = set(ran.get("failed_ids") or []) | set(ran.get("setup_errors") or {})
    outcomes = ran.get("test_outcomes") or {}
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


def _checks(root: Path, requirements: list[dict], checks: list[str], *, deadline: float, run, run_before,
            add, done: list[dict], trace: dict) -> None:
    """The seven checks in order, each recorded with ``add`` into ``done``; runs go through ``run`` and
    ``run_before``."""
    def left() -> int:
        return max(1, int(deadline - time.monotonic()))

    def execute(ids: list[str]) -> dict:
        trace["commands"].append("pytest " + " ".join(ids))
        ran = _confined(run(root, ids, timeout_s=left()))
        trace["isolation"].append(ran.get("isolation") or {})
        trace["verdicts"].append(ran.get("verdict"))
        return ran

    root = repo_tests.checked_repo(root)
    before = repo_tests.snapshot(root)
    change = repo_tests.change(root, "HEAD")
    weakened = verify.weakened_tests({change.renamed.get(rel, rel): text for rel, text in change.before.items()},
                                     change.after)
    add("test_weakening", not weakened, "; ".join(weakened), INTEGRITY)
    runner = patch_review._runner_changes(change)
    add("runner_configuration", not runner, "; ".join(runner), INTEGRITY)
    scan = repo_tests.ignored_inputs(root)
    add("ignored_inputs", not scan.loadable and scan.complete,
        ", ".join(scan.loadable[:10]) or ("" if scan.complete else "the scan stopped at its limit"), INTEGRITY)
    add("patch_changes_files", bool(change.files), "" if change.files else "no change against HEAD", ABSTAIN)
    ids = list(dict.fromkeys(repo_tests.inside(root, c, "test") for c in checks)) if requirements else []
    present = [i for i in ids if (root / i.partition("::")[0]).exists()]
    if present:
        passed, category, detail = _passes(execute(present), present)
        missing = [i for i in ids if i not in present]
        if passed and missing:
            passed, category, detail = False, ABSTAIN, "not on the tree: " + ", ".join(missing)
        add("stated_checks", passed, detail, category)
    else:
        add("stated_checks", not ids, "no cited check is on the tree" if ids else "no check was cited", ABSTAIN)
    if not all(c["passed"] for c in done):
        add("tests_touching_changed_modules", None, "not run: an earlier check did not pass")
    elif deadline - time.monotonic() < 1:
        add("tests_touching_changed_modules", False, f"the {OUT_OF_TIME} is spent", ABSTAIN)
    else:
        derived = [rel for rel in repo_tests.derive_tests(change)[0] if rel not in present]
        if not derived:
            add("tests_touching_changed_modules", True, "no other test file names a changed module")
        else:
            second = execute(derived)
            passed, category, detail = _passes(second, derived)
            if second.get("verdict") in (repo_tests.FAILED, "collection_error") and deadline - time.monotonic() >= 1:
                failing = sorted(({n.partition("::")[0] for n in [*(second.get("failed_ids") or []),
                                                                  *(second.get("setup_errors") or {})]}
                                  | patch_review._collection_errors(second.get("output") or "")) & set(derived))
                if failing:
                    texts = {rel: (root / rel).read_text(encoding="utf-8", errors="replace") for rel in failing}
                    replays = run_before(replace(change, before=texts), timeout_s=left())
                    detail += "; at the base commit: " + ", ".join(
                        f"{rel} {(replays.get(rel) or {}).get('verdict')}" for rel in failing)
            add("tests_touching_changed_modules", passed, detail, category)
    unchanged = repo_tests.snapshot(root) == before
    add("repository_unchanged", unchanged, "" if unchanged else "the working tree changed during the checks",
        INTEGRITY)


def direct_checks(tree: Path, requirements: list[dict], checks: list[str], *, timeout_s: int, run=repo_tests.run,
                  run_before=repo_tests.run_before, kill_after_s: float | None = None) -> dict:
    start = _clock()
    done: list[dict] = []
    trace: dict = {"commands": [], "isolation": [], "verdicts": []}

    def add(name: str, passed: bool | None, detail: str = "", category: str = "") -> None:
        done.append({"name": name, "passed": passed, "detail": detail[:500], "category": "" if passed else category})

    def ended(disposition: dict) -> dict:
        return {"checks": done, "trace": {"commands": trace["commands"], "isolation": trace["isolation"]},
                "disposition": _with_cost(disposition, start)}

    try:
        with component_process.window(timeout_s + KILL_GRACE_S if kill_after_s is None else kill_after_s):
            _checks(Path(tree), requirements, checks, deadline=start[0] + timeout_s, run=run, run_before=run_before,
                    add=add, done=done, trace=trace)
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
    ran_out = "timed_out" in trace["verdicts"] or any(OUT_OF_TIME in c["detail"] for c in failing)
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
