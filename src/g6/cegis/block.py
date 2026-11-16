# SPDX-License-Identifier: Apache-2.0
"""CegisBlock: the registry and MCP door (``g6_invoke``) to g6.cegis (agentic override kernel design 10.8).

One op, ``synthesize``: fill a sketch's holes so the function agrees with a table of inputs and outputs (the oracle is
data, ``TableOracle``, because a callable cannot come over MCP), checked on a domain: the one given, or the product of
the values each argument takes in the table. It runs every candidate (sensitive): only where the person allows
``cegis.synthesize``. A floating-point hole (``??f``) has no candidate grid and refuses with the limit cegis declares,
``G6_E_CEGIS_HOLE_UNSUPPORTED``; everything else is :func:`g6.cegis.cegis`'s own result and envelope, never
``verified``.

:func:`recheck` is cegis's re-check of a final answer for the agentic override kernel (design 10.3): the sketch filled
with the answer's control must be the answer's program, and that program must agree with the table on the domain
(``verify.check_candidate``, unchanged).
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from g6.cegis.hole import _HOLE_PATTERN, HoleUnsupported
from g6.cegis.loop import cegis
from g6.cegis.runner import default_runner
from g6.cegis.sketch import Sketch
from g6.cegis.verify import InputDomain, TableOracle, answered, check_candidate
from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result

Scalar = StrictBool | StrictInt | StrictFloat | StrictStr | None
_OPS: dict[str, str] = {
    "synthesize": "synthesize(sketch, cases=[{input: {arg: value}, output: value}], domain={arg: [values]}?, lo, hi, "
                  "fn_name, max_iterations, timeout_s): fill the sketch's holes (?? integer in [lo, hi], ??b boolean, "
                  '??{"a","b"} string; ??f float has no grid and refuses G6_E_CEGIS_HOLE_UNSUPPORTED) so the function '
                  "agrees with the table on the domain; runs every candidate (sensitive)",
}


class Case(BaseModel):
    """One row of the table: the function's arguments and what it must return."""

    model_config = ConfigDict(extra="forbid")

    input: dict[str, Scalar] = Field(max_length=32)
    output: Scalar


class CegisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["synthesize", "ops", "help"] = "help"
    sketch: str = Field("", max_length=20_000)
    cases: list[Case] = Field(default_factory=list, max_length=2000)
    domain: dict[str, list[Scalar]] | None = Field(None, max_length=32)
    lo: int = Field(-32, ge=-10_000, le=10_000)
    hi: int = Field(32, ge=-10_000, le=10_000)
    fn_name: str = Field("f", max_length=100)
    max_iterations: int = Field(20, ge=1, le=100)
    timeout_s: float = Field(60.0, gt=0, le=600, allow_inf_nan=False)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _table(data: CegisInput) -> tuple[TableOracle, InputDomain]:
    oracle = TableOracle.of([(case.input, case.output) for case in data.cases])
    if data.domain is not None:
        return oracle, InputDomain.of(data.domain)
    values: dict[str, list[Any]] = {}
    for case in data.cases:
        for name, value in case.input.items():
            seen = values.setdefault(name, [])
            if not any(type(v) is type(value) and v == value for v in seen):
                seen.append(value)
    return oracle, InputDomain.of(values)


def _synthesize(data: CegisInput) -> Result[dict]:
    if not data.sketch.strip() or not data.cases:
        return Result.fail("[G6_E_INPUT_VALIDATION] synthesize needs a sketch and at least one case")
    try:
        sketch = Sketch.from_src(data.sketch, data.lo, data.hi, fn_name=data.fn_name)
    except HoleUnsupported as exc:
        return Result.fail(str(exc))
    except ValueError as exc:
        return Result.fail(f"[G6_E_INPUT_VALIDATION] {exc}")
    try:
        oracle, domain = _table(data)
    except ValueError as exc:
        return Result.fail(f"[G6_E_INPUT_VALIDATION] {exc}")
    found = cegis(sketch, oracle, domain=domain, max_iterations=data.max_iterations, timeout_s=data.timeout_s)
    return Result.ok({"component": "cegis", "op": "synthesize",
                      **found.to_envelope(request_id=data.request_id, run_id=data.run_id),
                      "control": None if found.control is None else {str(k): v for k, v in found.control.items()},
                      "iterations": found.iterations, "reason": found.reason})


def _filled(src: str, control: Mapping[int, Any], lo: int, hi: int) -> str:
    """``src`` with each hole token replaced, left to right, by its control value's literal, each value checked against
    its token's kind and bounds without asking any part for a grid; ValueError when one does not fit."""
    out, at, index = [], 0, 0
    for match in _HOLE_PATTERN.finditer(src):
        token, value = match.group(), control.get(index)
        if token == "??b":
            fits = type(value) is bool
        elif token == "??f":
            fits = type(value) is float and math.isfinite(value) and lo <= value <= hi
        elif token.startswith("??{"):
            fits = type(value) is str and value in re.findall(r'"([^"]*)"', token[3:-1])
        else:
            fits = type(value) is int and lo <= value <= hi
        if not fits:
            raise ValueError(f"hole {index} ({token}) cannot take {value!r}")
        out += [src[at:match.start()], repr(value)]
        at, index = match.end(), index + 1
    if set(control) != set(range(index)):
        raise ValueError("the control does not name exactly the sketch's holes")
    return "".join([*out, src[at:]])


def recheck(params: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    """cegis's re-check of a final ``result`` of ``synthesize`` on ``params``, with no part substituted: ``passed``
    when the sketch filled with the result's control is the result's program and that program agrees with the table on
    every answered input of the domain (``verify.check_candidate``)."""
    data = CegisInput.model_validate({**dict(params), "op": "synthesize"})
    try:
        control = {int(k): v for k, v in dict(result.get("control") or {}).items()}
        program = _filled(data.sketch, control, data.lo, data.hi)
    except (TypeError, ValueError) as exc:
        return {"passed": False, "status": "mismatch", "reason": str(exc)[:300]}
    if program != result.get("program"):
        return {"passed": False, "status": "mismatch", "reason": "the program is not the sketch filled with the control"}
    oracle, domain = _table(data)
    inputs = domain.inputs()
    cases = answered(oracle, inputs)
    checked = check_candidate(Sketch.from_src(program, fn_name=data.fn_name), {}, cases,
                              skipped=len(inputs) - len(cases), runner=default_runner())
    return {"passed": checked.status == "no_counterexample", "status": checked.status, "compared": checked.compared,
            "skipped": checked.skipped, "reason": checked.reason}


@component_maturity(
    "beta",
    reason="counterexample-guided synthesis over a table, every candidate run in a confined child; a result holds only "
           "on the domain it names; qualified-draft at best",
)
class CegisBlock(AIBlock[CegisInput, dict, dict]):
    """Fill a sketch's holes from a table of examples, and say what the answer rests on. Never ``verified``."""

    def __init__(self, name: str = "cegis", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: CegisInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return _synthesize(data)


__all__ = ["Case", "CegisBlock", "CegisInput", "recheck"]
