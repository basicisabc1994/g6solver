# SPDX-License-Identifier: Apache-2.0
"""g6.core.llm: explicit providers, no silent paid fallback, honest failures. All offline."""
from __future__ import annotations

import http.server
import json
import threading
from types import SimpleNamespace

import pytest

from g6.core import llm


def test_default_provider_is_none_and_refuses_explicitly() -> None:
    caller = llm.provider_from_env({})
    with pytest.raises(llm.LLMError, match="G6_E_NO_MODEL"):
        caller.complete("s", "u")


def test_unknown_provider_is_refused_not_routed_to_a_paid_one() -> None:
    with pytest.raises(llm.LLMError, match="unknown G6_LLM_PROVIDER"):
        llm.provider_from_env({"G6_LLM_PROVIDER": "gpt-4o"})


def test_openai_compatible_requires_a_model_name() -> None:
    with pytest.raises(llm.LLMError, match="explicit model"):
        llm.provider_from_env({"G6_LLM_PROVIDER": "openrouter"})


def test_a_paid_provider_with_no_key_fails_by_name_and_sends_nothing(monkeypatch, key_file) -> None:
    sent: list = []
    monkeypatch.setattr(llm, "_post_json", lambda *args, **kwargs: sent.append(args) or {})
    for provider, key in (("openrouter", "OPENROUTER_API_KEY"), ("openai", "OPENAI_API_KEY"),
                          ("anthropic", "ANTHROPIC_API_KEY")):
        caller = llm.provider_from_env({"G6_LLM_PROVIDER": provider, "G6_LLM_MODEL": "some/model"})
        with pytest.raises(llm.LLMError, match=f"G6_E_NO_MODEL: {key} is not in ") as caught:
            caller.complete("s", "u")
        assert str(key_file.path) in str(caught.value) and f"{key}=" in str(caught.value)
    assert sent == []
    own = llm.provider_from_env({"G6_LLM_PROVIDER": "openai", "G6_LLM_MODEL": "m",
                                 "OPENAI_BASE_URL": "http://127.0.0.1:8000/v1"})   # a server of your own needs no key
    assert isinstance(own, llm.EmptyRetry) and own._inner.base_url == "http://127.0.0.1:8000/v1"


def test_a_key_in_the_environment_is_never_used(monkeypatch, key_file) -> None:
    # a key set for another program (a coding agent's own ANTHROPIC_API_KEY) is not one the person gave G6
    sent: list = []
    monkeypatch.setattr(llm, "_post_json", lambda *args, **kwargs: sent.append(args) or {})
    for provider, key in llm.PROVIDER_KEYS.items():
        monkeypatch.setenv(key, "from-the-environment")
        for env in (None, {"G6_LLM_PROVIDER": provider, "G6_LLM_MODEL": "m", key: "from-the-environment"}):
            monkeypatch.setenv("G6_LLM_PROVIDER", provider)
            monkeypatch.setenv("G6_LLM_MODEL", "m")
            with pytest.raises(llm.LLMError, match=f"{key} is not in "):
                llm.provider_from_env(env).complete("s", "u")
    assert sent == []


def test_a_component_can_name_the_provider_and_model_and_the_key_file_holds_the_key(key_file) -> None:
    key_file("OPENROUTER_API_KEY=placeholder\n")
    caller = llm.provider_from_env({"G6_LLM_PROVIDER": "anthropic"}, provider="openrouter", model="acme/model")
    assert caller._inner.model == "acme/model" and caller._inner.api_key == "placeholder"
    assert llm.PROVIDERS == ("ollama", "anthropic", "openai", "openrouter")
    assert llm.PROVIDER_KEYS == {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                                 "openrouter": "OPENROUTER_API_KEY"}


