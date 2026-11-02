# SPDX-License-Identifier: Apache-2.0
"""The loop reading its own records: what limits progress now, the work or the loop itself?

Plain code over the rows a cycle wrote. It makes no model call and writes nothing, and nothing it says changes what a cycle proposes or adopts:
the tick records it and a person reads it. It draws on the gate's verdicts, which stay out of a proposer's sight, so what it says must stay out of it too.

**The stages.** A cycle is a serial process, and each stage is counted in cycles: how many reached it and how many passed. A cycle offering twelve
candidates and keeping one is one cycle that passed, so a wide proposer does not read as a narrow stage. A stage with no record is unknown, and an
unknown stage is never a failure, which is how a row from an older log still reads.

**The levels.** A failure names the stage, or what its reason points at: a gate that finds too few cases points at the pool. The work is what candidates
could not do. The loop is its own pipeline. The owner holds the gate, the judge, the held-out splits, the envelope and the stop: the loop may name these
with evidence and may never edit them, because the cheapest way through a gate is to lower it. A gate that found no gain is the work and not the gate: it
did its job on candidates that were no better, and nothing there is for the owner to change.

**Stuck.** The same constraint for three cycles with no adoption is stuck, and the loop may not repeat the same kind of attempt, so ``change`` says what must
differ: its method first, then its level, then its frame. None of these reaches what is the owner's. Nothing in this file acts on it yet.

**Where this stops.** A wait or a refusal before a cycle leaves no row, so a loop kept out of every cycle (paused, frozen, a spent held-out split, a pool
refused at enrolment) cannot read itself here: those stand in the digest's stops. ``identify_constraint`` assumes serial stages and one bottleneck.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from g6.metaharness.lessons import ROOT_CAUSES, Lesson, repeats
from g6.metaharness.lessons import stuck as recurring
from g6.metaharness.schema import HOLDOUT_REUSED, INSUFFICIENT_EVIDENCE, NO_GAIN, OBJECTIVE_REGRESSED, SUITE_CANNOT_DISCRIMINATE
from g6.self_maintenance.constraints import StageMeasurement, identify_constraint
from g6.self_maintenance.envelope import TICK_BUDGET_SPENT

#: The loop's stages, in order: admitted by budget, not refused, candidates proposed, distinct from those tried, won tuning, won selection, passed the
#: gate, survived the monitor.
BUDGET, REFUSED, PROPOSED, DISTINCT, TUNING, SELECTION, GATE, MONITOR = STAGES = (
    "budget", "refused", "proposed", "distinct", "tuning", "selection", "gate", "monitor")
#: How many of the latest cycles count as recent.
RECENT = 12
WORK, LOOP, OWNER = "work", "loop", "owner"
#: Cycles of one constraint with no adoption that make the loop stuck.
STUCK = 3
#: What must differ once it is stuck, each after ``STUCK`` more cycles of the same.
CHANGES = ("method", "level", "frame")
UNKNOWN = "unknown"
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
POOL, HELD_OUT, JUDGE, ENVELOPE, STOP, PERSON_ONLY, ERROR, NO_GAIN_FOUND = (
    "pool", "held_out", "judge", "envelope", "stop", "person_only", "error", "no_gain")
#: What a reason points at, where that is not the stage it stopped at.
_POINTS_AT = {POOL_TOO_SMALL: POOL, IDLE: POOL, INSUFFICIENT_EVIDENCE: POOL, NO_GAIN: NO_GAIN_FOUND, CYCLE_FAILED: ERROR,
              HELD_OUT_SPENT: HELD_OUT, SPLIT_REUSED: HELD_OUT, SEALED_BREACH: HELD_OUT, HOLDOUT_REUSED: HELD_OUT,
              SUITE_CANNOT_DISCRIMINATE: JUDGE, FROZEN_CHANGED: ENVELOPE, UNMETERED_CALLER: ENVELOPE, STOPPED: STOP, PAUSED: STOP,
              CLASSIFIER_STOP: STOP, NO_CONSTRAINT: PERSON_ONLY}
_THE_WORK = (TUNING, SELECTION, NO_GAIN_FOUND, UNKNOWN)
_THE_OWNERS = (GATE, JUDGE, HELD_OUT, ENVELOPE, STOP, PERSON_ONLY)


def funnel(cycles: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """One tuning run's ``cycles`` as counts a row can hold, or nothing for a run that never began. ``offered``: valid candidates proposers gave. ``new``:
    those not tried before. ``repeated``: attempts the pool caught as repeats. ``measured``: run on the tuning split. ``kept``: of those, at least as good as
    their parent. ``raced``: entries in the races. A strategy keeps running counts over a run, so those are read from the last cycle it reached."""
    if not cycles:
        return {}
    asked = [strategy for cycle in cycles for strategy in cycle["strategies"].values()]
    last = {name: strategy for cycle in cycles for name, strategy in cycle["strategies"].items()}.values()
    return {"offered": sum(s.get("offered") or 0 for s in asked), "new": sum(s["proposed"] for s in asked),
            "repeated": sum(s.get("duplicates") or 0 for s in last), "measured": sum(s.get("measured") or 0 for s in last),
            "kept": sum(s.get("offered") or 0 for s in asked if s.get("attempts") is not None),
            "raced": sum(len(cycle["raced"]) for cycle in cycles)}


def _fields(value: Any) -> Mapping[str, Any]:
    """A row's field as a mapping. A log holds whatever JSON it was given, and reading it must never be what fails."""
    return value if isinstance(value, Mapping) else {}


