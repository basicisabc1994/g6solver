# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Money, cost rows and budgets for the workforce (Phase E Part 1: E1a, E1c; Ledger §4, §5, §18, §20, §21).

Pure types and arithmetic, no I/O: ``calls.py`` makes a ``CostEntry`` for each call it sends, ``runs.py`` stores
them as append-only evidence rows, and the runtime reads a ``BudgetPolicy`` from the mission.

* Money is a whole number of minor units with its ISO-4217 currency (Ledger §4). No float holds an amount, and
  amounts in two currencies are never added: Part 1 has no conversion records, so an amount in another currency
  than the budget's is counted apart and leaves the total unknown (plan ruling R3).
* An amount nobody measured is ``None`` (unknown), never 0. A total holding one has no amount, only its known
  part and how many entries are unknown (plan ruling R4).
* A mission's budget is read fail-closed: one that cannot be read is 0, so nothing that may cost money runs
  against it (Ledger §20; production-path E1c).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, fields
from datetime import datetime
from fractions import Fraction
from typing import Any


#: The share of the hard limit at which the soft limit starts (from the archive's ``venture.budget``).
STOP_FRACTION = 0.9

#: The currency a mission's budget is held in (founder decision 3 recommends AUD; plan ruling R3).
BUDGET_CURRENCY = "AUD"
#: Ledger §5's categories.
CATEGORIES = ("model_inference", "tool_api", "search", "compute", "storage", "bandwidth", "external_service",
              "human_attention", "other")
#: Where an entry's amount comes from (plan ruling R5).
SOURCES = (
    "declared_free",     # the tool's reviewed contract declares it costs nothing per call: 0, exact
    "declared_ceiling",  # no actual is reported, so the declared ceiling is charged: an estimate
    "unpriced",          # the tool declares no cost: unknown
    "owner_session",     # written in the owner's own chat session, whose cost G6 is not told: unknown
    "abandoned",         # an attempt that stopped before it finished: what it spent is unknown
)
#: Sources a mission's budget cannot bind: G6 can neither measure nor stop the owner's own session (ruling R6).
NOT_BINDING = frozenset({"owner_session"})
#: ``task_runs.cost_source``: the run's summary of its rows (``none``: closed before cost rows were kept).
SUMMARIES = ("none", "exact", "estimated", "unknown")
#: Context telemetry (Ledger §18, plan ruling R9): an attempt that wrote something and read more than this many
#: bytes per byte written, past the floor, is flagged as out of proportion.
CONTEXT_RATIO = 20
CONTEXT_FLOOR_BYTES = 4096
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
#: The fields a stored row holds itself (the evidence id, its ``ref`` and its time), never in its detail.
_ROW_FIELDS = ("id", "mission_id", "recorded_at")


