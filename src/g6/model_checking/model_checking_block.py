# SPDX-License-Identifier: Apache-2.0
"""ModelCheckingBlock: the registry and MCP door (``g6_invoke``) to g6.model_checking.

Each op calls one public function of the package and returns its labelled result unchanged, with ``component`` and
``op``; the envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``) comes from
:func:`g6.model_checking.envelope.finish`, so a refusal (a term outside the grammar, a vacuous model, a limit, Z3 or the
jar missing) is a ``blocked-escalated`` result, never a raised error. Unknown ops and unknown fields are refused by the
typed input (``G6_E_INPUT_VALIDATION``), so no field can carry code, name a jar or a download, or pass Java an option.

The symbolic ops run Z3 in process and are callable over MCP. :data:`SENSITIVE_OPS` run TLC or SANY, a program that
runs your spec (inside G6's operating-system boundary where one is ready, elsewhere as you), so the package lists them in ``__g6_sensitive_ops__`` and the registry refuses them over MCP
unless the person who runs the server lists them in ``G6_MCP_SENSITIVE``; they run from Python
(``get_registry().invoke(...)``, or :func:`g6.model_checking.tlc_check` and :func:`g6.model_checking.sany_parse`).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.model_checking import symbolic, tlc
from g6.model_checking.schema import STRUCTURAL_LIMIT, TEXT_LIMIT, StateModel

#: Run TLC or SANY, out of process: run from Python, and over MCP only when the server's G6_MCP_SENSITIVE lists them.
SENSITIVE_OPS: tuple[str, ...] = ("tlc_check", "sany_parse")
_MODEL = ("model={variables: {x: Int|Real|Bool|(_ BitVec n)}, init: term, transition: term over x and next_x, "
          "invariants: {name: term}}, all SMT-LIB")
_BOUND = "depth (steps, 1 to 50), time_limit (seconds, at most 60)"
_PY = (" (sensitive: it runs TLC, a program, on your spec; from Python, or over MCP when the server's "
       "G6_MCP_SENSITIVE lists it)")
_JAR = "the jar from G6_TLA2TOOLS_JAR, pinned by G6_TLA2TOOLS_SHA256 and never downloaded; Java from G6_JAVA or PATH"
_OPS: dict[str, str] = {
    "check_invariant": f"check_invariant({_MODEL}, {_BOUND}): bounded model checking; a state breaking an invariant "
                       "with its trace from an initial state, or none on paths of up to depth steps, re-checked",
    "prove_invariant": f"prove_invariant({_MODEL}, depth as the largest k): k-induction; k-inductive at k, a counterexample "
                       "from the initial states, or not inductive at k with the induction-step trace (not a "
                       "counterexample)",
    "check_deadlock": f"check_deadlock({_MODEL}, {_BOUND}): a reachable state with no successor, with its trace",
    "reachable": f"reachable({_MODEL}, goal=term, {_BOUND}): a trace to a state meeting the goal, or none within depth",
    "tlc_check": "tlc_check(spec=TLA+ module, config=text or specification, invariants=[names], properties=[names], "
                 f"constants={{name: value}}, check_deadlock, workers, time_limit (at most 600), heap_mb): TLC's "
                 f"verdict with its counts and trace; {_JAR}" + _PY,
    "sany_parse": f"sany_parse(spec, time_limit): SANY's syntax, semantic and level check, nothing model-checked; {_JAR}"
                  + _PY.replace("TLC", "SANY"),
}


class ModelCheckingInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["check_invariant", "prove_invariant", "check_deadlock", "reachable", "tlc_check", "sany_parse", "ops",
                "help"] = "help"
    model: StateModel = Field(default_factory=StateModel)
    goal: str = Field("", max_length=TEXT_LIMIT)
    depth: int = Field(symbolic.DEFAULT_DEPTH, ge=1)
    time_limit: float | None = Field(None, gt=0, allow_inf_nan=False)
    spec: str = Field("", max_length=2 * tlc.MAX_SPEC_CHARS)
    module: str = Field("", max_length=200)
    config: str = Field("", max_length=2 * tlc.MAX_CONFIG_CHARS)
    specification: str = Field("Spec", max_length=200)
    invariants: list[str] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    properties: list[str] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    constants: dict[str, str] = Field(default_factory=dict, max_length=STRUCTURAL_LIMIT)
    check_deadlock: bool = True
    workers: int = Field(1, ge=1)
    heap_mb: int = Field(tlc.DEFAULT_HEAP_MB, ge=1)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _common(d: ModelCheckingInput, default: float) -> dict[str, Any]:
    return {"time_limit": default if d.time_limit is None else d.time_limit, "request_id": d.request_id,
            "run_id": d.run_id}


def _symbolic(d: ModelCheckingInput) -> dict[str, Any]:
    return {"depth": d.depth, **_common(d, symbolic.DEFAULT_TIME_LIMIT_S)}


_RUN: dict[str, Callable[[ModelCheckingInput], Any]] = {
    "check_invariant": lambda d: symbolic.check_invariant(d.model, **_symbolic(d)),
    "prove_invariant": lambda d: symbolic.prove_invariant(d.model, **_symbolic(d)),
    "check_deadlock": lambda d: symbolic.check_deadlock(d.model, **_symbolic(d)),
    "reachable": lambda d: symbolic.reachable(d.model, d.goal, **_symbolic(d)),
    "tlc_check": lambda d: tlc.tlc_check(d.spec, module=d.module, config=d.config, specification=d.specification,
                                         invariants=d.invariants, properties=d.properties, constants=d.constants,
                                         check_deadlock=d.check_deadlock, workers=d.workers, heap_mb=d.heap_mb,
                                         **_common(d, tlc.DEFAULT_TIME_LIMIT_S)),
    "sany_parse": lambda d: tlc.sany_parse(d.spec, module=d.module, heap_mb=d.heap_mb,
                                           **_common(d, tlc.DEFAULT_TIME_LIMIT_S)),
}


@component_maturity(
    "beta",
    reason="symbolic models checked with Z3 in process, every trace and every claim re-checked on fresh solvers; TLC "
           "and SANY out of process from a jar pinned by SHA-256, over MCP only when the person running the server "
           "allows them (G6_MCP_SENSITIVE); qualified-draft at best",
)
class ModelCheckingBlock(AIBlock[ModelCheckingInput, dict, dict]):
    """Check a state machine for invariants, deadlocks and reachable goals, and say what the answer rests on. Never
    ``verified``."""

    def __init__(self, name: str = "model_checking", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: ModelCheckingInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["SENSITIVE_OPS", "ModelCheckingBlock", "ModelCheckingInput"]
