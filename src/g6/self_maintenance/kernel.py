# SPDX-License-Identifier: Apache-2.0
"""Self-maintenance decision kernel: deterministic selection, token reservations, fail-closed scope.

This module is deliberately independent of LLMs, shell commands, GitHub credentials
and the code that judges improvements. Agent claims cannot create approvals.
Budget reservations are conservative *estimates*, not hard token limits inside a
third-party Codex/Claude process; unknown actual usage is charged at full reserve.
A single trusted coordinator owns the ledger; multi-process scheduling needs a lock.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from math import isfinite
from pathlib import PurePosixPath
from typing import Literal, Sequence

from g6.core.ledger import ChainLedger, digest

PROVIDERS = ("codex", "claude")
# Neither prompts nor generated code can expand this allowlist.
SAFE_SOURCE_GLOBS = (
    "src/g6/code/*/*.py", "src/g6/knowledge/*/*.py",
    "src/g6/workspace/*/*.py", "src/g6/media/*/*.py",
    "src/g6/ml/*/*.py", "src/g6/agents/*/*.py",
)
PROTECTED_PARTS = frozenset({
    "core", "metaharness", "self_maintenance", "harness",
    "evidence", "invariants", "safety_engine", "action_gating",
    "coding_agent_gate", "integrity", "guards", "mcp",
    "tests", "test", ".github", "validation", "releases",
})
PROTECTED_FILES = frozenset({
    "pyproject.toml", "conftest.py", "pytest.ini", "tox.ini", "setup.cfg",
    "AGENTS.md", "CLAUDE.md", "CODEOWNERS", "LICENSE", "NOTICE",
})
MAX_TICKETS = 100
MAX_FILES = 8


def allowed_source(path: str) -> bool:
    if not isinstance(path, str) or len(path) > 320 or "\\" in path:
        return False
    p = PurePosixPath(path)
    if p.is_absolute() or str(p) != path or any(x in (".", "..", "") for x in p.parts):
        return False
    if any(x in PROTECTED_PARTS for x in p.parts) or p.name in PROTECTED_FILES:
        return False
    # Any block of code that decides or verifies another code change is outside scope.
    from g6.core.adaptation_gate import protected_hits
    return (not protected_hits((path,)) and
            any(fnmatchcase(path, pattern) for pattern in SAFE_SOURCE_GLOBS))


@dataclass(frozen=True)
class BudgetPolicy:
    # Modest defaults, deliberately requiring an operator to opt into greater use.
    day_tokens: int = 100_000
    cycle_tokens: int = 24_000
    attempt_tokens: int = 12_000
    reserve_tokens: int = 10_000
    provider_tokens: tuple[int, int] = (60_000, 40_000)  # codex, claude
    max_calls_per_day: int = 8
    max_calls_per_issue: int = 2
    max_cycle_seconds: int = 600
    max_usd_day: float = 20.0
    usd_reservation_per_call: float = 3.0

    def __post_init__(self) -> None:
        ints = (self.day_tokens, self.cycle_tokens, self.attempt_tokens,
                self.reserve_tokens, *self.provider_tokens, self.max_calls_per_day,
                self.max_calls_per_issue, self.max_cycle_seconds)
        if any(type(x) is not int or x <= 0 for x in ints):
            raise ValueError("budgets require positive integer limits")
        if self.reserve_tokens >= self.day_tokens or self.attempt_tokens > self.cycle_tokens:
            raise ValueError("invalid token reserve or attempt limit")
        if any(type(x) not in (int, float) or not isfinite(x) or x <= 0
               for x in (self.max_usd_day, self.usd_reservation_per_call)):
            raise ValueError("cost reservations must be positive finite amounts")
        if self.max_cycle_seconds > 900:
            raise ValueError("single agent calls cannot exceed the runtime's 900s ceiling")


@dataclass(frozen=True)
class MaintenanceTicket:
    id: str
    title: str
    evidence: str                         # link to reproducible CI failure / bug / benchmark
    files: tuple[str, ...]
    tests: tuple[str, ...]                 # existing host-selected regression ids
    severity: int = 2                     # host-assessed, 1..5
    confidence: float = 0.5               # probability a bounded repair is worthwhile, heuristic only
    benefit: int = 1                      # host-assessed 1..5
    estimated_tokens: int = 8000
    kind: Literal["bug", "regression", "complexity", "documentation"] = "bug"

    def __post_init__(self) -> None:
        if not self.id or not self.title.strip() or not self.evidence.strip():
            raise ValueError("ticket needs id, title, reproducible evidence")
        if not self.files or len(self.files) > MAX_FILES or not all(allowed_source(p) for p in self.files):
            raise ValueError("out of scope, too many, or protected source files")
        if not self.tests or len(self.tests) > 30 or any(
            not isinstance(t, str) or not t.startswith("tests/") or
            ".." in PurePosixPath(t.partition("::")[0]).parts or
            not t.partition("::")[0].endswith(".py")
            for t in self.tests
        ):
            raise ValueError("host-owned existing pytest node ids are required")
        if type(self.severity) is not int or not 1 <= self.severity <= 5:
            raise ValueError("invalid severity")
        if type(self.benefit) is not int or not 1 <= self.benefit <= 5:
            raise ValueError("invalid benefit")
        if not isinstance(self.confidence, (float, int)) or not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("invalid confidence")
        if type(self.estimated_tokens) is not int or self.estimated_tokens <= 0:
            raise ValueError("invalid estimate")


@dataclass(frozen=True)
class Reservation:
    ticket_id: str
    provider: str
    reserved_tokens: int
    reserved_usd: float
    index: int
    record_id: str


@dataclass(frozen=True)
class Plan:
    completion_state: str
    selected: str | None
    provider: str | None
    reserved_tokens: int
    ranked: tuple[tuple[str, float], ...]
    refusals: tuple[str, ...]
    rationale: str
    ledger_head: str


class BudgetBook:
    """One-worker, append-only budget and outcome book. Reopen to recover prior usage.

    Day keys are given by the host (UTC YYYY-MM-DD). A hosted scheduler must
    serialise reserve/settle across workers. The log is not a payment meter.
    """

    def __init__(self, ledger: ChainLedger | None = None) -> None:
        self.ledger = ledger if ledger is not None else ChainLedger()

    def _rows(self) -> list[dict]:
        return [dict(e["body"]) | {"kind": e["kind"], "id": e["id"]}
                for e in self.ledger.entries() if e["kind"] in ("maintenance_reserve", "maintenance_settle")]

    def usage(self, day: str) -> dict:
        reserves: dict[str, dict] = {}
        settles: dict[str, dict] = {}
        for e in self._rows():
            if e["kind"] == "maintenance_reserve" and e.get("day") == day:
                reserves[e["id"]] = e
            if e["kind"] == "maintenance_settle" and e.get("day") == day:
                settles[e["id"]] = e
        provider = {name: 0 for name in PROVIDERS}
        total_tokens, total_usd = 0, 0.0
        issue_counts: dict[str, int] = {}
        for ident, e in reserves.items():
            done = settles.get(ident)
            # Charge full reserved amount if no or untrustworthy usage receipt.
            tokens = e["tokens"] if done is None else done["tokens"]
            usd = e["usd"] if done is None else done["usd"]
            total_tokens += tokens
            total_usd += usd
            provider[e["provider"]] += tokens
            issue_counts[e["ticket_id"]] = issue_counts.get(e["ticket_id"], 0) + 1
        return {"tokens": total_tokens, "usd": round(total_usd, 4),
                "providers": provider, "calls": len(reserves), "issue_counts": issue_counts,
                "unsettled": sorted(set(reserves) - set(settles)), "ledger_head": self.ledger.head()}

    def reserve(self, ticket: MaintenanceTicket, provider: str, policy: BudgetPolicy, day: str,
                *, cycle_tokens_used: int = 0) -> Reservation:
        if provider not in PROVIDERS or not _valid_day(day):
            raise ValueError("unsupported provider or day")
        self.ledger.reload()
        used = self.usage(day)
        tokens = min(policy.attempt_tokens, ticket.estimated_tokens)
        if (used["tokens"] + tokens > policy.day_tokens - policy.reserve_tokens or
            cycle_tokens_used + tokens > policy.cycle_tokens or
            used["providers"][provider] + tokens > policy.provider_tokens[PROVIDERS.index(provider)] or
            used["usd"] + policy.usd_reservation_per_call > policy.max_usd_day or
            used["calls"] >= policy.max_calls_per_day or
            used["issue_counts"].get(ticket.id, 0) >= policy.max_calls_per_issue):
            raise ValueError("maintenance budget or per-issue attempt limit exhausted")
        rid = digest({"day": day, "ticket": ticket.id, "provider": provider,
                      "index": used["calls"] + 1, "head": self.ledger.head()})[:24]
        self.ledger.append("maintenance_reserve", rid, {
            "day": day, "ticket_id": ticket.id, "provider": provider,
            "tokens": tokens, "usd": policy.usd_reservation_per_call,
        })
        return Reservation(ticket.id, provider, tokens, policy.usd_reservation_per_call, used["calls"] + 1, rid)

    def settle(self, reservation: Reservation, day: str, *,
               observed_tokens: int | None = None, observed_usd: float | None = None,
               receipt_trusted: bool = False, outcome: str = "unknown") -> None:
        """Provider-reported usage is advisory unless independently metered by the host."""
        self.ledger.reload()
        original = self.ledger.get("maintenance_reserve", reservation.record_id)
        if original["day"] != day or original["provider"] != reservation.provider or original["ticket_id"] != reservation.ticket_id:
            raise ValueError("reservation/day mismatch")
        if any(e["kind"] == "maintenance_settle" and e["id"] == reservation.record_id for e in self._rows()):
            raise ValueError("reservation already settled")
        use = (receipt_trusted and type(observed_tokens) is int and observed_tokens >= 0 and
               type(observed_usd) in (int, float) and isfinite(observed_usd) and observed_usd >= 0)
        # Never refund a reservation because an untrusted model reports low usage.
        tokens = min(reservation.reserved_tokens, observed_tokens) if use else reservation.reserved_tokens
        usd = min(reservation.reserved_usd, observed_usd) if use else reservation.reserved_usd
        self.ledger.append("maintenance_settle", reservation.record_id, {
            "day": day, "tokens": tokens, "usd": usd, "outcome": outcome[:100],
            "source": "independent_meter" if use else "reservation_conservative",
        })


def _valid_day(day: str) -> bool:
    import datetime
    try:
        return datetime.date.fromisoformat(day).isoformat() == day
    except (ValueError, TypeError):
        return False


def rank_tickets(tickets: Sequence[MaintenanceTicket], *, policy: BudgetPolicy,
                 book: BudgetBook, day: str, preferred_provider: str = "codex") -> Plan:
    if not _valid_day(day) or preferred_provider not in PROVIDERS or len(tickets) > MAX_TICKETS:
        raise ValueError("invalid planning date, provider or ticket count")
    if len({t.id for t in tickets}) != len(tickets):
        raise ValueError("duplicate ticket id")
    book.ledger.reload()
    used = book.usage(day)
    ranked = sorted(((t, round(t.severity * t.benefit * t.confidence * 1000 / t.estimated_tokens, 8))
                     for t in tickets), key=lambda p: (-p[1], p[0].id))
    refusals: list[str] = []
    providers = (preferred_provider, *[p for p in PROVIDERS if p != preferred_provider])
    for ticket, score in ranked:
        if used["issue_counts"].get(ticket.id, 0) >= policy.max_calls_per_issue:
            refusals.append(ticket.id + ":attempt_limit")
            continue
        for provider in providers:
            estimate = min(policy.attempt_tokens, ticket.estimated_tokens)
            if (used["tokens"] + estimate <= policy.day_tokens - policy.reserve_tokens and
                estimate <= policy.cycle_tokens and
                used["providers"][provider] + estimate <= policy.provider_tokens[PROVIDERS.index(provider)] and
                used["usd"] + policy.usd_reservation_per_call <= policy.max_usd_day and
                used["calls"] < policy.max_calls_per_day):
                return Plan("qualified-draft", ticket.id, provider, estimate,
                            tuple((t.id, s) for t, s in ranked), tuple(refusals),
                            "risk-adjusted expected benefit per reserved token; no model execution", book.ledger.head())
        refusals.append(ticket.id + ":budget_exhausted")
    return Plan("blocked-escalated", None, None, 0, tuple((t.id, s) for t, s in ranked),
                tuple(refusals), "no budget-feasible ticket; nothing will run", book.ledger.head())
