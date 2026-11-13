# SPDX-License-Identifier: Apache-2.0
"""The person's decisions about suggestions, kept durably (design 17.3 "Suggestions").

A decision (adopted, dismissed, reopened) is written, with its head, before anything acknowledges it. A dismissal is
for this task by default, or for the project when the person chose that. It suppresses the intervention by its
identity alone (kind, target and scope), so it survives calls, retries, restarts, a reworded suggestion, new receipts
and a new version of the ordering rule. A dismissed intervention is offered again only when:

- the person reopened it;
- an expiry the person chose has passed;
- the task's stated outcome was superseded, when the dismissal declared that rule;
- the dismissal's record has passed the storage policy's period for decisions, which the dismissal form states.

``standing`` gives the reason with the answer, and the reason is shown. A store that cannot be read raises
``RecordUnreadable``: the caller then offers no suggestion at all, and the mandatory report is not affected.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from g6.core.ledger import LedgerError, digest
from g6.harness.workflow import policy
from g6.harness.workflow._store import Chain, RecordUnreadable
from g6.harness.workflow.record import TIME_FORMAT, EventRefused, ProjectRecord

SCHEMA = "workflow.decision.v1"
DECISIONS = ("adopted", "dismissed", "reopened")
SCOPES = ("task", "project")
REOPEN_RULES = ("never", "outcome_superseded", "record_expired")
#: Why a dismissed intervention is shown again, in fixed words.
REASONS = {
    "reopened": "you reopened it",
    "expired": "the expiry you chose for the dismissal has passed",
    "outcome_superseded": "the task's stated outcome changed since you dismissed it",
    "record_expired": "the dismissal's record passed the storage policy's period for decisions",
}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = {"schema", "project", "task", "scope", "intervention", "decision", "by", "at", "expires_at", "reopen_rule",
           "outcome_sha256", "policy_version"}


def _time(text: str) -> datetime:
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=UTC)


class Decisions:
    """One project's decisions: the project's own chain, and one chain for each task."""

    def __init__(self, project: str) -> None:
        self._store = ProjectRecord(project)
        self.project = project

    def _chain(self, task: str, scope: str) -> Chain:
        folder = self._store.root if scope == "project" else self._store._task_dir(task)
        return Chain(folder / policy.DECISIONS, lambda kind, ident, body, seen: self._rules(kind, body, task, scope),
                     head=True)

    def _rules(self, kind: str, body: dict, task: str, scope: str) -> list[str]:
        try:
            ok = (kind == "decision" and set(body) == _FIELDS and body["schema"] == SCHEMA
                  and body["project"] == self.project and body["scope"] == scope and body["decision"] in DECISIONS
                  and _HEX64.match(body["intervention"]) and _HEX64.match(body["task"])
                  and body["reopen_rule"] in REOPEN_RULES and isinstance(body["by"], str) and body["by"].strip()
                  and (scope == "project" or body["task"] == task) and _time(body["at"])
                  and (body["expires_at"] is None or _time(body["expires_at"])))
        except (KeyError, TypeError, ValueError):
            ok = False
        return [] if ok else ["an entry that is not a decision for this project and scope: refused"]

    def record(self, intervention_id: str, decision: str, *, task: str, scope: str = "task", by: str, at: str,
               expires_at: str | None = None, reopen_rule: str = "never", outcome_sha256: str = "") -> str:
        """Write one decision and keep the chain's head; the entry's id. It is on disk when this returns."""
        if decision not in DECISIONS or scope not in SCOPES or reopen_rule not in REOPEN_RULES \
                or not isinstance(intervention_id, str) or not _HEX64.match(intervention_id) \
                or not isinstance(by, str) or not by.strip() or len(by) > 80:
            raise EventRefused("a decision names an intervention, adopted, dismissed or reopened, its scope, and who made it")
        if expires_at is not None and _time(expires_at) <= _time(at):
            raise EventRefused("an expiry lies after the decision")
        body = {"schema": SCHEMA, "project": self.project, "task": task, "scope": scope, "intervention": intervention_id,
                "decision": decision, "by": by.strip(), "at": at, "expires_at": expires_at, "reopen_rule": reopen_rule,
                "outcome_sha256": outcome_sha256 if _HEX64.match(outcome_sha256 or "") else "",
                "policy_version": policy.POLICY_VERSION}
        ident = digest(["decision", intervention_id, decision, scope, task, at])
        try:
            self._chain(task, scope).append("decision", ident, body)
        except LedgerError as refused:
            raise EventRefused(str(refused)[:300]) from None
        return ident

    def held(self, *, task: str) -> list[dict]:
        """Every decision that bears on this task: the project's and the task's own, oldest first.
        ``RecordUnreadable`` when either store cannot be read."""
        rows = [entry["body"] for scope in ("project", "task") for entry in self._chain(task, scope).entries()]
        return sorted(rows, key=lambda body: body["at"])

    def standing(self, intervention_id: str, *, task: str, now: datetime, outcome_sha256: str) -> tuple[str, str]:
        """Where an intervention stands for this task: ``("suppressed", why)``, ``("reopened", why)`` or
        ``("open", "")``. The latest decision on it decides; a dismissal past the storage period no longer counts."""
        mine = [body for body in self.held(task=task) if body["intervention"] == intervention_id]
        if not mine:
            return "open", ""
        last = mine[-1]
        if last["decision"] == "reopened":
            return "reopened", REASONS["reopened"]
        if last["decision"] != "dismissed":
            return "open", ""
        age_days = (now.astimezone(UTC) - _time(last["at"])).total_seconds() / 86400
        if age_days > policy.RETENTION_DAYS["decisions"]:
            return "reopened", REASONS["record_expired"]
        if last["expires_at"] is not None and now.astimezone(UTC) >= _time(last["expires_at"]):
            return "reopened", REASONS["expired"]
        if last["reopen_rule"] == "outcome_superseded" and last["outcome_sha256"] and outcome_sha256 \
                and outcome_sha256 != last["outcome_sha256"]:
            return "reopened", REASONS["outcome_superseded"]
        return "suppressed", "you dismissed it for this " + last["scope"]


__all__ = ["DECISIONS", "Decisions", "REASONS", "REOPEN_RULES", "RecordUnreadable", "SCHEMA", "SCOPES"]
