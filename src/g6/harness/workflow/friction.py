# SPDX-License-Identifier: Apache-2.0
"""G6's own friction on a task (design 17.3 "G6's own friction is counted"; 17.5's self-friction row).

Counted from the task's events, each kind apart: the questions G6 showed and the person's time in those forms; the
approvals it asked for; its logical tool calls and, separately, their repeats; provider calls; wall time, G6's active
time and waiting. Whatever was not observed is ``None`` and named in ``missing``; it is never zero, and never
estimated. There is no person-effort figure other than the time a form was open: effort is not inferred from elapsed
time. Failed and abandoned runs are counted like any other.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from g6.harness.workflow.adapters import NO_OBSERVATION
from g6.harness.workflow.record import Event

#: Kinds that carry a dialog's seconds: a question form and a feedback form.
_FORMS = ("question_shown", "feedback_shown")
#: Dialogs opened by tools that name no task (a sharing grant, a mission or workflow approval) are not attributed to
#: a task, so they are never counted here: always listed as not measured.
ALWAYS_MISSING = ("provider_retries", "dialogs_outside_task_tools")


@dataclass(frozen=True)
class Friction:
    questions: int
    question_seconds: float | None
    approvals: int
    tool_calls: int
    tool_retries: int
    provider_calls: int | None
    provider_retries: int | None
    wall_seconds: float | None
    active_g6_seconds: float | None
    wait_seconds: float | None
    inspections: int
    feedback_invitations: int
    missing: tuple[str, ...]

    def to_dict(self) -> dict:
        return {**asdict(self), "missing": list(self.missing)}


def _sum(values: list) -> float | None:
    """The sum when every value was measured (and none is negative); None when any is absent."""
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 for v in values):
        return None
    return round(float(sum(values)), 3)


def friction(events: Sequence[Event], *, missing: Sequence[str] = ()) -> Friction:
    """The counters for one task's events (``adapters.task_view``). ``missing``: what the caller already knows was
    not observed (for example a question store it could not read)."""
    absent = [*missing, *ALWAYS_MISSING]
    if not events:
        absent.append(NO_OBSERVATION)
    by = Counter(e.kind for e in events)
    forms = [e for e in events if e.kind in _FORMS]
    asked = {ref.id for e in events if e.kind in ("question_shown", "question_pending")
             for ref in e.refs if ref.kind == "question"}
    question_seconds = _sum([e.data.get("seconds") for e in forms]) if forms else 0.0
    if question_seconds is None:
        absent.append("question_seconds")

    calls = [e for e in events if e.kind == "tool_call"]
    repeats = Counter((e.data.get("tool"), e.data.get("args_sha256")) for e in calls if e.data.get("args_sha256"))
    reviews = [e for e in events if e.kind == "review_finished" and e.data.get("outcome")]
    provider = _sum([e.data.get("model_calls") for e in reviews])
    # with nothing observed at all the count is unknown, never zero
    provider_calls = None if provider is None or not events else int(provider) + by["loop_round"]
    if provider_calls is None:
        absent.append("provider_calls")

    # wall time: each run's own measured span, and only when every run that began also returned its report measured
    runs = {e.run for e in events if e.kind == "run_started" and e.data.get("origin_kind") != "tool_calls"}
    returned = {e.run: e.data.get("run_seconds") for e in events if e.kind == "report_returned"}
    wall = _sum([returned.get(run) for run in runs]) if runs else None
    if wall is None:
        absent.append("wall_seconds")
    active = _sum([e.data.get("seconds") for e in calls] + [e.data.get("review_seconds") for e in reviews]) \
        if calls or reviews else None
    if active is None:
        absent.append("active_g6_seconds")
    wait = round(wall - active, 3) if wall is not None and active is not None and wall >= active else None
    if wait is None:
        absent.append("wait_seconds")
    return Friction(questions=len(asked), question_seconds=question_seconds, approvals=by["approval_shown"],
                    tool_calls=len(calls), tool_retries=sum(n - 1 for n in repeats.values()),
                    provider_calls=provider_calls, provider_retries=None, wall_seconds=wall,
                    active_g6_seconds=active, wait_seconds=wait, inspections=by["inspection"],
                    feedback_invitations=by["feedback_invited"], missing=tuple(dict.fromkeys(absent)))


def per_checked_outcome(rows: Sequence[Friction], checked_outcomes: int) -> dict:
    """Totals over every row given (failed and abandoned tasks included), and each total per scoped checked outcome.
    With no checked outcome the rates are None: nothing is divided by zero, and the totals still stand."""
    totals: dict[str, float | int | None] = {}
    for name in ("questions", "question_seconds", "approvals", "tool_calls", "tool_retries", "provider_calls",
                 "wall_seconds", "active_g6_seconds", "inspections", "feedback_invitations"):
        values = [getattr(row, name) for row in rows]
        totals[name] = None if any(v is None for v in values) else round(sum(values), 3)
    rates = {name: (round(total / checked_outcomes, 3) if total is not None and checked_outcomes > 0 else None)
             for name, total in totals.items()}
    return {"tasks": len(rows), "checked_outcomes": checked_outcomes, "totals": totals, "per_checked_outcome": rates,
            "not_measured": sorted({name for row in rows for name in row.missing})}


__all__ = ["ALWAYS_MISSING", "Friction", "friction", "per_checked_outcome"]
