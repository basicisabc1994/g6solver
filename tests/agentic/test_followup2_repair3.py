# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811 - imported pytest fixtures are also test parameters
"""Best-effort pins inspect code without dispatching caller hooks; the process is the boundary."""
from __future__ import annotations

import functools
from urllib.parse import SplitResult

import pytest
from pydantic import ValidationError

from g6._agentic import kernel, ledger
from g6.core import llm
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, in_process_child, no_child
from tests.agentic.test_followup2 import entries, offline  # noqa: F401

GRID = '{"value":[0.5]}'


def prepare(answer=None, **kwargs):
    return kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                          llm=llm.FakeLLM(answer, default=GRID), **kwargs)


@pytest.mark.parametrize("wrapper", [staticmethod, classmethod, functools.cache, functools.lru_cache],
                         ids=["staticmethod", "classmethod", "cache", "lru_cache"])
def test_wrapped_code_swap_refuses_before_child(monkeypatch, entries, wrapper):
    started = no_child(monkeypatch)

    def step(*args):
        return GRID

    held = wrapper(step)

    def answer(system, user):
        return held(system, user)

    pending = prepare(answer)
    assert type(pending) is kernel.Pending

    def changed(*args):
        pytest.fail("changed code ran")

    monkeypatch.setattr(step, "__code__", changed.__code__)
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == []


@pytest.mark.parametrize("binding", ["plain", "static", "class", "inherited", "call"])
def test_class_held_in_closure_follows_type_attributes(monkeypatch, entries, binding):
    started = no_child(monkeypatch)

    def step(*args):
        return GRID

    class Base:
        helper = step if binding == "inherited" else None

    class Helper(Base):
        if binding == "call":
            __call__ = step
        elif binding != "inherited":
            helper = staticmethod(step) if binding == "static" else classmethod(step) if binding == "class" else step

    def answer(system, user):
        return Helper.__call__(system, user) if binding == "call" else Helper.helper(system, user)

    pending = prepare(answer)
    assert type(pending) is kernel.Pending

    def changed(*args):
        pytest.fail("changed class code ran")

    monkeypatch.setattr(step, "__code__", changed.__code__)
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == []


def test_callable_inspection_bypasses_all_metaclass_hooks(monkeypatch, entries):
    in_process_child(monkeypatch)
    hooks = []

    class Meta(type):
        def __getattribute__(cls, name):
            hooks.append("getattribute")
            return type.__getattribute__(cls, name)

        def __eq__(cls, other):
            hooks.append("eq")
            return type.__eq__(cls, other)

        def __hash__(cls):
            hooks.append("hash")
            return type.__hash__(cls)

    class Session(metaclass=Meta):
        def __call__(self, system, user):
            return GRID

    pending = prepare(Session())
    assert type(pending) is kernel.Pending
    assert kernel.override(pending)["agentic"]["outcome"] == "substituted"
    assert hooks == []


@pytest.mark.parametrize(("make", "args", "expected"), [
    (lambda: functools.cache(lambda s, u: GRID), ("system", "user"), GRID),
    (lambda: functools.lru_cache(lambda s, u: GRID), ("system", "user"), GRID),
    (lambda: functools.partial(str.join, ""), ([GRID],), GRID),
    (lambda: functools.partial(int, base=10), ("1",), 1),
], ids=["cache", "lru_cache", "method_descriptor", "class"])
def test_ordinary_callable_edges_are_accepted(monkeypatch, entries, make, args, expected):
    in_process_child(monkeypatch)
    held = make()

    def answer(system, user):
        assert held(*args) == expected
        return GRID

    pending = prepare(answer)
    assert type(pending) is kernel.Pending
    assert kernel.override(pending)["agentic"]["outcome"] == "substituted"


@pytest.mark.parametrize("answer", [str, str.join, len, functools.partial(str), functools.partial(str.join, ""),
                                    functools.partial(len), functools.cache(lambda s, u: GRID)])
def test_opaque_responder_is_prepared_by_identity(answer):
    pending = prepare(answer)
    assert type(pending) is kernel.Pending
    kernel.declined(pending, "cancelled")


@pytest.mark.parametrize(("field", "value"), [("route", object()), ("route", ()), ("timeout", 600),
                                                ("max_new_tokens", True)])
def test_public_metadata_requires_exact_types_before_equality(monkeypatch, entries, field, value):
    started = no_child(monkeypatch)
    pending = prepare()
    object.__setattr__(pending.caller, field, value)
    reply = kernel.override(pending)
    assert reply["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == []


def test_untrusted_route_equality_never_runs(monkeypatch, entries):
    started = no_child(monkeypatch)
    hooks = []

    class Route:
        def __eq__(self, other):
            hooks.append("eq")
            return True

    pending = prepare()
    object.__setattr__(pending.caller, "route", Route())
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert hooks == [] and started == []


def test_endpoint_split_result_remains_valid_metadata(monkeypatch, entries):
    in_process_child(monkeypatch)
    monkeypatch.setattr(llm, "_post_json", lambda *a, **k: {"message": {"content": GRID}})
    pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                             llm=llm.OllamaCaller("fixture-model", "http://127.0.0.1:9"))
    assert type(pending.caller.route) is SplitResult
    assert kernel.override(pending)["agentic"]["outcome"] == "substituted"


@pytest.mark.parametrize("cap", [1, 3, 5])
def test_ledger_authority_list_is_bounded_by_run_budget(monkeypatch, entries, cap):
    in_process_child(monkeypatch)
    reply = kernel.override(prepare(budget=kernel.Budget(max_model_calls=cap)))
    assert reply["agentic"]["outcome"] == "substituted"
    body = entries[0]
    body["authority_sends"] = [{"answer": "allowed in code", "provider": ""}] * cap
    ledger.Entry.model_validate(body)
    body["authority_sends"].append({"answer": "allowed in code", "provider": ""})
    with pytest.raises(ValidationError, match="too_long"):
        ledger.Entry.model_validate(body)
