# SPDX-License-Identifier: Apache-2.0
"""CogArchActrBlock: the registry and MCP door (``g6_invoke``) to cog_arch_actr.

ACT-R declarative memory for agents, with a small procedural module. ``add_chunk`` keeps a chunk (or gives an
equivalent one another presentation); ``retrieve`` returns the matching chunk with the highest activation and its
breakdown (base level, spreading, noise) and latency, or a ``blocked-escalated`` refusal when the best match is below
the retrieval threshold; ``add_production`` keeps a production with variables and retrieval requests; ``run`` fires
productions from a goal until the caller's goal test passes against the goal buffer, with a trace; ``reward`` applies
utility learning to the productions used since the last reward. Presentations and utilities are written to the
memory's file under G6's state folder on every op, so learning accumulates across calls. The best any op says is
``qualified-draft``: the equations rank memories under hand-set parameters, with no fit to any person's data.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from g6.cogarch import _reply
from g6.cogarch._store import StoreBusy, store_lock
from g6.cogarch.cog_arch_actr import memory as mem
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result

COMPONENT = "cog_arch_actr"
KEPT, RETRIEVED, BELOW, NO_MATCH = "G6_E_ACTR_KEPT", "G6_E_ACTR_RETRIEVED", "G6_E_ACTR_BELOW_THRESHOLD", \
    "G6_E_ACTR_NO_MATCH"
PRODUCTION_KEPT, PRODUCTION_EXISTS, INVALID = "G6_E_ACTR_PRODUCTION_KEPT", "G6_E_ACTR_PRODUCTION_EXISTS", \
    "G6_E_ACTR_PRODUCTION_INVALID"
REACHED, STUCK, CLEARED, CAP = "G6_E_ACTR_GOAL_REACHED", "G6_E_ACTR_STUCK", "G6_E_ACTR_GOAL_CLEARED", "G6_E_ACTR_CAP"
REWARDED, NOTHING, INPUT = "G6_E_ACTR_REWARDED", "G6_E_ACTR_NOTHING_TO_REWARD", "G6_E_ACTR_INPUT"
SENSITIVE_OPS: tuple[str, ...] = ()
Value = StrictStr | StrictInt | None
RESERVED = ("isa", "name")
_OPS: dict[str, str] = {
    "add_chunk": "add_chunk(memory, name, isa, slots): keep a chunk; the same chunk again (by name or by content) is "
                 "another presentation of it",
    "retrieve": "retrieve(memory, request, context, parameters, time_limit_s): the matching chunk with the highest activation (base "
                "level from its presentations, spreading from context, seeded noise) and its latency, or a "
                "blocked-escalated refusal below the threshold; a retrieval is recorded as a presentation",
    "add_production": "add_production(memory, name, conditions, actions, utility): keep a production; =goal and "
                      "=retrieval tests with =variables and -negated slots, ?retrieval error, empty or full; actions "
                      "=goal, -goal, -retrieval and +retrieval (a request to declarative memory)",
    "run": "run(memory, goal, goal_test, parameters, max_cycles, time_limit_s): fire productions from the goal until "
           "the goal test passes against the goal buffer, with a trace of every cycle; stopped with a reason when no "
           "production matches, the goal is cleared or a cap is reached",
    "reward": "reward(memory, reward, alpha): utility learning for each production used since the last reward, the "
              "reward less the time since each use",
}
_ACTIONS: dict[str, str] = {
    KEPT: "Retrieve it later; each retrieval strengthens it, and time weakens it.",
    RETRIEVED: "Treat the chunk as the memory the equations rank first, not as a checked fact: check it against its "
               "source before relying on it.",
    BELOW: "Nothing was recalled: present the memory again, lower the threshold, or look it up at its source.",
    NO_MATCH: "No kept chunk matches the request: add one, or ask with fewer slots.",
    PRODUCTION_KEPT: "Run a goal; the production competes by utility, which reward changes.",
    PRODUCTION_EXISTS: "Keep the new production under another name.",
    INVALID: "Correct the production as the reasons say.",
    REACHED: "The goal test passed against the goal buffer; read the trace to see how. Give a reward to teach the "
             "productions used.",
    STUCK: "No production matched: add one for this state, or check the goal's slots.",
    CLEARED: "A production cleared the goal before the goal test passed: check the goal test, or the production "
             "that clears the goal.",
    CAP: "Raise the cap named, within its ceiling, or check for a production that loops.",
    REWARDED: "The utilities are updated; a later run chooses among matching productions by them.",
    NOTHING: "Run first: only productions used since the last reward are rewarded.",
    INPUT: "Correct the request as the reason says.",
    "G6_E_ACTR_CHUNK_EXISTS": "Keep the new chunk under another name.",
    "G6_E_ACTR_FULL": "Start another memory, or keep fewer chunks or productions.",
    "G6_E_ACTR_STORE": "The memory's file could not be read or written; check the state folder.",
    "G6_E_ACTR_STORE_BUSY": "Retry after the other call releases this memory's lock.",
    "G6_E_ACTR_NUMERIC": "Reduce the latency exponent or adjust the threshold; the latency must remain finite.",
}


def _slots(value: dict[str, Any], what: str, *, negations: bool) -> dict[str, Any]:
    for key in value:
        if not mem.SLOT.fullmatch(key) or (key.startswith("-") and not negations):
            raise ValueError(f"{what}: {key!r} is not a slot name (lower-case letters, digits, - and _)")
    for v in value.values():
        if isinstance(v, str) and len(v) > 200:
            raise ValueError(f"{what}: a value is at most 200 characters")
    return value


class CogArchActrInput(BaseModel):
    """One op's parameters; fields no op has are refused. Defaults are ACT-R's (the ACT-R 7.30+ Reference Manual)."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["add_chunk", "retrieve", "add_production", "run", "reward", "ops", "help"] = "help"
    memory: str = Field("default", max_length=64)
    name: str = Field("", max_length=64)
    isa: str = Field("", max_length=64)
    slots: dict[str, Value] = Field(default_factory=dict, max_length=50)
    request: dict[str, Value] = Field(default_factory=dict, max_length=50)
    context: dict[str, Value] = Field(default_factory=dict, max_length=20)
    conditions: dict[str, Any] = Field(default_factory=dict, max_length=3)
    actions: dict[str, Any] = Field(default_factory=dict, max_length=4)
    utility: float = Field(0.0, ge=-1000, le=1000)
    goal: dict[str, Value] = Field(default_factory=dict, max_length=50)
    goal_test: dict[str, Value] = Field(default_factory=dict, max_length=50)
    reward: float | None = Field(None, ge=-1000, le=1000)
    alpha: float = Field(0.2, gt=0, le=1)
    decay: float = Field(0.5, gt=0, le=5)
    threshold: float = Field(0.0, ge=-100, le=100)
    noise: float = Field(0.0, ge=0, le=10)
    latency_factor: float = Field(1.0, ge=0, le=100)
    latency_exponent: float = Field(1.0, ge=0, le=100)
    max_strength: float | None = Field(None, ge=-100, le=100)
    context_activation: float = Field(1.0, ge=0, le=100)
    goal_activation: float = Field(0.0, ge=0, le=100)
    utility_noise: float = Field(0.0, ge=0, le=100)
    seed: int = Field(0, ge=0, le=2**32)
    max_cycles: int = Field(100, ge=1, le=10_000)
    time_limit_s: float = Field(10.0, gt=0, le=60)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("slots")
    @classmethod
    def _plain_slots(cls, value: dict[str, Any]) -> dict[str, Any]:
        _slots(value, "slots", negations=False)
        reserved = [k for k in value if k in RESERVED]
        if reserved:
            raise ValueError(f"slots: {reserved[0]!r} is reserved (the chunk's type and name are given apart)")
        return value

    @field_validator("request", "goal_test")
    @classmethod
    def _tests(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _slots(value, "a request or goal test", negations=True)

    @field_validator("context", "goal")
    @classmethod
    def _plain(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _slots(value, "context or goal", negations=False)

    @field_validator("utility", "reward", "decay", "threshold", "max_strength")
    @classmethod
    def _finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("a number here is finite")
        return value


def _params(d: CogArchActrInput) -> mem.Params:
    return mem.Params(decay=d.decay, threshold=d.threshold, noise=d.noise, latency_factor=d.latency_factor,
                      latency_exponent=d.latency_exponent, max_strength=d.max_strength,
                      context_activation=d.context_activation, goal_activation=d.goal_activation,
                      utility_noise=d.utility_noise, seed=d.seed)


def _params_evidence(p: mem.Params) -> dict[str, Any]:
    return {"kind": "parameters", "set_by": "the caller, defaults from the ACT-R 7.30+ Reference Manual",
            "decay": p.decay, "threshold": p.threshold, "noise_s": p.noise, "latency_factor": p.latency_factor,
            "latency_exponent": p.latency_exponent, "max_strength": p.max_strength,
            "context_activation": p.context_activation, "goal_activation": p.goal_activation,
            "utility_noise_s": p.utility_noise, "fitted_to_data": False}


def _ids(d: CogArchActrInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _refuse(op: str, code: str, why: str, d: CogArchActrInput, **fields: Any) -> dict[str, Any]:
    return _reply.refuse(COMPONENT, op, code, why, action=_ACTIONS[code], reason=why, **_ids(d), **fields)


def _answer(op: str, code: str, said: str, d: CogArchActrInput, **fields: Any) -> dict[str, Any]:
    return _reply.answer(COMPONENT, op, code, said, action=_ACTIONS[code], **_ids(d), **fields)


def add_chunk(store: mem.Memory, state: mem.State, d: CogArchActrInput) -> dict[str, Any]:
    if not mem.NAME.fullmatch(d.name) or not d.isa.strip():
        return _refuse("add_chunk", INPUT, "give name (lower-case letters, digits, - and _) and isa (its type)", d)
    now = store.now(state)
    name, how = mem.add_chunk(state, d.name, d.isa, d.slots, now)
    state.latest = max(state.latest, now)
    store.save(state)
    chunk = state.chunks[name]
    said = {"new": f"Kept {name!r} in memory {store.name!r}.",
            "rehearsed": f"{name!r} was already kept: this is presentation {len(chunk.presentations)} of it.",
            "merged": f"An equivalent chunk is kept as {name!r}: this is another presentation of it, as ACT-R merges "
                      "an equivalent chunk."}[how]
    return _answer("add_chunk", KEPT, said, d, chunk=mem._view(name, chunk), how=how, at=now,
                   presentations=len(chunk.presentations) + chunk.dropped,
                   evidence=[{"kind": "memory", "path": str(store.path), "chunks": len(state.chunks)}])


def retrieve(store: mem.Memory, state: mem.State, d: CogArchActrInput) -> dict[str, Any]:
    if not d.request:
        return _refuse("retrieve", INPUT, "give request: the slots (and isa) the chunk must have", d)
    params, now = _params(d), store.now(state)
    rng, drawn = mem.rng_for(state, params)
    got = mem.retrieve(state, d.request, d.context, params.context_activation, params, now, rng,
                       deadline=mem._wall() + d.time_limit_s)
    store.save(state)
    evidence = [_params_evidence(params), {"kind": "noise", **drawn, "s": params.noise},
                {"kind": "retrieval", "at": now, "matched": got.matched, "threshold": params.threshold}]
    fields = {"request": d.request, "breakdown": got.breakdown, "latency_s": got.latency_s,
              "competitors": got.competitors, "threshold": params.threshold, "at": now, "evidence": evidence}
    if not got.ok:
        code = BELOW if got.matched else NO_MATCH
        return _refuse("retrieve", code, f"Nothing recalled: {got.reason}. Noting the failure takes "
                                         f"{got.latency_s:.4f} s.", d, **fields)
    b = got.breakdown
    said = (f"Recalled {got.name!r}: activation {b['activation']:.4f} (base level {b['base_level']:.4f} from "
            f"{b['presentations']} presentations, spreading {b['spreading']:.4f}, noise {b['noise']:.4f}), above the "
            f"threshold {params.threshold:g}, in {got.latency_s:.4f} s. The retrieval is recorded as a presentation.")
    return _answer("retrieve", RETRIEVED, said, d, chunk=got.chunk, **fields)


def add_production(store: mem.Memory, state: mem.State, d: CogArchActrInput) -> dict[str, Any]:
    if not mem.NAME.fullmatch(d.name):
        return _refuse("add_production", INPUT, "give name: lower-case letters, digits, - and _", d)
    problems = mem.production_problems(d.conditions, d.actions)
    if problems:
        return _refuse("add_production", INVALID, "The production is not valid: " + "; ".join(problems[:5]) + ".", d,
                       problems=problems)
    existing = state.productions.get(d.name)
    if existing is not None:
        if existing.conditions != d.conditions or existing.actions != d.actions:
            return _refuse("add_production", PRODUCTION_EXISTS, f"a different production is already kept as "
                                                                f"{d.name!r}", d)
        return _answer("add_production", PRODUCTION_KEPT, f"{d.name!r} was already kept, with utility "
                                                          f"{existing.utility:.4f}; nothing changed.", d,
                       new=False, utility=existing.utility)
    if len(state.productions) >= mem.MAX_PRODUCTIONS:
        return _refuse("add_production", "G6_E_ACTR_FULL", f"the memory holds MAX_PRODUCTIONS "
                                                           f"{mem.MAX_PRODUCTIONS} productions already", d)
    state.productions[d.name] = mem.Production(conditions=d.conditions, actions=d.actions, utility=d.utility)
    store.save(state)
    return _answer("add_production", PRODUCTION_KEPT, f"Kept the production {d.name!r} with utility {d.utility:g}.",
                   d, new=True, utility=d.utility,
                   evidence=[{"kind": "memory", "path": str(store.path), "productions": len(state.productions)}])


def run(store: mem.Memory, state: mem.State, d: CogArchActrInput) -> dict[str, Any]:
    if "isa" not in d.goal:
        return _refuse("run", INPUT, "give goal: the goal chunk, with isa and its slots", d)
    if not d.goal_test:
        return _refuse("run", INPUT, "give goal_test: the slots the goal buffer must hold when the goal is reached; "
                                     "it is the check the run is judged by", d)
    if not state.productions:
        return _refuse("run", STUCK, "the memory keeps no productions to fire", d)
    params, now = _params(d), store.now(state)
    rng, drawn = mem.rng_for(state, params)
    result = mem.run(state, d.goal, d.goal_test, params, now, rng, max_cycles=d.max_cycles,
                     time_limit_s=d.time_limit_s)
    store.save(state)
    evidence = [_params_evidence(params), {"kind": "noise", **drawn},
                {"kind": "goal_test", "by": "the caller's goal test against the final goal buffer",
                 "goal_test": d.goal_test, "passed": result.status == "goal_test_passed"}]
    fields = {"status": result.status, "cycles": len(result.trace), "trace": result.trace, "goal": result.goal,
              "model_time_s": result.ended - result.started, "started_at": result.started, "evidence": evidence}
    if result.status == "goal_test_passed":
        return _answer("run", REACHED, f"The goal test passed after {len(result.trace)} cycles "
                                       f"({result.ended - result.started:.3f} s of model time).", d, **fields)
    if result.status == "cap":
        limit = d.max_cycles if result.cap == "max_cycles" else d.time_limit_s
        return _refuse("run", CAP, f"The run stopped at its cap: {result.cap} {limit:g}, before the goal test passed.",
                       d, cap=result.cap, limit=limit, **fields)
    code, why = {"no_production_matches": (STUCK, "no production matches the buffers"),
                 "goal_cleared": (CLEARED, "a production cleared the goal")}[result.status]
    return _refuse("run", code, f"The run stopped after {len(result.trace)} cycles: {why}, and the goal test did not "
                                "pass.", d, **fields)


def reward(store: mem.Memory, state: mem.State, d: CogArchActrInput) -> dict[str, Any]:
    if d.reward is None:
        return _refuse("reward", INPUT, "give reward: the amount, in the same units as time (seconds) are taken from "
                                        "it", d)
    if not state.pending:
        return _refuse("reward", NOTHING, "no production has been used since the last reward", d)
    updates = mem.reward(state, d.reward, d.alpha)
    store.save(state)
    said = (f"Applied a reward of {d.reward:g} (the caller's, kept as given) to {len(updates)} uses with alpha "
            f"{d.alpha:g}.")
    return _answer("reward", REWARDED, said, d, updates=updates,
                   evidence=[{"kind": "claim", "field": "reward", "value": d.reward, "checked": False},
                             {"kind": "utility_learning", "alpha": d.alpha, "uses": len(updates)}])


@block_contract(
    purpose="rank an agent's memories the way ACT-R's declarative memory does (recency, frequency and context), refuse "
            "a recall below the retrieval threshold, and run productions that consult that memory against a goal test",
    mitigates_failure_modes=("unmodelled_recall",),
    verification_method="the ACT-R equations computed from the presentations kept in the memory's file (each "
                        "presentation and utility written on every op), checked by known values worked by hand from "
                        "the ACT-R reference manual; a run judged only by the caller's goal test against the final "
                        "goal buffer",
)
@component_maturity(
    "beta",
    reason="ACT-R's base-level, spreading, noise, latency and utility equations with persisted learning and a small "
           "production matcher with variables and retrieval requests; parameters hand-set, not fitted to any data",
)
class CogArchActrBlock(AIBlock[CogArchActrInput, dict, dict]):
    """ACT-R declarative memory for agents, and a small procedural module. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: CogArchActrInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            store = mem.Memory(data.memory)
            with store_lock(store.path):
                state = store.load()
                run_op = {"add_chunk": add_chunk, "retrieve": retrieve, "add_production": add_production, "run": run,
                          "reward": reward}[data.op]
                return Result.ok(run_op(store, state, data))
        except StoreBusy as exc:
            return Result.ok(_refuse(data.op, "G6_E_ACTR_STORE_BUSY", str(exc), data))
        except OSError as exc:
            return Result.ok(_refuse(data.op, "G6_E_ACTR_STORE", str(exc)[:200], data))
        except ValueError as exc:
            return Result.ok(_refuse(data.op, "G6_E_ACTR_NUMERIC", str(exc), data))
        except mem.Refused as no:
            return Result.ok(_refuse(data.op, no.code, no.reason, data, **no.fields))


__all__ = ["COMPONENT", "SENSITIVE_OPS", "CogArchActrBlock", "CogArchActrInput"]
