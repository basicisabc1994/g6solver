# SPDX-License-Identifier: Apache-2.0
"""PrologBlock: the registry and MCP door (``g6_invoke``) to g6.prolog.

``query``, ``prove``, ``check_program`` and ``swipl_version`` run SWI-Prolog, a program on your machine, so the package
lists them in ``__g6_sensitive_ops__`` and the registry refuses them over MCP unless the person who runs the server
lists them in ``G6_MCP_SENSITIVE``; they run from Python (``get_registry().invoke(...)``, or :func:`g6.prolog.query`
and the others). ``ops`` and ``help`` answer anywhere. Each SWI-Prolog op returns its labelled result unchanged, with
``component`` and ``op``; the envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``,
``run_id``) comes from :func:`g6.prolog.envelope.finish`, so a refusal is a ``blocked-escalated`` result, never a
raised error. Unknown ops and unknown fields are refused by the typed input (``G6_E_INPUT_VALIDATION``), so no field
can name an executable, a file to consult, a pack, a flag for SWI-Prolog or code to run in G6's process.

The program and the goal must keep to the restricted syntax (:mod:`g6.prolog`), which is not a security boundary.
SWI-Prolog runs inside G6's operating-system boundary where one is ready, and elsewhere as you, so run programs you do
not trust inside your own sandbox, such as a container or a virtual machine.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.prolog import ops, runner
from g6.prolog.envelope import INSTALL_SWIPL

#: Run SWI-Prolog, out of process: run from Python, and over MCP only when the server's G6_MCP_SENSITIVE lists them.
SENSITIVE_OPS: tuple[str, ...] = ("query", "prove", "check_program", "swipl_version")
_SENSITIVE = (" (sensitive: it runs SWI-Prolog, a program, on your machine; from Python, or over MCP when the server's "
              "G6_MCP_SENSITIVE lists it)")
_LIMITS = (f"max_solutions (at most {ops.MAX_SOLUTIONS}, default {ops.DEFAULT_MAX_SOLUTIONS}), time_limit (seconds, at "
           f"most {runner.MAX_TIME_LIMIT_S:g}), inference_limit (default {ops.DEFAULT_INFERENCE_LIMIT:,})")
_OPS: dict[str, str] = {
    "query": f"query(program=Prolog facts and rules, goal=one goal such as ancestor(tom, X), {_LIMITS}, memory_mb): "
             "the program is read clause by clause, never consulted (no directive runs), and the program and the goal "
             "must keep to the restricted syntax (the program's own predicates and a short allow-list, no variable or "
             "module-qualified goals); each answer's bindings, and exhausted, true only when the search finished "
             "below the cap; every answer re-checked in a second SWI-Prolog process; qualified-draft at best"
             + _SENSITIVE,
    "prove": "prove(program, goal, time_limit, inference_limit, memory_mb): whether the goal is derivable from the "
             "program (succeeded), with its first answer, re-checked in a second process; a failure means not "
             "derivable from this program under Prolog's closed world, not false; qualified-draft at best" + _SENSITIVE,
    "check_program": "check_program(program, time_limit, memory_mb): SWI-Prolog reads the program without consulting it "
                     "and reports the predicates it defines, and each syntax error, directive, refused clause and goal "
                     "outside the restricted syntax with its line and column; nothing is queried" + _SENSITIVE,
    "swipl_version": "swipl_version(time_limit): which SWI-Prolog would run a program, from swipl --version; nothing "
                     "is run on a program. " + INSTALL_SWIPL + _SENSITIVE,
}


class PrologInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["query", "prove", "check_program", "swipl_version", "ops", "help"] = "help"
    program: str = Field("", max_length=2 * ops.MAX_PROGRAM_CHARS)
    goal: str = Field("", max_length=2 * ops.MAX_GOAL_CHARS)
    max_solutions: int = Field(ops.DEFAULT_MAX_SOLUTIONS, ge=1)
    time_limit: float | None = Field(None, gt=0, allow_inf_nan=False)
    inference_limit: int = Field(ops.DEFAULT_INFERENCE_LIMIT, ge=1)
    memory_mb: int = Field(runner.DEFAULT_MEMORY_MB, ge=1)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


@component_maturity(
    "beta",
    reason="SWI-Prolog out of process on a program read clause by clause, never consulted, in a restricted syntax "
           "(the program's own predicates and a short allow-list) that is not a security boundary; bounded by "
           "inference, time and stack limits; every answer re-checked in a second process; qualified-draft at best",
)
class PrologBlock(AIBlock[PrologInput, dict, dict]):
    """Query a Prolog program with SWI-Prolog and re-check every answer. Never ``verified``."""

    def __init__(self, name: str = "prolog", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: PrologInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        ids = {"request_id": data.request_id, "run_id": data.run_id}
        seconds = runner.DEFAULT_TIME_LIMIT_S if data.time_limit is None else data.time_limit
        if data.op == "query":
            result = ops.query(data.program, data.goal, max_solutions=data.max_solutions, time_limit=seconds,
                               inference_limit=data.inference_limit, memory_mb=data.memory_mb, **ids)
        elif data.op == "prove":
            result = ops.prove(data.program, data.goal, time_limit=seconds, inference_limit=data.inference_limit,
                               memory_mb=data.memory_mb, **ids)
        elif data.op == "check_program":
            result = ops.check_program(data.program, time_limit=seconds, memory_mb=data.memory_mb, **ids)
        else:
            result = ops.swipl_version(time_limit=ops.VERSION_TIME_LIMIT_S if data.time_limit is None
                                       else data.time_limit, **ids)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["PrologBlock", "PrologInput", "SENSITIVE_OPS"]
