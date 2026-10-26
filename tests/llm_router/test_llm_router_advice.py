# SPDX-License-Identifier: Apache-2.0
"""g6.llm_router.advice: the answers read only from the committed table (tasks, ranked models, a route with its
fallback chain, a supervision chain, a profile and a dated cost estimate), each inside the library envelope, none
``verified``, a price that is variable or unknown never given as a number."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from g6.invariants import check_envelope
from g6.llm_router import advice
from g6.llm_router.envelope import (
    COMMITTED_TABLE,
    COST_ESTIMATE,
    INPUT_INVALID,
    NO_MODEL,
    NO_SUPERVISOR,
    PRICE_UNKNOWN,
    UNKNOWN_MODEL,
    RouterResult,
)
from g6.llm_router.registry import (
    UNKNOWN_PRICE,
    CognitiveTask,
    CognitiveTaskRegistry,
    ModelProfile,
    default_registry,
    table,
)

DATE = table()["prices_as_of"]


def _profile(model_id: str, provider: str, *, score: float, tier: int, local: bool = False, prompt: float = 0.0,
             completion: float = 0.0, complexity: int = 2) -> ModelProfile:
    return ModelProfile(model_id=model_id, display_name=model_id, provider=provider, family="t", cost_tier=tier,
                        task_scores={"code_generation": score, "general_chat": score}, is_local=local,
                        pricing_prompt_per_m=prompt, pricing_completion_per_m=completion,
                        max_tool_complexity=complexity)


@pytest.fixture
def small() -> CognitiveTaskRegistry:
    rows = [
        _profile("ollama/a:7b", "ollama", score=0.5, tier=0, local=True),
        _profile("ollama/b:14b", "ollama", score=0.6, tier=0, local=True, complexity=3),
        _profile("openrouter/x/cheap", "openrouter", score=0.6, tier=2, prompt=0.1, completion=0.4),
        _profile("openrouter/x/dear", "openrouter", score=0.9, tier=3, prompt=3, completion=15, complexity=4),
        _profile("openrouter/x/auto", "openrouter", score=0.9, tier=2, prompt=UNKNOWN_PRICE,
                 completion=UNKNOWN_PRICE),
        _profile("openrouter/x/free", "openrouter", score=0.4, tier=1),
    ]
    return CognitiveTaskRegistry({p.model_id: p for p in rows})


def _ok(result: RouterResult, card: str = COMMITTED_TABLE) -> dict:
    payload = result.model_dump(mode="json")
    assert check_envelope(payload) == [], check_envelope(payload)
    assert payload["completion_state"] == "qualified-draft" and payload["code"] == "", payload["reason"]
    assert payload["warning_card"]["code"] == card and payload["evidence"]
    return payload


def _blocked(result: RouterResult, code: str) -> dict:
    payload = result.model_dump(mode="json")
    assert check_envelope(payload) == []
    assert payload["completion_state"] == "blocked-escalated" and payload["code"] == code, payload
    assert payload["warning_card"]["code"] == code and payload["reason"]
    return payload


def _numbers(value) -> list[float]:
    """Every number anywhere in a JSON value."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [value]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers(v)]
    if isinstance(value, list):
        return [n for v in value for n in _numbers(v)]
    return []


# ── list_tasks ────────────────────────────────────────────────────────────


def test_list_tasks_names_every_cognitive_task_with_its_kind_of_work_and_how_many_models_rank_for_it():
    out = _ok(advice.list_tasks(request_id="r", run_id="u"))
    assert [t["task"] for t in out["tasks"]] == [t.value for t in CognitiveTask] and out["count"] == 16
    by_task = {t["task"]: t for t in out["tasks"]}
    assert by_task["code_generation"]["use_case"] == "coding" and by_task["embedding"]["models"] >= 2
    assert (out["request_id"], out["run_id"]) == ("r", "u")


# ── models_for_task ───────────────────────────────────────────────────────


def test_models_are_ranked_by_score_with_a_reason_for_each_rank(small):
    out = _ok(advice.models_for_task("code_generation", registry=small))
    ids = [m["model_id"] for m in out["models"]]
    # 0.9 tie: the known price beats the unknown one; 0.6 tie: the local model beats the paid one
    assert ids == ["openrouter/x/dear", "openrouter/x/auto", "ollama/b:14b", "openrouter/x/cheap", "ollama/a:7b",
                   "openrouter/x/free"]
    assert [m["rank"] for m in out["models"]] == [1, 2, 3, 4, 5, 6]
    for row in out["models"]:
        assert f"score {row['score']:g}" in row["reason"] and "code_generation" in row["reason"]
    assert "local" in out["models"][2]["reason"] and "no per-token price" in out["models"][2]["reason"]
    assert DATE in out["models"][0]["reason"] and "highest first" in out["ranking"]
    assert out["prices_as_of"] == DATE


