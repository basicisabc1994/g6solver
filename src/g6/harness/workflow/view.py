# SPDX-License-Identifier: Apache-2.0
"""The narrow view (design 17.3 "Report order", 17.4 step 2): after the whole mandatory report, the stated outcome,
one obstacle with its receipt or "observation is insufficient", one bounded next check, and G6's own cost.

The block is appended to a report that is already rendered and never changes a byte of it (``with_block``). It
decides nothing, and anything that goes wrong while building it leaves the report as it was. Observation, limits and
the offered check are on separate labelled lines. It names no constraint, no cause and no saving, and says that the
value to the person's work is not measured unless a measurement exists.

How much is shown is ``level()``: never above ``ADMITTED_LEVEL``, which code raises only together with the recorded
result of the gate that admits the next level (``validation/workflow_value/decisions.json``); the person may lower it
(``G6_WORKFLOW_VIEW``), an agent cannot change it, and with the workflow record off there is no view at all.

Everything in the view is the person's workflow data: ``build`` returns only a withholding notice unless the source
``workflow:<project>`` is shared with the provider that will read it.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from g6.core import cloud_consent as cloud
from g6.harness.workflow import adapters, policy
from g6.harness.workflow.friction import friction
from g6.harness.workflow.obstacle import PROVENANCE_WORDS, WORDS, cut, next_check, obstacles, stated_outcome
from g6.harness.workflow.record import workflow_source

LEVELS = ("off", "narrow", "diagnosis", "suggestions")
#: Raised only by the change that records a passed gate. G2 has not run: narrow is what G2 measures.
ADMITTED_LEVEL = "narrow"
VIEW_ENV = "G6_WORKFLOW_VIEW"
MAX_LINES = 8
HEADING = "## Outcome, obstacle and next step (optional; decides nothing above)"
NOT_SHOWN = ("- **Not shown by this:** that it is the only obstacle, that delivery improves, or that your wider "
             "workflow was observed.")
WITHHELD = ("- **Workflow view withheld:** G6's local workflow record is not shared with this provider, so nothing "
            "from it is shown. The report above is complete without it. Sharing is the person's decision "
            "(g6_cloud_grant).")
VALUE_RESULTS = ("not_measured", "observed", "self_reported", "controlled")
VALUE_SCOPE = "this task, as G6 observed it"
MISSING_WORDS = {
    adapters.NO_OBSERVATION: "G6 recorded nothing for this task",
    "questions": "the question store could not be read",
    "no_receipt_backed_obstacle": "nothing G6 can show a receipt for stands in the way of a stated check",
}


def level() -> str:
    """The level in force: off when the workflow record is off; else what the person set (``G6_WORKFLOW_VIEW``),
    when that is a level at or below the admitted one; else the admitted level."""
    if not policy.enabled()[0]:
        return "off"
    asked = os.environ.get(VIEW_ENV)
    top = LEVELS.index(ADMITTED_LEVEL)
    return asked if asked in LEVELS and LEVELS.index(asked) <= top else ADMITTED_LEVEL


def shared(project: str, provider: str | None) -> bool:
    """Whether the project's workflow source is shared with ``provider``. No provider known means not shared."""
    return bool(provider) and provider != "cloud:unknown" and cloud.is_granted(workflow_source(project), provider)


def _value_result(events) -> str:
    kinds = {e.kind for e in events}
    if "suggestion_assessed" in kinds:
        return "observed"
    return "self_reported" if "feedback_answered" in kinds else "not_measured"


def _confirmed(task: str) -> tuple[tuple, tuple[str, ...]]:
    """The expectations the person confirmed for the task (A's standard), and what could not be read."""
    from g6.harness import question_store as qs

    try:
        store = qs.TaskQuestions(task)
        return (tuple(store.state().standard().expectations) if store.exists() else ()), ()
    except Exception:  # noqa: BLE001 - a store G6 cannot read confirms nothing here; the review reports it itself
        return (), ("questions",)


