# SPDX-License-Identifier: Apache-2.0
"""Auto advice stays local until admission can be previewed without side effects."""
import json
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

from g6.core import cloud_consent as cloud, consent_callers, llm, selector
from g6.mcp import mission


SKIPPED = 'skipped: model rerank needs rerank="model" until the consent fix ships'
PREMORTEM_SKIPPED = "skipped: model proposals need propose=true until the consent fix ships"


@contextmanager
def no_auto_access(monkeypatch):
    """Fail on a send or consent access; preserve any enclosing capture."""
    def forbidden(*args, **kwargs):
        pytest.fail("auto must stay lexical without consulting model or consent state")

    with monkeypatch.context() as patch:
        for name in ("provider_from_env", "destination", "guard"):
            patch.setattr(consent_callers, name, forbidden)
        for name in ("_open", "_decision", "preview", "is_granted", "_written",
                     "_folder", "_mutex", "_append", "admission", "refuse_unshared"):
            patch.setattr(cloud, name, forbidden)
        context = nullcontext(cloud.current_capture()) if cloud.current_capture() else cloud.collect_sources()
        with context as capture:
            before = tuple(capture.sources)
            refused = capture.refused_provider
            yield
            assert tuple(capture.sources) == before
            assert capture.refused_provider == refused


def assert_auto_step(step, consumer="select"):
    expected = PREMORTEM_SKIPPED if consumer == "premortem" else SKIPPED
    assert not step["ran"] and step["model_calls"] == 0 and step["why"] == expected


@pytest.mark.parametrize("consumer", ["select", "premortem"])
@pytest.mark.parametrize("client", [None, "openai:api", "openai:codex"])
def test_auto_answers_without_resolving_or_recording_model_advice(monkeypatch, consumer, client):
    # Resolving a model or consulting a ledger would reintroduce an auto path
    # whose refused send could taint the otherwise answerable reply.
    def forbidden(*args, **kwargs):
        pytest.fail("auto must stay lexical without consulting model or consent state")

    monkeypatch.setattr(consent_callers, "provider_from_env", forbidden)
    for name in ("_open", "_decision", "preview", "refuse_unshared", "admission"):
        monkeypatch.setattr(cloud, name, forbidden)
    monkeypatch.setattr(mission.repo_tests, "checked_repo", lambda value: Path("synthetic-repository"))
    monkeypatch.setattr(mission.repo_tests, "_git", lambda *args: b"tests/test_boundary.py\0")
    with cloud.collect_sources() as capture:
        capture.client = (client, object()) if client else None
        capture.client_arguments = {"task": "review patch", "phase": "verify"}
        # Existing repository/prompt origins must survive, with no model_inputs
        # or client_request from an optional send added to this capture.
        cloud.record_sources((cloud.Source.text("model_inputs", "synthetic-prompt", "prior text"),))
        before = tuple(capture.sources)
        if consumer == "select":
            baseline = selector.select("review patch", "verify", rerank="lexical")
            reply = selector.select("review patch", "verify")
            assert reply["candidates"] == baseline["candidates"]
            step = next(s for s in reply["mechanisms"] if s["stage"] == "model_rerank")
        else:
            baseline = mission.premortem("synthetic-repository", "review patch", propose=False)
            reply = mission.premortem("synthetic-repository", "review patch")
            assert reply["hypotheses"] == baseline["hypotheses"]
            step = reply["model_step"]
        assert reply["ok"] and reply["completion_state"] == "qualified-draft"
        assert tuple(capture.sources) == before and not capture.refused_provider
    assert step == {"ran": False, "why": PREMORTEM_SKIPPED if consumer == "premortem" else SKIPPED, "model_calls": 0,
                    **({"stage": "model_rerank", "disagreements": []} if consumer == "select" else {})}


@pytest.mark.parametrize("consumer", ["select", "premortem"])
def test_explicit_model_advice_still_uses_one_guarded_fake_call(monkeypatch, consumer):
    fake = llm.FakeLLM(default=json.dumps({"order": [], "hypotheses": []}))
    monkeypatch.setattr(consent_callers, "provider_from_env", lambda: fake)
    guarded = []
    real_guard = consent_callers.guard

    def guard(caller):
        guarded.append(caller)
        return real_guard(caller)

    monkeypatch.setattr(consent_callers, "guard", guard)
    monkeypatch.setattr(mission.repo_tests, "checked_repo", lambda value: Path("synthetic-repository"))
    monkeypatch.setattr(mission.repo_tests, "_git", lambda *args: b"tests/test_boundary.py\0")
    if consumer == "select":
        reply = selector.select("review patch", "verify", rerank="model")
        step = next(s for s in reply["mechanisms"] if s["stage"] == "model_rerank")
    else:
        reply = mission.premortem("synthetic-repository", "review patch", propose=True)
        step = reply["model_step"]
    assert reply["ok"] and step["ran"] and step["model_calls"] == 1
    assert guarded == [fake] and len(fake.calls) == 1