def test_models_for_task_can_be_limited_to_local_models_or_to_providers(small):
    local = _ok(advice.models_for_task("code_generation", local_only=True, registry=small))
    assert [m["model_id"] for m in local["models"]] == ["ollama/b:14b", "ollama/a:7b"]
    remote = _ok(advice.models_for_task("code_generation", providers=["openrouter"], min_score=0.5, limit=2,
                                        registry=small))
    assert [m["model_id"] for m in remote["models"]] == ["openrouter/x/dear", "openrouter/x/auto"]
    assert remote["considered"] == 3


def test_a_variable_price_in_a_ranking_is_never_a_number(small):
    out = _ok(advice.models_for_task("code_generation", registry=small))
    auto = next(m for m in out["models"] if m["model_id"] == "openrouter/x/auto")
    assert auto["prompt_usd_per_million"] is None and auto["completion_usd_per_million"] is None
    assert auto["price_known"] is False and "variable or unknown" in auto["reason"]
    assert UNKNOWN_PRICE not in _numbers(out)


def test_no_model_for_the_filter_is_blocked_and_says_why(small):
    out = _blocked(advice.models_for_task("embedding", registry=small), NO_MODEL)
    assert "embedding" in out["reason"] and out["models"] == []


def test_an_unknown_task_or_provider_is_refused():
    _blocked(advice.models_for_task("astrology"), INPUT_INVALID)
    _blocked(advice.models_for_task("code_generation", providers=["acme"]), INPUT_INVALID)


def test_the_committed_table_ranks_code_models_from_its_own_scores():
    out = _ok(advice.models_for_task(CognitiveTask.CODE_GENERATION, local_only=True))
    reg = default_registry()
    scores = [reg.get_profile(m["model_id"]).task_scores["code_generation"] for m in out["models"]]
    assert scores == sorted(scores, reverse=True) and all(m["is_local"] for m in out["models"])
    assert "not measured" in out["warning_card"]["message"] and DATE in out["warning_card"]["message"]


# ── route ─────────────────────────────────────────────────────────────────


def test_route_defaults_to_local_models_only(small):
    out = _ok(advice.route("code_generation", registry=small))
    assert out["providers"] == ["ollama"]
    assert out["model"]["model_id"] == "ollama/b:14b" and [m["model_id"] for m in out["fallbacks"]] == ["ollama/a:7b"]
    assert out["chain"] == ["ollama/b:14b", "ollama/a:7b"]
    assert out["not_considered"] == {"openrouter": 4}
    assert all(not m.startswith("openrouter/") for m in out["chain"])


def test_route_draws_its_fallbacks_only_from_the_providers_you_allow_local_first(small):
    out = _ok(advice.route("code_generation", providers=["ollama", "openrouter"], fallbacks=3, registry=small))
    assert out["chain"] == ["ollama/b:14b", "ollama/a:7b", "openrouter/x/dear", "openrouter/x/auto"]
    best = _ok(advice.route("code_generation", providers=["ollama", "openrouter"], fallbacks=1, local_first=False,
                            registry=small))
    assert best["chain"] == ["openrouter/x/dear", "openrouter/x/auto"]
    assert _ok(advice.route("code_generation", providers=["ollama"], fallbacks=0, registry=small))["fallbacks"] == []


def test_route_can_be_limited_to_the_local_models_you_have_installed(small):
    out = _ok(advice.route("code_generation", installed=["a:7b"], registry=small))
    assert out["chain"] == ["ollama/a:7b"]
    _blocked(advice.route("code_generation", installed=["not-pulled:1b"], registry=small), NO_MODEL)


def test_route_says_it_did_not_check_the_model_is_installed_or_reachable(small):
    card = _ok(advice.route("code_generation", registry=small))["warning_card"]
    assert "not checked" in card["message"] and "TaskRouter" in card["suggested_action"]


def test_route_with_no_model_among_the_allowed_providers_is_blocked(small):
    out = _blocked(advice.route("embedding", registry=small), NO_MODEL)
    assert "ollama" in out["reason"] and out["chain"] == []


# ── supervision_chain ─────────────────────────────────────────────────────


def test_supervision_chain_climbs_from_the_cheapest_worker_and_marks_each_paid_step(small):
    out = _ok(advice.supervision_chain("code_generation", registry=small, specialists=()))
    chain = out["chain"]
    assert chain[0]["model_id"] == "ollama/b:14b" and chain[0]["role"] == "worker"
    assert [link["model_id"] for link in chain] == ["ollama/b:14b", "openrouter/x/dear"]
    assert chain[1]["role"] == "supervisor" and chain[1]["requires_paid_api"] is True
    assert chain[1]["tier"] == "COST_PAID_PREMIUM" and out["requires_paid_api"] is True
    assert "ollama/b:14b" in chain[1]["reason"]


def test_a_local_only_chain_has_nobody_above_the_best_local_model_so_it_is_blocked(small):
    # the chain starts at the best-scoring model of the cheapest tier; among local models nobody scores higher
    out = _blocked(advice.supervision_chain("code_generation", providers=["ollama"], registry=small, specialists=()),
                   NO_SUPERVISOR)
    assert [link["model_id"] for link in out["chain"]] == ["ollama/b:14b"] and out["requires_paid_api"] is False
    assert "ollama" in out["reason"]


