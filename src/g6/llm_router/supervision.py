# SPDX-License-Identifier: Apache-2.0
"""Supervision by cost tier: a dearer or more capable model checks the work of a cheaper one.

Ported from the earlier router. A model's tier is its ``cost_tier`` in the committed table: free local, free cloud,
paid and cheap, paid and premium. For a task, a model supervises another only when it scores at least as high on the
task, and its tier is higher, or the tiers are equal and it handles more complex tool use and scores higher on the
task: a higher tier alone is not enough (a dearer model that does the task worse is no check on it). With no task, the
tier and the tool use decide. A specialist (:data:`DEFAULT_SPECIALISTS`) supervises a
task's workers up to a tier, when the registry holds it. Every pick reads only the table: nothing here calls a model.

Changed from the earlier router: a model the table does not know is refused (:func:`get_tier` raises ``KeyError``),
where it was given the free local tier, so an unknown, possibly paid, model read as free. A specialist is taken only
when it can check its worker (a higher tier, or the same tier and a higher score for the task) and is not already in the
chain; otherwise the general rule picks (LLM_ROUTER-02: on the committed table a specialist rule pointed back to the
chain's own worker, so the code chain stopped at two links). And with a task, a model with no score for it supervises
nothing (a chat model does not check an embedding model's work), and a dearer model that scores lower on the task
supervises nothing either (on the committed table a paid coder scoring 0.75 checked a local one scoring 0.78). Not
ported:
``select_optimal_model``, which estimated a model's accuracy from a constant of the earlier codebase's theory module.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from g6.llm_router.registry import CognitiveTask, CognitiveTaskRegistry


class ModelTier(IntEnum):
    COST_FREE_LOCAL = 0
    COST_FREE_CLOUD = 1
    COST_PAID_CHEAP = 2
    COST_PAID_PREMIUM = 3


def get_tier(model_id: str, registry: CognitiveTaskRegistry) -> ModelTier:
    """The model's cost tier; ``KeyError`` for a model the table does not know (never assumed free)."""
    prof = registry.get_profile(model_id)
    if prof is None:
        raise KeyError(f"{model_id} is not in the model table, so its cost tier is unknown")
    return ModelTier(prof.cost_tier)


@dataclass(frozen=True)
class Specialist:
    """``specialist`` supervises ``task`` for any worker at or below ``max_worker_tier``."""

    specialist: str
    task: CognitiveTask
    max_worker_tier: ModelTier = ModelTier.COST_PAID_CHEAP


#: The earlier router's specialists, unchanged. OpenRouter stopped serving a free Qwen3 Coder endpoint before
#: 2026-08-16, so the code specialist for free local workers is a paid model: its supervision says it needs a paid API.
DEFAULT_SPECIALISTS: tuple[Specialist, ...] = (
    Specialist("ollama/qwen2.5-coder:32b", CognitiveTask.CODE_GENERATION, ModelTier.COST_PAID_CHEAP),
    Specialist("ollama/qwen2.5-coder:32b", CognitiveTask.CODE_REVIEW, ModelTier.COST_PAID_CHEAP),
    Specialist("ollama/deepseek-r1:32b", CognitiveTask.MATHEMATICAL_REASONING, ModelTier.COST_PAID_CHEAP),
    Specialist("openrouter/qwen/qwen3-coder", CognitiveTask.CODE_GENERATION, ModelTier.COST_FREE_LOCAL),
    Specialist("openrouter/nvidia/nemotron-3-super-120b-a12b:free", CognitiveTask.GENERAL_CHAT,
               ModelTier.COST_FREE_LOCAL),
    Specialist("openrouter/deepseek/deepseek-v4-flash", CognitiveTask.LONG_CONTEXT_PROCESSING,
               ModelTier.COST_FREE_CLOUD),
    Specialist("openrouter/deepseek/deepseek-v4-pro", CognitiveTask.CODE_GENERATION, ModelTier.COST_PAID_CHEAP),
    Specialist("openrouter/deepseek/deepseek-v4-pro", CognitiveTask.MATHEMATICAL_REASONING, ModelTier.COST_PAID_CHEAP),
)


@dataclass(frozen=True)
class SupervisionResult:
    supervisor: str
    worker: str
    task: CognitiveTask | None
    supervisor_tier: ModelTier
    worker_tier: ModelTier
    requires_paid_api: bool
    specialist_override: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "supervisor": self.supervisor,
            "worker": self.worker,
            "task": self.task.value if self.task else None,
            "supervisor_tier": self.supervisor_tier.name,
            "worker_tier": self.worker_tier.name,
            "requires_paid_api": self.requires_paid_api,
            "specialist_override": self.specialist_override,
        }


def _task_value(task: CognitiveTask | None) -> str:
    return task.value if task else CognitiveTask.GENERAL_CHAT.value


