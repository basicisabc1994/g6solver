# SPDX-License-Identifier: Apache-2.0
"""LeanBlock: the registry and MCP door (``g6_invoke``) to g6.lean.

``check_proof``, ``lean_version`` and ``prove`` run Lean, a program that runs the caller's source (inside G6's
operating-system boundary where one is ready, ``g6.lean.runner``; elsewhere as you), so the package
lists them in ``__g6_sensitive_ops__`` and the registry refuses them over MCP unless the person who runs the server
lists them in ``G6_MCP_SENSITIVE``. ``prove`` and ``sketch`` use the model caller the block was built with
(``LeanBlock(llm=...)``, from the person's own code, or over MCP the model the person configured in their key file);
with none, ``prove`` uses the core tactics only and ``sketch`` answers ``G6_E_LEAN_NO_MODEL``. The Lean ops run from
Python
(``get_registry().invoke(...)``, or :func:`g6.lean.check_proof` and :func:`g6.lean.lean_version`). ``ops`` and ``help``
answer anywhere. Each Lean op returns its labelled result unchanged, with ``component`` and ``op``; the envelope
(``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``) comes from
:func:`g6.lean.envelope.finish`, so a refusal is a ``blocked-escalated`` result, never a raised error. Unknown ops and
unknown fields are refused by the typed input (``G6_E_INPUT_VALIDATION``), so no field can name an executable, a
Lake project, Mathlib, a download or a flag for Lean.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.llm import LLMCaller
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.lean import proof, runner, search
from g6.lean.envelope import INSTALL_LEAN, STANDARD_AXIOMS

#: Run Lean, out of process: run from Python, and over MCP only when the server's G6_MCP_SENSITIVE lists them.
SENSITIVE_OPS: tuple[str, ...] = ("check_proof", "lean_version", "prove")
_PY = (" (sensitive: it runs Lean, a program, on your machine; from Python, or over MCP when the server's "
       "G6_MCP_SENSITIVE lists it)")
_OPS: dict[str, str] = {
    "check_proof": "check_proof(source=one Lean 4 file, theorem=its full name, expected_statement=the statement as "
                   "Lean prints it (optional), time_limit (seconds, at most 600), memory_mb): the source is screened, "
                   "then Lean elaborates it and G6 reads Lean's own #check, #print and #print axioms on the theorem: "
                   "it must be declared in the source as a theorem, with no sorry and no axiom beyond "
                   f"{', '.join(STANDARD_AXIOMS)}; qualified-draft at best" + _PY,
    "lean_version": "lean_version(time_limit): which Lean would check, from lean --version; nothing is checked. "
                    + INSTALL_LEAN + _PY,
    "prove": f"prove(statement=as Lean prints it, preamble=your definitions (optional), budget=candidates, at most "
             f"{search.MAX_BUDGET}, deadline_s=the whole search, at most {search.MAX_DEADLINE_S:g} s, time_limit=each "
             f"check, memory_mb): G6 writes theorem {search.GOAL} : statement := proof for "
             f"each candidate, the core tactics {', '.join(search.CORE_TACTICS)} first, then proofs the block's "
             "injected model proposes; each is judged only by check_proof against your statement; the first it "
             "passes is the answer, qualified-draft at best" + _PY,
    "sketch": "sketch(statement): the block's injected model outlines a proof in words; a draft, Lean checks nothing "
              "(with no model given: G6_E_LEAN_NO_MODEL)",
}


class LeanInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["check_proof", "lean_version", "prove", "sketch", "ops", "help"] = "help"
    source: str = Field("", max_length=2 * proof.MAX_SOURCE_CHARS)
    theorem: str = Field("", max_length=2 * proof.MAX_NAME_CHARS)
    expected_statement: str = Field("", max_length=2 * proof.MAX_STATEMENT_CHARS)
    statement: str = Field("", max_length=2 * proof.MAX_STATEMENT_CHARS)
    preamble: str = Field("", max_length=proof.MAX_SOURCE_CHARS)
    budget: int = Field(search.DEFAULT_BUDGET, ge=1, le=search.MAX_BUDGET)
    deadline_s: float = Field(search.DEFAULT_DEADLINE_S, gt=0, le=search.MAX_DEADLINE_S, allow_inf_nan=False)
    time_limit: float | None = Field(None, gt=0, allow_inf_nan=False)
    memory_mb: int = Field(runner.DEFAULT_MEMORY_MB, ge=1)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


@component_maturity(
    "beta",
    reason="Lean 4 out of process on a screened source; a pass needs Lean's own report of a theorem declared in the "
           "source, with no sorry, only the standard axioms and, when given, the expected statement; over MCP only "
           "when the person running the server allows it (G6_MCP_SENSITIVE); qualified-draft at best",
)
class LeanBlock(AIBlock[LeanInput, dict, dict]):
    """Check a Lean 4 proof of a named theorem and say exactly what Lean reported. Never ``verified``."""

    def __init__(self, name: str = "lean", state: dict | None = None, *, llm: LLMCaller | None = None) -> None:
        super().__init__(name=name, state=state or {})
        self.llm = llm  # the person's own code's, or over MCP the one they configured; with none, no model is called

    def infer(self, data: LeanInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        ids = {"request_id": data.request_id, "run_id": data.run_id}
        if data.op in ("prove", "sketch"):
            if data.op == "sketch":
                found = search.sketch(data.statement, llm=self.llm, **ids)
            else:
                found = search.prove(data.statement, llm=self.llm, budget=data.budget, preamble=data.preamble,
                                     time_limit=runner.DEFAULT_TIME_LIMIT_S if data.time_limit is None
                                     else data.time_limit, deadline_s=data.deadline_s, memory_mb=data.memory_mb,
                                     **ids)
            return Result.ok({"component": self.name, "op": data.op, **found.model_dump(mode="json")})
        if data.op == "check_proof":
            result = proof.check_proof(data.source, data.theorem, expected_statement=data.expected_statement,
                                       time_limit=runner.DEFAULT_TIME_LIMIT_S if data.time_limit is None
                                       else data.time_limit, memory_mb=data.memory_mb, **ids)
        else:
            result = proof.lean_version(time_limit=proof.VERSION_TIME_LIMIT_S if data.time_limit is None
                                        else data.time_limit, **ids)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["LeanBlock", "LeanInput", "SENSITIVE_OPS"]
