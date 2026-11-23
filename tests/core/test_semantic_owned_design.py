"""Every known wrapper needs a guarded path; inheritance supplies no custody."""
import importlib
from contextlib import contextmanager

import pytest

from g6.core import consent_callers as cc, llm, semantic
from g6.reasoning.budget import Budget, MeteredCaller


WRAPPERS = sorted(name for name, kind in cc.CALLER_KINDS.items() if kind.startswith("wrapper:"))


@pytest.mark.parametrize("qualified", WRAPPERS)
def test_every_known_wrapper_has_a_nonmutating_ownership_policy(qualified):
    module, _, name = qualified.rpartition(".")
    base = getattr(importlib.import_module(module), name)
    caller = object.__new__(base)
    leaf = llm.OpenAICompatibleCaller("fixture", base_url="https://example.invalid", api_key=None)
    slot = cc.CALLER_KINDS[qualified].partition(":")[2]
    setattr(caller, slot, (leaf,) if slot == "_callers" else leaf)
    if qualified == "g6.core.llm.TimingLLMCaller":
        caller._on_latency = lambda seconds: None
    before = dict(vars(caller))
    sender = semantic.owned(caller)
    stateless = qualified in {"g6.core.consent_callers.CallerWrapper", "g6.core.llm.TimingLLMCaller"}
    assert (sender is not None) == stateless, qualified
    assert vars(caller) == before


@pytest.mark.parametrize("qualified", WRAPPERS)
def test_every_wrapper_subclass_is_refused_without_sending(qualified):
    module, _, name = qualified.rpartition(".")
    base = getattr(importlib.import_module(module), name)
    kind = type("DictionaryRoute", (base,), {
        "complete": lambda self, system, user, **kw: vars(self)["raw"].complete(system, user, **kw),
    })
    raw = llm.FakeLLM(default='{"value":true}')
    caller = object.__new__(kind)
    slot = cc.CALLER_KINDS[qualified].partition(":")[2]
    setattr(caller, slot, [raw] if slot == "_callers" else raw)
    caller.raw = raw
    assert semantic.owned(caller) is None, qualified
    assert not raw.calls


def test_unknown_dictionary_wrapper_cannot_reach_transport(monkeypatch):
    class DictionaryWrapper(cc.CallerWrapper):
        def complete(self, system, user, **kwargs):
            return vars(self)["inner"].complete(system, user, **kwargs)

    sent = []
    monkeypatch.setattr(llm, "_post_json", lambda *a, **kw: sent.append(True))
    caller = DictionaryWrapper(llm.OpenAICompatibleCaller("fixture", base_url="https://example.invalid", api_key=None))
    assert semantic.owned(caller) is None
    with semantic.request(llm=caller):
        got = semantic.decide("refusal", "fixture", False)
    assert got.value is False and not sent


def test_known_wrapper_checks_admission_at_send_without_mutating_input(monkeypatch):
    sent, denied = [], []

    @contextmanager
    def refuse(*args, **kwargs):
        denied.append(True)
        raise llm.LLMError("G6_E_CLOUD_CONSENT_REQUIRED")
        yield

    monkeypatch.setattr(cc, "admission", refuse)
    monkeypatch.setattr(llm, "_post_json", lambda *a, **kw: sent.append(True))
    leaf = llm.OpenAICompatibleCaller("fixture", base_url="https://example.invalid", api_key=None)
    wrapper = cc.CallerWrapper(leaf)
    sender = semantic.owned(wrapper)
    assert sender is not None and wrapper.inner is leaf
    with pytest.raises(llm.LLMError, match="CONSENT_REQUIRED"):
        sender.complete("system", "user")
    assert denied and not sent and wrapper.inner is leaf


def test_reusing_a_known_meter_preserves_its_spent_budget():
    leaf = llm.FakeLLM(default="fixture")
    meter = MeteredCaller(leaf, Budget(max_model_calls=1))
    assert semantic.owned(meter) is meter
    meter.complete("", "")
    with pytest.raises(llm.LLMError, match="BUDGET_EXHAUSTED"):
        semantic.owned(meter).complete("", "")
    assert len(leaf.calls) == 1


def test_a_wrapper_requiring_inner_replacement_is_refused_without_resetting_state():
    leaf = llm.OpenAICompatibleCaller("fixture", base_url="https://example.invalid", api_key=None)
    meter = MeteredCaller(leaf)
    assert semantic.owned(meter) is None
    assert meter.inner is leaf and meter.calls == 0
