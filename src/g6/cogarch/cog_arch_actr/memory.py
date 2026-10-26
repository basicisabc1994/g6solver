# SPDX-License-Identifier: Apache-2.0
"""ACT-R declarative memory and a small procedural module, kept in one file per memory under G6's state folder.

One source of truth (ACTR-02): every presentation of a chunk (its entry, an equivalent chunk added again, each
successful retrieval) and every production's utility and pending use is written to the memory's file, and every op
reads it back, so base-level and utility learning accumulate across calls. Time never runs backwards in a memory: an
op's "now" is the later of the clock and the latest time the memory has recorded.

Productions (ACTR-03) test buffers with variables (``"=x"`` binds a value on its first use and must match it after;
``"-slot"`` tests that a slot does not hold a value), and act by modifying the goal (``"=goal"``), clearing a buffer
(``"-goal"``, ``"-retrieval"``) or asking declarative memory for a chunk (``"+retrieval"``), so a run consults memory
with the same activation equations as :func:`retrieve`. A run ends when its goal test passes against the goal buffer,
or it is stopped and says why.
"""
from __future__ import annotations

import json
import random
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from g6.cogarch._store import atomic_write
from g6.cogarch.cog_arch_actr import equations as eq
from g6.core.storage import state_path

#: The clock "now" is read from (seconds); the wall clock a run's time limit is read from. Tests replace them.
_clock: Callable[[], float] = time.time
_wall: Callable[[], float] = time.monotonic

ACTION_TIME_S = 0.05     # a production firing (ACT-R's :dat default)
MAX_CHUNKS, MAX_PRODUCTIONS, MAX_PRESENTATIONS, MAX_PENDING = 10_000, 500, 5_000, 10_000
COMPETITORS = 5
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SLOT = re.compile(r"^-?[a-z][a-z0-9_-]{0,63}$")
VAR = re.compile(r"^=[a-z][a-z0-9_-]{0,63}$")
CONDITIONS = ("=goal", "=retrieval", "?retrieval")
ACTIONS = ("=goal", "-goal", "-retrieval", "+retrieval")
QUERIES = ("error", "empty", "full")


class Refused(Exception):  # noqa: N818 - a refusal with its code, raised out of the store
    def __init__(self, code: str, reason: str, **fields: Any) -> None:
        super().__init__(reason)
        self.code, self.reason, self.fields = code, reason, fields


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isa: str
    slots: dict[str, str | int | None]
    presentations: list[float] = Field(min_length=1)
    dropped: int = 0


