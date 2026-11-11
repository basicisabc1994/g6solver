# SPDX-License-Identifier: Apache-2.0
"""The optional feedback question (design 17.3 "The optional feedback question"; 17.4 step 5).

G6 takes feedback the person volunteers at any time and asks for nothing. It asks "Did this make your work easier?"
only when all of these hold: the flow is admitted (the measured steps before it have passed their gates); the person
opted in; the task has reached its report; no behaviour question is pending; the task's one question budget (A's)
has room; the client can show a typed dialog; and this project has had no invitation in the last seven days. The
budget slot and the invitation are both written before the dialog opens. A skip, a decline, a cancel or a dialog
that cannot be shown is recorded as missing and is not retried: the slot and the week stay spent.

Opting in is never asked for. It comes from ``G6_FEEDBACK_QUESTION=on`` in the server's own environment, or from a
line the person writes in ``corrections.json``; opting out from either, or from the form itself, and an opt-out
stands until the person opts in again.

An answer never confirms behaviour, never gates or reopens a task and never changes a review. Feedback and anything
derived from it is the person's data under its own consent source, ``workflow-feedback:<project-id>``, which a grant
for the repository or for the workflow record does not cover.
"""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta

from g6.core.ledger import LedgerError, digest
from g6.harness import question_store as qs
from g6.harness.workflow import policy
from g6.harness.workflow._store import Chain, RecordUnreadable
from g6.harness.workflow.record import TIME_FORMAT, EventRefused, ProjectRecord

SCHEMA = "workflow.feedback.v1"
QUESTION = "Did this make your work easier?"
OPTIONS = ("easier", "no_different", "harder", "skip")
COOLDOWN_DAYS = 7
OPT_IN_ENV = "G6_FEEDBACK_QUESTION"
MAX_TEXT = 2000
KINDS = ("opt", "unsolicited", "invited", "closed")
RESULTS = ("answered", "skipped", "declined", "cancelled", "unavailable", "invalid")
#: The view level at which the feedback flow is admitted: the last measured step before it (G4).
ADMITTED_AT = "suggestions"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class InvitationRefused(ValueError):
    """No invitation may be issued now; the message is the fixed reason."""


def feedback_source(project: str) -> str:
    if not isinstance(project, str) or not _HEX64.match(project):
        raise ValueError("a project id is the 64-hex hash project_id gives")
    return "workflow-feedback:" + project


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TIME_FORMAT)


def _time(text: str) -> datetime:
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=UTC)


