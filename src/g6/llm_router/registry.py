# SPDX-License-Identifier: Apache-2.0
"""The cognitive tasks, and the committed table of model profiles: which models suit a task, which tasks suit a model.

Ported from the earlier router's task registry. Its hardware-fit pairing is not ported, so the use cases it shared
with that code are a local enum here (:class:`UseCase`), and its mapping of archive components to model tool
complexity is not ported either (it named components this library does not have).

The table is ``data/model_profiles.json``, read with :mod:`importlib.resources`. Its prices are US dollars per million
tokens as of :func:`table`'s ``prices_as_of``; its task scores were assigned by hand and are estimates, not
measurements. A price of :data:`UNKNOWN_PRICE` (``-1``) means variable or unknown: :meth:`ModelProfile.prices` gives it
as ``None``, never as a number, and a profile with no price at all is unknown, never free.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from enum import Enum
from functools import lru_cache
from importlib import resources
from typing import Any

#: A price the table does not know, or that varies per request.
UNKNOWN_PRICE = -1.0


class UseCase(str, Enum):
    """A broad kind of work, which groups the cognitive tasks."""

    GENERAL = "general"
    CODING = "coding"
    REASONING = "reasoning"
    CHAT = "chat"
    MULTIMODAL = "multimodal"
    EMBEDDING = "embedding"


class CognitiveTask(str, Enum):
    EMAIL_DRAFTING = "email_drafting"
    SCIENTIFIC_ANALYSIS = "scientific_analysis"
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    MATHEMATICAL_REASONING = "mathematical_reasoning"
    LEGAL_ANALYSIS = "legal_analysis"
    MEDICAL_REASONING = "medical_reasoning"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    DATA_EXTRACTION = "data_extraction"
    CREATIVE_WRITING = "creative_writing"
    INSTRUCTION_FOLLOWING = "instruction_following"
    TOOL_USE = "tool_use"
    LONG_CONTEXT_PROCESSING = "long_context_processing"
    EMBEDDING = "embedding"
    GENERAL_CHAT = "general_chat"


class Modality(str, Enum):
    TEXT = "text"
    VISION = "vision"
    IMAGE_GENERATION = "image_gen"
    AUDIO_INPUT = "audio_in"
    AUDIO_OUTPUT = "audio_out"
    CODE = "code"
    EMBEDDING = "embedding"
    MULTIMODAL_NATIVE = "multimodal_native"


TASK_TO_USE_CASE: dict[CognitiveTask, UseCase] = {
    CognitiveTask.EMAIL_DRAFTING: UseCase.CHAT,
    CognitiveTask.SCIENTIFIC_ANALYSIS: UseCase.REASONING,
    CognitiveTask.CODE_GENERATION: UseCase.CODING,
    CognitiveTask.CODE_REVIEW: UseCase.CODING,
    CognitiveTask.MATHEMATICAL_REASONING: UseCase.REASONING,
    CognitiveTask.LEGAL_ANALYSIS: UseCase.REASONING,
    CognitiveTask.MEDICAL_REASONING: UseCase.REASONING,
    CognitiveTask.SUMMARIZATION: UseCase.GENERAL,
    CognitiveTask.TRANSLATION: UseCase.GENERAL,
    CognitiveTask.DATA_EXTRACTION: UseCase.GENERAL,
    CognitiveTask.CREATIVE_WRITING: UseCase.CHAT,
    CognitiveTask.INSTRUCTION_FOLLOWING: UseCase.GENERAL,
    CognitiveTask.TOOL_USE: UseCase.GENERAL,
    CognitiveTask.LONG_CONTEXT_PROCESSING: UseCase.GENERAL,
    CognitiveTask.EMBEDDING: UseCase.EMBEDDING,
    CognitiveTask.GENERAL_CHAT: UseCase.CHAT,
}


def _price(value: float) -> float | None:
    return None if value < 0 else value


@dataclass(frozen=True)
class ModelProfile:
    model_id: str
    display_name: str
    provider: str
    family: str
    cost_tier: int
    task_scores: dict[str, float]
    modalities: tuple[str, ...] = ("text",)
    max_tool_complexity: int = 1
    context_window: int = 128_000
    max_output_tokens: int = 16_384
    is_local: bool = False
    parameter_count_b: float | None = None
    specializations: tuple[str, ...] = ()
    pricing_prompt_per_m: float = UNKNOWN_PRICE
    pricing_completion_per_m: float = UNKNOWN_PRICE
    zdr_available: bool = False
    distillable: bool = False
    reasoning_efforts: tuple[str, ...] = ()

    @property
    def price_known(self) -> bool:
        """Both prices are stated numbers (zero included); ``False`` when either is variable or unknown."""
        return self.pricing_prompt_per_m >= 0 and self.pricing_completion_per_m >= 0

    def prices(self) -> tuple[float | None, float | None]:
        """US dollars per million input and output tokens, each ``None`` when variable or unknown."""
        return _price(self.pricing_prompt_per_m), _price(self.pricing_completion_per_m)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "provider": self.provider,
            "family": self.family,
            "cost_tier": self.cost_tier,
            "task_scores": dict(self.task_scores),
            "modalities": list(self.modalities),
            "max_tool_complexity": self.max_tool_complexity,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "is_local": self.is_local,
            "parameter_count_b": self.parameter_count_b,
            "specializations": list(self.specializations),
            "pricing_prompt_per_m": self.pricing_prompt_per_m,
            "pricing_completion_per_m": self.pricing_completion_per_m,
            "zdr_available": self.zdr_available,
            "distillable": self.distillable,
            "reasoning_efforts": list(self.reasoning_efforts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelProfile:
        return cls(
            model_id=data["model_id"],
            display_name=data["display_name"],
            provider=data["provider"],
            family=data["family"],
            cost_tier=data["cost_tier"],
            task_scores=dict(data["task_scores"]),
            modalities=tuple(data.get("modalities", ("text",))),
            max_tool_complexity=data.get("max_tool_complexity", 1),
            context_window=data.get("context_window", 128_000),
            max_output_tokens=data.get("max_output_tokens", 16_384),
            is_local=data.get("is_local", False),
            parameter_count_b=data.get("parameter_count_b"),
            specializations=tuple(data.get("specializations", ())),
            pricing_prompt_per_m=data.get("pricing_prompt_per_m", UNKNOWN_PRICE),
            pricing_completion_per_m=data.get("pricing_completion_per_m", UNKNOWN_PRICE),
            zdr_available=data.get("zdr_available", False),
            distillable=data.get("distillable", False),
            reasoning_efforts=tuple(data.get("reasoning_efforts", ())),
        )


def _local_name(model_id: str) -> str:
    return model_id.split("/", 1)[1] if model_id.startswith("ollama/") else model_id


class CognitiveTaskRegistry:
    def __init__(self, profiles: dict[str, ModelProfile] | None = None) -> None:
        self._profiles: dict[str, ModelProfile] = profiles or {}
        self._task_index: dict[str, list[tuple[str, float]]] | None = None

    def _invalidate_index(self) -> None:
        self._task_index = None

    def _ensure_index(self) -> dict[str, list[tuple[str, float]]]:
        if self._task_index is not None:
            return self._task_index
        idx: dict[str, list[tuple[str, float]]] = {}
        for task in CognitiveTask:
            scored = [(mid, prof.task_scores.get(task.value, 0.0)) for mid, prof in self._profiles.items()]
            idx[task.value] = sorted(((m, s) for m, s in scored if s > 0.0), key=lambda t: (-t[1], t[0]))
        self._task_index = idx
        return idx

    # --- Task-model queries ---

    def tasks_for_model(self, model_id: str) -> list[tuple[CognitiveTask, float]]:
        prof = self._profiles.get(model_id)
        if prof is None:
            return []
        result = [(task, prof.task_scores.get(task.value, 0.0)) for task in CognitiveTask]
        return sorted(((t, s) for t, s in result if s > 0.0), key=lambda t: -t[1])

    def models_for_task(self, task: CognitiveTask | str, *, min_score: float = 0.0,
                        provider: str | None = None) -> list[tuple[str, float]]:
        """``(model_id, score)`` for every model with a score above zero for the task, highest first."""
        task_val = task.value if isinstance(task, CognitiveTask) else task
        return [(mid, score) for mid, score in self._ensure_index().get(task_val, [])
                if score >= min_score and (provider is None or self._profiles[mid].provider == provider)]

    def best_model_for_use_case(self, use_case: UseCase, *, provider: str | None = None) -> str | None:
        tasks = [t for t, uc in TASK_TO_USE_CASE.items() if uc == use_case]
        if not tasks:
            return None
        best_mid: str | None = None
        best_score = -1.0
        for mid, prof in self._profiles.items():
            if provider is not None and prof.provider != provider:
                continue
            avg = sum(prof.task_scores.get(t.value, 0.0) for t in tasks) / len(tasks)
            if avg > best_score:
                best_score = avg
                best_mid = mid
        return best_mid

    def model_modalities(self, model_id: str) -> tuple[str, ...]:
        prof = self._profiles.get(model_id)
        return prof.modalities if prof is not None else ()

    # --- Profile queries ---

    def get_profile(self, model_id: str) -> ModelProfile | None:
        return self._profiles.get(model_id)

    def all_models(self) -> list[str]:
        return list(self._profiles.keys())

    def models_by_provider(self, provider: str) -> list[ModelProfile]:
        return [p for p in self._profiles.values() if p.provider == provider]

    def filtered(self, *, providers: Iterable[str] | None = None, local_only: bool = False,
                 installed: Iterable[str] | None = None) -> CognitiveTaskRegistry:
        """A new registry holding only the profiles of ``providers`` (all when ``None``), only local ones when
        ``local_only``, and, when ``installed`` names local models (``"llama3.1:8b"`` or ``"ollama/llama3.1:8b"``),
        only those of the local models. This registry is unchanged."""
        allowed = None if providers is None else set(providers)
        names = None if installed is None else {_local_name(n) for n in installed}
        keep = {mid: p for mid, p in self._profiles.items()
                if (allowed is None or p.provider in allowed) and (p.is_local or not local_only)
                and (names is None or p.provider != "ollama" or _local_name(mid) in names)}
        return CognitiveTaskRegistry(keep)

    # --- Mutations ---

    def register_model(self, profile: ModelProfile) -> None:
        self._profiles[profile.model_id] = profile
        self._invalidate_index()

    def update_task_score(self, model_id: str, task: CognitiveTask | str, score: float) -> None:
        prof = self._profiles.get(model_id)
        if prof is None:
            raise KeyError(f"Unknown model: {model_id}")
        task_val = task.value if isinstance(task, CognitiveTask) else task
        self._profiles[model_id] = _dc_replace(prof, task_scores={**prof.task_scores, task_val: score})
        self._invalidate_index()

    # --- Serialization ---

    def to_dict(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._profiles.values()]

    @classmethod
    def from_dict(cls, data: list[dict[str, Any]]) -> CognitiveTaskRegistry:
        profiles = {}
        for entry in data:
            p = ModelProfile.from_dict(entry)
            profiles[p.model_id] = p
        return cls(profiles)


@lru_cache(maxsize=1)
def _committed() -> tuple[dict[str, Any], tuple[ModelProfile, ...]]:
    text = resources.files(__package__).joinpath("data", "model_profiles.json").read_text(encoding="utf-8")
    raw = json.loads(text)
    return {k: v for k, v in raw.items() if k != "models"}, tuple(ModelProfile.from_dict(r) for r in raw["models"])


def table() -> dict[str, Any]:
    """What the committed table says about itself: ``about``, ``prices_as_of``, ``prices``, ``scores``, ``changes``
    and ``unknown_price`` (a fresh copy)."""
    return json.loads(json.dumps(_committed()[0]))


def default_registry() -> CognitiveTaskRegistry:
    """A new registry over the committed table (a fresh copy each call, so changing one changes no other)."""
    return CognitiveTaskRegistry({p.model_id: p for p in _committed()[1]})


__all__ = ["TASK_TO_USE_CASE", "UNKNOWN_PRICE", "CognitiveTask", "CognitiveTaskRegistry", "Modality", "ModelProfile",
           "UseCase", "default_registry", "table"]
