# SPDX-License-Identifier: Apache-2.0
"""g6.llm_router.supervision: which model can check another's work, by cost tier. Ported from the earlier router's
supervision tests; one behaviour is fixed, not ported: a model the table does not know was given the free local tier,
so an unknown (possibly paid) model read as free. It is now refused."""
from __future__ import annotations

import pytest

from g6.llm_router.registry import CognitiveTask, CognitiveTaskRegistry, ModelProfile, default_registry
from g6.llm_router.supervision import (
    DEFAULT_SPECIALISTS,
    ModelTier,
    Specialist,
    SupervisionPolicy,
    SupervisionResult,
    get_tier,
)

# ── Fixtures ──────────────────────────────────────────────────────────────


def _prof(
    model_id: str,
    provider: str,
    cost_tier: int,
    max_tool_complexity: int = 3,
    modalities: tuple[str, ...] = ("text",),
    task_scores: dict[str, float] | None = None,
    pricing_prompt: float = 0.0,
    pricing_completion: float = 0.0,
) -> ModelProfile:
    scores = task_scores or {t.value: 0.5 for t in CognitiveTask if t != CognitiveTask.EMBEDDING}
    return ModelProfile(
        model_id=model_id,
        display_name=model_id,
        provider=provider,
        family="test",
        cost_tier=cost_tier,
        task_scores=scores,
        modalities=modalities,
        max_tool_complexity=max_tool_complexity,
        pricing_prompt_per_m=pricing_prompt,
        pricing_completion_per_m=pricing_completion,
    )


def _all(score: float) -> dict[str, float]:
    return {t.value: score for t in CognitiveTask if t != CognitiveTask.EMBEDDING}


@pytest.fixture
def policy() -> SupervisionPolicy:
    profiles = {
        "ollama/tiny-1b": _prof("ollama/tiny-1b", "ollama", 0, max_tool_complexity=1, task_scores=_all(0.25)),
        "ollama/7b": _prof("ollama/7b", "ollama", 0, max_tool_complexity=2, task_scores=_all(0.55)),
        "ollama/70b": _prof("ollama/70b", "ollama", 0, max_tool_complexity=3, task_scores=_all(0.72)),
        "openrouter/free-120b": _prof("openrouter/free-120b", "openrouter", 1, max_tool_complexity=3,
                                      task_scores=_all(0.65)),
        "openrouter/cheap-model": _prof("openrouter/cheap-model", "openrouter", 2, max_tool_complexity=3,
                                        pricing_prompt=0.15, pricing_completion=0.60, task_scores=_all(0.75)),
        "openrouter/premium-model": _prof("openrouter/premium-model", "openrouter", 3, max_tool_complexity=4,
                                          pricing_prompt=5.0, pricing_completion=25.0, task_scores=_all(0.92)),
    }
    return SupervisionPolicy(CognitiveTaskRegistry(profiles), specialists=())


# ── Tier assignment ───────────────────────────────────────────────────────


def test_tier_from_cost_tier(policy: SupervisionPolicy):
    assert get_tier("ollama/tiny-1b", policy._registry) == ModelTier.COST_FREE_LOCAL
    assert get_tier("ollama/7b", policy._registry) == ModelTier.COST_FREE_LOCAL
    assert get_tier("openrouter/free-120b", policy._registry) == ModelTier.COST_FREE_CLOUD
    assert get_tier("openrouter/cheap-model", policy._registry) == ModelTier.COST_PAID_CHEAP
    assert get_tier("openrouter/premium-model", policy._registry) == ModelTier.COST_PAID_PREMIUM


def test_an_unknown_model_is_refused_never_assumed_free(policy: SupervisionPolicy):
    with pytest.raises(KeyError, match="not in the model table"):
        get_tier("nonexistent/model", policy._registry)


def test_model_tier_ordering():
    assert ModelTier.COST_FREE_LOCAL < ModelTier.COST_FREE_CLOUD
    assert ModelTier.COST_FREE_CLOUD < ModelTier.COST_PAID_CHEAP
    assert ModelTier.COST_PAID_CHEAP < ModelTier.COST_PAID_PREMIUM