def _minor(value: Any) -> int | None:
    """A whole, non-negative number of minor units, or None (a bool, a float or text is refused)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


@dataclass(frozen=True)
class Money:
    """An amount in whole minor units of one ISO-4217 currency (Ledger §4)."""

    amount_minor_units: int
    currency: str

    def __post_init__(self) -> None:
        if _minor(self.amount_minor_units) is None:
            raise ValueError(f"an amount is a whole number of minor units >= 0, got {self.amount_minor_units!r}")
        if not (isinstance(self.currency, str) and _CURRENCY.match(self.currency)):
            raise ValueError(f"a currency is an ISO-4217 code (three capital letters), got {self.currency!r}")

    @classmethod
    def from_dict(cls, data: Any) -> Money:
        if not isinstance(data, dict) or set(data) != {"amount_minor_units", "currency"}:
            raise ValueError("money is {'amount_minor_units': <whole number>, 'currency': '<ISO-4217>'}")
        return cls(data["amount_minor_units"], data["currency"])

    def as_dict(self) -> dict:
        return {"amount_minor_units": self.amount_minor_units, "currency": self.currency}


def format_money(amount_minor_units: int, currency: str) -> str:
    """'0.30 AUD' for 30 and 'AUD', by integer arithmetic (no float touches an amount)."""
    return f"{amount_minor_units // 100}.{amount_minor_units % 100:02d} {currency}"


@dataclass(frozen=True)
class CostEntry:
    """One measurable variable cost (Ledger §5). The ids and time are stamped by the ledger that stores it."""

    category: str
    source: str
    amount_minor_units: int | None
    currency: str | None
    estimated: bool = False
    method: str = ""
    provider: str = ""
    resource_name: str = ""
    quantity: int | None = None
    quantity_unit: str = ""
    metadata: dict = field(default_factory=dict)
    id: str = ""
    mission_id: str = ""
    work_unit_id: str = ""
    attempt_id: str = ""
    worker_instance_id: str = ""
    capability_invocation_id: str = ""
    recorded_at: str = ""

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}, got {self.category!r}")
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}, got {self.source!r}")
        if self.amount_minor_units is None:
            if not self.method:
                raise ValueError("an unknown amount says why it is unknown (its method)")
            if self.estimated:
                raise ValueError("an unknown amount is not an estimate")
        else:
            Money(self.amount_minor_units, self.currency)  # both refused as Money refuses them
            if self.estimated and not self.method:
                raise ValueError("an estimate keeps its method")
        if self.quantity is not None and _minor(self.quantity) is None:
            raise ValueError(f"a quantity is a whole number >= 0, got {self.quantity!r}")

    @property
    def known(self) -> bool:
        return self.amount_minor_units is not None

    @property
    def binding(self) -> bool:
        """Whether a mission's budget binds this cost (plan ruling R6)."""
        return self.source not in NOT_BINDING

    def as_detail(self) -> dict:
        """The stored form (an evidence row's ``detail``): every field but those the row itself holds."""
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in _ROW_FIELDS}

    @classmethod
    def from_detail(cls, detail: dict, **row: Any) -> CostEntry:
        """A stored entry: its detail plus the row's own ``id``, ``mission_id`` and ``recorded_at`` (other keys of the
        detail, such as the event's actor, are not part of the entry)."""
        names = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in {**detail, **row}.items() if key in names})


@dataclass(frozen=True)
class CostTotal:
    """What a set of entries cost in one currency: the known part, and what kept the total from being known."""

    currency: str
    known_minor_units: int = 0
    entries: int = 0
    unknown_entries: int = 0
    estimated_entries: int = 0
    other_currency_entries: int = 0

    @property
    def amount_minor_units(self) -> int | None:
        """The total, or None when one entry's amount is unknown or in another currency (never 0 for unknown)."""
        return None if self.unknown_entries or self.other_currency_entries else self.known_minor_units

    @property
    def summary(self) -> str:
        """``task_runs.cost_source`` for these entries: ``exact``, ``estimated`` or ``unknown``."""
        if self.unknown_entries or self.other_currency_entries:
            return "unknown"
        return "estimated" if self.estimated_entries else "exact"

    def plus(self, other: CostTotal) -> CostTotal:
        if other.currency != self.currency:
            raise ValueError("totals in two currencies are not added without a conversion record (Ledger §4)")
        return CostTotal(self.currency, self.known_minor_units + other.known_minor_units,
                         self.entries + other.entries, self.unknown_entries + other.unknown_entries,
                         self.estimated_entries + other.estimated_entries,
                         self.other_currency_entries + other.other_currency_entries)


def total(entries: Iterable[CostEntry], currency: str = BUDGET_CURRENCY, *, binding_only: bool = False) -> CostTotal:
    """The entries' total in ``currency`` (``binding_only``: only what a mission's budget binds)."""
    known = count = unknown = estimated = other = 0
    for entry in entries:
        if binding_only and not entry.binding:
            continue
        count += 1
        if entry.amount_minor_units is None:
            unknown += 1
        elif entry.currency != currency:
            other += 1
        else:
            known += entry.amount_minor_units
            estimated += int(entry.estimated)
    return CostTotal(currency, known, count, unknown, estimated, other)