def build(project: str, task: str, *, assessment: Mapping | None = None, loop: Mapping | None = None,
          intent: Mapping | None = None, provider: str | None = None, job_id: str = "", request: str = "") -> dict:
    """The ``workflow`` reply field for one task: ``{}`` when the view is off; a withholding notice when the source
    is not shared with ``provider``; else the outcome, obstacles, next check, friction and what is missing."""
    now = level()
    if now == "off":
        return {}
    if not shared(project, provider):
        return {"level": now, "withheld": True, "policy_version": policy.POLICY_VERSION}
    confirmed, unread = _confirmed(task)
    try:
        events = adapters.task_view(project, task)
    except Exception:  # noqa: BLE001 - an unreadable part of the record is missing coverage, not a failure
        events, unread = (), (*unread, "record")
    outcome = stated_outcome(intent, confirmed, task)
    found = obstacles(assessment, loop, events, job_id=job_id)
    cost = friction(events, missing=unread)
    missing = list(cost.missing)
    if not found:
        missing.append("no_receipt_backed_obstacle")
    check = next_check(found, assessment)
    deeper = {}
    if LEVELS.index(now) >= LEVELS.index("diagnosis"):      # reached only once G3's result admits it
        from g6.harness.workflow import diagnose

        found_out = diagnose.diagnose(events, assessment=assessment, loop=loop, outcome=outcome, job_id=job_id)
        deeper["diagnosis"] = found_out.to_dict()
        if LEVELS.index(now) >= LEVELS.index("suggestions"):      # reached only once G4's result admits it
            deeper.update(_suggestions(project, task, found_out, assessment, outcome, job_id))
    if now == LEVELS[-1]:      # the last admitted step: say when the person's own opt-in allows one feedback question
        deeper.update(_feedback(project, task, events))
    return {"level": now, "policy_version": policy.POLICY_VERSION, **deeper,
            "request": {"shown": cut(request), "text_sha256": cloud.sha256(request), "said": "the request, as given"}
            if request else None,
            "outcome": outcome.to_dict() if outcome else None,
            "obstacles": [one.to_dict() for one in found], "next_check": check, "friction": cost.to_dict(),
            "missing": missing, "value_result": _value_result(events), "value_scope": VALUE_SCOPE}


def _suggestions(project: str, task: str, diagnosis: Any, assessment: Mapping | None, outcome: Any, job_id: str) -> dict:
    """At most three suggestions in the published order, without what the person dismissed; each one shown is recorded
    as eligible and displayed. A decision store that cannot be read means no suggestion at all (fail closed)."""
    from datetime import UTC, datetime

    from g6.harness.workflow import suggest
    from g6.harness.workflow.decisions import Decisions, RecordUnreadable
    from g6.harness.workflow.record import ProjectRecord, now as stamp

    digest = outcome.text_sha256 if outcome else ""
    try:
        found = suggest.candidates(diagnosis, assessment)
        shown, again = suggest.ranked(found, Decisions(project), task=task, now=datetime.now(UTC), outcome_sha256=digest)
    except RecordUnreadable:
        return {"suggestions": [], "reopened": [], "suggestions_note": "the decisions G6 holds for this project cannot be "
                                                                      "read, so no suggestion is offered"}
    try:
        record, at = ProjectRecord(project), stamp()
        for one in suggest.order(found):
            suggest.record_event(record, task, "suggestion_eligible", one.intervention, at=at, provenance="rule_derived")
        for one in shown:
            suggest.record_event(record, task, "suggestion_displayed", one.intervention, at=at, native=job_id or at,
                                 extra={"outcome_sha256": digest} if digest else None)
    except Exception:  # noqa: BLE001 - a display that could not be recorded is still shown; a decision on it is refused
        pass
    return {"suggestions": [one.to_dict() for one in shown], "reopened": list(again)}


def _feedback(project: str, task: str, events) -> dict:
    """``{"feedback_question": "allowed"}`` when every rule for the optional question holds now, else nothing. The
    question itself is asked only through its own tool and the client's dialog."""
    from datetime import UTC, datetime

    from g6.harness import question_store as qs
    from g6.harness.workflow import feedback

    try:
        store = qs.TaskQuestions(task)
        questions = store.state() if store.exists() else qs.State(default=qs.default_budget()[0],
                                                                  budget_note=qs.default_budget()[1])
        ok, _why = feedback.may_invite(project, task, questions=questions, dialog=True, now=datetime.now(UTC),
                                       report_reached=any(e.kind == "report_returned" for e in events))
    except Exception:  # noqa: BLE001 - a store G6 cannot read invites nothing
        ok = False
    return {"feedback_question": "allowed"} if ok else {}