# ── can_supervise ─────────────────────────────────────────────────────────


def test_higher_tier_can_supervise(policy: SupervisionPolicy):
    assert policy.can_supervise("openrouter/premium-model", "ollama/tiny-1b")
    assert policy.can_supervise("openrouter/cheap-model", "ollama/7b")
    assert policy.can_supervise("openrouter/free-120b", "ollama/tiny-1b")


def test_lower_tier_cannot_supervise(policy: SupervisionPolicy):
    assert not policy.can_supervise("ollama/tiny-1b", "openrouter/premium-model")
    assert not policy.can_supervise("ollama/7b", "openrouter/cheap-model")


def test_same_model_cannot_supervise_self(policy: SupervisionPolicy):
    assert not policy.can_supervise("ollama/70b", "ollama/70b")


def test_same_tier_higher_complexity_can_supervise(policy: SupervisionPolicy):
    assert policy.can_supervise("ollama/70b", "ollama/tiny-1b", CognitiveTask.GENERAL_CHAT)


def test_same_tier_same_complexity_cannot_supervise():
    profiles = {
        "local/a": _prof("local/a", "ollama", 0, max_tool_complexity=2, task_scores=_all(0.50)),
        "local/b": _prof("local/b", "ollama", 0, max_tool_complexity=2, task_scores=_all(0.50)),
    }
    p = SupervisionPolicy(CognitiveTaskRegistry(profiles), specialists=())
    assert not p.can_supervise("local/a", "local/b", CognitiveTask.GENERAL_CHAT)


def test_a_higher_tier_with_a_lower_score_for_the_task_cannot_supervise():
    # the committed table had a free local coder (0.78) checked by a paid one that scores lower (0.75): a dearer model
    # that does the task worse is not a check on it
    profiles = {
        "ollama/sharp": _prof("ollama/sharp", "ollama", 0, task_scores=_all(0.78)),
        "openrouter/dear": _prof("openrouter/dear", "openrouter", 2, pricing_prompt=1, pricing_completion=2,
                                 task_scores=_all(0.75)),
        "openrouter/equal": _prof("openrouter/equal", "openrouter", 1, task_scores=_all(0.78)),
    }
    p = SupervisionPolicy(CognitiveTaskRegistry(profiles), specialists=())
    assert not p.can_supervise("openrouter/dear", "ollama/sharp", CognitiveTask.CODE_GENERATION)
    assert p.can_supervise("openrouter/equal", "ollama/sharp", CognitiveTask.CODE_GENERATION)  # at least its score
    assert p.get_supervisor("ollama/sharp", CognitiveTask.CODE_GENERATION).supervisor == "openrouter/equal"
    lone = SupervisionPolicy(CognitiveTaskRegistry({k: profiles[k] for k in ("ollama/sharp", "openrouter/dear")}),
                             specialists=(Specialist("openrouter/dear", CognitiveTask.CODE_GENERATION,
                                                     ModelTier.COST_FREE_LOCAL),))
    assert lone.get_supervisor("ollama/sharp", CognitiveTask.CODE_GENERATION) is None  # a specialist neither


def test_same_tier_higher_complexity_but_lower_score_for_the_task_cannot_supervise():
    profiles = {
        "local/wide": _prof("local/wide", "ollama", 0, max_tool_complexity=3, task_scores=_all(0.40)),
        "local/sharp": _prof("local/sharp", "ollama", 0, max_tool_complexity=2, task_scores=_all(0.60)),
    }
    p = SupervisionPolicy(CognitiveTaskRegistry(profiles), specialists=())
    assert not p.can_supervise("local/wide", "local/sharp", CognitiveTask.CODE_REVIEW)
    assert p.can_supervise("local/wide", "local/sharp")  # with no task, the wider tool use is enough


# ── get_supervisor ────────────────────────────────────────────────────────


def test_get_supervisor_for_the_smallest_local_model(policy: SupervisionPolicy):
    result = policy.get_supervisor("ollama/tiny-1b", CognitiveTask.GENERAL_CHAT)
    assert result is not None
    assert result.worker == "ollama/tiny-1b"
    assert result.worker_tier == ModelTier.COST_FREE_LOCAL


