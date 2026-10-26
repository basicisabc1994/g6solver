# SPDX-License-Identifier: Apache-2.0
"""The coding specialisation (META_HARNESS_PLAN.md Phase 7; CR's coding-agent harness, within the owner's rulings).

A coding workflow tunes how your coding agent is run on tasks in your repository. The executor is your agent: given a
task, it works in a fresh copy of the repository and returns the copy's path. What judges it:

* the standard check: the agent returned a changed working copy;
* the oracle (inside G6's own evaluator on held-out cases, and its dev runner on dev cases, the runs proposers learn
  from): ``g6.workforce.repo_tests.run`` runs the case's hidden tests on that copy, and the case passes only on the
  verdict ``passed``. With ``mutation=True`` the oracle also requires the case's tests to kill mutants of the changed
  code (``g6.workforce.mutation_check``): mutation testing therefore runs only where the oracle runs, never on live
  runs (ECHA section 10);
* an optional check a profile may add: ``coding_agent_gate.scan_diff`` finds no test shortcut in the change (a
  tighten-only signal: it can fail a run, never pass one).

The agent's own tests and claims never decide a case. The tests are not an operating-system sandbox
(``g6.workforce.repo_tests``).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from g6.harness import Check
from g6.metaharness.executor import ExecutorFactory
from g6.metaharness.schema import Model, Suite, SuiteCase, Workflow


def _workdir(out: str) -> Path | None:
    text = (out or "").strip()
    if not text or "\n" in text or "\x00" in text:
        return None
    path = Path(text)
    return path if path.is_dir() else None


WORKDIR = Check("the agent returned the path of its working copy", lambda out: _workdir(out) is not None,
                check_id="workdir", known_bad=("", "line one\nline two"))


def _no_shortcut(out: str) -> bool:
    from g6.coding_agent_gate import scan_diff
    from g6.workforce.repo_tests import change, diff_text

    repo = _workdir(out)
    return repo is not None and not scan_diff(diff_text(change(repo)))


NO_SHORTCUT = Check("the change takes no test shortcut (coding_agent_gate.scan_diff)", _no_shortcut,
                    check_id="no-shortcut")


def coding_oracle(*, mutation: bool = False, timeout_s: int = 300,
                  run: Callable[..., dict] | None = None) -> Callable[[str, Any], bool]:
    """``oracle(output, expected)`` with ``expected = {"tests": [...]}``: the hidden tests pass on the copy."""

    def oracle(output: str, expected: Any) -> bool:
        from g6.workforce import repo_tests

        repo = _workdir(output)
        tests = list((expected or {}).get("tests") or []) if isinstance(expected, Mapping) else []
        if repo is None or not tests:
            return False
        result = (run or repo_tests.run)(repo, tests, timeout_s=timeout_s)
        if result.get("verdict") != "passed":
            return False
        if mutation:
            return _mutants_killed(repo, tests, timeout_s=timeout_s, run=run or repo_tests.run)
        return True

    return oracle


def _mutants_killed(repo: Path, tests: Sequence[str], *, timeout_s: int, run: Callable[..., dict]) -> bool:
    import uuid
    from types import SimpleNamespace

    from g6.workforce import mutation_check, repo_tests

    changed = [p for p in repo_tests.change(repo).files if p.endswith(".py") and not repo_tests.is_test_file(p)]
    for rel in changed:
        def run_mutant(source: str, seconds: int, rel: str = rel) -> SimpleNamespace:
            got = run(repo, list(tests), timeout_s=seconds, mutant=(rel, source))
            ok = got.get("verdict") == "passed"
            return SimpleNamespace(execution_id=uuid.uuid4().hex, passed=int(got.get("passed", ok)),
                                   failed=0 if ok else 1, not_passed=0 if ok else 1, reason="")

        record = mutation_check.check((repo / rel).read_text(encoding="utf-8"), run_mutant,
                                      budget_s=float(timeout_s), base_duration_s=1.0)
        if record.get("survived") or record.get("stopped") or not record.get("tried"):
            return False   # a surviving mutant, a stopped check or nothing tried: the tests do not pin the change
    return True


def coding_workflow(name: str, *, dev: Sequence[SuiteCase], held_out: Sequence[SuiteCase],
                    models: Mapping[str, Model], provenance: Mapping[str, str] | None = None, mutation: bool = False,
                    oracle_run: Callable[..., dict] | None = None, **kw: Any) -> Workflow:
    """A coding workflow: each case's ``input`` is the task; its ``expected`` is ``{"tests": [hidden test ids]}``."""
    return Workflow(name=name, task="{input}", checks=(WORKDIR,), oracle=coding_oracle(mutation=mutation, run=oracle_run),
                    suite=Suite(tuple(dev), tuple(held_out), dict(provenance or {"dev": "caller", "held_out": "caller"})),
                    models=dict(models), optional_checks={"no-shortcut": NO_SHORTCUT}, **kw)


def agent_factory(agent: Callable[[str, Any], str]) -> ExecutorFactory:
    """``agent(task, llm) -> path of the changed working copy``: your coding agent, given the profile's metered model."""
    return lambda ctx: (lambda contract: agent(contract.task, ctx.caller("answer")))
