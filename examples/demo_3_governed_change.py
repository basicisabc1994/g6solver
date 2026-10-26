# SPDX-License-Identifier: Apache-2.0
"""Demo 3 — governed harness modification.

A small project's reply template (the harness's "prompt" surface) makes a task fail: its replies never state the
refund window. The script then works through the change the way G6 governs one:

1. The failing task: ``g6.harness`` blocks it on the check "states the refund window".
2. The diagnosis: the failed check and the output that failed it.
3. A proposed change to the template, made in its own git worktree so the project is untouched.
4. Verification in that worktree: the project's tests and a held-out suite (written separately; here it stands in
   for a reviewer the proposer never sees) are run before and after the change (``regression_delta``); the change's
   paths are checked (``classify_change_scope``); the failing task is re-run against the changed template.
5. The decision: ``govern_patch_promotion``, bound to the exact diff (``patch_digest``). This project opts in to
   promoting low-risk changes without a person (``autonomous_low_risk=True``); without that opt-in every change waits
   for one.
6. A promoted change is merged and a later task uses it; a refused one is rolled back (the worktree and branch are
   removed) and the record of why is kept.

It runs a bad change first (it drops the greeting the held-out suite requires), then a good one. Needs git.

    python examples/demo_3_governed_change.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = {
    "src/replies/__init__.py": ('from pathlib import Path\n\nTEMPLATE = Path(__file__).with_name("reply.txt")\n\n\n'
                                'def reply(name, refund_days):\n'
                                '    return TEMPLATE.read_text(encoding="utf-8").format(name=name, '
                                'refund_days=refund_days).strip()\n'),
    "src/replies/reply.txt": "Hello {name},\nThanks for asking about refunds. You can also take store credit.\n",
    "tests/replies/test_reply.py": ("from replies import reply\n\n\ndef test_names_the_customer():\n"
                                    "    assert reply('Ana', 14).startswith('Hello Ana')\n\n\n"
                                    "def test_mentions_store_credit():\n    assert 'store credit' in reply('Ana', 14)\n"),
}
HELD_OUT = ("from replies import reply\n\n\ndef test_greets_by_name_on_its_own_line():\n"
            "    assert reply('Bo', 30).splitlines()[0] == 'Hello Bo,'\n\n\n"
            "def test_leaves_no_placeholder_unfilled():\n    assert '{' not in reply('Bo', 30)\n")
BAD = "Refunds are issued within {refund_days} days. You can also take store credit.\n"
GOOD = ("Hello {name},\nRefunds are issued within {refund_days} days of a return. You can also take store credit.\n")
GOAL = "Reply to a customer asking how refunds work"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=G6 demo", "-c", "user.email=demo@example.invalid", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout


def _render(repo: Path, name: str = "Ana", days: int = 14) -> str:
    code = f"import sys; sys.path.insert(0, 'src'); from replies import reply; print(reply({name!r}, {days}))"
    return subprocess.run([sys.executable, "-c", code], cwd=repo, check=True, capture_output=True, text=True).stdout


def _suite(repo: Path, held_out: Path):
    """One run of the project's tests and the held-out suite, as a ``RunSummary``."""
    from g6.self_training import RunSummary

    env = dict(os.environ, PYTHONPATH=str(repo / "src"))
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rA", "tests", str(held_out)],
                          cwd=repo, env=env, capture_output=True, text=True)
    rows = [line.split()[:2] for line in done.stdout.splitlines() if line.startswith(("PASSED ", "FAILED ", "ERROR "))]
    name = lambda node: node.replace("\\", "/").rsplit("/", 1)[-1]  # noqa: E731 - same name before and after
    return RunSummary(failing=frozenset(name(n) for s, n in rows if s != "PASSED"),
                      collected=frozenset(name(n) for _, n in rows), evaluable=bool(rows))


def _task(repo: Path, memory, name: str = "Ana") -> dict:
    from g6.harness import Check, Standard, run

    standard = Standard(GOAL, (Check("states the refund window", lambda out: " days" in out),
                               Check("mentions store credit", lambda out: "store credit" in out)))
    return run(standard, lambda contract: _render(repo, name), memory=memory)