def test_get_supervisor_picks_the_highest_score_that_can_supervise(policy: SupervisionPolicy):
    result = policy.get_supervisor("ollama/tiny-1b", CognitiveTask.GENERAL_CHAT)
    assert result is not None and result.supervisor == "openrouter/premium-model"


def test_get_supervisor_breaks_a_score_tie_on_the_lower_price():
    profiles = {
        "ollama/w": _prof("ollama/w", "ollama", 0, max_tool_complexity=1, task_scores=_all(0.3)),
        "openrouter/dear": _prof("openrouter/dear", "openrouter", 2, pricing_prompt=2, pricing_completion=8,
                                 task_scores=_all(0.8)),
        "openrouter/cheap": _prof("openrouter/cheap", "openrouter", 2, pricing_prompt=1, pricing_completion=2,
                                  task_scores=_all(0.8)),
    }
    p = SupervisionPolicy(CognitiveTaskRegistry(profiles), specialists=())
    assert p.get_supervisor("ollama/w", CognitiveTask.SUMMARIZATION).supervisor == "openrouter/cheap"


def test_get_supervisor_requires_paid_api(policy: SupervisionPolicy):
    result = policy.get_supervisor("openrouter/free-120b", CognitiveTask.GENERAL_CHAT)
    assert result is not None
    assert result.requires_paid_api is True
    assert result.supervisor_tier >= ModelTier.COST_PAID_CHEAP


def test_get_supervisor_for_premium_returns_none(policy: SupervisionPolicy):
    result = policy.get_supervisor("openrouter/premium-model", CognitiveTask.GENERAL_CHAT)
    assert result is None


def test_get_supervisor_no_task(policy: SupervisionPolicy):
    result = policy.get_supervisor("ollama/tiny-1b")
    assert result is not None


# ── get_worker ────────────────────────────────────────────────────────────


def test_get_worker_for_premium(policy: SupervisionPolicy):
    result = policy.get_worker("openrouter/premium-model", CognitiveTask.CODE_GENERATION)
    assert result is not None
    assert result.supervisor == "openrouter/premium-model"
    wrk_tier = get_tier(result.worker, policy._registry)
    assert wrk_tier < ModelTier.COST_PAID_PREMIUM


def test_get_worker_for_the_smallest_local_model_returns_none(policy: SupervisionPolicy):
    result = policy.get_worker("ollama/tiny-1b", CognitiveTask.GENERAL_CHAT)
    assert result is None


# ── Specialist overrides ──────────────────────────────────────────────────


def test_specialist_override(policy: SupervisionPolicy):
    policy.add_specialist_override(
        "openrouter/cheap-model",
        CognitiveTask.CODE_GENERATION,
        max_worker_tier=ModelTier.COST_FREE_CLOUD,
    )
    result = policy.get_supervisor("ollama/70b", CognitiveTask.CODE_GENERATION)
    assert result is not None
    assert result.supervisor == "openrouter/cheap-model"
    assert result.specialist_override is True
    assert result.requires_paid_api is True


def test_a_specialist_below_the_workers_tier_is_skipped_not_followed(policy: SupervisionPolicy):
    # inverted from the earlier router's test, which had a free local model supervise a paid one that scores higher
    # (LLM_ROUTER-02): a supervisor must be able to check its worker, so the general rule picks instead
    policy.add_specialist_override(
        "ollama/70b",
        CognitiveTask.CODE_GENERATION,
        max_worker_tier=ModelTier.COST_PAID_CHEAP,
    )
    result = policy.get_supervisor("openrouter/cheap-model", CognitiveTask.CODE_GENERATION)
    assert result is not None
    assert result.supervisor == "openrouter/premium-model"
    assert result.specialist_override is False


def test_specialist_override_wrong_task(policy: SupervisionPolicy):
    policy.add_specialist_override(
        "ollama/70b",
        CognitiveTask.CODE_GENERATION,
        max_worker_tier=ModelTier.COST_PAID_CHEAP,
    )
    result = policy.get_supervisor("openrouter/cheap-model", CognitiveTask.TRANSLATION)
    assert result is None or result.specialist_override is False


