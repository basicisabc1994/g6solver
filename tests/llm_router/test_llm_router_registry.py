# SPDX-License-Identifier: Apache-2.0
"""g6.llm_router.registry: the cognitive tasks and the committed model table, which models suit a task and which tasks
suit a model. Ported from the earlier router's task registry tests (its hardware-fit and archive-component mapping
tests are not ported, because neither is), plus new checks that the committed table is honest about its own figures."""
from __future__ import annotations

import json
import math
from importlib import resources

import pytest

from g6.llm_router.registry import (
    TASK_TO_USE_CASE,
    UNKNOWN_PRICE,
    CognitiveTask,
    CognitiveTaskRegistry,
    Modality,
    ModelProfile,
    UseCase,
    default_registry,
    table,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_profile(
    model_id: str,
    provider: str = "openrouter",
    family: str = "test",
    cost_tier: int = 2,
    modalities: tuple[str, ...] = ("text",),
    max_tool_complexity: int = 3,
    task_scores: dict[str, float] | None = None,
    **kwargs,
) -> ModelProfile:
    scores = task_scores or {t.value: 0.5 for t in CognitiveTask if t != CognitiveTask.EMBEDDING}
    return ModelProfile(
        model_id=model_id,
        display_name=model_id,
        provider=provider,
        family=family,
        cost_tier=cost_tier,
        task_scores=scores,
        modalities=modalities,
        max_tool_complexity=max_tool_complexity,
        **kwargs,
    )


@pytest.fixture
def registry() -> CognitiveTaskRegistry:
    profiles = {
        "ollama/tiny-1b": _make_profile(
            "ollama/tiny-1b", provider="ollama", family="tiny",
            cost_tier=0, max_tool_complexity=1, modalities=("text",),
            is_local=True,
            task_scores={t.value: 0.25 for t in CognitiveTask if t != CognitiveTask.EMBEDDING},
        ),
        "ollama/test-7b": _make_profile(
            "ollama/test-7b", provider="ollama", family="llama",
            cost_tier=0, max_tool_complexity=2, modalities=("text",),
            is_local=True,
            task_scores={t.value: 0.55 for t in CognitiveTask if t != CognitiveTask.EMBEDDING},
        ),
        "ollama/coder-32b": _make_profile(
            "ollama/coder-32b", provider="ollama", family="qwen",
            cost_tier=0, max_tool_complexity=3, modalities=("text", "code"),
            is_local=True,
            task_scores={
                "code_generation": 0.78, "code_review": 0.75,
                "tool_use": 0.68, "general_chat": 0.55,
                **{t.value: 0.50 for t in CognitiveTask
                   if t.value not in ("code_generation", "code_review", "tool_use", "general_chat", "embedding")},
            },
        ),
        "openrouter/vision-model": _make_profile(
            "openrouter/vision-model", provider="openrouter", family="gemini",
            cost_tier=3, max_tool_complexity=4,
            modalities=("text", "vision", "audio_in", "multimodal_native"),
            task_scores={t.value: 0.88 for t in CognitiveTask if t != CognitiveTask.EMBEDDING},
        ),
        "openrouter/premium-model": _make_profile(
            "openrouter/premium-model", provider="openrouter", family="claude",
            cost_tier=3, max_tool_complexity=4, modalities=("text", "vision", "code"),
            task_scores={t.value: 0.92 for t in CognitiveTask if t != CognitiveTask.EMBEDDING},
        ),
        "ollama/embed-model": _make_profile(
            "ollama/embed-model", provider="ollama", family="nomic",
            cost_tier=0, max_tool_complexity=1, modalities=("embedding",),
            is_local=True,
            task_scores={"embedding": 0.80},
        ),
    }
    return CognitiveTaskRegistry(profiles)


# ── Enum completeness ─────────────────────────────────────────────────────


def test_task_to_use_case_complete():
    for task in CognitiveTask:
        assert task in TASK_TO_USE_CASE, f"Missing TASK_TO_USE_CASE mapping for {task}"


def test_cognitive_task_values():
    assert len(CognitiveTask) == 16


def test_modality_values():
    assert Modality.TEXT.value == "text"
    assert Modality.VISION.value == "vision"
    assert Modality.EMBEDDING.value == "embedding"


def test_use_case_is_a_local_enum_not_the_hardware_fit_one():
    assert {u.value for u in UseCase} == {"general", "coding", "reasoning", "chat", "multimodal", "embedding"}
    assert UseCase.__module__ == "g6.llm_router.registry"


# ── Task-model queries ────────────────────────────────────────────────────


def test_tasks_for_model(registry: CognitiveTaskRegistry):
    tasks = registry.tasks_for_model("ollama/coder-32b")
    assert len(tasks) > 0
    task_values = [t.value for t, _ in tasks]
    assert "code_generation" in task_values
    scores = [s for _, s in tasks]
    assert scores == sorted(scores, reverse=True)


def test_tasks_for_unknown_model(registry: CognitiveTaskRegistry):
    assert registry.tasks_for_model("nonexistent/model") == []


def test_models_for_task(registry: CognitiveTaskRegistry):
    results = registry.models_for_task(CognitiveTask.CODE_GENERATION)
    assert len(results) > 0
    model_ids = [mid for mid, _ in results]
    assert "openrouter/premium-model" in model_ids
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)