def _count(fields: Mapping[str, Any], name: str) -> int:
    value = fields.get(name)
    return max(value, 0) if isinstance(value, int) and not isinstance(value, bool) else 0


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _proposing(row: Mapping[str, Any]) -> dict[str, bool]:
    """The stages only ``tuning`` records: an older row has none, and a count a row leaves out reads as none."""
    tuned, read = _fields(row.get("tuning")), {}
    if not tuned:
        return read
    offered, new, measured = _count(tuned, "offered"), _count(tuned, "new"), _count(tuned, "measured")
    read[PROPOSED] = bool(offered or measured or _count(tuned, "repeated"))
    if read[PROPOSED]:                 # measured and never offered: it lost on tuning, so it was new as far as anything can tell, and what it lost shows at the next stage
        read[DISTINCT] = bool(new or measured > _count(tuned, "kept"))
    if measured:
        read[TUNING] = _count(tuned, "kept") > 0
    return read


def _read(row: Mapping[str, Any]) -> dict[str, bool]:
    """What one row says of each stage: passed, failed, or no key where it holds no record."""
    outcome, code, audit, decision = row.get("outcome"), row.get("code"), _fields(row.get("audit")), _fields(row.get("decision"))
    read: dict[str, bool] = {}
    if code == TICK_BUDGET_SPENT:      # it stopped where it was told to and compared nothing, so it reached no later stage
        return {BUDGET: False}
    if "budget" in row:
        read[BUDGET] = True
    read[REFUSED] = outcome not in _HELD
    read |= _proposing(row)
    if _count(_fields(row.get("tuning")), "raced") or row.get("champion"):
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
    waited = sum(_count(_fields(row.get("budget")), "waited") for row in recent)
    found = []
    for name in STAGES:
        said = [one[name] for one in read if name in one]
        attempted = len(said) + (waited if name == BUDGET else 0)
        if attempted:
            found.append(StageMeasurement(name, attempted, sum(said), 1.0, True))
    return tuple(found)


def _reason(row: Mapping[str, Any]) -> str:
    """Why a cycle went no further, as a stable code. A step the harness blocked carries its code at the head of its reason."""
    code = _text(row.get("code"))
    if code and code != CYCLE:
        return code
    found = _CODE.search(str(row.get("reason", "")))
    return found.group() if found else CYCLE


def _refused_by(row: Mapping[str, Any]) -> str:
    """The reason the gate gave, with the objective where one objective refused it."""
    decision = _fields(row.get("decision"))
    code = _text(decision.get("code")) or _reason(row)
    reasons = decision.get("reasons")
    said = str(reasons[0]) if isinstance(reasons, list | tuple) and reasons else str(row.get("reason", ""))
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


def _named(stage: str, row: Mapping[str, Any]) -> str:
    """What failing ``stage`` on this row names: the stage, or what its reason points at."""
    if stage == GATE:
        return _POINTS_AT.get(_text(_fields(row.get("decision")).get("code")) or _reason(row), stage)
    return _POINTS_AT.get(_reason(row), stage) if stage == REFUSED else stage


def _limit(row: Mapping[str, Any]) -> str | None:
    """What stopped this cycle: the first stage it failed, named. ``None`` for an adoption, and where its row says nothing of where it stopped."""
    read = _read(row)
    stage = None if row.get("outcome") == "adopted" else next((name for name in STAGES if read.get(name) is False), None)
    return _named(stage, row) if stage else None


def _held(rows: Sequence[Mapping[str, Any]]) -> tuple[str | None, int]:
    """The constraint the latest cycle stopped at, and how many cycles in a row it has held."""
    limit, held = _limit(rows[-1]) if rows else None, 0
    for row in reversed(rows):
        if limit is None or _limit(row) != limit:
            break
        held += 1
    return limit, held


def _narrowest(rows: Sequence[Mapping[str, Any]]) -> str:
    """What the stage that passes the smallest share names. Only a stage that failed can be it, so what no row records never is."""
    failing = [stage for stage in stages(rows) if stage.succeeded < stage.attempted]
    if not failing:
        return UNKNOWN
    stage = identify_constraint(failing).bottleneck
    named = Counter(_named(stage, row) for row in rows if _read(row).get(stage) is False)
    return named.most_common(1)[0][0] if named else stage


