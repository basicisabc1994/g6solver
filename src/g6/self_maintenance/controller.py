# SPDX-License-Identifier: Apache-2.0
"""G6-on-G6 maintenance controller: person-approved, bounded agent runs in an isolated copy.

No unattended publish/merge, no self-approval and no evaluator edits. The host
must provide a version-controlled disposable workspace, independent check runner,
and trusted test identities. The controller never creates GitHub credentials.

A cycle runs an agent or a checker only with a person's approval: a ``run_approval``
item in the operator's queue (``ActionQueue.request_run``), decided by someone other
than the requester and bound to exactly this ticket, provider and day
(``cycle_binding``). The live agent path (``run_existing_agent``) refuses in this
release, before any runtime is built: its body is kept, unreachable, for the mission
container of a later phase.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from g6.core.ledger import digest
from g6.operator.queue import APPROVAL_MISMATCH, ActionQueue, QueueError
from g6.self_maintenance.codes import LIVE_AGENT_OFF, SelfMaintenanceRefused
from g6.self_maintenance.constraints import StageMeasurement, focus_plan
from g6.self_maintenance.kernel import BudgetBook, BudgetPolicy, MaintenanceTicket, allowed_source


@dataclass(frozen=True)
class CycleReport:
    state: str
    ticket: str
    provider: str
    reason: str
    reserved_tokens: int
    observed_tokens: int | None
    observed_usd: float | None
    evidence: tuple[str, ...]
    next_action: str
    audit_head: str
    approved_by: str = ""


def cycle_binding(ticket: MaintenanceTicket, provider: str, day: str) -> str:
    """What a person's approval of one cycle binds: the ticket as planned, the provider and the day."""
    return digest({"ticket": asdict(ticket), "provider": provider, "day": day})