class Production(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conditions: dict[str, Any]
    actions: dict[str, Any]
    utility: float = 0.0
    fired: int = 0


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunks: dict[str, Chunk] = Field(default_factory=dict)
    productions: dict[str, Production] = Field(default_factory=dict)
    pending: list[tuple[str, float]] = Field(default_factory=list)
    latest: float = 0.0
    draws: int = 0


@dataclass(frozen=True)
class Params:
    """The equations' parameters for one op, ACT-R's defaults unless the caller sets them."""

    decay: float = 0.5
    threshold: float = 0.0
    noise: float = 0.0
    latency_factor: float = 1.0
    latency_exponent: float = 1.0
    max_strength: float | None = None
    context_activation: float = 1.0
    goal_activation: float = 0.0
    utility_noise: float = 0.0
    seed: int = 0


class Memory:
    """One named memory: a JSON file under ``state_path("cogarch", "cog_arch_actr")``."""

    def __init__(self, name: str = "default", *, root: Path | None = None) -> None:
        if not NAME.fullmatch(name):
            raise Refused("G6_E_ACTR_INPUT", "a memory's name is lower-case letters, digits, - and _ (at most 64)")
        self.name = name
        self.path = (root or state_path("cogarch", "cog_arch_actr")) / f"{name}.json"

    def load(self) -> State:
        if not self.path.is_file():
            return State()
        try:
            return State.model_validate(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError) as exc:
            raise Refused("G6_E_ACTR_STORE", f"the memory {self.name!r} could not be read: "
                                                  f"{str(exc).splitlines()[0][:200]}") from exc

    def save(self, state: State) -> None:
        atomic_write(self.path, state.model_dump_json())

    @staticmethod
    def now(state: State) -> float:
        return max(_clock(), state.latest)


def rng_for(state: State, params: Params) -> tuple[random.Random, dict[str, int]]:
    """The seeded generator for one op, and what to record: the seed and this memory's draw number."""
    drawn = {"seed": params.seed, "draw": state.draws}
    state.draws += 1
    return random.Random(f"{params.seed}:{drawn['draw']}"), drawn


def _present(chunk: Chunk, at: float) -> None:
    chunk.presentations.append(at)
    if len(chunk.presentations) > MAX_PRESENTATIONS:
        chunk.dropped += len(chunk.presentations) - MAX_PRESENTATIONS
        del chunk.presentations[:-MAX_PRESENTATIONS]


def add_chunk(state: State, name: str, isa: str, slots: Mapping[str, str | int | None], now: float) -> tuple[str, str]:
    """Add a chunk, or give an existing equivalent one another presentation. Returns (name, how)."""
    existing = state.chunks.get(name)
    if existing is not None:
        if existing.isa != isa or existing.slots != dict(slots):
            raise Refused("G6_E_ACTR_CHUNK_EXISTS", f"a different chunk is already kept as {name!r}")
        _present(existing, now)
        return name, "rehearsed"
    twin = next((n for n, c in sorted(state.chunks.items()) if c.isa == isa and c.slots == dict(slots)), None)
    if twin is not None:
        _present(state.chunks[twin], now)
        return twin, "merged"
    if len(state.chunks) >= MAX_CHUNKS:
        raise Refused("G6_E_ACTR_FULL", f"the memory holds MAX_CHUNKS {MAX_CHUNKS} chunks already")
    state.chunks[name] = Chunk(isa=isa, slots=dict(slots), presentations=[now])
    return name, "new"


def _view(name: str, chunk: Chunk) -> dict[str, Any]:
    return {"name": name, "isa": chunk.isa, **chunk.slots}


def _test(test: Mapping[str, Any], have: Mapping[str, Any] | None, bound: dict[str, Any], *,
          negated: bool) -> bool:
    """One buffer's slot tests (positive or negated) against ``have``, binding variables into ``bound``."""
    if have is None:
        return False
    for key, want in test.items():
        if key.startswith("-") != negated:
            continue
        value = have.get(key[1:] if negated else key)
        if isinstance(want, str) and VAR.fullmatch(want):
            var = want[1:]
            if negated:
                if bound.get(var, object()) == value:
                    return False
            elif var in bound:
                if bound[var] != value:
                    return False
            elif value is None:
                return False
            else:
                bound[var] = value
        elif (value == want) is negated:
            return False
    return True


def matches(request: Mapping[str, Any], chunk: Mapping[str, Any]) -> bool:
    """Whether ``chunk`` (``isa`` and its slots) matches a request of literal values and negated slots."""
    return _test(request, chunk, {}, negated=False) and _test(request, chunk, {}, negated=True)


@dataclass
class Retrieval:
    ok: bool
    name: str = ""
    chunk: dict[str, Any] = field(default_factory=dict)
    breakdown: dict[str, Any] = field(default_factory=dict)
    latency_s: float = 0.0
    competitors: list[dict[str, Any]] = field(default_factory=list)
    matched: int = 0
    reason: str = ""


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and _wall() >= deadline:
        raise Refused("G6_E_ACTR_CAP", "retrieval reached the call's time limit", cap="time_limit_s")


def retrieve(state: State, request: Mapping[str, Any], sources: Mapping[str, Any], source_activation: float,
             params: Params, now: float, rng: random.Random, *, deadline: float | None = None) -> Retrieval:
    """The matching chunk with the highest activation, if it is at or above the threshold; its presentation is recorded
    at the time the retrieval completes. Ties go to the first name in order."""
    _check_deadline(deadline)
    fan_counts: Counter = Counter()
    if params.max_strength is not None and sources and source_activation:
        for chunk in state.chunks.values():
            _check_deadline(deadline)
            fan_counts.update(chunk.slots.values())
    scored = []
    for name in sorted(state.chunks):
        _check_deadline(deadline)
        chunk = state.chunks[name]
        if not matches(request, _view(name, chunk)):
            continue
        base = eq.base_level((now - t for t in chunk.presentations), params.decay)
        spread, terms = eq.spreading(sources, source_activation, params.max_strength, chunk.slots, (),
                                     fan_counts=fan_counts)
        noise = eq.logistic_noise(params.noise, rng)
        scored.append((base + spread + noise, name, {"base_level": base, "spreading": spread, "noise": noise,
                                                     "activation": base + spread + noise, "presentations":
                                                     len(chunk.presentations) + chunk.dropped,
                                                     "spreading_terms": terms}))
    _check_deadline(deadline)
    if not scored:
        return Retrieval(False, latency_s=eq.failure_time(params.threshold, params.latency_factor,
                                                          params.latency_exponent),
                         reason="no chunk in memory matches the request")
    scored.sort(key=lambda s: -s[0])     # stable: equal activations keep name order
    competitors = [{"name": n, **{k: v for k, v in b.items() if k != "spreading_terms"}}
                   for _, n, b in scored[:COMPETITORS]]
    best, name, breakdown = scored[0]
    if best < params.threshold:
        return Retrieval(False, name=name, breakdown=breakdown, competitors=competitors, matched=len(scored),
                         latency_s=eq.failure_time(params.threshold, params.latency_factor, params.latency_exponent),
                         reason=f"the best match, {name}, has activation {best:.4f}, below the threshold "
                                f"{params.threshold:g}")
    _check_deadline(deadline)
    latency = eq.retrieval_time(best, params.latency_factor, params.latency_exponent)
    _present(state.chunks[name], now + latency)
    state.latest = max(state.latest, now + latency)
    return Retrieval(True, name, _view(name, state.chunks[name]), breakdown, latency, competitors, len(scored))


# --- productions ----------------------------------------------------------------------------------------------------

def production_problems(conditions: Mapping[str, Any], actions: Mapping[str, Any]) -> list[str]:
    """What is wrong with a production, or ``[]``: known buffers, slot names, and every variable an action or a
    negated test uses bound by a positive test."""
    problems = [f"condition {k!r} is not one of {', '.join(CONDITIONS)}" for k in conditions if k not in CONDITIONS]
    problems += [f"action {k!r} is not one of {', '.join(ACTIONS)}" for k in actions if k not in ACTIONS]
    if not conditions:
        problems.append("a production needs at least one condition")
    if not actions:
        problems.append("a production needs at least one action")
    if "?retrieval" in conditions and conditions["?retrieval"] not in QUERIES:
        problems.append(f"?retrieval is one of {', '.join(QUERIES)}")
    bound: set[str] = set()
    negated_vars: set[str] = set()
    for key in ("=goal", "=retrieval"):
        test = conditions.get(key, {})
        if not isinstance(test, dict):
            problems.append(f"{key} is a mapping of slot tests")
            continue
        for slot, want in test.items():
            if not isinstance(slot, str) or not SLOT.fullmatch(slot):
                problems.append(f"{key}: {slot!r} is not a slot name")
            if not (want is None or isinstance(want, (str, int)) and not isinstance(want, bool)):
                problems.append(f"{key}.{slot}: a value is a string, an integer or null")
            elif isinstance(want, str) and want.startswith("="):
                if not VAR.fullmatch(want):
                    problems.append(f"{key}.{slot}: {want!r} is not a variable")
                (negated_vars if slot.startswith("-") else bound).add(want[1:])
    problems += [f"the negated test's variable ={v} is not bound by a positive test" for v in sorted(negated_vars - bound)]
    for key in ("=goal", "+retrieval"):
        change = actions.get(key)
        if change is None:
            continue
        if not isinstance(change, dict) or not change:
            problems.append(f"{key} is a non-empty mapping of slots")
            continue
        for slot, value in change.items():
            if not isinstance(slot, str) or not SLOT.fullmatch(slot) or (slot.startswith("-") and key == "=goal"):
                problems.append(f"{key}: {slot!r} is not a slot name")
            if not (value is None or isinstance(value, (str, int)) and not isinstance(value, bool)):
                problems.append(f"{key}.{slot}: a value is a string, an integer or null")
            elif isinstance(value, str) and value.startswith("=") and value[1:] not in bound:
                problems.append(f"{key}.{slot}: the variable {value} is not bound by a condition")
    for key in ("-goal", "-retrieval"):
        if key in actions and actions[key] is not True:
            problems.append(f"{key} is true (clear the buffer)")
    return problems


def _resolve(spec: Mapping[str, Any], bound: Mapping[str, Any]) -> dict[str, Any]:
    return {k: bound[v[1:]] if isinstance(v, str) and VAR.fullmatch(v) else v for k, v in spec.items()}


def _bindings(prod: Production, goal: dict | None, retrieval: dict | None, retrieval_failed: bool) -> dict | None:
    query = prod.conditions.get("?retrieval")
    if query == "error" and not retrieval_failed or query == "empty" and retrieval is not None \
            or query == "full" and retrieval is None:
        return None
    bound: dict[str, Any] = {}
    tests = [(prod.conditions[k], have) for k, have in (("=goal", goal), ("=retrieval", retrieval))
             if k in prod.conditions]
    for negated in (False, True):
        if not all(_test(test, have, bound, negated=negated) for test, have in tests):
            return None
    return bound


@dataclass
class RunResult:
    status: str
    goal: dict[str, Any] | None
    trace: list[dict[str, Any]]
    started: float
    ended: float
    cap: str = ""


def run(state: State, goal: Mapping[str, Any], goal_test: Mapping[str, Any], params: Params, now: float,
        rng: random.Random, *, max_cycles: int, time_limit_s: float) -> RunResult:
    """Fire productions from ``goal`` until the goal test passes against the goal buffer, no production matches, the
    goal is cleared, or a cap is reached. Model time starts at ``now``; each firing takes 0.05 s and a retrieval its
    latency."""
    t, deadline = now, _wall() + time_limit_s
    goal_buf: dict[str, Any] | None = dict(goal)
    retrieval: dict[str, Any] | None = None
    failed = False
    trace: list[dict[str, Any]] = []

    def done(status: str, cap: str = "") -> RunResult:
        state.latest = max(state.latest, t)
        return RunResult(status, goal_buf, trace, now, t, cap)

    for cycle in range(1, max_cycles + 1):
        if goal_buf is not None and matches(goal_test, goal_buf):
            return done("goal_test_passed")
        if _wall() > deadline:
            return done("cap", "time_limit_s")
        conflict = []
        for name in sorted(state.productions):
            prod = state.productions[name]
            bound = _bindings(prod, goal_buf, retrieval, failed)
            if bound is not None:
                conflict.append((prod.utility + eq.logistic_noise(params.utility_noise, rng), name, bound))
        if not conflict:
            return done("no_production_matches")
        conflict.sort(key=lambda c: -c[0])
        score, name, bound = conflict[0]
        prod = state.productions[name]
        selected, t = t, t + ACTION_TIME_S
        prod.fired += 1
        state.pending.append((name, selected))
        del state.pending[:-MAX_PENDING]
        step: dict[str, Any] = {"cycle": cycle, "selected_at": selected, "production": name, "utility": prod.utility,
                                "bindings": bound, "conflict_set": [c[1] for c in conflict]}
        actions = prod.actions
        if "=goal" in actions and goal_buf is not None:
            goal_buf.update(_resolve(actions["=goal"], bound))
        if actions.get("-goal"):
            goal_buf = None
        if actions.get("-retrieval"):
            retrieval, failed = None, False
        if "+retrieval" in actions:
            request = _resolve(actions["+retrieval"], bound)
            sources = {k: v for k, v in (goal_buf or {}).items() if k != "isa"}
            got = retrieve(state, request, sources, params.goal_activation, params, t, rng, deadline=deadline)
            t += got.latency_s
            retrieval, failed = (got.chunk, False) if got.ok else (None, True)
            step["retrieval"] = {"request": request, "retrieved": got.name if got.ok else None,
                                 "activation": got.breakdown.get("activation"), "latency_s": got.latency_s,
                                 "reason": got.reason}
        step["goal"] = dict(goal_buf) if goal_buf is not None else None
        trace.append(step)
        if goal_buf is None:
            return done("goal_cleared")
    if goal_buf is not None and matches(goal_test, goal_buf):
        return done("goal_test_passed")
    return done("cap", "max_cycles")


def reward(state: State, amount: float, alpha: float) -> list[dict[str, Any]]:
    """Utility learning for each production used since the last reward, in order: the effective reward is the reward
    less the time since that use's selection, the reward taken as given at the memory's latest time."""
    at = state.latest
    updates = []
    for name, selected in state.pending:
        prod = state.productions.get(name)
        if prod is None:
            continue
        effective = amount - (at - selected)
        before = prod.utility
        prod.utility = eq.utility_update(before, effective, alpha)
        updates.append({"production": name, "selected_at": selected, "effective_reward": effective,
                        "utility_before": before, "utility_after": prod.utility})
    state.pending.clear()
    return updates


__all__ = ["ACTIONS", "CONDITIONS", "Chunk", "Memory", "Refused", "Params", "Production", "Retrieval",
           "RunResult", "State", "add_chunk", "matches", "production_problems", "retrieve", "reward", "rng_for", "run"]
