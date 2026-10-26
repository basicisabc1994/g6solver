# SPDX-License-Identifier: Apache-2.0
"""DatalogBlock: the registry and MCP door (``g6_invoke``) to g6.datalog.

Each op calls one public function of the package and returns its labelled result unchanged, with ``component`` and
``op``; the envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``) comes from
:func:`g6.datalog.envelope.finish`, so a refusal (a program that does not parse, an unsafe rule, negation through
recursion, a size or time limit, Z3 asked for and missing) is a ``blocked-escalated`` result, never a raised error.
Unknown ops and unknown fields are refused by the typed input (``G6_E_INPUT_VALIDATION``), so no field can carry code:
the earlier version's ``run_code`` is not an op. Only assert_facts and reset write session state; both are
sensitive.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.datalog.language import MAX_TEXT
from g6.datalog.ops import DEFAULT_TIME_LIMIT_S, check_program, evaluate, explain, query

#: A shape limit on what is parsed. The language's own limits are smaller and are refused with their own codes.
STRUCTURAL_LIMIT = 100_000
_COMMON = "facts={relation: [[value, ...]]}, engine=auto|z3|python, time_limit (seconds, at most 60)"
_OPS: dict[str, str] = {
    "assert_facts": "assert_facts(session, program, facts, dry_run=True): check and retain asserted facts; program fixed per session",
    "reset": "reset(session, dry_run=True): clear asserted facts and retain the session program",
    "evaluate": f"evaluate(program, relations=[names], {_COMMON}): the relations a rule defines (or those named), from "
                "a pure-Python engine and Z3's Datalog engine that must agree, checked for closure, support and "
                "negation",
    "query": f"query(program, query='ancestor(alice, X)', {_COMMON}): each distinct answer to the goal, or to each "
             "query written in the program, with its derivation",
    "explain": f"explain(program, query='ancestor(alice, carol)', {_COMMON}): why a ground atom holds, as its "
               "derivation tree down to the facts, or that it is not derivable",
    "check": "check(program, facts): the parse, arity, safety and stratification report, with nothing evaluated",
}


class DatalogInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["evaluate", "query", "explain", "check", "assert_facts", "reset", "ops", "help"] = "help"
    session: str = Field("", max_length=200)
    dry_run: bool = True
    program: str = Field("", max_length=2 * MAX_TEXT)
    facts: dict[str, list[list[Any]]] = Field(default_factory=dict, max_length=STRUCTURAL_LIMIT)
    query: str = Field("", max_length=STRUCTURAL_LIMIT)
    relations: list[str] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    engine: Literal["auto", "z3", "python"] = "auto"
    time_limit: float = Field(DEFAULT_TIME_LIMIT_S, gt=0, allow_inf_nan=False)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _common(d: DatalogInput) -> dict[str, Any]:
    return {"facts": d.facts, "engine": d.engine, "time_limit": d.time_limit, "request_id": d.request_id,
            "run_id": d.run_id}


_RUN: dict[str, Callable[[DatalogInput], Any]] = {
    "evaluate": lambda d: evaluate(d.program, relations=d.relations or None, **_common(d)),
    "query": lambda d: query(d.program, d.query, **_common(d)),
    "explain": lambda d: explain(d.program, d.query, **_common(d)),
    "check": lambda d: check_program(d.program, facts=d.facts, request_id=d.request_id, run_id=d.run_id),
}


@component_maturity(
    "beta",
    reason="a strict Datalog on a pure-Python engine and Z3's Datalog engine, which must agree; every model checked in "
           "pure Python before it is reported; qualified-draft at best",
)
class DatalogBlock(AIBlock[DatalogInput, dict, dict]):
    """Work out what a Datalog program derives, and say what the answer rests on. Never ``verified``."""

    def __init__(self, name: str = "datalog", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: DatalogInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        if data.session or data.op in ("assert_facts", "reset"):
            from g6.datalog.sessions import session_op
            result = session_op(data.op, data.session, program=data.program, facts=data.facts, goal=data.query,
                                relations=data.relations, engine=data.engine, time_limit=data.time_limit,
                                dry_run=data.dry_run, request_id=data.request_id, run_id=data.run_id)
        else:
            result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["DatalogBlock", "DatalogInput"]
