# SPDX-License-Identifier: Apache-2.0
"""g6.core.llm.FallbackCaller: the callers you listed, in your order, the next one only when the current one raises
LLMError; which one answered and why each earlier one failed is recorded; no caller you did not list is ever added."""
from __future__ import annotations

import pytest

from g6.core import llm
from g6.core.llm import FakeLLM, FallbackCaller, LLMError


class Failing:
    """A caller that always fails the way a real one does when its server is down or its key is missing."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.calls = 0

    def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
        self.calls += 1
        raise LLMError(self.reason)


def test_the_first_caller_that_answers_is_used_and_the_rest_are_not_called() -> None:
    first, second = FakeLLM(default="from first"), FakeLLM(default="from second")
    chain = FallbackCaller([first, second], names=["a", "b"])
    assert chain.complete("s", "u") == "from first"
    assert chain.answered == "a" and chain.failures == []
    assert len(first.calls) == 1 and second.calls == []


def test_the_next_caller_is_tried_only_after_an_llm_error_and_each_failure_is_recorded() -> None:
    down, no_key = Failing("request to http://127.0.0.1:11434 failed"), Failing("HTTP 401 from openrouter")
    last = FakeLLM(default="answer")
    chain = FallbackCaller([down, no_key, last], names=["ollama/a", "openrouter/b", "ollama/c"])
    assert chain.complete("system", "user", max_tokens=99) == "answer"
    assert chain.answered == "ollama/c"
    assert chain.failures == [("ollama/a", "request to http://127.0.0.1:11434 failed"),
                              ("openrouter/b", "HTTP 401 from openrouter")]
    assert (down.calls, no_key.calls, last.calls) == (1, 1, [("system", "user")])


def test_when_every_caller_fails_it_raises_llm_error_naming_each_failure() -> None:
    chain = FallbackCaller([Failing("first down"), Failing("second down")], names=["one", "two"])
    with pytest.raises(LLMError, match="every caller failed") as raised:
        chain.complete("s", "u")
    assert "one: first down" in str(raised.value) and "two: second down" in str(raised.value)
    assert chain.answered is None and [n for n, _ in chain.failures] == ["one", "two"]


def test_an_error_that_is_not_an_llm_error_is_not_swallowed() -> None:
    class Broken:
        def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
            raise ValueError("a bug, not a provider failure")

    backup = FakeLLM()
    with pytest.raises(ValueError, match="a bug"):
        FallbackCaller([Broken(), backup]).complete("s", "u")
    assert backup.calls == []


def test_records_are_reset_on_every_call() -> None:
    flaky = Failing("down")
    chain = FallbackCaller([flaky, FakeLLM(default="ok")], names=["x", "y"])
    chain.complete("s", "u")
    chain.complete("s", "u")
    assert chain.failures == [("x", "down")] and chain.answered == "y" and flaky.calls == 2


def test_it_never_adds_a_caller_you_did_not_list(monkeypatch: pytest.MonkeyPatch, key_file) -> None:
    # every way a paid provider could be reached by default is set, and still nothing beyond the list is called
    key_file("OPENROUTER_API_KEY=placeholder\n")
    monkeypatch.setenv("G6_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder")
    monkeypatch.setattr(llm, "_post_json", lambda *a, **k: pytest.fail("a caller nobody listed was called"))
    chain = FallbackCaller([Failing("down")], names=["only"])
    with pytest.raises(LLMError):
        chain.complete("s", "u")
    assert chain.names == ("only",)


def test_it_needs_at_least_one_caller_and_a_name_for_each() -> None:
    with pytest.raises(LLMError, match="at least one caller"):
        FallbackCaller([])
    with pytest.raises(LLMError, match="one name per caller"):
        FallbackCaller([FakeLLM(), FakeLLM()], names=["a"])


def test_default_names_say_which_caller_and_model() -> None:
    chain = FallbackCaller([llm.OllamaCaller("llama3.1:8b"), FakeLLM()])
    assert chain.names == ("OllamaCaller(llama3.1:8b)", "FakeLLM")


def test_it_is_an_llm_caller_and_passes_max_tokens_through() -> None:
    seen: list[int] = []

    class Recorder:
        def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
            seen.append(max_tokens)
            return "ok"

    chain = FallbackCaller([Recorder()])
    assert isinstance(chain, llm.LLMCaller)
    chain.complete("s", "u", max_tokens=321)
    assert seen == [321]


def test_the_kill_switch_still_stops_every_real_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    chain = FallbackCaller([llm.OllamaCaller("a", base_url="http://127.0.0.1:9"),
                            llm.OllamaCaller("b", base_url="http://127.0.0.1:9")], names=["a", "b"])
    with pytest.raises(LLMError, match="every caller failed"):
        chain.complete("s", "u")
    assert all("G6_E_LLM_DISABLED" in reason for _, reason in chain.failures)
