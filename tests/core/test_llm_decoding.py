# SPDX-License-Identifier: Apache-2.0
"""Decoding control: a caller can fix temperature and seed, so a paired comparison measures the change, not the dice
(frontier design §4.2)."""
from __future__ import annotations

import pytest

from g6.core import llm


@pytest.fixture
def sent(monkeypatch):
    bodies: list[dict] = []

    def fake_post(url, body, headers, timeout):
        bodies.append(body)
        return {"message": {"content": "ok"}, "choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm, "_post_json", fake_post)
    return bodies


def test_ollama_sends_temperature_and_seed(sent):
    llm.OllamaCaller("m", temperature=0.0, seed=7).complete("s", "u", max_tokens=50)
    assert sent[0]["options"] == {"num_predict": 50, "temperature": 0.0, "seed": 7}


def test_unset_options_are_not_sent(sent):
    llm.OllamaCaller("m").complete("s", "u", max_tokens=50)
    assert sent[0]["options"] == {"num_predict": 50}


def test_vary_seed_gives_each_call_its_own_seed(sent):
    caller = llm.OllamaCaller("m", temperature=0.7, seed=7, vary_seed=True)
    for _ in range(3):
        caller.complete("s", "u")
    assert [b["options"]["seed"] for b in sent] == [7, 8, 9]


def test_openai_compatible_sends_temperature_and_seed(sent):
    llm.OpenAICompatibleCaller("m", base_url="http://x", api_key=None, temperature=0.0, seed=3).complete("s", "u")
    assert sent[0]["temperature"] == 0.0 and sent[0]["seed"] == 3
    llm.OpenAICompatibleCaller("m", base_url="http://x", api_key=None).complete("s", "u")
    assert "temperature" not in sent[1] and "seed" not in sent[1]