def test_a_chain_with_nobody_to_supervise_the_worker_is_blocked():
    lone = CognitiveTaskRegistry({"ollama/a:7b": _profile("ollama/a:7b", "ollama", score=0.5, tier=0, local=True)})
    out = _blocked(advice.supervision_chain("code_generation", registry=lone), NO_SUPERVISOR)
    assert [link["model_id"] for link in out["chain"]] == ["ollama/a:7b"]


def test_the_committed_supervision_chain_uses_its_specialists():
    out = _ok(advice.supervision_chain("code_generation"))
    assert len(out["chain"]) >= 2 and any(link["specialist"] for link in out["chain"])


# ── model_profile ─────────────────────────────────────────────────────────


def test_model_profile_gives_the_committed_profile_and_the_tasks_it_ranks_for():
    out = _ok(advice.model_profile("ollama/qwen2.5-coder:32b"))
    assert out["model_id"] == "ollama/qwen2.5-coder:32b" and out["profile"]["is_local"] is True
    scores = [t["score"] for t in out["tasks"]]
    assert scores == sorted(scores, reverse=True) and out["tasks"][0]["task"] in ("code_generation", "code_review")
    assert out["prices_as_of"] == DATE


def test_a_profile_never_shows_a_variable_price_as_a_number(small):
    out = _ok(advice.model_profile("openrouter/x/auto", registry=small))
    assert out["profile"]["pricing_prompt_per_m"] is None and out["profile"]["price_known"] is False
    assert UNKNOWN_PRICE not in _numbers(out)


def test_an_unknown_model_is_blocked():
    _blocked(advice.model_profile("openrouter/acme/none"), UNKNOWN_MODEL)
    _blocked(advice.estimate_cost("openrouter/acme/none", 10, 10), UNKNOWN_MODEL)


# ── estimate_cost ─────────────────────────────────────────────────────────


def test_a_cost_estimate_uses_the_committed_per_million_prices_and_states_their_date():
    out = _ok(advice.estimate_cost("openrouter/anthropic/claude-opus-4.7", 1_000_000, 200_000), COST_ESTIMATE)
    assert out["estimated_usd"] == pytest.approx(5.0 + 0.2 * 25.0)
    assert (out["prompt_usd_per_million"], out["completion_usd_per_million"]) == (5.0, 25.0)
    assert out["prices_as_of"] == DATE and DATE in out["warning_card"]["message"]
    assert "not verified" in out["warning_card"]["message"].lower()


def test_a_cost_estimate_for_a_local_model_is_zero_and_says_what_it_leaves_out():
    out = _ok(advice.estimate_cost("ollama/llama3.1:8b", 5000, 5000), COST_ESTIMATE)
    assert out["estimated_usd"] == 0.0 and "electricity" in out["warning_card"]["message"]


def test_a_cost_estimate_for_a_free_endpoint_says_free_endpoints_change():
    out = _ok(advice.estimate_cost("openrouter/nvidia/nemotron-3-super-120b-a12b:free", 100, 100), COST_ESTIMATE)
    assert out["estimated_usd"] == 0.0 and "withdrawn" in out["warning_card"]["message"]


def test_a_cost_estimate_never_shows_a_variable_price_as_a_number(small):
    out = _blocked(advice.estimate_cost("openrouter/x/auto", 1000, 1000, registry=small), PRICE_UNKNOWN)
    assert out["estimated_usd"] is None and out["prompt_usd_per_million"] is None
    assert out["completion_usd_per_million"] is None and out["prices_as_of"] == DATE
    assert UNKNOWN_PRICE not in _numbers(out) and '": -1' not in json.dumps(out)


def test_a_cost_estimate_refuses_negative_token_counts():
    _blocked(advice.estimate_cost("ollama/llama3.1:8b", -1, 0), INPUT_INVALID)


# ── the envelope ──────────────────────────────────────────────────────────


def test_no_result_can_be_verified():
    with pytest.raises(ValidationError):
        RouterResult(operation="route", completion_state="verified", warning_card={"code": "G6_E_X"}, evidence=[])


@pytest.mark.parametrize("call", [
    lambda: advice.list_tasks(),
    lambda: advice.models_for_task("summarization"),
    lambda: advice.route("summarization"),
    lambda: advice.supervision_chain("summarization"),
    lambda: advice.model_profile("ollama/mistral:7b"),
    lambda: advice.estimate_cost("ollama/mistral:7b", 1, 1),
])
def test_every_answer_records_where_its_figures_came_from(call):
    evidence = call().evidence[0]
    assert evidence["source"] == "g6.llm_router data/model_profiles.json" and evidence["prices_as_of"] == DATE
    assert evidence["network"] is False and "not measured" in evidence["scores"]