def _level(constraint: str) -> str:
    return OWNER if constraint in _THE_OWNERS else WORK if constraint in _THE_WORK else LOOP


@dataclass(frozen=True)
class Diagnosis:
    """What limits progress now. ``level``: ``work``, ``loop`` or ``owner``. ``evidence``: the counts it rests on. ``change``: what the next attempt must do
    differently, once ``stuck``. ``repeated``: each mistake that came back after its lesson, from ``g6.metaharness.lessons``."""

    level: str
    constraint: str
    evidence: Mapping[str, Any]
    stuck: bool
    change: str | None
    repeated: Mapping[str, Mapping[str, Any]]


def diagnose(rows: Iterable[Mapping[str, Any]], lessons: Iterable[Lesson] = (), seen: Iterable[tuple[int, str, str]] = ()) -> Diagnosis:
    """What the recent cycles say limits progress. A cycle adopted within the last ``STUCK`` is a loop that moves, so the work limits it, and the latest cause
    a cycle named says where. Past that it reads the cycles since its last adoption. ``lessons`` and ``seen``: as ``g6.metaharness.lessons.repeats`` takes."""
    recent = _recent(rows)
    last = max((i for i, row in enumerate(recent) if row.get("outcome") == "adopted"), default=-1)
    since = recent[last + 1:]
    moving = last >= 0 and len(since) < STUCK
    limit, held = _held(recent)
    stuck = held >= STUCK
    if moving:
        constraint = next((row["constraint"] for row in reversed(recent) if _text(row.get("constraint"))), UNKNOWN)
    else:
        constraint = limit if stuck else _narrowest(since)
    basis = recent if moving else since
    measured = stages(basis)
    known = {stage.name for stage in measured}
    returned = repeats(lessons, seen)
    keeps = recurring(returned)
    return Diagnosis(WORK if moving else _level(constraint), constraint,
                     {"cycles": len(basis), "adopted": sum(row.get("outcome") == "adopted" for row in recent), "held": held,
                      "stages": {stage.name: {"attempted": stage.attempted, "succeeded": stage.succeeded} for stage in measured},
                      "unknown": [name for name in STAGES if name not in known], "refusals": refusals(basis)},
                     stuck, CHANGES[min(held // STUCK, len(CHANGES)) - 1] if stuck else None,
                     {cause: {**row, "stuck": cause in keeps} for cause, row in returned.items()})


def lessons_from(records: Iterable[Any], root_cause: Callable[[str], str | None] | None, *, generation: int, tick: int, scope: str) -> tuple[Lesson, ...]:
    """One lesson for each cause the domain can place on the closed list, from what an independent witness saw: a failure none saw teaches nothing yet.
    ``root_cause``: the domain's, if it has one. The words are plain code's, so the hypothesis is only that placing, and a model may better them later."""
    if root_cause is None:
        return ()
    cases: dict[str, list[str]] = defaultdict(list)
    for record in records:
        if record.independently_observed:
            cases[record.cause].append(record.case_id)
    found = []
    for cause, ids in sorted(cases.items()):
        root = root_cause(cause)
        if root in ROOT_CAUSES:
            found.append(Lesson(f"{tick}:{cause}", generation, root, tuple(ids), f"{cause} is a case of {root}",
                                ("the cause was read wrongly", "the judge wants something the case does not state"),
                                f"a later generation fails with {cause} again", f"{scope}, tuning split"))
    return tuple(found)


def lessons_in(rows: Iterable[Mapping[str, Any]]) -> tuple[Lesson, ...]:
    """The lessons the rows hold. A row from before lessons holds none, and one that does not read is left out: nothing may rest on it."""
    found = []
    for row in rows:
        for kept in row.get("lessons") or ():
            try:
                found.append(Lesson(**kept))
            except (TypeError, ValueError):
                continue
    return tuple(found)


def seen_in(lessons: Iterable[Lesson]) -> list[tuple[int, str, str]]:
    """Each failure the lessons record, as ``g6.metaharness.lessons.repeats`` takes them."""
    return [(lesson.generation, lesson.root_cause, case) for lesson in lessons for case in lesson.case_ids]


def read(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The diagnosis of ``rows`` as a row holds it. This only reads and says, so it may cost a cycle nothing: a failure here is recorded and goes no further."""
    try:
        rows = list(rows)
        known = lessons_in(rows)
        return asdict(diagnose(rows, known, seen_in(known)))
    except Exception as exc:  # noqa: BLE001 - see above: no surprise in the records reaches the work
        return {"unreadable": f"{type(exc).__name__}: {exc}"[:200]}


__all__ = ["CHANGES", "Diagnosis", "LOOP", "OWNER", "RECENT", "STAGES", "STUCK", "UNKNOWN", "WORK", "diagnose", "funnel", "lessons_from", "lessons_in", "read",
           "refusals", "seen_in", "stages"]