def call_cost(ceiling: Money | None, *, model_free: bool, component: str, op: str, charged_model_calls: int,
              process_model_calls: int) -> CostEntry:
    """What one call that was sent cost (Ledger §5), from what its reviewed contract declares (plan ruling R5): a
    declared 0 is exact; a declared ceiling is charged as an estimate when no actual is reported (the venture
    budget's rule: charge the reservation); no declaration is unknown, never 0. ``charged_model_calls`` are the
    model calls this call's own context noted; ``process_model_calls`` the process-wide delta, kept for audit."""
    category = "model_inference" if charged_model_calls else ("compute" if model_free else "tool_api")
    quantity, unit = (charged_model_calls, "model calls") if charged_model_calls else (1, "calls")
    common: dict[str, Any] = {
        "category": category, "provider": component, "resource_name": f"{component}.{op}", "quantity": quantity,
        "quantity_unit": unit,
        "metadata": {"model_calls": charged_model_calls, "process_model_calls": process_model_calls}}
    if ceiling is None:
        return CostEntry(source="unpriced", amount_minor_units=None, currency=None,
                         method="this tool declares no cost, and G6 cannot see what the call cost", **common)
    if ceiling.amount_minor_units == 0:
        return CostEntry(source="declared_free", amount_minor_units=0, currency=ceiling.currency,
                         method="the tool's reviewed contract declares it costs nothing per call", **common)
    return CostEntry(source="declared_ceiling", amount_minor_units=ceiling.amount_minor_units,
                     currency=ceiling.currency, estimated=True,
                     method="no actual cost is reported for this tool, so its declared ceiling is charged", **common)


def owner_session_cost() -> CostEntry:
    """A unit the owner's own chat session wrote: at least one model call, at a cost G6 is not told (ruling R6)."""
    return CostEntry(category="model_inference", source="owner_session", amount_minor_units=None, currency=None,
                     method="written in the owner's own chat session, whose cost G6 is not told",
                     provider="owner's chat session", resource_name="owner's chat session", quantity=1,
                     quantity_unit="model calls (at least)")


def abandoned_cost() -> CostEntry:
    """An attempt that stopped before it finished: whatever it spent is unknown (plan ruling R18)."""
    return CostEntry(category="other", source="abandoned", amount_minor_units=None, currency=None,
                     method="the attempt stopped before it finished, so what it spent is not known")


def hard_limit(value: Any) -> tuple[int, bool]:
    """A mission's hard machine-cost limit in minor units, and whether it could be read. Fail closed: anything but
    a whole number >= 0 (None, text, a bool, a float, a negative) is 0."""
    minor = _minor(value)
    return (0, False) if minor is None else (minor, True)


def soft_limit(hard: int) -> int:
    """``STOP_FRACTION`` of the hard limit, rounded down, in exact integer arithmetic (plan ruling R13)."""
    fraction = Fraction(str(STOP_FRACTION))
    return hard * fraction.numerator // fraction.denominator


@dataclass(frozen=True)
class BudgetPolicy:
    """Ledger §20 for one mission, derived from it (no table; plan ruling R12)."""

    mission_id: str
    currency: str
    hard_machine_cost_limit: int
    soft_machine_cost_limit: int
    ceiling_readable: bool
    #: A strict budget the caller asked for (never derived from a hosted owner in the library).
    charged: bool = False
    max_human_attention_seconds: int | None = None
    max_attempts_per_work_unit: int | None = None
    max_total_attempts: int | None = None
    max_worker_instances: int | None = None
    max_concurrency: int = 1
    max_resource_tier_without_approval: str | None = None
    allow_budget_escalation_request: bool = False

    def pressure(self, spent: CostTotal) -> str:
        """``SOFT_BUDGET``, ``BUDGET_EXHAUSTED`` or "" for the known binding spend. A mission with no budget (0) has
        no pressure: nothing that may cost money runs against it at all."""
        if not self.hard_machine_cost_limit:
            return ""
        if spent.known_minor_units >= self.hard_machine_cost_limit:
            return "BUDGET_EXHAUSTED"
        if spent.known_minor_units and spent.known_minor_units >= self.soft_machine_cost_limit:
            return "SOFT_BUDGET"
        return ""


def budget_policy(mission: dict, *, charged: bool = False) -> BudgetPolicy:
    """The mission's budget policy, its ceiling read fail-closed."""
    hard, readable = hard_limit(mission.get("budget_cents"))
    return BudgetPolicy(mission_id=str(mission.get("mission_id", "")), currency=BUDGET_CURRENCY,
                        hard_machine_cost_limit=hard, soft_machine_cost_limit=soft_limit(hard),
                        ceiling_readable=readable, charged=charged)


def size_of(content: Any) -> int:
    """The bytes of content as it is stored (sorted JSON, UTF-8)."""
    return len(json.dumps(content, sort_keys=True, default=str).encode("utf-8"))


