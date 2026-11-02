# SPDX-License-Identifier: Apache-2.0
"""The loop reading its own records: what limits progress now, the work or the loop itself?

Plain code over the rows a cycle wrote. It makes no model call and writes nothing, and nothing it says changes what a cycle proposes or adopts:
the tick records it and a person reads it. It draws on the gate's verdicts, which stay out of a proposer's sight, so what it says must stay out of it too.

**The stages.** A cycle is a serial process, and each stage is counted in cycles: how many reached it and how many passed. A cycle offering twelve
candidates and keeping one is one cycle that passed, so a wide proposer does not read as a narrow stage. A stage with no record is unknown, and an
unknown stage is never a failure, which is how a row from an older log still reads.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from g6.metaharness.schema import OBJECTIVE_REGRESSED
from g6.self_maintenance.constraints import StageMeasurement
from g6.self_maintenance.envelope import TICK_BUDGET_SPENT

#: The loop's stages, in order: admitted by budget, not refused, candidates proposed, distinct from those tried, won tuning, won selection, passed the
#: gate, survived the monitor.
BUDGET, REFUSED, PROPOSED, DISTINCT, TUNING, SELECTION, GATE, MONITOR = STAGES = (
    "budget", "refused", "proposed", "distinct", "tuning", "selection", "gate", "monitor")
#: How many of the latest cycles count as recent.
RECENT = 12
# The loop's own codes. ``g6.metaharness.loop`` imports this file, so they are written out; a test holds them equal.
CYCLE = "G6_E_LOOP_CYCLE"
IDLE = "G6_E_LOOP_IDLE"
NO_CONSTRAINT = "G6_E_LOOP_NO_CONSTRAINT"
POOL_TOO_SMALL = "G6_E_LOOP_POOL_TOO_SMALL"
HELD_OUT_SPENT = "G6_E_LOOP_HELD_OUT_SPENT"
SPLIT_REUSED = "G6_E_LOOP_SPLIT_REUSED"
SEALED_BREACH = "G6_E_LOOP_SEALED_BREACH"
FROZEN_CHANGED = "G6_E_LOOP_FROZEN_CHANGED"
UNMETERED_CALLER = "G6_E_LOOP_UNMETERED_CALLER"
STOPPED = "G6_E_LOOP_STOPPED"
PAUSED = "G6_E_LOOP_PAUSED"
CLASSIFIER_STOP = "G6_E_LOOP_CLASSIFIER_STOP"
CYCLE_FAILED = "G6_E_LOOP_CYCLE_FAILED"
#: Outcomes that went no further than this stage, whatever the reason. ``idle`` is here because a pool with no failure left gave the cycle nothing to do.
_HELD = ("blocked", "error", "sealed_breach", "no_constraint", "idle")
_CODE = re.compile(r"\bG6_E_[A-Z0-9_]+\b")


def funnel(cycles: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """One tuning run's ``cycles`` as counts a row can hold, or nothing for a run that never began. ``offered``: valid candidates proposers gave. ``new``:
    those not tried before. ``repeated``: attempts the pool caught as repeats. ``measured``: run on the tuning split. ``kept``: of those, at least as good as
    their parent. ``raced``: entries in the races. A strategy keeps running counts over a run, so those are read from its last cycle."""
    if not cycles:
        return {}
    asked = [strategy for cycle in cycles for strategy in cycle["strategies"].values()]
    last = cycles[-1]["strategies"].values()
    return {"offered": sum(s.get("offered") or 0 for s in asked), "new": sum(s["proposed"] for s in asked),
            "repeated": sum(s.get("duplicates") or 0 for s in last), "measured": sum(s.get("measured") or 0 for s in last),
            "kept": sum(s.get("offered") or 0 for s in asked if s.get("attempts") is not None),
            "raced": sum(len(cycle["raced"]) for cycle in cycles)}


def _proposing(row: Mapping[str, Any]) -> dict[str, bool]:
    """The stages only ``tuning`` records: an older row has none, and a row that has one read them all."""
    tuned, read = row.get("tuning"), {}
    if not tuned:
        return read
    offered, new, measured = tuned["offered"], tuned["new"], tuned["measured"]
    read[PROPOSED] = bool(offered or measured or tuned["repeated"])
    if read[PROPOSED]:                 # a candidate that lost on tuning was new as far as anything can tell: what it lost shows at the next stage
        read[DISTINCT] = bool(new or (measured and not offered))
    if measured:
        read[TUNING] = tuned["kept"] > 0
    return read


def _read(row: Mapping[str, Any]) -> dict[str, bool]:
    """What one row says of each stage: passed, failed, or no key where it holds no record."""
    outcome, code, audit, decision = row.get("outcome"), row.get("code"), row.get("audit") or {}, row.get("decision") or {}
    read: dict[str, bool] = {}
    if code == TICK_BUDGET_SPENT:      # it stopped where it was told to and compared nothing, so it reached no later stage
        return {BUDGET: False}
    if "budget" in row:
        read[BUDGET] = True
    read[REFUSED] = outcome not in _HELD
    read |= _proposing(row)
    tuned = row.get("tuning") or {}
    if tuned.get("raced") or row.get("champion"):
        read[SELECTION] = bool(row.get("champion"))
    if decision.get("verdict"):
        read[GATE] = decision["verdict"] == "recommend"
    elif outcome == "refused":
        read[GATE] = False
    if outcome == "rolled_back":
        read[MONITOR] = False
    elif audit.get("audited"):
        read[MONITOR] = True
    return read


def _recent(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return list(rows)[-RECENT:]


def stages(rows: Iterable[Mapping[str, Any]]) -> tuple[StageMeasurement, ...]:
    """The recent cycles as serial stages, for ``identify_constraint``. A stage no row records is left out: that is what unknown means. A wait for budget
    leaves no row, so the next cycle says how many it followed, and those are ticks the budget stage did not admit. These are the tick's own records, so
    they count as independently observed. A cycle is the unit at every stage, so each has the same capacity and only its share passing tells them apart."""
    recent = _recent(rows)
    read = [_read(row) for row in recent]
    waited = sum((row.get("budget") or {}).get("waited", 0) for row in recent)
    found = []
    for name in STAGES:
        said = [one[name] for one in read if name in one]
        attempted = len(said) + (waited if name == BUDGET else 0)
        if attempted:
            found.append(StageMeasurement(name, attempted, sum(said), 1.0, True))
    return tuple(found)


def _reason(row: Mapping[str, Any]) -> str:
    """Why a cycle went no further, as a stable code. A step the harness blocked carries its code at the head of its reason."""
    code = row.get("code") or ""
    if code and code != CYCLE:
        return code
    found = _CODE.search(str(row.get("reason", "")))
    return found.group() if found else CYCLE


def _refused_by(row: Mapping[str, Any]) -> str:
    """The reason the gate gave, with the objective where one objective refused it."""
    decision = row.get("decision") or {}
    code = decision.get("code") or _reason(row)
    said = (decision.get("reasons") or [str(row.get("reason", ""))])[0]
    return f"{code}:{said.split(':', 1)[0]}" if code == OBJECTIVE_REGRESSED and ":" in said else code


def refusals(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """The recent cycles that were stopped, by reason, so an objective that refuses everything shows. A candidate that lost a race was not refused."""
    found: Counter[str] = Counter()
    for row in _recent(rows):
        read = _read(row)
        if read.get(GATE) is False:
            found[_refused_by(row)] += 1
        elif read.get(REFUSED) is False:
            found[_reason(row)] += 1
    return dict(found)


__all__ = ["STAGES", "funnel", "refusals", "stages"]