class FeedbackStore:
    """One project's feedback: opt-in and opt-out, what the person volunteered, each invitation and how it closed.
    A chained file with a kept head, so a cooldown or an opt-out cannot be undone by cutting the file back."""

    def __init__(self, project: str) -> None:
        self.project = project
        self._chain = Chain(ProjectRecord(project).root / policy.FEEDBACK, self._rules, head=True)

    def _rules(self, kind: str, ident: str, body: dict, seen) -> list[str]:
        try:
            ok = kind in KINDS and body.get("schema") == SCHEMA and body.get("project") == self.project \
                and bool(_time(body["at"]))
            if ok and kind == "closed":
                ok = ("invited", body.get("invitation")) in seen and body.get("result") in RESULTS \
                    and not any(k == "closed" and one.get("invitation") == body["invitation"] for (k, _i), one in seen.items())
            if ok and kind == "opt":
                ok = body.get("choice") in ("in", "out")
        except (KeyError, TypeError, ValueError):
            ok = False
        return [] if ok else ["an entry that is not this project's feedback, or closes no open invitation: refused"]

    def entries(self) -> list[dict]:
        """Every entry, oldest first; ``RecordUnreadable`` when the store cannot be read."""
        return self._chain.entries()

    def _append(self, held, kind: str, ident: str, body: dict) -> None:
        try:
            held.append(kind, ident, {"schema": SCHEMA, "project": self.project, **body})
        except LedgerError as refused:
            raise EventRefused(str(refused)[:300]) from None

    def opted_in(self) -> tuple[bool, str]:
        """Whether the person opted in, and why not. The latest opt-out in the store stands over the environment."""
        latest = next((e["body"]["choice"] for e in reversed(self.entries()) if e["kind"] == "opt"), "")
        env = os.environ.get(OPT_IN_ENV)
        if latest == "out":
            return False, "the person opted out of the feedback question"
        if env == "off" or env not in (None, "on"):
            return False, f"{OPT_IN_ENV} is not on where the server is registered"
        if env == "on" or latest == "in":
            return True, ""
        return False, "the person has not opted in to the feedback question"

    def opt(self, choice: str, *, by: str, at: str, via: str) -> None:
        if choice not in ("in", "out") or not isinstance(by, str) or not by.strip():
            raise EventRefused("an opt-in or opt-out names its choice and who made it")
        with self._chain.open() as held:
            self._append(held, "opt", digest(["opt", choice, at, len(held.entries())]),
                         {"choice": choice, "by": by.strip()[:80], "via": str(via)[:40], "at": at})

    def unsolicited(self, task: str, text: str, *, relayed_by: str, at: str) -> str:
        """Keep feedback the person volunteered, as relayed (an attributed label, not authenticated). It asks
        nothing and spends no budget."""
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or not _HEX64.match(str(task)):
            raise EventRefused(f"feedback is some text of at most {MAX_TEXT} characters, for a task")
        with self._chain.open() as held:
            ident = digest(["unsolicited", task, text, at, len(held.entries())])
            self._append(held, "unsolicited", ident, {"task": task, "text": text, "relayed_by": str(relayed_by)[:80],
                                                       "provenance": "attributed_label", "at": at})
        return ident

    def last_invitation(self) -> datetime | None:
        times = [_time(e["body"]["at"]) for e in self.entries() if e["kind"] == "invited"]
        return max(times) if times else None

    def reserve_invitation(self, task: str, *, now: datetime) -> str:
        """Write this project's one invitation for the week, under the store's lock: of two callers one wins.
        ``InvitationRefused`` inside the cooldown."""
        with self._chain.open() as held:
            times = [_time(e["body"]["at"]) for e in held.entries() if e["kind"] == "invited"]
            if times and now.astimezone(UTC) - max(times) < timedelta(days=COOLDOWN_DAYS):
                raise InvitationRefused(f"this project had a feedback invitation in the last {COOLDOWN_DAYS} days")
            ident = digest(["invited", task, _stamp(now), len(held.entries())])
            self._append(held, "invited", ident, {"task": task, "at": _stamp(now)})
        return ident

    def close(self, invitation: str, result: str, *, option: str = "", shown_at: str, back_at: str,
              seconds: float) -> None:
        """How an invitation ended. Once: a closed invitation is never closed, or shown, again."""
        if result not in RESULTS or (option in OPTIONS[:3]) != (result == "answered"):
            raise EventRefused("an invitation closes as answered with an option, or as missing with none")
        with self._chain.open() as held:
            if held.has("closed", digest(["closed", invitation])):
                raise EventRefused("an invitation closes once")
            self._append(held, "closed", digest(["closed", invitation]), {
                "invitation": invitation, "result": result, "option": option if result == "answered" else "",
                "shown_at": shown_at, "back_at": back_at, "seconds": round(float(seconds), 3), "at": back_at})


def may_invite(project: str, task: str, *, questions: qs.State, report_reached: bool, dialog: bool,
               now: datetime) -> tuple[bool, str]:
    """Whether G6 may ask the feedback question on this task now, and the fixed reason when not. Nothing is written.
    A feedback store that cannot be read invites nothing."""
    from g6.harness.workflow import view

    if view.LEVELS.index(view.level()) < view.LEVELS.index(ADMITTED_AT):
        return False, "the feedback question is not switched on: the measured steps before it have not passed"
    try:
        store = FeedbackStore(project)
        opted, why = store.opted_in()
        last = store.last_invitation()
    except RecordUnreadable:
        return False, "the feedback G6 holds for this project cannot be read, so it asks nothing"
    if not opted:
        return False, why
    if not report_reached:
        return False, "the task has not reached its report"
    if questions.pending is not None:
        return False, "a behaviour question is still pending for this task"
    if questions.left < 1:
        return False, questions.budget_note or "the task's question budget has no room"
    if not dialog:
        return False, "the client cannot show a typed dialog, and the question is never put to the agent"
    if last is not None and now.astimezone(UTC) - last < timedelta(days=COOLDOWN_DAYS):
        return False, f"this project had a feedback invitation in the last {COOLDOWN_DAYS} days"
    return True, ""


__all__ = ["ADMITTED_AT", "COOLDOWN_DAYS", "FeedbackStore", "InvitationRefused", "KINDS", "MAX_TEXT", "OPTIONS",
           "OPT_IN_ENV", "QUESTION", "RESULTS", "SCHEMA", "feedback_source", "may_invite"]