def test_models_for_task_min_score(registry: CognitiveTaskRegistry):
    all_results = registry.models_for_task(CognitiveTask.CODE_GENERATION, min_score=0.0)
    filtered = registry.models_for_task(CognitiveTask.CODE_GENERATION, min_score=0.70)
    assert len(filtered) < len(all_results)
    for _, score in filtered:
        assert score >= 0.70


def test_models_for_task_provider_filter(registry: CognitiveTaskRegistry):
    results = registry.models_for_task(CognitiveTask.GENERAL_CHAT, provider="ollama")
    for mid, _ in results:
        assert registry.get_profile(mid).provider == "ollama"


def test_models_for_task_string_arg(registry: CognitiveTaskRegistry):
    results = registry.models_for_task("code_generation")
    assert len(results) > 0


def test_models_for_task_leaves_out_a_model_with_no_score_for_it(registry: CognitiveTaskRegistry):
    assert [mid for mid, _ in registry.models_for_task(CognitiveTask.EMBEDDING)] == ["ollama/embed-model"]


def test_best_model_for_use_case(registry: CognitiveTaskRegistry):
    best = registry.best_model_for_use_case(UseCase.CODING)
    assert best is not None
    assert best == "openrouter/premium-model"


def test_best_model_for_use_case_provider_filter(registry: CognitiveTaskRegistry):
    best = registry.best_model_for_use_case(UseCase.CODING, provider="ollama")
    assert best is not None
    prof = registry.get_profile(best)
    assert prof.provider == "ollama"


def test_best_model_for_use_case_with_a_provider_that_has_no_model_is_none(registry: CognitiveTaskRegistry):
    assert registry.best_model_for_use_case(UseCase.CHAT, provider="nonexistent-provider") is None


def test_best_model_for_a_use_case_no_task_maps_to_is_none(registry: CognitiveTaskRegistry):
    unused = [uc for uc in UseCase if uc not in set(TASK_TO_USE_CASE.values())]
    assert unused == [UseCase.MULTIMODAL]
    assert registry.best_model_for_use_case(UseCase.MULTIMODAL) is None
    assert CognitiveTaskRegistry({}).best_model_for_use_case(UseCase.CHAT) is None


def test_model_modalities(registry: CognitiveTaskRegistry):
    mods = registry.model_modalities("openrouter/vision-model")
    assert "vision" in mods
    assert "text" in mods
    assert registry.model_modalities("nonexistent") == ()


# ── Profile queries ───────────────────────────────────────────────────────


def test_get_profile(registry: CognitiveTaskRegistry):
    prof = registry.get_profile("ollama/tiny-1b")
    assert prof is not None
    assert prof.provider == "ollama"
    assert prof.is_local is True
    assert registry.get_profile("nonexistent") is None


def test_all_models(registry: CognitiveTaskRegistry):
    assert len(registry.all_models()) == 6


def test_models_by_provider(registry: CognitiveTaskRegistry):
    ollama_models = registry.models_by_provider("ollama")
    assert all(p.provider == "ollama" for p in ollama_models)
    assert len(ollama_models) == 4