def test_a_specialist_missing_from_the_registry_is_skipped(policy: SupervisionPolicy):
    policy.add_specialist_override("openrouter/not-here", CognitiveTask.GENERAL_CHAT,
                                   max_worker_tier=ModelTier.COST_PAID_PREMIUM)
    result = policy.get_supervisor("ollama/tiny-1b", CognitiveTask.GENERAL_CHAT)
    assert result is not None and result.supervisor != "openrouter/not-here" and not result.specialist_override


def test_every_default_specialist_names_a_model_in_the_committed_table():
    reg = default_registry()
    assert DEFAULT_SPECIALISTS and all(isinstance(s, Specialist) for s in DEFAULT_SPECIALISTS)
    for s in DEFAULT_SPECIALISTS:
        assert reg.get_profile(s.specialist) is not None, s.specialist


# ── Supervision chain ─────────────────────────────────────────────────────


def test_supervision_chain(policy: SupervisionPolicy):
    chain = policy.get_supervision_chain(CognitiveTask.GENERAL_CHAT)
    assert len(chain) >= 2
    # the chain starts with the best free local worker (ollama/70b scores highest at the free local tier)
    assert get_tier(chain[0], policy._registry) == ModelTier.COST_FREE_LOCAL
    for i in range(1, len(chain)):
        assert policy.can_supervise(chain[i], chain[i - 1], CognitiveTask.GENERAL_CHAT)


def test_supervision_chain_no_duplicates(policy: SupervisionPolicy):
    chain = policy.get_supervision_chain(CognitiveTask.CODE_GENERATION)
    assert len(chain) == len(set(chain))


def test_supervision_chain_handles_empty_registry():
    assert SupervisionPolicy(CognitiveTaskRegistry({})).get_supervision_chain() == []


# ── requires_paid_api ─────────────────────────────────────────────────────


def test_free_supervisor_no_paid_flag(policy: SupervisionPolicy):
    result = policy.get_supervisor("ollama/tiny-1b", CognitiveTask.GENERAL_CHAT)
    if result is not None and get_tier(result.supervisor, policy._registry) < ModelTier.COST_PAID_CHEAP:
        assert result.requires_paid_api is False


def test_paid_supervisor_has_paid_flag(policy: SupervisionPolicy):
    result = policy.get_supervisor("openrouter/free-120b", CognitiveTask.GENERAL_CHAT)
    assert result is not None
    assert result.requires_paid_api is True


# ── SupervisionResult serialization ───────────────────────────────────────


def test_supervision_result_to_dict():
    sr = SupervisionResult(
        supervisor="openrouter/opus",
        worker="ollama/tiny-1b",
        task=CognitiveTask.CODE_GENERATION,
        supervisor_tier=ModelTier.COST_PAID_PREMIUM,
        worker_tier=ModelTier.COST_FREE_LOCAL,
        requires_paid_api=True,
        specialist_override=False,
    )
    d = sr.to_dict()
    assert d["supervisor"] == "openrouter/opus"
    assert d["task"] == "code_generation"
    assert d["supervisor_tier"] == "COST_PAID_PREMIUM"
    assert d["requires_paid_api"] is True


# ── Default policy (the committed table) ──────────────────────────────────


def test_default_policy_loads():
    pol = SupervisionPolicy(default_registry())
    chain = pol.get_supervision_chain(CognitiveTask.CODE_GENERATION)
    assert len(chain) >= 2


def test_default_policy_supervises_the_smallest_local_model():
    pol = SupervisionPolicy(default_registry())
    result = pol.get_supervisor("ollama/llama3.1:8b", CognitiveTask.GENERAL_CHAT)
    assert result is not None
    assert result.worker == "ollama/llama3.1:8b"


def test_a_local_only_policy_never_names_a_paid_supervisor():
    pol = SupervisionPolicy(default_registry().filtered(providers=["ollama"]))
    chain = pol.get_supervision_chain(CognitiveTask.CODE_GENERATION)
    assert chain and all(m.startswith("ollama/") for m in chain)
    for worker in chain:
        found = pol.get_supervisor(worker, CognitiveTask.CODE_GENERATION)
        assert found is None or found.requires_paid_api is False