def test_the_key_file_can_hold_the_settings_and_the_environment_overrides_them(monkeypatch, key_file) -> None:
    key_file("G6_LLM_PROVIDER=openrouter\nG6_LLM_MODEL=acme/model\nOPENROUTER_API_KEY=placeholder\n")
    caller = llm.provider_from_env()
    assert caller._inner.model == "acme/model" and caller._inner.api_key == "placeholder"
    monkeypatch.setenv("G6_LLM_PROVIDER", "none")
    assert isinstance(llm.provider_from_env(), llm.NoModel)


def test_keys_given_in_python_are_used_as_given(key_file) -> None:
    caller = llm.provider_from_env({"G6_LLM_PROVIDER": "openrouter", "G6_LLM_MODEL": "m"},
                                   keys={"OPENROUTER_API_KEY": "given"})
    assert caller._inner.api_key == "given"
    anthropic = llm.provider_from_env({"G6_LLM_PROVIDER": "anthropic"}, keys={"ANTHROPIC_API_KEY": "given"})
    assert anthropic._inner._api_key == "given"


def test_empty_retry_escalates_then_raises() -> None:
    budgets: list[int] = []

    class Empty:
        def complete(self, system, user, *, max_tokens=llm.DEFAULT_MAX_TOKENS):  # noqa: ANN001
            budgets.append(max_tokens)
            return "  "

    with pytest.raises(llm.LLMError, match="empty completion"):
        llm.EmptyRetry(Empty()).complete("s", "u", max_tokens=100)
    assert budgets == [100, 8192, 16384, 32768]
    assert sum(budgets) == llm.authorized_completion_tokens(100)


def test_kill_switch_stops_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    with pytest.raises(llm.LLMError, match="G6_E_LLM_DISABLED"):
        llm.OllamaCaller(base_url="http://127.0.0.1:9").complete("s", "u")


def test_ollama_caller_against_a_local_stub() -> None:
    seen: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            seen["path"] = self.path
            seen["body"] = json.loads(self.rfile.read(int(self.headers["content-length"])))
            payload = json.dumps({"message": {"content": "hello"}}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a) -> None:  # noqa: ANN002
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        out = llm.OllamaCaller("m", base_url=f"http://127.0.0.1:{server.server_port}").complete("sys", "hi")
    finally:
        server.shutdown()
    assert out == "hello"
    assert seen["path"] == "/api/chat"
    assert seen["body"]["messages"][1] == {"role": "user", "content": "hi"}


def test_anthropic_refusal_raises_instead_of_returning_text() -> None:
    response = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"), content=[])
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **k: response)))
    with pytest.raises(llm.LLMError, match="G6_E_MODEL_REFUSED: cyber"):
        llm.AnthropicCaller(client=client).complete("s", "u")


def test_anthropic_uses_default_model_and_fallbacks() -> None:
    captured: dict = {}

    def create(**kw):  # noqa: ANN003
        captured.update(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    assert llm.AnthropicCaller(client=client).complete("s", "u") == "ok"
    assert captured["model"] == "claude-opus-5-5"
    assert captured["fallbacks"] == "default"


def test_built_in_callers_are_counted_so_zero_cannot_claim_model_free(monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core.model_calls import counting
    response = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")])
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **k: response)))
    monkeypatch.setattr(llm, "_post_json", lambda *a: {"message": {"content": "x"}, "choices": [{}]})
    with counting() as calls:
        llm.FakeLLM().complete("s", "u")
        llm.AnthropicCaller(client=client).complete("s", "u")
        llm.OllamaCaller("m").complete("s", "u")
        llm.OpenAICompatibleCaller("m", base_url="http://x", api_key=None).complete("s", "u")
        with pytest.raises(llm.LLMError):
            llm.NoModel().complete("s", "u")
        monkeypatch.setenv("G6_DISABLE_LLM", "1")
        with pytest.raises(llm.LLMError):
            llm.OllamaCaller("m").complete("s", "u")
    assert calls() == 4   # a refused or unconfigured call asked no model


def test_extract_json_tolerates_fences_and_prose() -> None:
    assert llm.extract_json('Sure:\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    with pytest.raises(llm.LLMError):
        llm.extract_json("no json here")