def context_record(*, read_bytes: int, output_bytes: int, reused_inputs: int) -> dict:
    """Ledger §18's per-attempt context telemetry. No counted door reports tokens or cache hits in Part 1, so they
    are None (unknown), never 0 (plan ruling R9)."""
    flagged = (output_bytes > 0 and read_bytes > CONTEXT_FLOOR_BYTES and read_bytes > CONTEXT_RATIO * output_bytes)
    return {"input_tokens": None, "output_tokens": None, "retrieved_artifact_bytes": read_bytes,
            "output_bytes": output_bytes, "context_reuse": reused_inputs, "cache_hits": None,
            "out_of_proportion": flagged}


# --- human attention (Phase E Part 2, E1b; Ledger §6, §23) ------------------------------------------------------------

#: Ledger §6's kinds of intervention, and who intervened.
INTERVENTION_KINDS = ("clarification", "approval", "correction", "verification", "manual_execution", "escalation",
                      "configuration", "other")
ACTOR_TYPES = ("user", "operator", "administrator", "domain_expert")
#: How an intervention's time is known: measured (a page that timed it, E4c) or not at all (plan ruling P2.2).
DURATION_SOURCES = ("unknown", "measured")
#: Prices human attention when an operator sets it: whole minor units an hour and a currency, e.g. "6000 AUD"
#: (Ledger §23; plan ruling P2.7). Unset, attention is unpriced, never 0.
HOURLY_RATE_ENV = "G6_WORKFORCE_HOURLY_RATE"


def _zoned(value: Any) -> datetime | None:
    """An ISO-8601 time with a zone, or None (a time without one is a different moment on each machine)."""
    try:
        found = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return found if found.tzinfo is not None else None


@dataclass(frozen=True)
class HumanIntervention:
    """One time a person spent attention on a mission (Ledger §6). The ids and time are stamped by the store. Its
    duration is measured (both times, and exactly the seconds between them) or unknown; it is never inferred, from
    message counts or anything else (plan ruling P2.2)."""

    kind: str
    actor_type: str
    decided_by: str
    required_by_policy: bool
    reason: str
    outcome: str
    work_unit_id: str = ""
    decision_id: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_seconds: int | None = None
    duration_source: str = "unknown"
    metadata: dict = field(default_factory=dict)
    id: str = ""
    mission_id: str = ""
    recorded_at: str = ""

    def __post_init__(self) -> None:
        if self.kind not in INTERVENTION_KINDS:
            raise ValueError(f"kind must be one of {INTERVENTION_KINDS}, got {self.kind!r}")
        if self.actor_type not in ACTOR_TYPES:
            raise ValueError(f"actor_type must be one of {ACTOR_TYPES}, got {self.actor_type!r}")
        if self.duration_source not in DURATION_SOURCES:
            raise ValueError(f"duration_source must be one of {DURATION_SOURCES}, got {self.duration_source!r}")
        if not isinstance(self.required_by_policy, bool):  # one refusal type for every malformed field
            raise ValueError(f"required_by_policy is true or false, got {self.required_by_policy!r}")  # noqa: TRY004
        for name in ("decided_by", "reason", "outcome"):
            value = getattr(self, name)
            if not (isinstance(value, str) and value.strip()):
                raise ValueError(f"an intervention names its {name}")
        if self.duration_source == "unknown":
            if self.duration_seconds is not None or self.started_at or self.ended_at:
                raise ValueError("an unmeasured intervention has no duration and no times: its time is unknown, "
                                 "never inferred")
            return
        start, end = _zoned(self.started_at), _zoned(self.ended_at)
        if start is None or end is None or end < start:
            raise ValueError("a measured duration needs its start and end times, each with a zone, the end not "
                             "before the start")
        if self.duration_seconds != int((end - start).total_seconds()):
            raise ValueError("a measured duration is the seconds between its start and end, never a guess")

    @property
    def measured(self) -> bool:
        return self.duration_source == "measured"

    def as_detail(self) -> dict:
        """The stored form (an evidence row's ``detail``): every field but those the row itself holds."""
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in _ROW_FIELDS}

    @classmethod
    def from_detail(cls, detail: dict, **row: Any) -> HumanIntervention:
        """A stored intervention: its detail plus the row's own ``id``, ``mission_id`` and ``recorded_at`` (other keys
        of the detail, such as the event's actor, are not part of it)."""
        names = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in {**detail, **row}.items() if key in names})

    @classmethod
    def timed(cls, *, started: datetime, ended: datetime, **values: Any) -> HumanIntervention:
        """An intervention whose time was measured (the web approval page, E4c, times it), to the whole second."""
        start, end = started.replace(microsecond=0), ended.replace(microsecond=0)
        return cls(started_at=start.isoformat(), ended_at=end.isoformat(),
                   duration_seconds=int((end - start).total_seconds()), duration_source="measured", **values)


