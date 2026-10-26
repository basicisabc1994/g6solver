# SPDX-License-Identifier: Apache-2.0
"""LLMRouterBlock: the registry and MCP door (``g6_invoke``) to g6.llm_router.

Each op calls one public function of the package and returns its labelled result unchanged, with ``component`` and
``op``; the envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``) comes from
:mod:`g6.llm_router.envelope`, so a refusal (no model for the filter, an unknown model, a price that is not known, a
server that cannot be reached) is a ``blocked-escalated`` result, never a raised error. Unknown ops and unknown fields
are refused by the typed input (``G6_E_INPUT_VALIDATION``), as is an op missing the field it needs.

The ops that read only the committed table are callable over MCP. :data:`SENSITIVE_OPS` reach the network or change
the machine, so the package lists them in ``__g6_sensitive_ops__`` and the registry refuses them over MCP unless the
person who runs the server lists them in ``G6_MCP_SENSITIVE``; they run from Python (``get_registry().invoke(...)``, or
the functions in :mod:`g6.llm_router.providers`). No field names an address: the Ollama ops reach the local service
the package declares, and the catalogue its one public URL.

The block's contract (:func:`~g6.core.contract_decorator.block_contract`) names the failure modes it guards against
and every refusal code it can give.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from g6.core.ai_block import AIBlock
from g6.core.block_contract import FailureMode, FailureType
from g6.core.contract_decorator import block_contract
from g6.core.llm import PROVIDERS
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.core.state_surface import StateSurface
from g6.llm_router import advice, envelope, providers
from g6.llm_router.advice import LOCAL_ONLY
from g6.llm_router.outcomes import MAX_ROWS, OutcomeStore
from g6.llm_router.registry import CognitiveTask

#: Reach the network or change the machine: run from Python, and over MCP only when the server's G6_MCP_SENSITIVE
#: lists them.
SENSITIVE_OPS: tuple[str, ...] = ("list_local_models", "pull_model", "delete_model", "fetch_openrouter_catalogue")
_PY = " (sensitive: it {}; from Python, or over MCP when the server's G6_MCP_SENSITIVE lists it)"
_OPS: dict[str, str] = {
    "list_tasks": "list_tasks(): the cognitive tasks a model can be ranked for, each with its kind of work and how "
                  "many models in the table have a score for it",
    "models_for_task": "models_for_task(task, providers=[ollama|anthropic|openai|openrouter], local_only, min_score, "
                       "limit): the models ranked for a task from the committed table, each with the reason for its "
                       "rank, and the price's age",
    "route": "route(task, providers=[ollama] by default, fallbacks, installed=[local model names], local_first, "
             "use_outcomes, seed): one model and an ordered fallback chain, drawn only from the providers you allow; "
             "with use_outcomes, the outcomes recorded for the task tilt each score by at most 0.05",
    "supervision_chain": "supervision_chain(task, providers): from the cheapest worker up, each model able to check "
                         "the work of the one below it (at least its score for the task, and a higher cost tier or the "
                         "same tier and a higher score), and which steps need a paid API",
    "model_profile": "model_profile(model): one model's committed profile, with the tasks it has a score for",
    "estimate_cost": "estimate_cost(model, input_tokens, output_tokens): a cost estimate from the committed prices, "
                     "with their date and age, under a stale-price warning when they are old; a variable or unknown "
                     "price gives no estimate",
    "record_outcome": "record_outcome(task, model, success, latency_ms, cost_usd, reviewer_verdict, "
                      "human_correction): record how a model did on a task, as your report, under the G6 home; G6 "
                      "checks none of it",
    "outcome_stats": "outcome_stats(task): the outcomes recorded for a task, per model, with how often a reviewer's "
                     "verdict disagreed with the reported success and how many a person corrected; it ranks nothing",
    "list_local_models": "list_local_models(timeout): the models installed in your local Ollama"
                         + _PY.format("reaches the local Ollama server"),
    "pull_model": "pull_model(model, timeout): download a model into your local Ollama"
                  + _PY.format("changes the machine"),
    "delete_model": "delete_model(model, timeout): remove a model from your local Ollama"
                    + _PY.format("changes the machine"),
    "fetch_openrouter_catalogue": "fetch_openrouter_catalogue(timeout): OpenRouter's live models and prices, compared "
                                  "with the committed table, which it never changes" + _PY.format("reaches the network"),
}
_NEEDS = {"models_for_task": ("task",), "route": ("task",), "supervision_chain": ("task",), "model_profile": ("model",),
          "estimate_cost": ("model",), "pull_model": ("model",), "delete_model": ("model",),
          "record_outcome": ("task", "model", "success"), "outcome_stats": ("task",)}

Provider = Literal[PROVIDERS]  # type: ignore[valid-type] - the providers g6.core.llm builds
ModelName = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")]


class LLMRouterInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["list_tasks", "models_for_task", "route", "supervision_chain", "model_profile", "estimate_cost",
                "record_outcome", "outcome_stats", "list_local_models", "pull_model", "delete_model",
                "fetch_openrouter_catalogue", "ops", "help"] = "help"
    task: CognitiveTask | None = None
    model: str = Field("", max_length=200)
    providers: list[Provider] | None = Field(None, min_length=1, max_length=len(PROVIDERS))
    local_only: bool = False
    min_score: float = Field(0.0, ge=0, le=1, allow_inf_nan=False)
    limit: int = Field(10, ge=1, le=100)
    fallbacks: int = Field(2, ge=0, le=10)
    local_first: bool = True
    installed: list[ModelName] | None = Field(None, max_length=1000)
    input_tokens: int = Field(0, ge=0, le=10**10)
    output_tokens: int = Field(0, ge=0, le=10**10)
    use_outcomes: bool = False
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    success: bool | None = None
    latency_ms: float = Field(0.0, ge=0, le=86_400_000, allow_inf_nan=False)
    cost_usd: float = Field(0.0, ge=0, le=1_000_000, allow_inf_nan=False)
    reviewer_verdict: str = Field("", max_length=200)
    human_correction: str = Field("", max_length=2000)
    timeout: float = Field(0.0, ge=0, le=3600, allow_inf_nan=False)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @model_validator(mode="after")
    def _has_what_the_op_needs(self) -> LLMRouterInput:
        for needed in _NEEDS.get(self.op, ()):
            if getattr(self, needed) in (None, ""):
                raise ValueError(f"{self.op} needs {needed}")
        if self.op in ("pull_model", "delete_model") and not providers.is_model_name(self.model):
            raise ValueError(f"{self.model!r} is not an Ollama model name")
        return self


def _ids(d: LLMRouterInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _net(d: LLMRouterInput) -> dict[str, Any]:
    """The timeout, when given (otherwise each function's own default)."""
    return {"timeout": d.timeout} if d.timeout else {}


_RUN: dict[str, Callable[[LLMRouterInput], Any]] = {
    "list_tasks": lambda d: advice.list_tasks(**_ids(d)),
    "models_for_task": lambda d: advice.models_for_task(d.task, providers=d.providers, local_only=d.local_only,
                                                        min_score=d.min_score, limit=d.limit, **_ids(d)),
    "route": lambda d: advice.route(d.task, providers=LOCAL_ONLY if d.providers is None else d.providers,
                                    fallbacks=d.fallbacks, installed=d.installed, local_first=d.local_first,
                                    outcomes=OutcomeStore() if d.use_outcomes else None, seed=d.seed, **_ids(d)),
    "supervision_chain": lambda d: advice.supervision_chain(d.task, providers=d.providers, **_ids(d)),
    "model_profile": lambda d: advice.model_profile(d.model, **_ids(d)),
    "estimate_cost": lambda d: advice.estimate_cost(d.model, d.input_tokens, d.output_tokens, **_ids(d)),
    "record_outcome": lambda d: advice.record_outcome(d.task, d.model, bool(d.success), latency_ms=d.latency_ms,
                                                      cost_usd=d.cost_usd, reviewer_verdict=d.reviewer_verdict,
                                                      human_correction=d.human_correction, **_ids(d)),
    "outcome_stats": lambda d: advice.outcome_stats(d.task, **_ids(d)),
    "list_local_models": lambda d: providers.list_local_models(**_net(d), **_ids(d)),
    "pull_model": lambda d: providers.pull_model(d.model, **_net(d), **_ids(d)),
    "delete_model": lambda d: providers.delete_model(d.model, **_net(d), **_ids(d)),
    "fetch_openrouter_catalogue": lambda d: providers.fetch_openrouter_catalogue(**_net(d), **_ids(d)),
}


@block_contract(
    purpose="Pick a language model for a kind of task from G6's committed table, local first, with an ordered "
            "fallback chain drawn only from the providers you allow, a supervision chain by cost tier, and cost "
            "estimates that state the date and age of their prices; qualified-draft at best.",
    outputs=("RouterResult",),
    good_at=("a local-first route that never reaches a provider you did not allow",
             "a cost estimate with its price's date and age, a stale-price warning, or none when the price is unknown",
             "a g6.core.llm FallbackCaller over the route, recording which model answered and why others failed"),
    bad_at=("knowing which model is better on your task: the task scores were assigned by hand, never measured",
            "knowing today's prices: the committed table is dated, and only the live catalogue says what changed",
            "knowing a model is installed or reachable: a route reads only the table"),
    known_failure_modes=(
        FailureMode("hand_assigned_scores", severity="medium", failure_type=FailureType.UNVERIFIED_OUTPUT.value,
                    description="a ranking rests on scores assigned by hand in an earlier router"),
        FailureMode("dated_prices", severity="medium", failure_type=FailureType.UNVERIFIED_OUTPUT.value,
                    description="a price or cost rests on the committed table's dated prices"),
        FailureMode("provider_unreachable", severity="low", failure_type=FailureType.BACKEND_UNAVAILABLE.value,
                    description="a model server or the live catalogue cannot be reached, or answers with an error"),
        FailureMode("reported_outcomes", severity="medium", failure_type=FailureType.UNVERIFIED_OUTPUT.value,
                    description="recorded outcomes are callers' reports G6 never checks; they tilt a route by at most "
                                "0.05, and only when it asks"),
    ),
    mitigates_failure_modes=("unchosen_model", "stale_price", "poor_fit_route"),
    state_surface=StateSurface(persistence="global", queryable_via="ledger",
                               retention=f"the latest {MAX_ROWS} outcomes"),
    verification_method="none: every answer is qualified-draft at best and says what it rests on",
    review_level="user_review",
    forbidden_claims=("verified", "a measured score", "a current price"),
    escalation_triggers=(*envelope.REFUSALS, envelope.STALE_PRICE),
)
@component_maturity(
    "beta",
    reason="routes from a committed table of hand-assigned task scores and prices dated 2026-08-16; live calls go "
           "only to providers you allow; qualified-draft at best",
)
class LLMRouterBlock(AIBlock[LLMRouterInput, dict, dict]):
    """Pick a model for a task and say what the pick rests on. Never ``verified``."""

    def __init__(self, name: str = "llm_router", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: LLMRouterInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["SENSITIVE_OPS", "LLMRouterBlock", "LLMRouterInput"]
