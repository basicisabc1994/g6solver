# SPDX-License-Identifier: Apache-2.0
"""Held-out admission: may a distilled candidate be trusted on cases it never saw? (audit 025 §6, §11, HD-004).

``evaluate(candidate, holdout, negative_control=...)`` runs the frozen candidate on evaluator-owned held-out cases
out of process (``g6.distillation.sandbox``); the candidate never sees an expected value. It refuses, before
running anything, a held-out case whose id is among the candidate's training cases (a training witness is not a
holdout, ECHA-18), an empty holdout and a source the pre-filter refuses. It then requires controls:

- the **negative control**, a candidate known to be wrong, must return a wrong answer on at least one held-out case.
  An error, a timeout or a refusal is not a wrong answer: a control that answered no case (no ``solve``, refused by
  the pre-filter, raising or timing out on every case) shows nothing, and the report is ``refused`` with
  ``G6_E_DISTILLATION_NEGATIVE_CONTROL_DID_NOT_RUN`` before the candidate runs. One that answered but never wrongly
  means the cases cannot tell a wrong answer from a right one, and the report is ``inconclusive``;
- an optional **positive control**, known to be right, must pass every case. If it fails one, the cases or the
  expected values are wrong, and the report is ``inconclusive``.

With a ``baseline`` (the same task done without the candidate: a callable from input to output, run in this
process), the report adds a paired comparison on the same cases: wins, losses and a one-sided exact sign test.

The report is evidence for a decision, not the decision. Its best state is ``qualified-draft``, because the held-out
cases are the caller's. Serving or promoting a candidate is the caller's governance (for example
``g6.self_training.govern_patch_promotion``), never this module's.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from g6.distillation.sandbox import check_source, run_batch

#: The negative control answered no held-out case, so it cannot show the cases tell wrong from right.
NEGATIVE_CONTROL_DID_NOT_RUN = "G6_E_DISTILLATION_NEGATIVE_CONTROL_DID_NOT_RUN"


@dataclass(frozen=True)
class Candidate:
    """A distilled function, frozen: ``source`` defines ``solve(x)``. ``training_case_ids`` are the cases it was
    made from; none of them may be held out."""

    source: str
    origin_episode_ids: tuple[str, ...] = ()
    training_case_ids: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HoldoutCase:
    """An evaluator-owned case: the candidate receives ``input`` and never ``expected``."""

    case_id: str
    input: Any
    expected: Any


@dataclass(frozen=True)
class HoldoutReport:
    """What the held-out run found. ``verdict``: ``passed`` (every case passed and the controls behaved), ``failed``
    (a case failed and the controls behaved), ``inconclusive`` (a control misbehaved) or ``refused`` (the candidate
    was not run). ``negative_control_failed``: the cases the negative control answered wrongly. ``code``: a ``G6_E_``
    code when the report names one."""

    verdict: str
    candidate_digest: str
    reason: str = ""
    passed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    outcomes: dict[str, dict] = field(default_factory=dict)
    negative_control_failed: tuple[str, ...] = ()
    positive_control_failed: tuple[str, ...] = ()
    baseline: dict[str, Any] | None = None
    code: str = ""

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.verdict == "passed" else "blocked-escalated"

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "completion_state": self.completion_state, "reason": self.reason,
                "candidate_digest": self.candidate_digest, "passed": list(self.passed), "failed": list(self.failed),
                "outcomes": dict(self.outcomes), "negative_control_failed": list(self.negative_control_failed),
                "positive_control_failed": list(self.positive_control_failed), "baseline": self.baseline,
                "code": self.code}


def same(got: Any, expected: Any) -> bool:
    """JSON equality, with floats equal to 1e-9 (relative or absolute)."""
    if isinstance(got, bool) or isinstance(expected, bool):
        return got is expected
    if isinstance(got, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(got, expected, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(got, list) and isinstance(expected, list):
        return len(got) == len(expected) and all(same(a, b) for a, b in zip(got, expected))
    if isinstance(got, dict) and isinstance(expected, dict):
        return got.keys() == expected.keys() and all(same(got[k], expected[k]) for k in got)
    return got == expected


def _sign_test(wins: int, losses: int) -> float:
    n = wins + losses
    return 1.0 if n == 0 else sum(math.comb(n, k) for k in range(wins, n + 1)) / 2 ** n


def _failures(candidate: Candidate, cases: Sequence[HoldoutCase], **limits: Any) -> tuple[list[str], dict]:
    runs = run_batch(candidate.source, [c.input for c in cases], **limits)
    outcomes = {c.case_id: {"status": r.status, "pass": r.ok and same(r.value, c.expected), "reason": r.reason}
                for c, r in zip(cases, runs)}
    return [cid for cid, o in outcomes.items() if not o["pass"]], outcomes


def evaluate(candidate: Candidate, holdout: Sequence[HoldoutCase], *, negative_control: Candidate,
             positive_control: Candidate | None = None, baseline: Callable[[Any], Any] | None = None,
             timeout_s: float = 10.0, memory_mb: int = 256) -> HoldoutReport:
    """Run ``candidate`` on ``holdout`` with the required controls; see the module docstring."""
    cases = list(holdout)
    digest = candidate.digest
    leaked = sorted({c.case_id for c in cases} & set(candidate.training_case_ids))
    if not cases:
        return HoldoutReport("refused", digest, "no held-out case was given")
    if len({c.case_id for c in cases}) != len(cases):
        return HoldoutReport("refused", digest, "two held-out cases share an id")
    if leaked:
        return HoldoutReport("refused", digest, "these held-out cases are among the candidate's training cases, so "
                             "they are not held out: " + ", ".join(leaked[:5]))
    problems = check_source(candidate.source)
    if problems:
        return HoldoutReport("refused", digest, "the candidate's source is refused: " + problems[0])
    limits = {"timeout_s": timeout_s, "memory_mb": memory_mb}
    # Only a wrong answer the negative control returned counts as its failure: an error, a timeout or a refusal
    # fails a case without showing that the cases can tell a wrong candidate from a right one.
    _, control = _failures(negative_control, cases, **limits)
    if not any(o["status"] == "ok" for o in control.values()):
        why = next((o["reason"] for o in control.values() if o["reason"]), "no outcome")
        return HoldoutReport("refused", digest, "the negative control answered no held-out case, so it cannot show "
                             f"the cases tell a wrong candidate from a right one: {why}",
                             code=NEGATIVE_CONTROL_DID_NOT_RUN)
    negative = [cid for cid, o in control.items() if o["status"] == "ok" and not o["pass"]]
    failed, outcomes = _failures(candidate, cases, **limits)
    positive = _failures(positive_control, cases, **limits)[0] if positive_control is not None else []
    passed = tuple(c.case_id for c in cases if c.case_id not in failed)
    paired = None
    if baseline is not None:
        base_pass = {}
        for c in cases:
            try:
                base_pass[c.case_id] = same(baseline(c.input), c.expected)
            except Exception:  # noqa: BLE001 - a baseline that raises did not pass the case
                base_pass[c.case_id] = False
        wins = sum(c.case_id in passed and not base_pass[c.case_id] for c in cases)
        losses = sum(c.case_id not in passed and base_pass[c.case_id] for c in cases)
        paired = {"baseline_passed": sum(base_pass.values()), "wins": wins, "losses": losses,
                  "p_one_sided": round(_sign_test(wins, losses), 6)}
    if not negative:
        verdict, reason = "inconclusive", ("the negative control answered no held-out case wrongly, so the cases "
                                           "cannot tell a wrong candidate from a right one")
    elif positive:
        verdict, reason = "inconclusive", ("the positive control failed " + ", ".join(positive[:5])
                                           + ", so the cases or their expected values are wrong")
    elif failed:
        verdict, reason = "failed", f"{len(failed)} of {len(cases)} held-out case(s) failed"
    else:
        verdict, reason = "passed", f"all {len(cases)} held-out case(s) passed, and the controls behaved"
    return HoldoutReport(verdict, digest, reason, passed, tuple(failed), outcomes, tuple(negative), tuple(positive),
                         paired)


__all__ = ["NEGATIVE_CONTROL_DID_NOT_RUN", "Candidate", "HoldoutCase", "HoldoutReport", "evaluate", "same"]