def run_one_cycle(
    tickets: tuple[MaintenanceTicket, ...],
    stages: tuple[StageMeasurement, ...],
    ticket_stages: dict[str, str],
    *,
    book: BudgetBook,
    policy: BudgetPolicy,
    day: str,
    workspace: str,
    agent_runner: Callable[..., object],
    independent_checker: Callable[[Path, MaintenanceTicket], dict],
    queue: ActionQueue | None = None,
    venture_id: str = "",
    approval: str = "",
    preferred_provider: str = "codex",
) -> CycleReport:
    """One approved planning/repair/check cycle, not an unsupervised daemon.

    Agents have workspace write permission on a disposable copy ONLY. They do
    not see protected tests, production credentials or the evaluator's source.
    Runner and verifier are injected by a separate host and never chosen by
    the proposing agent. With no approval (``approval``, the queue item's id in
    ``venture_id``) the cycle is a simulation; an approval given for another
    ticket, provider or day is ``approval_mismatch``; neither runs anything.
    """
    plan = focus_plan(tickets, stages, ticket_stages, book=book, policy=policy,
                      day=day, provider=preferred_provider)
    if plan.selected is None or plan.provider is None:
        return CycleReport("blocked-escalated", "", "", plan.rationale, 0, None, None,
                           (), "measure limiting stage and correct constraints", book.ledger.head())
    ticket = next(t for t in tickets if t.id == plan.selected)
    if queue is None or not approval:
        return CycleReport("qualified-draft", ticket.id, plan.provider, "simulation_only", 0, None, None,
                           (), "a person must approve a bounded disposable-copy run", book.ledger.head())
    try:
        approved_by = queue.run_approver(venture_id, approval, binding=cycle_binding(ticket, plan.provider, day))
    except (QueueError, LookupError) as exc:
        if str(exc).startswith(f"[{APPROVAL_MISMATCH}]"):
            return CycleReport("blocked-escalated", ticket.id, plan.provider, "approval_mismatch", 0, None, None,
                               (), "the approval was given for another ticket, provider or day", book.ledger.head())
        return CycleReport("qualified-draft", ticket.id, plan.provider, "simulation_only", 0, None, None,
                           (), "a person must approve a bounded disposable-copy run", book.ledger.head())
    path = Path(workspace)
    if not path.is_absolute() or not path.is_dir() or not (path / ".git").exists():
        return CycleReport("blocked-escalated", ticket.id, plan.provider, "invalid_disposable_workspace",
                           0, None, None, (), "provide a separately versioned disposable checkout", book.ledger.head(),
                           approved_by)
    if not all(allowed_source(f) for f in ticket.files):
        return CycleReport("blocked-escalated", ticket.id, plan.provider, "protected_scope",
                           0, None, None, (), "change must be routed for independent human review", book.ledger.head(),
                           approved_by)
    reservation = book.reserve(ticket, plan.provider, policy, day)
    observed_tokens, observed_usd = None, None
    claim = "not_started"
    try:
        try:
            agent = agent_runner(
                provider=plan.provider,
                workspace=str(path),
                goal=ticket.title + "\nReproduce: " + ticket.evidence,
                allowed_paths=ticket.files,
                deadline_s=min(policy.max_cycle_seconds, 900),
                dry_run=False,
            )
        except SelfMaintenanceRefused as refused:
            claim = refused.code
            reason = "live_agent_off" if refused.code == LIVE_AGENT_OFF else "agent_failed_or_refused"
            return CycleReport("blocked-escalated", ticket.id, plan.provider, reason, reservation.reserved_tokens,
                               None, None, (), "no live agent runs in this release; run the mission container",
                               book.ledger.head(), approved_by)
        claim = str(getattr(agent, "state", "unknown"))
        usage = getattr(agent, "usage", {})
        if isinstance(usage, dict):
            observed_tokens = usage.get("tokens") if type(usage.get("tokens")) is int else None
            observed_usd = (usage.get("cost_usd") if type(usage.get("cost_usd")) in (int, float) else None)
        if claim != "qualified-draft":
            return CycleReport("blocked-escalated", ticket.id, plan.provider, "agent_failed_or_refused",
                               reservation.reserved_tokens, observed_tokens, observed_usd, (),
                               "inspect refusal; do not retry without a fresh budget decision", book.ledger.head(),
                               approved_by)
        # The checker is host-owned; no agent claims can replace it.
        checked = independent_checker(path, ticket)
        changed = checked.get("changed_files", ())
        if not isinstance(changed, (list, tuple)) or not all(
            isinstance(p, str) and p in ticket.files and allowed_source(p) for p in changed
        ) or not changed:
            return CycleReport("blocked-escalated", ticket.id, plan.provider, "unsafe_or_missing_changed_scope",
                               reservation.reserved_tokens, observed_tokens, observed_usd, (),
                               "discard candidate and inspect changed paths", book.ledger.head(), approved_by)
        if checked.get("test_integrity") != "intact" or checked.get("verdict") != "passed":
            return CycleReport("blocked-escalated", ticket.id, plan.provider, "independent_checks_not_passed",
                               reservation.reserved_tokens, observed_tokens, observed_usd, (),
                               "discard or send candidate to diagnosis", book.ledger.head(), approved_by)
        # A run may be successful within frozen check scope but never self-approve.
        receipts = tuple(str(x) for x in checked.get("receipt_ids", ()) if isinstance(x, str))[:30]
        if not receipts:
            return CycleReport("blocked-escalated", ticket.id, plan.provider, "missing_independent_receipts",
                               reservation.reserved_tokens, observed_tokens, observed_usd, (),
                               "obtain a host-authored verifier receipt", book.ledger.head(), approved_by)
        return CycleReport("qualified-draft", ticket.id, plan.provider, "candidate_checked_in_scope",
                           reservation.reserved_tokens, observed_tokens, observed_usd, receipts,
                           "open a draft PR for independent human review; never merge automatically",
                           book.ledger.head(), approved_by)
    finally:
        # A provider's self-reported usage is not independent metering.
        book.settle(reservation, day, observed_tokens=observed_tokens,
                    observed_usd=observed_usd, receipt_trusted=False, outcome=claim)


def run_existing_agent(*, provider: str, workspace: str, goal: str,
                       allowed_paths: tuple[str, ...], deadline_s: int, dry_run: bool) -> object:
    """Refuses, in this release, before any runtime is built or any process starts: the live agents run with writes
    and the network on, which waits for the mission container (P3). ``_run_existing_agent_live`` keeps the path."""
    raise SelfMaintenanceRefused(LIVE_AGENT_OFF, "no live Codex or Claude Code agent runs in this release; the "
                                                 "mission container of a later phase brings them")


def _run_existing_agent_live(*, provider: str, workspace: str, goal: str,
                             allowed_paths: tuple[str, ...], deadline_s: int, dry_run: bool) -> object:
    """Adapt existing Codex/Claude runtimes. Tool-level path constraints still require validation. Unreachable in P1.

    Caller must independently isolate the disposable copy and enforce the changed
    path allowlist afterwards. These CLIs can read files outside the workspace
    within their own sandbox policies; use no secrets in the host account.
    """
    from g6.agents._runtime import run_agent
    if provider == "codex":
        from g6.agents.agent_codex import CodexRuntime
        runtime = CodexRuntime()
    elif provider == "claude":
        from g6.agents.agent_claude import ClaudeCodeRuntime
        runtime = ClaudeCodeRuntime()
    else:
        raise ValueError("unsupported provider")
    task = runtime.task(goal, workspace, writes=True, deadline_s=deadline_s,
                        context=("Allowed changes: " + ", ".join(allowed_paths) +
                                 ". Do not alter tests, security gates, judging logic or releases."))
    return run_agent(runtime, task, dry_run=dry_run)


__all__ = ["CycleReport", "cycle_binding", "run_existing_agent", "run_one_cycle"]