def _code(text: Any) -> str:
    from g6.harness import patch_report

    return patch_report.code(text)


def block(view: Mapping) -> list[str]:
    """The lines appended after the report: at most ``MAX_LINES``; one line when the view is withheld; none when
    there is no view."""
    if not view:
        return []
    if view.get("withheld"):
        return [WITHHELD]
    outcome, found, cost = view.get("outcome"), view.get("obstacles") or [], view.get("friction") or {}
    lines = [HEADING]
    if outcome:
        lines.append(f"- **Stated outcome ({PROVENANCE_WORDS[outcome['provenance']]}):** {_code(outcome['shown'])}")
    else:
        lines.append("- **Stated outcome:** not stated. G6 asks nothing more about it here.")
    if found:
        first = found[0]
        more = f" ({len(found) - 1} more in this reply's workflow field)" if len(found) > 1 else ""
        receipt = _code(f"{first['receipt']['kind']}:{first['receipt']['id']}")
        lines.append(f"- **Observed, with a receipt:** {WORDS[first['kind']]}; scope: {_code(first['scope'])}; "
                     f"receipt {receipt}{more}")
        lines.append(NOT_SHOWN)
    else:
        why = [MISSING_WORDS[name] for name in view.get("missing") or [] if name in MISSING_WORDS]
        lines.append("- **Observation is insufficient:** " + ("; ".join(why) or "G6 observed too little") +
                     ". No obstacle is named.")
    if view.get("diagnosis"):
        from g6.harness.workflow import diagnose

        lines += diagnose.lines(view["diagnosis"])
    check = view.get("next_check")
    if "suggestions" in view:      # the suggestions level: up to three, in place of the single next check
        from g6.harness.workflow import suggest

        lines += suggest.lines(view["suggestions"], view.get("reopened") or [], MAX_LINES - len(lines) - 1)
    else:
        lines.append(f"- **A next check you may run:** {_code(check['command'])}" if check and check.get("command")
                     else "- **A next check you may run:** none")
    unmeasured = [n for n in cost.get("missing") or [] if n in ("wall_seconds", "active_g6_seconds", "provider_calls",
                                                                "question_seconds")]
    active = cost.get("active_g6_seconds")
    said = (f"{cost.get('questions', 0)} question(s), {cost.get('approvals', 0)} approval(s), "
            f"{cost.get('tool_calls', 0)} tool call(s)" + (f", {active:g} s of G6 time" if active is not None else ""))
    value = {"not_measured": "not measured", "observed": "observed for this task only",
             "self_reported": "self-reported for this task only", "controlled": "measured in a controlled run"}[
        view.get("value_result", "not_measured")]
    lines.append(f"- **G6's own cost on this task:** {said}" + (f"; not measured: {', '.join(unmeasured)}"
                                                                if unmeasured else "") +
                 f". **Value to your work:** {value}.")
    if view.get("feedback_question") == "allowed" and len(lines) < MAX_LINES:
        lines.append("- **Feedback, as you opted in:** G6 may ask once, in your client's dialog, whether this made your work "
                     "easier (g6_feedback). Skipping is fine, and it changes nothing above.")
    return lines[:MAX_LINES]


def appended(repo: Any, task: str, report: str, *, loop: Mapping | None = None, provider: str | None) -> str:
    """`report` with this task's block after it, for a surface with no reply fields (the Stop hook); the report
    unchanged when anything goes wrong."""
    try:
        project, task_id = adapters.identities(repo, task)
        return with_block(report, build(project, task_id, loop=loop, provider=provider, request=task))
    except Exception:  # noqa: BLE001 - optional advice never costs the person the mandatory report
        return report


def with_block(report: str, view: Mapping | None) -> str:
    """``report`` followed by the block. The report itself is unchanged: the result always starts with it, and a
    view that cannot be rendered adds nothing."""
    try:
        lines = block(view or {})
    except Exception:  # noqa: BLE001 - optional advice never costs the person the mandatory report
        return report
    return f"{report}\n\n" + "\n".join(lines) if lines else report


__all__ = ["ADMITTED_LEVEL", "HEADING", "LEVELS", "MAX_LINES", "VALUE_RESULTS", "VIEW_ENV", "WITHHELD", "appended",
           "block", "build", "level", "shared", "with_block"]