def _propose(label: str, template: str, repo: Path, work: Path, held_out: Path, log: Path) -> bool:
    from g6.harness import Memory
    from g6.self_training import (CANARY, PROMOTE_AUTO, ChangeScope, HarnessPatch, PatchEvalReport, ScopeVerdict,
                                  classify_change_scope, decision_matches, govern_patch_promotion, regression_delta)

    tree = work / f"wt-{label}"
    _git("worktree", "add", "-q", "-b", f"proposal-{label}", str(tree), cwd=repo)
    before = _suite(tree, held_out)
    (tree / "src/replies/reply.txt").write_text(template, encoding="utf-8")
    diff, changed = _git("diff", cwd=tree), tuple(_git("diff", "--name-only", cwd=tree).split())
    after = _suite(tree, held_out)
    delta = regression_delta(after, before)
    scope = classify_change_scope(ChangeScope("replies", changed), roots=("src/{component}/", "tests/{component}/"))
    scratch = Memory(":memory:")
    fixed = _task(tree, scratch)["results"]["states-the-refund-window"] == "pass"
    scratch.close()
    print(f"  diff touches:   {list(changed)}  (scope: {scope.verdict.value})")
    print(f"  suite before:   {len(before.collected) - len(before.failing)}/{len(before.collected)} passed;"
          f" after: {len(after.collected) - len(after.failing)}/{len(after.collected)} passed")
    print(f"  regression:     {delta.state}" + (f" ({'; '.join(delta.reasons)})" if delta.state != "passed" else ""))
    print(f"  failing task:   {'fixed' if fixed else 'still fails'} with the change")
    patch = HarnessPatch(patch_id=label, agent_id="proposer", target_surfaces=["prompt"], touched_paths=list(changed),
                         diff=diff)
    report = PatchEvalReport(targeted_failure_fixed=fixed, regression_pass_rate=1.0 if delta.state == "passed" else 0.0,
                             target_metric_improvement=1.0 if fixed else 0.0, evaluator_confidence=0.9,
                             independent_eval=True, evaluator_id="held-out-suite")
    decision = govern_patch_promotion(patch, report, autonomous_low_risk=True)  # the project's opt-in (step 5)
    promoted = (decision.decision in (PROMOTE_AUTO, CANARY) and scope.verdict == ScopeVerdict.ALLOW
                and decision_matches(decision, patch))
    print(f"  decision:       {decision.decision} ({decision.risk_tier}), label {decision.reliability_label}")
    print(f"  because:        {decision.reasons[0]}")
    record = {"proposal": label, "decision": decision.to_dict(), "regression": delta.state,
              "regression_reasons": list(delta.reasons), "scope": scope.verdict.value, "targeted_failure_fixed": fixed}
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    if promoted:
        _git("commit", "-qam", f"proposal {label}: {decision.decision}", cwd=tree)
        _git("merge", "-q", "--ff-only", f"proposal-{label}", cwd=repo)
        print("  outcome:        merged into the project")
    else:
        print("  outcome:        rolled back (worktree and branch removed); the record of why is kept")
    _git("worktree", "remove", "--force", str(tree), cwd=repo)
    if not promoted:
        _git("branch", "-D", f"proposal-{label}", cwd=repo)
    return promoted


def main() -> int:
    if shutil.which("git") is None:
        print("This demo needs git on PATH.")
        return 2
    work = Path(tempfile.mkdtemp(prefix="g6_demo3_"))
    os.environ["G6_HOME"] = str(work / "home")
    from g6.harness import Memory

    memory = Memory()
    try:
        repo, held_out, log = work / "project", work / "held_out" / "test_held_out.py", work / "promotion_log.jsonl"
        for rel, text in PROJECT.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8")
        held_out.parent.mkdir(parents=True)
        held_out.write_text(HELD_OUT, encoding="utf-8")
        _git("init", "-q", cwd=repo)
        _git("add", ".", cwd=repo)
        _git("commit", "-qm", "the project", cwd=repo)

        print("1. The failing task")
        first = _task(repo, memory)
        print(f"  label: {first['completion_state']}; next action: {first['next_action']}")
        print("2. Diagnosis")
        print(f"  failed checks: {[k for k, v in first['results'].items() if v == 'fail']}")
        print(f"  the output:    {first['output'].strip()!r}")
        print("3-6. A bad change (it drops the greeting)")
        _propose("drop-greeting", BAD, repo, work, held_out, log)
        print("3-6. A good change (it adds the refund window)")
        _propose("add-refund-window", GOOD, repo, work, held_out, log)
        print("Later task, another customer, on the project as it now is")
        later = _task(repo, memory, name="Cy")
        print(f"  label: {later['completion_state']}; output: {later['output'].strip()!r}")
        print(f"Promotion log: {sum(1 for _ in log.open(encoding='utf-8'))} record(s), one per proposal")
    finally:
        memory.close()
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
