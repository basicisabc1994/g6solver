# SPDX-License-Identifier: Apache-2.0
"""``regression_delta``: did a change break, drop or skip a test that ran before it? (audit 024 §11)

Ported from the archive's ``self_improve/verifier`` (the failing-line parser and the set difference of
``_check_no_new_test_failures``); the subprocess, git stash and advisory-review parts stay in the archive. The caller's
executor runs the suite and supplies each run as a :class:`RunSummary`. Three answers, never a reliability label:

* ``passed``: no new failure, no test lost and none newly skipped, against an evaluable baseline;
* ``failed``: a new failure, a lost test, or a newly skipped one (audit 024 REL-003: a change that skips every test
  must not read as green); with no baseline, any failure counts, since an old one cannot be told from a new one;
* ``unevaluable``: a run that did not complete, a run that collected nothing, or no baseline to compare a clean run
  with. Never read as "no regression".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from g6.self_training.patch_promotion_gate import PatchEvalReport

_FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)")
# Forced colour (FORCE_COLOR, PY_COLORS, some CI runners) prefixes the summary line with an escape sequence.
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

RegressionState = Literal["passed", "failed", "unevaluable"]
EVALUATOR_ID = "g6.self_training.regression_delta"


def failing_node_ids(pytest_output: str) -> frozenset[str]:
    """The node ids pytest's summary reports ``FAILED`` or ``ERROR`` (ANSI colour stripped first)."""
    found = set()
    for line in pytest_output.splitlines():
        match = _FAILURE_LINE.match(_ANSI_ESCAPE.sub("", line).strip())
        if match:
            found.add(match.group(1))
    return frozenset(found)


@dataclass(frozen=True)
class RunSummary:
    """One run of the suite, as the caller's executor parsed it: node ids that failed, were collected, were skipped;
    ``evaluable`` is False when the run gave no verdict (timeout, no interpreter, a collection abort)."""

    failing: frozenset[str] = frozenset()
    collected: frozenset[str] = frozenset()
    skipped: frozenset[str] = frozenset()
    evaluable: bool = True
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegressionVerdict:
    state: RegressionState
    new_failures: frozenset[str] = frozenset()
    lost: frozenset[str] = frozenset()
    newly_skipped: frozenset[str] = frozenset()
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_eval_report(self) -> PatchEvalReport:
        """The regression facts for :func:`govern_patch_promotion`. ``independent_eval`` is False (the change's own
        suite is no independent evaluator), so the gate holds the result at ``qualified-draft`` at best; fields this
        check does not measure keep their unknown defaults."""
        return PatchEvalReport(regression_pass_rate=1.0 if self.state == "passed" else 0.0,
                               independent_eval=False, evaluator_id=EVALUATOR_ID)


def regression_delta(change: RunSummary, baseline: RunSummary | None) -> RegressionVerdict:
    """The verdict on ``change`` against ``baseline`` (the same suite without the change)."""
    if not change.evaluable:
        return RegressionVerdict("unevaluable", reasons=("the run with the change gave no verdict",))
    if not change.collected:
        return RegressionVerdict("unevaluable", reasons=("the run with the change collected no test",))
    if baseline is None or not baseline.evaluable:
        if change.failing:
            return RegressionVerdict("failed", new_failures=change.failing,
                                     reasons=("tests fail and there is no baseline to tell old failures from new",))
        return RegressionVerdict("unevaluable", reasons=("no evaluable baseline to compare the change with",))
    new = change.failing - baseline.failing
    lost = baseline.collected - change.collected
    skipped = (change.skipped - baseline.skipped) & baseline.collected
    reasons = ([f"{len(new)} test(s) fail only with the change"] if new else []) + (
        [f"{len(lost)} test(s) that ran before are no longer collected"] if lost else []) + (
        [f"{len(skipped)} test(s) that ran before are now skipped"] if skipped else [])
    if reasons:
        return RegressionVerdict("failed", new, lost, skipped, tuple(reasons))
    return RegressionVerdict("passed", reasons=("no new failure, no test lost, none newly skipped",))


__all__ = ["EVALUATOR_ID", "RegressionVerdict", "RunSummary", "failing_node_ids", "regression_delta"]