def test_filtered_keeps_only_the_providers_asked_for(registry: CognitiveTaskRegistry):
    local = registry.filtered(providers=["ollama"])
    assert {local.get_profile(m).provider for m in local.all_models()} == {"ollama"}
    assert registry.filtered(local_only=True).all_models() == local.all_models()
    installed = registry.filtered(installed=["test-7b", "ollama/coder-32b"])
    assert sorted(installed.all_models()) == ["ollama/coder-32b", "ollama/test-7b", "openrouter/premium-model",
                                              "openrouter/vision-model"]
    assert len(registry.all_models()) == 6  # the original is unchanged


# ── Mutations ─────────────────────────────────────────────────────────────


def test_register_model(registry: CognitiveTaskRegistry):
    new_prof = _make_profile("test/new-model", task_scores={"code_generation": 0.99})
    registry.register_model(new_prof)
    assert "test/new-model" in registry.all_models()
    results = registry.models_for_task("code_generation")
    model_ids = [mid for mid, _ in results]
    assert "test/new-model" in model_ids


def test_update_task_score(registry: CognitiveTaskRegistry):
    old_results = registry.models_for_task(CognitiveTask.CODE_GENERATION)
    old_scores = {mid: s for mid, s in old_results}
    old_tiny_score = old_scores.get("ollama/tiny-1b", 0.0)

    registry.update_task_score("ollama/tiny-1b", CognitiveTask.CODE_GENERATION, 0.99)

    new_results = registry.models_for_task(CognitiveTask.CODE_GENERATION)
    new_scores = {mid: s for mid, s in new_results}
    assert new_scores["ollama/tiny-1b"] == 0.99
    assert new_scores["ollama/tiny-1b"] != old_tiny_score


def test_update_task_score_unknown_model(registry: CognitiveTaskRegistry):
    with pytest.raises(KeyError):
        registry.update_task_score("nonexistent", CognitiveTask.CODE_GENERATION, 0.5)
    with pytest.raises(KeyError):
        CognitiveTaskRegistry({}).update_task_score("nope", CognitiveTask.GENERAL_CHAT, 0.5)


def test_index_invalidation(registry: CognitiveTaskRegistry):
    registry.models_for_task(CognitiveTask.GENERAL_CHAT)
    assert registry._task_index is not None

    new_prof = _make_profile("test/invalidation", task_scores={"general_chat": 0.99})
    registry.register_model(new_prof)
    assert registry._task_index is None

    results = registry.models_for_task(CognitiveTask.GENERAL_CHAT)
    assert registry._task_index is not None
    model_ids = [mid for mid, _ in results]
    assert "test/invalidation" in model_ids


# ── Serialization ─────────────────────────────────────────────────────────


def test_json_round_trip(registry: CognitiveTaskRegistry):
    data = registry.to_dict()
    json_str = json.dumps(data)
    parsed = json.loads(json_str)
    restored = CognitiveTaskRegistry.from_dict(parsed)
    assert set(restored.all_models()) == set(registry.all_models())
    for mid in registry.all_models():
        orig = registry.get_profile(mid)
        rest = restored.get_profile(mid)
        assert orig.model_id == rest.model_id
        assert orig.modalities == rest.modalities
        assert orig.task_scores == rest.task_scores


def test_model_profile_to_from_dict():
    prof = _make_profile(
        "test/roundtrip",
        modalities=("text", "vision"),
        pricing_prompt_per_m=1.5,
        zdr_available=True,
        distillable=True,
    )
    d = prof.to_dict()
    restored = ModelProfile.from_dict(d)
    assert restored.model_id == prof.model_id
    assert restored.modalities == prof.modalities
    assert restored.pricing_prompt_per_m == 1.5
    assert restored.zdr_available is True
    assert restored.distillable is True


def test_a_profile_with_no_price_is_unknown_never_free():
    unknown = ModelProfile.from_dict({"model_id": "x/y", "display_name": "y", "provider": "openrouter",
                                      "family": "y", "cost_tier": 2, "task_scores": {}})
    assert unknown.pricing_prompt_per_m == UNKNOWN_PRICE and unknown.price_known is False
    assert unknown.prices() == (None, None)
    known = _make_profile("a/b", pricing_prompt_per_m=0.3, pricing_completion_per_m=1.0)
    assert known.price_known is True and known.prices() == (0.3, 1.0)


# ── Default registry (loads the committed table) ──────────────────────────