class SupervisionPolicy:
    def __init__(self, registry: CognitiveTaskRegistry, *,
                 specialists: Iterable[Specialist] | None = None) -> None:
        """``specialists`` defaults to :data:`DEFAULT_SPECIALISTS`; pass ``()`` for none."""
        self._registry = registry
        self._specialist_overrides: list[Specialist] = list(DEFAULT_SPECIALISTS if specialists is None
                                                            else specialists)

    def add_specialist_override(self, specialist: str, task: CognitiveTask, *,
                                max_worker_tier: ModelTier = ModelTier.COST_PAID_CHEAP) -> None:
        self._specialist_overrides.append(Specialist(specialist, task, max_worker_tier))

    def _score(self, model_id: str, task: CognitiveTask) -> float:
        return self._registry.get_profile(model_id).task_scores.get(task.value, 0.0)

    def _check_specialist(self, worker_model: str, task: CognitiveTask | None,
                          exclude: Iterable[str] = ()) -> str | None:
        """The first specialist for the task that can check the worker: in the registry, not in ``exclude`` (the
        chain so far), with a score for the task at least the worker's, and a higher tier or the same tier and a higher
        score."""
        if task is None:
            return None
        worker_tier, skip = get_tier(worker_model, self._registry), {worker_model, *exclude}
        for override in self._specialist_overrides:
            if override.task != task or worker_tier > override.max_worker_tier or override.specialist in skip:
                continue
            if self._registry.get_profile(override.specialist) is None:
                continue
            tier, score = get_tier(override.specialist, self._registry), self._score(override.specialist, task)
            worker_score = self._score(worker_model, task)
            if score > 0 and score >= worker_score and (tier > worker_tier or tier == worker_tier and
                                                        score > worker_score):
                return override.specialist
        return None

    def can_supervise(self, supervisor: str, worker: str, task: CognitiveTask | None = None) -> bool:
        if supervisor == worker:
            return False
        sup_tier = get_tier(supervisor, self._registry)
        wrk_tier = get_tier(worker, self._registry)
        if task is not None and (self._score(supervisor, task) <= 0
                                 or self._score(supervisor, task) < self._score(worker, task)):
            return False
        if sup_tier > wrk_tier:
            return True
        if sup_tier == wrk_tier:
            sup_prof = self._registry.get_profile(supervisor)
            wrk_prof = self._registry.get_profile(worker)
            if sup_prof.max_tool_complexity > wrk_prof.max_tool_complexity:
                if task is not None:
                    return sup_prof.task_scores.get(task.value, 0.0) > wrk_prof.task_scores.get(task.value, 0.0)
                return True
        return False

    def _best(self, candidates: Iterable[str], task: CognitiveTask | None) -> str | None:
        """The highest score for the task; a tie goes to the lower total price per million tokens."""
        best_mid: str | None = None
        best_score, best_cost = -1.0, float("inf")
        for mid in candidates:
            prof = self._registry.get_profile(mid)
            score = prof.task_scores.get(_task_value(task), 0.0)
            prompt, completion = prof.prices()
            cost = (prompt + completion) if prompt is not None and completion is not None else float("inf")
            if score > best_score or (score == best_score and cost < best_cost):
                best_mid, best_score, best_cost = mid, score, cost
        return best_mid

    def get_supervisor(self, worker_model: str, task: CognitiveTask | None = None, *,
                       exclude: Iterable[str] = ()) -> SupervisionResult | None:
        """Who checks ``worker_model``'s work: a specialist that can, else the best model that can supervise it; never
        one in ``exclude``."""
        exclude = set(exclude)
        worker_tier = get_tier(worker_model, self._registry)
        specialist = self._check_specialist(worker_model, task, exclude)
        if specialist is not None:
            sup_tier = get_tier(specialist, self._registry)
            return SupervisionResult(supervisor=specialist, worker=worker_model, task=task, supervisor_tier=sup_tier,
                                     worker_tier=worker_tier, requires_paid_api=sup_tier >= ModelTier.COST_PAID_CHEAP,
                                     specialist_override=True)
        best_mid = self._best((m for m in self._registry.all_models()
                               if m not in exclude and self.can_supervise(m, worker_model, task)), task)
        if best_mid is None:
            return None
        sup_tier = get_tier(best_mid, self._registry)
        return SupervisionResult(supervisor=best_mid, worker=worker_model, task=task, supervisor_tier=sup_tier,
                                 worker_tier=worker_tier, requires_paid_api=sup_tier >= ModelTier.COST_PAID_CHEAP)

    def get_worker(self, supervisor_model: str, task: CognitiveTask | None = None) -> SupervisionResult | None:
        sup_tier = get_tier(supervisor_model, self._registry)
        best_mid = self._best((m for m in self._registry.all_models() if self.can_supervise(supervisor_model, m, task)),
                              task)
        if best_mid is None:
            return None
        return SupervisionResult(supervisor=supervisor_model, worker=best_mid, task=task, supervisor_tier=sup_tier,
                                 worker_tier=get_tier(best_mid, self._registry),
                                 requires_paid_api=sup_tier >= ModelTier.COST_PAID_CHEAP)

    def get_supervision_chain(self, task: CognitiveTask | None = None) -> list[str]:
        """From the cheapest, best-scoring worker up: each model supervises the one before it (at most 11)."""
        all_models = self._registry.all_models()
        if not all_models:
            return []

        def _sort_key(m: str) -> tuple[int, float, str]:
            return get_tier(m, self._registry), -self._registry.get_profile(m).task_scores.get(_task_value(task), 0.0), m

        current = min(all_models, key=_sort_key)
        chain, seen = [current], {current}
        for _ in range(10):
            result = self.get_supervisor(current, task, exclude=seen)
            if result is None or result.supervisor in seen:
                break
            chain.append(result.supervisor)
            seen.add(result.supervisor)
            current = result.supervisor
        return chain


__all__ = ["DEFAULT_SPECIALISTS", "ModelTier", "Specialist", "SupervisionPolicy", "SupervisionResult", "get_tier"]
