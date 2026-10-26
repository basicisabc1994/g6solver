# SPDX-License-Identifier: Apache-2.0
"""agent_router merged into g6.llm_router (B05: AGENT_ROUTER-01 and -02): the recorded-outcome learning signal.

Outcomes are kept in one SQLite file under the G6 home, so a later call (over MCP too) reads what an earlier one
recorded; they come from a caller's report or from a FallbackCaller's failures. They are claims: they reach a route only
through the documented bounded rule (asked for; at least MIN_OUTCOMES; each score moved by at most OUTCOME_WEIGHT / 2;
no model or provider added; local models first), they never change a label or make a route look checked, and nothing
here calls a model. The archive router's task-type matrix, review rules and model re-rank are not ported."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import g6.llm_router
from g6.core.contract_decorator import get_block_contract_attr
from g6.core.failure_modes import FailureModeRegistry
from g6.core.llm import FakeLLM, LLMError
from g6.core.model_calls import model_calls
from g6.invariants import check_envelope
from g6.llm_router import TaskRouter, advice
from g6.llm_router import envelope as env
from g6.llm_router.llm_router_block import LLMRouterBlock
from g6.llm_router.registry import CognitiveTaskRegistry, ModelProfile
from g6.mcp import invoke_reply

SRC = Path(g6.llm_router.__file__).parent
TASK = "summarization"


def _outcomes():  # noqa: ANN202 - the module under test, imported when the test runs
    from g6.llm_router import outcomes

    return outcomes


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _local(model_id: str, score: float) -> ModelProfile:
    return ModelProfile(model_id=model_id, display_name=model_id, provider="ollama", family="t", cost_tier=0,
                        task_scores={TASK: score}, is_local=True, pricing_prompt_per_m=0.0,
                        pricing_completion_per_m=0.0)


def _reg(*rows: tuple[str, float]) -> CognitiveTaskRegistry:
    profiles = [_local(m, s) for m, s in rows or (("ollama/a:7b", 0.70), ("ollama/b:7b", 0.66), ("ollama/c:7b", 0.50))]
    paid = ModelProfile(model_id="openrouter/x/paid", display_name="paid", provider="openrouter", family="t",
                        cost_tier=2, task_scores={TASK: 0.95}, pricing_prompt_per_m=1.0, pricing_completion_per_m=2.0)
    return CognitiveTaskRegistry({p.model_id: p for p in (*profiles, paid)})


class Failing:
    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        raise LLMError("not pulled")


# ── D1: a model that keeps failing a task (poor_fit_route) ───────────────


def test_a_model_that_keeps_failing_a_task_drops_below_a_close_rival_when_the_route_uses_outcomes(home):
    store, reg = _outcomes().OutcomeStore(), _reg()
    for _ in range(20):
        store.record(TASK, "ollama/a:7b", False)
        store.record(TASK, "ollama/b:7b", True)
    assert advice.route(TASK, registry=reg).chain[0] == "ollama/a:7b"          # the committed order
    for seed in range(40):
        tilted = advice.route(TASK, registry=reg, outcomes=store, seed=seed)
        assert tilted.chain[:2] == ["ollama/b:7b", "ollama/a:7b"], seed
    rows = {r["model_id"]: r for r in tilted.model_dump(mode="json")["fallbacks"] + [tilted.model["model_id"] and
                                                                                       tilted.model]}
    assert rows["ollama/a:7b"]["recorded"] == {"successes": 0, "failures": 20} and rows["ollama/a:7b"]["tilt"] < 0


def test_failures_a_fallback_caller_saw_count_too(home):
    # a case the rule was not written around: nobody reports anything, the FallbackCaller's failures are recorded
    store, reg = _outcomes().OutcomeStore(), _reg()
    build = lambda provider, model_id: Failing() if model_id == "ollama/a:7b" else FakeLLM(default="ok")  # noqa: E731
    for _ in range(6):
        caller = TaskRouter(registry=reg, build=build).caller_for(TASK)
        assert caller.complete("s", "u") == "ok"
        assert store.record_fallback(TASK, caller, accepted=None) == 1        # only what G6 saw: a model that raised
    assert store.counts(TASK, ["ollama/a:7b", "ollama/b:7b"]) == {"ollama/a:7b": (0, 6), "ollama/b:7b": (0, 0)}
    store.record_fallback(TASK, caller, accepted=True)                         # the answer was good: your report
    assert store.counts(TASK, ["ollama/b:7b"]) == {"ollama/b:7b": (1, 0)}
    route = TaskRouter(registry=reg, outcomes=store, seed=5).route(TASK)
    assert route.chain[0] == "ollama/b:7b"


# ── the bounded rule ──────────────────────────────────────────────────────


def test_no_number_of_reports_moves_a_score_by_more_than_half_the_weight():
    o = _outcomes()
    assert (o.OUTCOME_WEIGHT, o.MIN_OUTCOMES) == (0.1, 5)
    for seed in range(300):
        tilt = o.tilts({"x": (10**9, 0), "y": (0, 10**9), "z": (0, 0)}, seed=seed)
        assert all(abs(v) <= o.OUTCOME_WEIGHT / 2 for v in tilt.values()), seed


def test_a_model_the_table_puts_more_than_the_weight_ahead_is_never_overtaken(home):
    store, reg = _outcomes().OutcomeStore(), _reg(("ollama/a:7b", 0.80), ("ollama/b:7b", 0.69))
    for _ in range(60):
        store.record(TASK, "ollama/a:7b", False, reviewer_verdict="rejected")
        store.record(TASK, "ollama/b:7b", True, reviewer_verdict="approved")
    for seed in range(60):
        assert advice.route(TASK, registry=reg, outcomes=store, seed=seed).chain[0] == "ollama/a:7b", seed


def test_below_the_minimum_nothing_is_tilted_and_the_reply_says_so(home):
    store, reg = _outcomes().OutcomeStore(), _reg()
    for _ in range(4):
        store.record(TASK, "ollama/a:7b", False)
    out = advice.route(TASK, registry=reg, outcomes=store, seed=1).model_dump(mode="json")
    info = out["evidence"][0]["recorded_outcomes"]
    assert (info["outcomes"], info["applied"]) == (4, False) and out["model"]["tilt"] == 0.0
    assert out["chain"] == advice.route(TASK, registry=reg).chain


def test_outcomes_never_add_a_model_or_a_provider_and_local_models_stay_first(home):
    store, reg = _outcomes().OutcomeStore(), _reg()
    for _ in range(50):
        store.record(TASK, "openrouter/x/paid", True)
        store.record(TASK, "ollama/c:7b", True)
    local = advice.route(TASK, registry=reg, outcomes=store, seed=2, installed=["a:7b", "b:7b"])
    assert local.chain == ["ollama/a:7b", "ollama/b:7b"]          # neither the paid one nor an uninstalled one
    both = advice.route(TASK, providers=["ollama", "openrouter"], registry=reg, outcomes=store, seed=2, fallbacks=5)
    assert both.chain[-1] == "openrouter/x/paid"                               # local first, whatever was reported


def test_a_route_ignores_recorded_outcomes_unless_asked(home):
    store, reg = _outcomes().OutcomeStore(), _reg()
    for _ in range(30):
        store.record(TASK, "ollama/a:7b", False)
    out = advice.route(TASK, registry=reg).model_dump(mode="json")
    assert out["chain"][0] == "ollama/a:7b" and "recorded_outcomes" not in out["evidence"][0]


def test_the_seed_is_recorded_so_a_tilted_route_can_be_replayed(home):
    store, reg = _outcomes().OutcomeStore(), _reg()
    for _ in range(10):
        store.record(TASK, "ollama/b:7b", True)
    first = advice.route(TASK, registry=reg, outcomes=store).model_dump(mode="json")
    seed = first["evidence"][0]["recorded_outcomes"]["seed"]
    again = advice.route(TASK, registry=reg, outcomes=store, seed=seed).model_dump(mode="json")
    assert isinstance(seed, int) and again["chain"] == first["chain"] and again["model"] == first["model"]


# ── G1 and G3: reports are claims ─────────────────────────────────────────


def test_reports_and_approvals_never_make_a_route_look_checked(home):
    store, reg = _outcomes().OutcomeStore(), _reg()
    for _ in range(50):
        store.record(TASK, "ollama/a:7b", True, reviewer_verdict="approved", human_correction="")
    out = advice.route(TASK, registry=reg, outcomes=store, seed=1).model_dump(mode="json")
    assert check_envelope(out) == [] and out["completion_state"] == "qualified-draft"
    info = out["evidence"][0]["recorded_outcomes"]
    assert info["checked"] is False and info["applied"] is True and "did not check" in out["warning_card"]["message"]
    assert out["warning_card"]["code"] == env.COMMITTED_TABLE


def test_over_mcp_a_record_is_a_claim_and_a_caller_cannot_mark_it_checked(home):
    base = {"task": TASK, "model": "ollama/llama3.1:70b", "success": True}
    reply = invoke_reply("llm_router", "record_outcome", json.dumps(base))
    assert reply["ok"] and reply["completion_state"] == "qualified-draft"
    assert reply["warning_card"]["code"] == env.RECORDED_OUTCOMES and reply["result"]["evidence"][0]["checked"] is False
    for extra in ({"checked": True}, {"verified": True}, {"approved_by": "the owner"}, {"source": "fallback"}):
        refused = invoke_reply("llm_router", "record_outcome", json.dumps({**base, **extra}))
        assert refused["ok"] is False and refused["warning_card"]["code"] == "G6_E_INPUT_VALIDATION", extra
    for missing in ("success", "model", "task"):
        refused = invoke_reply("llm_router", "record_outcome",
                               json.dumps({k: v for k, v in base.items() if k != missing}))
        assert refused["ok"] is False and refused["warning_card"]["code"] == "G6_E_INPUT_VALIDATION", missing
    unknown = invoke_reply("llm_router", "record_outcome", json.dumps({**base, "model": "acme/none"}))
    assert unknown["result"]["code"] == env.UNKNOWN_MODEL


# ── AGENT_ROUTER-01: outcomes are recorded and kept ──────────────────────


def test_over_mcp_outcomes_are_kept_and_a_later_call_reads_them(home):
    for _ in range(6):
        reply = invoke_reply("llm_router", "record_outcome",
                             json.dumps({"task": TASK, "model": "ollama/llama3.1:70b", "success": False}))
        assert reply["ok"], reply.get("error")
    assert (home / "llm_router" / "outcomes.sqlite").is_file()
    stats = invoke_reply("llm_router", "outcome_stats", json.dumps({"task": TASK}))["result"]
    assert stats["models"] == [{"model_id": "ollama/llama3.1:70b", "outcomes": 6, "successes": 0, "failures": 6,
                                "from_fallback": 0, "with_verdict": 0, "verdict_disagreements": 0,
                                "human_corrections": 0}]
    route = invoke_reply("llm_router", "route", json.dumps({"task": TASK, "use_outcomes": True, "seed": 3}))["result"]
    info = route["evidence"][0]["recorded_outcomes"]
    assert (info["outcomes"], info["applied"], info["seed"]) == (6, True, 3)


def test_outcome_stats_counts_where_a_reviewers_verdict_disagrees_with_the_reported_success(home):
    store = _outcomes().OutcomeStore()
    model = "ollama/a:7b"
    store.record(TASK, model, True, reviewer_verdict="rejected: wrong API")    # disagrees
    store.record(TASK, model, False, reviewer_verdict="Approved.")             # disagrees
    store.record(TASK, model, True, reviewer_verdict="LGTM")                   # a word it does not read
    store.record(TASK, model, True, reviewer_verdict="pass")                   # agrees
    store.record(TASK, model, False, human_correction="fixed the import")
    out = advice.outcome_stats(TASK, outcomes=store).model_dump(mode="json")
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == env.RECORDED_OUTCOMES
    (row,) = out["models"]
    assert row == {"model_id": model, "outcomes": 5, "successes": 3, "failures": 2, "from_fallback": 0,
                   "with_verdict": 3, "verdict_disagreements": 2, "human_corrections": 1}
    assert out["totals"]["verdict_disagreement_rate"] == pytest.approx(2 / 3)
    assert out["totals"]["human_correction_rate"] == pytest.approx(1 / 5)


@pytest.mark.parametrize(("verdict", "said"), [("approved", True), ("Rejected.", False), ("pass: fine", True),
                                               ("", None), ("lgtm", None), ("safe", None), ("verified", None)])
def test_a_verdicts_polarity_is_read_coarsely_and_label_words_are_not_read_at_all(verdict, said):
    assert _outcomes().polarity(verdict) is said


# ── G4: an unreadable store tightens ─────────────────────────────────────


def test_an_unreadable_store_leaves_the_committed_order_and_refuses_a_record(home):
    (home / "llm_router" / "outcomes.sqlite").mkdir(parents=True)              # a folder where the file should be
    store, reg = _outcomes().OutcomeStore(), _reg()
    out = advice.route(TASK, registry=reg, outcomes=store, seed=1).model_dump(mode="json")
    info = out["evidence"][0]["recorded_outcomes"]
    assert out["chain"] == advice.route(TASK, registry=reg).chain and info["applied"] is False
    assert "could not be read" in out["warning_card"]["message"]
    refused = advice.record_outcome(TASK, "ollama/a:7b", True, outcomes=store, registry=reg).model_dump(mode="json")
    assert refused["completion_state"] == "blocked-escalated" and refused["code"] == env.STORE_FAILED
    assert advice.outcome_stats(TASK, outcomes=store).model_dump(mode="json")["code"] == env.STORE_FAILED


def test_the_store_keeps_at_most_max_rows(home, monkeypatch):
    o = _outcomes()
    monkeypatch.setattr(o, "MAX_ROWS", 10)
    store = o.OutcomeStore()
    for i in range(15):
        store.record(TASK, "ollama/a:7b", i % 2 == 0)
    assert sum(store.counts(TASK, ["ollama/a:7b"])["ollama/a:7b"]) == 10


# ── nothing calls a model; nothing of the matrix is ported ───────────────


def test_recording_reading_and_tilting_call_no_model(home):
    before = model_calls()
    store, reg = _outcomes().OutcomeStore(), _reg()
    for _ in range(6):
        store.record(TASK, "ollama/b:7b", True)
    advice.route(TASK, registry=reg, outcomes=store, seed=1)
    advice.outcome_stats(TASK, outcomes=store)
    assert model_calls() == before
    text = (SRC / "outcomes.py").read_text(encoding="utf-8")
    assert "g6.core.llm" not in text.split('"""', 2)[2] and "import random" in text


def test_none_of_the_archive_routers_matrix_review_rules_or_re_rank_is_ported():
    text = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")).lower()
    for word in ("task_type_matrix", "codex", "label_if_passed", "patch_server", "docs_generator", "new_mcp_endpoint",
                 "llmblockcaller", "agentic_runtime", "open_tenant_db"):
        assert word not in text, word
    assert not list(SRC.rglob("*.yaml")) and (SRC / "outcomes.py").is_file()
    assert len((SRC / "outcomes.py").read_text(encoding="utf-8").splitlines()) <= 170


def test_poor_fit_route_is_declared_by_the_package_and_its_contract_and_no_longer_planned():
    assert "poor_fit_route" in g6.llm_router.__g6_mitigates__
    assert "g6.llm_router" not in FailureModeRegistry.planned()
    contract = get_block_contract_attr(LLMRouterBlock)
    assert "poor_fit_route" in contract.mitigates_failure_modes and contract.state_surface.persistence == "global"
    assert env.STORE_FAILED in contract.escalation_triggers