def test_default_registry_loads():
    reg = default_registry()
    assert len(reg.all_models()) == 47
    providers = {reg.get_profile(m).provider for m in reg.all_models()}
    assert providers == {"ollama", "openrouter"}  # the one Bonsai profile is not ported: G6 has no caller for it


def test_default_registry_is_a_fresh_copy_each_time():
    reg = default_registry()
    reg.register_model(_make_profile("test/only-here"))
    assert default_registry().get_profile("test/only-here") is None


def test_default_registry_pricing():
    reg = default_registry()
    opus = reg.get_profile("openrouter/anthropic/claude-opus-4.7")
    assert opus is not None
    assert opus.pricing_prompt_per_m == 5.0
    assert opus.pricing_completion_per_m == 25.0

    free = reg.get_profile("openrouter/nvidia/nemotron-3-super-120b-a12b:free")
    assert free is not None
    assert free.pricing_prompt_per_m == 0.0
    assert free.pricing_completion_per_m == 0.0
    assert free.cost_tier <= 1, "a zero-priced profile must sit on a free cost_tier"


def test_default_registry_zdr_distillable():
    reg = default_registry()
    opus = reg.get_profile("openrouter/anthropic/claude-opus-4.7")
    assert opus.zdr_available is True
    assert opus.distillable is False

    ds = reg.get_profile("openrouter/deepseek/deepseek-v4-flash")
    assert ds.zdr_available is True
    assert ds.distillable is True


def test_default_registry_modalities():
    reg = default_registry()
    gemini = reg.get_profile("openrouter/google/gemini-3.1-pro-preview")
    assert "vision" in gemini.modalities
    assert "audio_in" in gemini.modalities
    assert "multimodal_native" in gemini.modalities


# ── The committed table is honest about its own figures (new) ────────────


def _raw() -> dict:
    return json.loads(resources.files("g6.llm_router").joinpath("data", "model_profiles.json").read_text("utf-8"))


def test_the_table_states_when_its_prices_were_collected_and_that_its_scores_are_not_measured():
    info = table()
    assert info["prices_as_of"] == "2026-08-16" and info["prices_as_of"] in info["prices"]
    assert "not measurements" in info["scores"] and "not benchmarked" in info["scores"]
    assert info["unknown_price"] == UNKNOWN_PRICE == -1
    assert set(info) >= {"about", "prices", "scores", "changes"} and "models" not in info


def test_every_price_is_a_stated_number_or_minus_one_never_a_floored_guess():
    for row in _raw()["models"]:
        for key in ("pricing_prompt_per_m", "pricing_completion_per_m"):
            value = row[key]
            assert isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value), row
            assert value >= 0 or value == -1, (row["model_id"], key, value)


def test_a_cost_tier_agrees_with_the_price_it_claims():
    # ported from the earlier router's price-authority gate: a free tier is a claim about money
    for row in _raw()["models"]:
        free = row["pricing_prompt_per_m"] == 0 and row["pricing_completion_per_m"] == 0
        if row["is_local"]:
            assert row["cost_tier"] == 0 and row["provider"] == "ollama" and free, row["model_id"]
        else:
            assert (row["cost_tier"] == 1) == free and row["cost_tier"] >= 1, row["model_id"]


def test_every_model_id_names_its_provider_and_every_task_score_is_a_known_task():
    tasks = {t.value for t in CognitiveTask}
    ids = [row["model_id"] for row in _raw()["models"]]
    assert len(ids) == len(set(ids))
    for row in _raw()["models"]:
        assert row["model_id"].startswith(row["provider"] + "/"), row["model_id"]
        assert set(row["task_scores"]) <= tasks and all(0 <= s <= 1 for s in row["task_scores"].values())
        assert row["parameter_count_b"] is None or row["parameter_count_b"] > 0, row["model_id"]


def test_no_out_of_date_default_is_chosen_with_made_up_data():
    # the earlier router defaulted to model names that have since changed; nothing here picks a paid default
    import g6.llm_router.registry as registry_module

    source = resources.files("g6.llm_router").joinpath("registry.py").read_text("utf-8")
    assert "claude-opus-4-6" not in source and "gpt-4o-mini" not in source
    assert not [name for name in dir(registry_module) if name.startswith("DEFAULT_MODEL")]
