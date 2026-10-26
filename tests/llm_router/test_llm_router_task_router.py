# SPDX-License-Identifier: Apache-2.0
"""g6.llm_router.TaskRouter: an LLMCaller for a cognitive task, a FallbackCaller over g6.core.llm's callers for the
route's chain, drawn only from the providers you allow (Ollama alone by default). Keys are read only as g6.core.llm
reads them, from the person's key file ($G6_HOME/.env), never from the environment or a project's .env file, and
nothing is written into the environment. No test reaches the network: g6.core.llm's one HTTP function is replaced by a
recorder."""
from __future__ import annotations

import os

import pytest

from g6.core import keys, llm
from g6.core.llm import FakeLLM, FallbackCaller, LLMError
from g6.llm_router import TaskRouter
from g6.llm_router.envelope import INPUT_INVALID, NO_MODEL
from g6.llm_router.registry import CognitiveTaskRegistry, ModelProfile


def _profile(model_id: str, provider: str, score: float, *, local: bool) -> ModelProfile:
    return ModelProfile(model_id=model_id, display_name=model_id, provider=provider, family="t",
                        cost_tier=0 if local else 2, task_scores={"summarization": score}, is_local=local,
                        pricing_prompt_per_m=0.0 if local else 0.5, pricing_completion_per_m=0.0 if local else 1.5)


@pytest.fixture
def reg() -> CognitiveTaskRegistry:
    rows = [_profile("ollama/big:32b", "ollama", 0.7, local=True), _profile("ollama/small:7b", "ollama", 0.5, local=True),
            _profile("openrouter/acme/paid", "openrouter", 0.9, local=False)]
    return CognitiveTaskRegistry({p.model_id: p for p in rows})


class Failing:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
        raise LLMError(self.reason)


@pytest.fixture
def posts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict]]:
    """Every request g6.core.llm would send, as (url, model, headers); Ollama answers "down", OpenRouter answers."""
    sent: list[tuple[str, str, dict]] = []

    def fake_post(url: str, body: dict, headers: dict, timeout: float) -> dict:
        sent.append((url, body["model"], headers))
        if "/api/chat" in url:
            raise LLMError(f"request to {url} failed: connection refused")
        return {"choices": [{"message": {"content": "from openrouter"}}]}

    monkeypatch.setattr(llm, "_post_json", fake_post)
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    return sent


def test_the_default_allows_only_the_local_provider(reg):
    router = TaskRouter(registry=reg)
    assert router.providers == ("ollama",)
    caller = router.caller_for("summarization")
    assert isinstance(caller, FallbackCaller) and caller.names == ("ollama/big:32b", "ollama/small:7b")


def test_the_caller_tries_the_route_in_order_and_records_who_answered(reg):
    built = {"ollama/big:32b": Failing("big is not pulled"), "ollama/small:7b": FakeLLM(default="small answered")}
    caller = TaskRouter(registry=reg, build=lambda provider, model_id: built[model_id]).caller_for("summarization")
    assert caller.complete("s", "u") == "small answered"
    assert caller.answered == "ollama/small:7b" and caller.failures == [("ollama/big:32b", "big is not pulled")]


def test_the_chain_is_the_route_and_never_reaches_a_provider_you_did_not_allow(reg):
    router = TaskRouter(registry=reg, fallbacks=5, build=lambda provider, model_id: FakeLLM())
    assert router.caller_for("summarization").names == tuple(router.route("summarization").chain)
    assert not [n for n in router.caller_for("summarization").names if n.startswith("openrouter/")]
    both = TaskRouter(["ollama", "openrouter"], registry=reg, fallbacks=5, build=lambda p, m: FakeLLM())
    assert both.caller_for("summarization").names == ("ollama/big:32b", "ollama/small:7b", "openrouter/acme/paid")


def test_the_default_builder_uses_the_core_callers_with_the_model_name_the_provider_expects(reg, posts, key_file):
    key_file("OPENROUTER_API_KEY=placeholder-key\n")
    env = {"OLLAMA_BASE_URL": "http://127.0.0.1:9"}
    caller = TaskRouter(["ollama", "openrouter"], registry=reg, env=env).caller_for("summarization")
    assert caller.complete("s", "u") == "from openrouter" and caller.answered == "openrouter/acme/paid"
    assert [(url, model) for url, model, _ in posts] == [
        ("http://127.0.0.1:9/api/chat", "big:32b"), ("http://127.0.0.1:9/api/chat", "small:7b"),
        ("https://openrouter.ai/api/v1/chat/completions", "acme/paid")]
    assert posts[2][2] == {"authorization": "Bearer placeholder-key"}


def test_without_a_key_the_paid_provider_fails_by_name_and_sends_nothing(reg, posts, key_file):
    key_file("# no key here\n")
    caller = TaskRouter(["openrouter"], registry=reg, env={}).caller_for("summarization")
    with pytest.raises(LLMError, match="every caller failed"):
        caller.complete("s", "u")
    assert f"OPENROUTER_API_KEY is not in {keys.path()}" in caller.failures[0][1] and posts == []


def test_a_key_in_a_dot_env_file_is_never_read(reg, posts, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=placeholder-from-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    caller = TaskRouter(["openrouter"], registry=reg).caller_for("summarization")
    with pytest.raises(LLMError):
        caller.complete("s", "u")
    assert posts == [] and "OPENROUTER_API_KEY" not in os.environ


def test_the_key_file_is_read_when_the_caller_is_built_and_the_environment_never_written(reg, posts, monkeypatch,
                                                                                          key_file):
    # owner ruling 2026-10-21: a key set in the environment (for another program) is not one the person gave G6
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder-env")
    key_file("OPENROUTER_API_KEY=placeholder-file\n")
    before = dict(os.environ)
    caller = TaskRouter(["openrouter"], registry=reg).caller_for("summarization")
    assert caller.complete("s", "u") == "from openrouter"
    assert posts[0][2] == {"authorization": "Bearer placeholder-file"} and dict(os.environ) == before


def test_the_kill_switch_stops_every_call(reg, posts, monkeypatch):
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    caller = TaskRouter(registry=reg).caller_for("summarization")
    with pytest.raises(LLMError):
        caller.complete("s", "u")
    assert posts == [] and all("G6_E_LLM_DISABLED" in reason for _, reason in caller.failures)


def test_installed_and_fallbacks_shape_the_chain(reg):
    build = lambda provider, model_id: FakeLLM()  # noqa: E731
    assert TaskRouter(registry=reg, installed=["small:7b"], build=build).caller_for("summarization").names == (
        "ollama/small:7b",)
    assert TaskRouter(registry=reg, fallbacks=0, build=build).caller_for("summarization").names == ("ollama/big:32b",)


def test_no_route_raises_llm_error_with_its_code(reg):
    with pytest.raises(LLMError, match=NO_MODEL):
        TaskRouter(registry=reg).caller_for("embedding")
    with pytest.raises(LLMError, match=INPUT_INVALID):
        TaskRouter(registry=reg).caller_for("astrology")


def test_an_unknown_provider_is_refused_when_the_router_is_made():
    with pytest.raises(ValueError, match="unknown provider"):
        TaskRouter(["ollama", "acme"])
    with pytest.raises(ValueError, match="at least one provider"):
        TaskRouter([])


def test_on_the_committed_table_the_default_route_is_a_local_model():
    caller = TaskRouter(build=lambda provider, model_id: FakeLLM()).caller_for("code_generation")
    assert caller.names and all(n.startswith("ollama/") for n in caller.names)
