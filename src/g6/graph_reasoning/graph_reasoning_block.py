# SPDX-License-Identifier: Apache-2.0
"""GraphReasoningBlock: the registry and MCP door (``g6_invoke``) to g6.graph_reasoning.

Each op calls one public function of the package and returns its labelled result unchanged, with ``component`` and
``op``; the envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``) comes from
:func:`g6.graph_reasoning.envelope.finish`, so a refusal (a model that does not parse, an interval outside [0, 1], an
unsafe rule, a conflict, a size or time limit, PyReason asked for and missing) is a ``blocked-escalated`` result, never
a raised error. Unknown ops and unknown fields are refused by the typed input (``G6_E_INPUT_VALIDATION``), so no field
can carry code or a path: the earlier version's ``run_code`` is not an op. No op runs code, writes a file or reaches
the network, so none is sensitive.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.graph_reasoning.language import MAX_TEXT
from g6.graph_reasoning.ops import DEFAULT_TIME_LIMIT_S, check_model, explain, query, reason

#: A shape limit on what is parsed. The language's own limits are smaller and are refused with their own codes.
STRUCTURAL_LIMIT = 100_000
_COMMON = ("graph={nodes: [...], edges: [[source, target], ...]}, facts=[{label, node or edge, bound}], "
           "engine=auto|pyreason|python, time_limit (seconds, at most 60)")
_OPS: dict[str, str] = {
    "reason": f"reason(program, labels=[names], {_COMMON}): the truth interval [lower, upper] of every label on the "
              "graph's nodes and edges that facts and annotated rules (thresholds, annotation functions) give, from a "
              "pure-Python fixpoint over exact intervals, and PyReason too when it imports and evaluates the model "
              "the same way; checked for closure, support and soundness",
    "query": f"query(program, atom='popular(alice)', {_COMMON}): the interval of one label on one node or edge, with "
             "the steps that set its lower and upper bounds",
    "explain": f"explain(program, atom='popular(alice)', {_COMMON}): why an interval holds, as its derivation down to "
               "the facts, or why it stayed unknown",
    "check": "check(program, graph, facts): the parse, interval, arity, safety and stratification report, and whether "
             "PyReason could cross-check the model, with nothing evaluated",
}


class GraphReasoningInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["reason", "query", "explain", "check", "ops", "help"] = "help"
    program: str = Field("", max_length=2 * MAX_TEXT)
    graph: dict[str, list[Any]] = Field(default_factory=dict, max_length=2)
    facts: list[dict[str, Any]] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    atom: str = Field("", max_length=STRUCTURAL_LIMIT)
    labels: list[str] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    engine: Literal["auto", "pyreason", "python"] = "auto"
    time_limit: float = Field(DEFAULT_TIME_LIMIT_S, gt=0, allow_inf_nan=False)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _common(d: GraphReasoningInput) -> dict[str, Any]:
    return {"graph": d.graph, "facts": d.facts, "engine": d.engine, "time_limit": d.time_limit,
            "request_id": d.request_id, "run_id": d.run_id}


_RUN: dict[str, Callable[[GraphReasoningInput], Any]] = {
    "reason": lambda d: reason(d.program, labels=d.labels or None, **_common(d)),
    "query": lambda d: query(d.program, d.atom, **_common(d)),
    "explain": lambda d: explain(d.program, d.atom, **_common(d)),
    "check": lambda d: check_model(d.program, graph=d.graph, facts=d.facts, request_id=d.request_id, run_id=d.run_id),
}


@component_maturity(
    "beta",
    reason="annotated logic over graphs in the style of PyReason, on a pure-Python fixpoint over exact intervals; every "
           "interpretation checked for closure, support and soundness before it is reported; PyReason as a second "
           "engine when it imports, which PyReason 3.7.0 does not on this library's Python; qualified-draft at best",
)
class GraphReasoningBlock(AIBlock[GraphReasoningInput, dict, dict]):
    """Work out the truth intervals a graph's facts and rules give, and say what each rests on. Never ``verified``."""

    def __init__(self, name: str = "graph_reasoning", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: GraphReasoningInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["GraphReasoningBlock", "GraphReasoningInput"]
