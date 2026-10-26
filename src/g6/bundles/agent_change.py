# SPDX-License-Identifier: Apache-2.0
"""Bundle: Did my coding agent really fix it? G6 runs the checks on your repository itself, then holds the agent's
report to what it saw.

No model. On the git repository you name (the agent's change in its working tree, or committed since the base you
give), G6:

1. reads the change from git (``g6.workforce.repo_tests.change``);
2. runs the tests you name, or the tests the change touches (``derive_tests``), in place, and reads pytest's own
   per-test report, never what the tests print;
3. reruns the original version of each test file the change edited on the new code (``run_before``): a test that
   passed before the change and fails in its original form now was changed to hide something;
4. compares each changed test file before and after by its syntax tree (``verify.weakened_tests``: a removed or
   rewritten assert, a removed test, a new skip, an early return);
5. scans the diff for shortcuts (``coding_agent_gate.scan_diff``: an added skip marker, ``|| true``, ``--no-verify``,
   a removed or tautological assertion);
6. makes small deliberate mistakes on the changed lines, within a time budget, and reruns the tests on each
   (``mutation_check``): a mistake the tests still pass is a blind spot;
7. holds the agent's report to all of that (``coding_agent_gate.verify_answer``, with the runs as G6-observed
   evidence): "all tests pass" over failing tests, or a claim nothing backs, is flagged.

The best label is ``qualified-draft``: the tests are yours or the agent's, not independent of the change (DECISIONS
#22), and a passing run says the tests passed, not that the task is done. It is not an operating-system sandbox: the
tests run with your permissions, in your folder. Git is read before and after, a run that changed tracked files says
so, and network connections are refused inside the test process.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from typing import Any

from g6.bundles import Bundle, BundleResult, Field, weakest

MUTATION_BUDGET_S = 180
_SHOWN = 5


def _listed(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _row(check: str, result: str, detail: str, state: str) -> dict[str, str]:
    return {"check": check, "result": result, "detail": detail, "label": state}


def _as_run(out: dict) -> SimpleNamespace:
    """A ``repo_tests.run`` answer in the shape ``mutation_check`` reads: executed only when the tests ran."""
    from g6.workforce import repo_tests as rt

    ran = out.get("verdict") in rt.RAN
    return SimpleNamespace(execution_id=uuid.uuid4().hex if ran else "", passed=int(out.get("passed", 0)),
                           failed=int(out.get("failed", 0)), not_passed=int(out.get("not_passed", 0)),
                           reason="" if ran else str(out.get("verdict", "")))


def _mutations(change, repo, tests: list[str], seconds: float) -> tuple[dict[str, str], list[str]]:
    from g6.workforce import mutation_check
    from g6.workforce import repo_tests as rt

    tried = caught = 0
    survived: list[str] = []
    stopped = ""
    started = time.monotonic()
    for rel in change.sources[:rt.MUTATED_FILES]:
        lines = rt.changed_lines(change, rel)
        remaining = MUTATION_BUDGET_S - (time.monotonic() - started)
        if not lines or remaining <= 0:
            continue

        def runner(source: str, timeout_s: int, rel: str = rel) -> SimpleNamespace:
            try:
                return _as_run(rt.run(repo, tests, mutant=(rel, source), timeout_s=timeout_s))
            except rt.RepoRefused as exc:
                return SimpleNamespace(execution_id="", passed=0, failed=0, not_passed=0, reason=str(exc))

        record = mutation_check.check((repo / rel).read_text(encoding="utf-8", errors="replace"), runner,
                                      budget_s=remaining, base_duration_s=seconds, lines=lines)
        tried, caught = tried + record["tried"], caught + record["rejected"]
        survived += [f"{rel}: {s}" for s in record["survived"]]
        stopped = stopped or record["stopped"]
    if not tried:
        return _row("Small deliberate mistakes on the changed lines", "not run",
                    stopped or "the change has no changed Python source lines to try", "qualified-draft"), []
    detail = f"{caught} of {tried} caught by the tests"
    if survived:
        detail += "; not caught: " + "; ".join(survived[:_SHOWN])
    notes = [f"Your tests still passed when G6 changed {s}: no test checks that behaviour (or the change happens to "
             "be equivalent)." for s in survived[:_SHOWN]]
    return _row("Small deliberate mistakes on the changed lines", "passed" if not survived else "gaps", detail,
                "qualified-draft"), notes


def run(inputs: dict[str, str], llm: Any = None) -> BundleResult:
    from g6.coding_agent_gate import Evidence, scan_diff, verify_answer
    from g6.workforce import repo_tests as rt
    from g6.workforce import verify

    folder, task, answer = (str(inputs.get(key, "")).strip() for key in ("repo", "task", "answer"))
    if not folder or not task or not answer:
        return BundleResult("blocked-escalated", "Give the repository folder, what you asked the agent to do, and "
                            "what it said it did.", code="G6_E_INPUT_VALIDATION")
    base = str(inputs.get("base", "")).strip() or "HEAD"
    try:
        repo = rt.checked_repo(folder)
        change = rt.change(repo, base)
        if not change.files:
            return BundleResult("blocked-escalated", f"git shows no change against {base}, so there is nothing to "
                                "check. Leave the agent's change uncommitted, or give the base it started from.",
                                code="G6_E_NO_CHANGE")
        named = _listed(inputs.get("tests", ""))
        tests, more = (named, 0) if named else rt.derive_tests(change)
        if not tests:
            return BundleResult("blocked-escalated", "No test touches this change (none was changed, and none names a "
                                "changed module). Name the tests to run.", code="G6_E_NO_TESTS")
        diff = rt.diff_text(change)
        found = rt.run(repo, tests)
    except rt.RepoRefused as exc:
        return BundleResult("blocked-escalated", str(exc), code=exc.code)

    rows: list[dict[str, str]] = []
    notes = ["G6 ran your repository's tests on this computer with your permissions; it is not an operating-system "
             "sandbox. Network connections were refused inside the tests.",
             "The best label is qualified-draft: the tests are yours or the agent's, not independent of the change, "
             "and passing tests do not prove the task is done."]
    verdict, ran = found["verdict"], found["verdict"] in rt.RAN
    if verdict == rt.PASSED and found["total"]:
        detail = f"{found['passed']} passed" + (f", {found['not_passed']} skipped (a skip is not a pass)"
                                                 if found["not_passed"] else "")
        rows.append(_row("Your tests on the change", "passed", detail, "qualified-draft"))
    elif verdict == rt.FAILED:
        rows.append(_row("Your tests on the change", "failed", f"{found['failed']} failed: "
                         + ", ".join(found["failed_ids"][:_SHOWN]), "blocked-escalated"))
    else:
        said = found["said"] or "no test ran to a result (every test was skipped, or none was collected)"
        rows.append(_row("Your tests on the change", "not run", said + (
            ": " + ", ".join(found["changed"][:_SHOWN]) if found["changed"] else ""), "blocked-escalated"))
    if more:
        notes.append(f"{more} more test file(s) touch this change and were not run (the limit is {rt.MAX_DERIVED}); "
                     "name them to run them.")

    if change.before:
        try:
            old = rt.run_before(change)
        except rt.RepoRefused as exc:
            old = {rel: {"verdict": "not_run", "said": str(exc)} for rel in change.before}
        broke = [rel for rel, out in old.items() if out.get("verdict") == rt.FAILED]
        skipped = {rel: out.get("said", "") for rel, out in old.items() if out.get("verdict") != rt.PASSED
                   and rel not in broke}
        if broke:
            rows.append(_row("Original tests on the new code", "failed", "the original version of "
                             + ", ".join(broke[:_SHOWN]) + " passed before the change and fails now: the change "
                             "broke it, and the edited test hides that", "blocked-escalated"))
        else:
            detail = f"{len(old) - len(skipped)} original test file(s) still pass"
            if skipped:
                detail += "; not run: " + "; ".join(f"{rel} ({why})" for rel, why in list(skipped.items())[:_SHOWN])
            rows.append(_row("Original tests on the new code", "passed" if len(skipped) < len(old) else "not run",
                             detail, "qualified-draft"))
        before = {change.renamed.get(rel, rel): text for rel, text in change.before.items()}
        weakened = verify.weakened_tests(before, change.after)
        rows.append(_row("Tests weakened (by their syntax)", "failed" if weakened else "passed",
                         "; ".join(weakened[:_SHOWN]) or "no assert removed or changed, no test removed, no skip or "
                         "early return added", "blocked-escalated" if weakened else "qualified-draft"))
    else:
        rows.append(_row("Original tests on the new code", "not applicable", "the change edited no existing test file",
                         "qualified-draft"))

    shortcuts = scan_diff(diff)
    rows.append(_row("Shortcuts in the diff", "failed" if shortcuts else "passed",
                     ", ".join(shortcuts) or "no skip marker, '|| true', '--no-verify' or removed assertion added",
                     "blocked-escalated" if shortcuts else "qualified-draft"))
    if change.runner_files:
        notes.append("The change edits test-runner configuration (" + ", ".join(change.runner_files[:_SHOWN])
                     + "); read it yourself: G6 says it, and does not judge it.")

    if verdict == rt.PASSED and found["total"]:
        row, gaps = _mutations(change, repo, tests, float(found.get("seconds") or 1))
        rows.append(row)
        notes += gaps

    evidence = [Evidence("file", rel, observed_by="g6") for rel in change.files[:50]]
    if ran:
        evidence.append(Evidence("test", f"pytest {' '.join(tests[:3])}" + (" ..." if len(tests) > 3 else ""),
                                 exit_code=0 if verdict == rt.PASSED else 1, observed_by="g6"))
    if "@@" in diff:
        evidence.append(Evidence("diff", f"git diff {base}", output=diff, observed_by="g6"))
    decision = verify_answer(task, answer, evidence=evidence, task_contract=task, author="the coding agent")
    findings = "; ".join(f.message for f in decision.findings[:_SHOWN])
    rows.append(_row("The agent's report against what G6 saw", decision.verdict,
                     findings or "no rule of the coding-agent gate objected", decision.completion_state))

    state = weakest([r["label"] for r in rows])
    failed = [r["check"] for r in rows if r["label"] == "blocked-escalated"]
    summary = ("Needs a person: " + "; ".join(failed) + "." if failed else
               "No check objected. The tests ran and passed on the change; this is a qualified draft, not proof.")
    return BundleResult(state, summary, rows, notes,
                        code="G6_E_AGENT_CHANGE_FLAGGED" if failed else "G6_E_QUALIFIED_DRAFT")


BUNDLE = Bundle(
    id="agent-change-check",
    title="Did my coding agent really fix it?",
    summary="G6 runs your repository's tests itself, checks the agent did not weaken or skip them, and holds the "
            "agent's report to what it saw.",
    fields=(
        Field("repo", "Repository folder", kind="text", placeholder="C:\\code\\my-project   or   /home/me/my-project",
              help="The full path of the git repository your coding agent changed. G6 runs its tests here, on this "
                   "computer."),
        Field("task", "What you asked the agent to do",
              placeholder="Fix the off-by-one in paginate() so the last page is not dropped."),
        Field("answer", "What the agent said it did", placeholder="Fixed the range in paginate(); all tests pass."),
        Field("tests", "Tests to run (optional)", placeholder="tests/test_page.py\ntests/test_api.py::test_list",
              help="One per line. Blank: the tests the change touches."),
        Field("base", "Compare against (optional)", kind="text", placeholder="HEAD",
              help="A git ref. Blank: HEAD, so the agent's uncommitted change. Give e.g. main to include its commits."),
    ),
    run=run,
    disclaimer="Runs your repository's own tests on this computer with your permissions (not an operating-system "
               "sandbox). Network connections are refused inside the tests. G6 reads git and does not write your "
               "files; a run that changed tracked files is reported.",
)
