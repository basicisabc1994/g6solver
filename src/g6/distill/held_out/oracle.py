# SPDX-License-Identifier: Apache-2.0
"""Executable labels from fixed/wrong implementations and a shared mutant sample.

The existing repo_tests runner owns all execution and containment. This adapter
only stages explicit bounded inputs; it never executes Python in this process.
An oracle result is evidence about this sample, not proof of test adequacy.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Literal

from g6.core import confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.workforce import mutation_check, repo_tests
from pydantic import Field, model_validator

from .case_gate import CaseRecord, Record, SHA256

TEST_PATH = "tests/test_case.py"
EXCLUSION_RULE = "exclude unless both fixed suites pass and every wrong/mutant body settles;minimum sample required;v1"
KILL_RULE = "known-wrong:before-fails-after-passes OR after-kills strict-subset before-kills;v1"


class OraclePolicy(Record):
    min_mutants: int = Field(default=30, ge=1, le=512)
    max_mutants: int = Field(default=128, ge=1, le=512)
    per_run_s: int = Field(default=20, ge=1, le=120)
    wall_s: int = Field(default=600, ge=1, le=3600)

    @model_validator(mode="after")
    def sample_bounds(self):
        if self.min_mutants > self.max_mutants:
            raise ValueError("minimum exceeds maximum sample")
        return self


class OracleResult(Record):
    case_id: str = Field(pattern=SHA256)
    label: Literal["positive", "negative", "excluded"]
    reason: str
    before_kills: tuple[str, ...] = ()
    after_kills: tuple[str, ...] = ()
    wrong_witnesses: tuple[str, ...] = ()
    policy_sha256: str = Field(pattern=SHA256)


def _run(case: CaseRecord, implementation: str, suite: str, *, workspace: Path, timeout_s: int,
         python: str | None = None) -> str:
    if workspace.is_symlink() or workspace.is_junction():
        raise ValueError("linked staging workspace refused")
    # G6_HOME/held_out is evaluator custody, never a repository mount.
    home = os.environ.get("G6_HOME")
    if home and workspace.resolve().is_relative_to((Path(home) / "distill" / "held_out").resolve()):
        raise ValueError("sealed custody cannot be an execution workspace")
    with confine.run_folder(prefix="distill-oracle-", dir=workspace) as folder:
        staging = Path(folder)
        repo = staging / "repo"
        repo.mkdir()
        for rel, source in case.files.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(implementation if rel == case.target else source, encoding="utf-8", newline="\n")
        tests = repo / TEST_PATH
        tests.parent.mkdir(parents=True, exist_ok=True)
        tests.write_text(suite, encoding="utf-8", newline="\n")
        # No import root: the repository's top is already first on the import path, and "." is refused as one.
        raw = repo_tests._run(repo, [TEST_PATH], roots=(), timeout_s=timeout_s, network=False,
                              copied=True, python=python or sys.executable, scratch_dir=staging,
                              require_isolation=True)
        # Defence against a broken adapter: an unconfined result is never evidence.
        if not isinstance(raw.get("isolation"), dict) or not raw["isolation"].get("backend"):
            raise repo_tests.RepoRefused(ISOLATION_UNAVAILABLE, "oracle requires an operating-system boundary")
        return repo_tests.established(raw, sources=tuple(case.files)).of(TEST_PATH)


def label(case: CaseRecord, *, policy: OraclePolicy | None = None, workspace: Path,
          python: str | None = None) -> OracleResult:
    """Label a supplied case; callers receive digests and decisions, never staged code. ``python`` is the interpreter
the confined runs use (default: this one); every run still requires an operating-system boundary.

Isolation refusals propagate unchanged, without retry. Other unavailable
observations are exclusions. No operator identity is consulted for the label.
"""
    case = CaseRecord.model_validate(case.model_dump(mode="json"))
    policy = OraclePolicy.model_validate((policy or OraclePolicy()).model_dump(mode="json"))
    workspace = Path(workspace)
    if not workspace.is_dir():
        raise ValueError("an existing evaluator workspace is required")
    deadline = time.monotonic() + policy.wall_s
    before, after, witnesses = set(), set(), set()

    def answer(label_: str, reason: str) -> OracleResult:
        return OracleResult(case_id=case.case_id, label=label_, reason=reason, before_kills=tuple(sorted(before)),
                            after_kills=tuple(sorted(after)), wrong_witnesses=tuple(sorted(witnesses)),
                            policy_sha256=digest(policy.model_dump(mode="json")))

    def run(implementation: str, suite: str) -> str:
        remaining = int(deadline - time.monotonic())
        if remaining < 1:
            return "unknown"
        return _run(case, implementation, suite, workspace=workspace, timeout_s=min(remaining, policy.per_run_s),
                    python=python)

    fixed = case.files[case.target]
    if run(fixed, case.before_tests) != "passed" or run(fixed, case.after_tests) != "passed":
        return answer("excluded", "fixed_suite_did_not_pass")
    try:
        sample = mutation_check.mutants(fixed, limit=policy.max_mutants, lines=case.changed_lines, deadline=deadline)
    except mutation_check.Unfinished:
        return answer("excluded", "mutation_sample_unfinished")
    mutants = {digest(m.source): m.source for m in sample if m.source != fixed}
    if len(mutants) < policy.min_mutants:
        return answer("excluded", "insufficient_mutant_sample")
    for ident, implementation in sorted({digest(s): s for s in case.known_wrong}.items()):
        old, new = run(implementation, case.before_tests), run(implementation, case.after_tests)
        if old not in ("passed", "failed") or new not in ("passed", "failed"):
            return answer("excluded", "wrong_implementation_unsettled")
        if old == "failed" and new == "passed":
            witnesses.add(ident)
    for ident, implementation in sorted(mutants.items()):
        old, new = run(implementation, case.before_tests), run(implementation, case.after_tests)
        if old not in ("passed", "failed") or new not in ("passed", "failed"):
            return answer("excluded", "mutant_unsettled")
        if old == "failed":
            before.add(ident)
        if new == "failed":
            after.add(ident)
    if witnesses:
        return answer("positive", "known_wrong_witness")
    if after < before:
        return answer("positive", "mutation_kill_set_shrank")
    return answer("negative", "no_loss_observed_on_registered_sample")