@dataclass(frozen=True)
class AttentionTotal:
    """What a mission's recorded interventions add up to (Ledger §8's human attention, §23's cost of it)."""

    interventions: int = 0
    by_kind: tuple[tuple[str, int], ...] = ()
    required_by_policy: int = 0
    known_seconds: int = 0
    unmeasured: int = 0
    cost: Money | None = None
    cost_note: str = ""

    @property
    def seconds(self) -> int | None:
        """Every intervention's time added up, or None when one was not timed (never a partial sum), or when none was
        recorded: the owner's time in the chat is never measured, so it is unknown, never 0 (fix pass M5)."""
        return None if self.unmeasured or not self.interventions else self.known_seconds

    def as_dict(self) -> dict:
        return {"interventions": self.interventions, "by_kind": dict(self.by_kind),
                "required_by_policy": self.required_by_policy, "known_seconds": self.known_seconds,
                "unmeasured": self.unmeasured, "seconds": self.seconds,
                "cost": self.cost.as_dict() if self.cost is not None else None, "cost_note": self.cost_note}


def attention_cost(seconds: int, rate: Money) -> Money:
    """Ledger §23: seconds / 3600 times the hourly rate, in whole minor units rounded half up (integer arithmetic)."""
    return Money((int(seconds) * rate.amount_minor_units + 1800) // 3600, rate.currency)


def attention_total(interventions: Iterable[HumanIntervention], *, hourly_rate: Money | None = None) -> AttentionTotal:
    """The interventions' total. Priced only with a configured rate and only when every one was timed (plan ruling
    P2.7): unset is unpriced and an untimed one is unknown, never 0."""
    found = list(interventions)
    unmeasured = sum(1 for i in found if not i.measured)
    known = sum(i.duration_seconds or 0 for i in found if i.measured)
    if hourly_rate is None:
        cost, note = None, "unpriced: no hourly rate is set for human attention"
    elif not found:  # Ledger §4, §23: unknown is never 0 (fix pass M5)
        cost, note = None, "not known: no intervention was recorded, and the owner's time in the chat is not measured"
    elif unmeasured:
        cost, note = None, f"not known: {unmeasured} of {len(found)} interventions were not timed"
    else:
        cost = attention_cost(known, hourly_rate)
        note = f"{known} s at {format_money(hourly_rate.amount_minor_units, hourly_rate.currency)} an hour"
    return AttentionTotal(len(found), tuple(sorted(Counter(i.kind for i in found).items())),
                          sum(1 for i in found if i.required_by_policy), known, unmeasured, cost, note)


def parse_hourly_rate(text: Any) -> Money | None:
    """'6000 AUD' (whole minor units and a currency) as Money; anything else is no rate at all (unpriced), never 0."""
    parts = str(text or "").split()
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    try:
        return Money(int(parts[0]), parts[1])
    except ValueError:
        return None


__all__ = ["ACTOR_TYPES", "BUDGET_CURRENCY", "CATEGORIES", "CONTEXT_FLOOR_BYTES",
           "CONTEXT_RATIO", "DURATION_SOURCES", "HOURLY_RATE_ENV", "INTERVENTION_KINDS", "NOT_BINDING", "SOURCES",
           "SUMMARIES", "AttentionTotal", "BudgetPolicy", "CostEntry", "CostTotal", "HumanIntervention", "Money",
           "abandoned_cost", "attention_cost", "attention_total", "budget_policy", "call_cost",
           "context_record", "format_money", "hard_limit", "owner_session_cost", "parse_hourly_rate", "size_of",
           "soft_limit", "total"]
