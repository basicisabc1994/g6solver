# SPDX-License-Identifier: Apache-2.0
"""CausalBlock: the registry and MCP door (``g6_invoke``) to g6.causal.

Each op calls one public function of the package and returns its labelled result unchanged, with ``component`` and
``op``; the envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``) comes from
:func:`g6.causal.envelope.finish`, so a refusal (a graph that does not parse, a cycle, an unknown node, rows that do
not fit, an effect the graph does not identify, DoWhy missing for an estimate) is a ``blocked-escalated`` result,
never a raised error. Unknown ops and unknown fields are refused by the typed input (``G6_E_INPUT_VALIDATION``), so no
field can carry code or a file path: the earlier version's ``run_code`` is not an op, and data arrive as rows, never
as a path. No op runs code, writes a file or reaches the network, so none is sensitive.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.causal.estimation import DEFAULT_SIMULATIONS, LINEAR, estimate_effect
from g6.causal.graph import MAX_TEXT
from g6.causal.ops import adjustment_sets, backdoor_paths, d_separated, identify, instruments
from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result

#: A shape limit on what is parsed. Each op's own limits are smaller and are refused with their own codes.
STRUCTURAL_LIMIT = 100_000
_GRAPH = ("graph={nodes: [...], edges: [[cause, effect], ...], unobserved: [...]} or DOT text "
          "(digraph { Z -> T; T -> Y; U [observed=no]; })")
_COMMON = "engine=auto|dowhy|python, time_limit (seconds, at most 600)"
_OPS: dict[str, str] = {
    "d_separated": f"d_separated({_GRAPH}, x, y, given=[...], {_COMMON}): whether given d-separates x from y, with a "
                   "path left open when it does not; checked against DoWhy's d-separation",
    "backdoor_paths": f"backdoor_paths({_GRAPH}, treatment, outcome, given=[...], {_COMMON}): every path from the "
                      "treatment to the outcome that starts with an arrow into the treatment, each open or blocked",
    "adjustment_sets": f"adjustment_sets({_GRAPH}, treatment, outcome, {_COMMON}): every minimal set of observed "
                       "variables to control for (the backdoor criterion), each checked by DoWhy too",
    "instruments": f"instruments({_GRAPH}, treatment, outcome, {_COMMON}): every observed instrumental variable",
    "identify": f"identify({_GRAPH}, treatment, outcome, {_COMMON}): whether the causal effect is identifiable by "
                "the backdoor, frontdoor or instrumental-variable criterion, with the estimand written out, or why "
                "not; G6's answer and DoWhy's must agree",
    "estimate_effect": f"estimate_effect({_GRAPH}, data=[{{column: number or true/false}}, ...] inline rows, "
                       "treatment, outcome, method=backdoor.linear_regression|backdoor.propensity_score_stratification|"
                       "backdoor.propensity_score_weighting|iv.instrumental_variable|frontdoor.two_stage_regression, "
                       "refute=true, simulations (at most 100), seed, time_limit): the effect with DoWhy and its 95% "
                       "confidence interval, recomputed by G6 for linear regression and tried by DoWhy's placebo, "
                       "random-common-cause and data-subset refuters; needs pip install 'g6[causal] @ git+https://github.com/basicisabc1994/g6solver'",
}
Method = Literal["backdoor.linear_regression", "backdoor.propensity_score_stratification",
                 "backdoor.propensity_score_weighting", "iv.instrumental_variable", "frontdoor.two_stage_regression"]
Names = list[str] | str


class CausalInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["d_separated", "backdoor_paths", "adjustment_sets", "instruments", "identify", "estimate_effect",
                "ops", "help"] = "help"
    graph: Annotated[str, Field(max_length=2 * MAX_TEXT)] | dict[str, Any] | None = None
    x: Names = Field(default_factory=list)
    y: Names = Field(default_factory=list)
    given: list[str] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    treatment: str = Field("", max_length=200)
    outcome: str = Field("", max_length=200)
    data: list[dict[str, Any]] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    method: Method = LINEAR
    refute: bool = True
    simulations: int = DEFAULT_SIMULATIONS
    seed: int = 0
    engine: Literal["auto", "dowhy", "python"] = "auto"
    time_limit: float | None = Field(None, gt=0, allow_inf_nan=False)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _common(d: CausalInput, *, engine: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"request_id": d.request_id, "run_id": d.run_id}
    if engine:
        out["engine"] = d.engine
    if d.time_limit is not None:
        out["time_limit"] = d.time_limit
    return out


_RUN: dict[str, Callable[[CausalInput], Any]] = {
    "d_separated": lambda d: d_separated(d.graph, d.x, d.y, d.given, **_common(d)),
    "backdoor_paths": lambda d: backdoor_paths(d.graph, d.treatment, d.outcome, d.given, **_common(d)),
    "adjustment_sets": lambda d: adjustment_sets(d.graph, d.treatment, d.outcome, **_common(d)),
    "instruments": lambda d: instruments(d.graph, d.treatment, d.outcome, **_common(d)),
    "identify": lambda d: identify(d.graph, d.treatment, d.outcome, **_common(d)),
    "estimate_effect": lambda d: estimate_effect(d.graph, d.data, d.treatment, d.outcome, d.method,
                                                 refute=d.refute, simulations=d.simulations, seed=d.seed,
                                                 **_common(d, engine=False)),
}


@component_maturity(
    "beta",
    reason="exact graph criteria in pure Python, cross-checked against DoWhy; DoWhy's estimates recomputed by G6 for "
           "linear regression and tried by three refuters; qualified-draft at best, since data cannot confirm a "
           "causal graph",
)
class CausalBlock(AIBlock[CausalInput, dict, dict]):
    """Work out what a causal graph lets data answer, estimate the effect, and say what it rests on. Never
    ``verified``."""

    def __init__(self, name: str = "causal", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: CausalInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["CausalBlock", "CausalInput"]
