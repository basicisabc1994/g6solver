# SPDX-License-Identifier: Apache-2.0
"""Prepared execution uses sealed plain data and provider consent, without an override dialog."""
from __future__ import annotations

import copy

import pytest

from g6._agentic import kernel, ledger
from g6.core import llm
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, in_process_child, no_child

GRID = '{"value":[0.5]}'


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket
    import sys

    connect = socket.socket.connect

    def refuse(self, address):
        frame = sys._getframe(1)
        if frame.f_globals.get("__name__") == "socket" and "socketpair" in frame.f_code.co_name:
            return connect(self, address)
        pytest.fail("no connection allowed")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *a, **k: pytest.fail("no connection allowed"))

    def append(entry):
        ledger.Entry.model_validate(entry)
        return "fixture-ledger"

    monkeypatch.setattr(ledger, "append", append)


def prepare(**kwargs):
    request_id = "prepared-boundary"
    return kernel.prepare("cegis", "synthesize", kwargs.pop("params", copy.deepcopy(FLOAT_PARAMS)),
                          allow=ALLOW, llm=kwargs.pop("llm", llm.FakeLLM(default=GRID)),
                          request_id=request_id, **kwargs)


@pytest.mark.parametrize("field,value", [
    ("max_model_calls", type("Calls", (int,), {})(3)),
    ("max_output_chars", type("Chars", (int,), {})(20_000)),
    ("max_wall_clock_s", type("Seconds", (float,), {})(2.0)),
    ("max_model_calls", True),
])
def test_budget_rejects_non_exact_numbers_before_execution(monkeypatch, field, value):
    no_child(monkeypatch)
    values = {"max_model_calls": 3, "max_output_chars": 20_000, "max_wall_clock_s": 2.0, field: value}
    reply = prepare(budget=kernel.Budget(**values))
    assert type(reply) is dict and reply["agentic"]["code"] == "G6_E_INPUT_VALIDATION"


@pytest.mark.parametrize("value", [type("Case", (dict,), {})({"x": 1}), type("Text", (str,), {})("x")])
def test_parameters_reject_non_json_exact_types(monkeypatch, value):
    no_child(monkeypatch)
    reply = prepare(params={**FLOAT_PARAMS, "extra": value})
    assert type(reply) is dict and reply["agentic"]["code"] == "G6_E_INPUT_VALIDATION"


def test_oversize_parameters_refuse_before_the_original(monkeypatch):
    monkeypatch.setattr(kernel, "invoke_checked", lambda *a, **k: pytest.fail("oversize input reached original"))
    reply = prepare(params={"extra": "x" * 1_000_001})
    assert reply["agentic"]["code"] == "G6_E_INPUT_VALIDATION"


def test_nested_parameter_mutation_cannot_change_execution(monkeypatch):
    served = in_process_child(monkeypatch)
    params = copy.deepcopy(FLOAT_PARAMS)
    pending = prepare(params=params)
    bound = pending.binding["Parameters digest"]
    params["cases"][0]["output"] = 7.0
    kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, ""))
    assert served and all(kernel.digest(r["params"]) == bound for r in served)


@pytest.mark.parametrize("field,value", [("model", "changed"), ("timeout", 7.0),
                                        ("responder", lambda s, u: GRID), ("default", "changed")])
def test_snapshot_digest_covers_every_constructor_input(monkeypatch, field, value):
    started = no_child(monkeypatch)
    pending = prepare(llm=llm.FakeLLM(lambda s, u: GRID))
    object.__setattr__(pending.caller, field, value)
    reply = kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, ""))
    assert reply["agentic"]["code"] == kernel.DIGEST_MISMATCH and started == []


def test_public_pending_metadata_cannot_disable_checker_or_change_parameters(monkeypatch):
    served = in_process_child(monkeypatch)
    pending = prepare()
    object.__setattr__(pending, "checker", "")
    object.__setattr__(pending, "validator", "")
    object.__setattr__(pending, "params", {})
    reply = kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, ""))
    assert reply["agentic"]["outcome"] == "substituted" and reply["agentic"]["rechecked"] is True
    assert served[-1]["checker"] == "g6.cegis.block:recheck"


def test_unknown_caller_is_refused_without_reading_attributes(monkeypatch):
    no_child(monkeypatch)
    reads = []

    class Unknown:
        def __getattribute__(self, name):
            reads.append(name)
            return object.__getattribute__(self, name)

    reply = prepare(llm=Unknown())
    assert reply["agentic"]["code"] == kernel.CALLER_UNMETERED and reads == []


def test_digest_subclass_cannot_supply_comparison(monkeypatch):
    no_child(monkeypatch)

    class Forged(str):
        def __ne__(self, other):
            return False

    pending = prepare()
    reply = kernel.override(pending, confirmation=kernel.Confirmation(Forged("wrong"), ""))
    assert reply["agentic"]["code"] == kernel.DIGEST_MISMATCH


def test_worker_error_formatting_failure_counts_as_a_failed_model_call(monkeypatch):
    in_process_child(monkeypatch)

    class Meta(type):
        @property
        def __name__(cls):
            raise RuntimeError("unreadable")

    class Odd(Exception, metaclass=Meta):
        pass

    def answer(s, u):
        raise Odd()

    pending = prepare(llm=llm.FakeLLM(answer))
    reply = kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, ""))
    assert reply["agentic"]["reason"] == "model_failed"
    assert reply["agentic"]["cost"]["failed_model_calls"] == 1


def test_override_needs_no_confirmation_or_name(monkeypatch):
    in_process_child(monkeypatch)
    reply = kernel.override(prepare())
    assert reply["agentic"]["outcome"] == "substituted"
    assert reply["agentic"]["model"].startswith("FakeLLM") and "original" in reply


def test_legacy_confirmation_name_is_never_dispatched(monkeypatch):
    in_process_child(monkeypatch)

    class Name:
        def __str__(self):
            pytest.fail("obsolete name formatted")

    pending = prepare()
    reply = kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, Name()))
    assert reply["agentic"]["outcome"] == "substituted"


def test_mcp_runs_without_requesting_override_consent(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from g6.mcp import agentic, consent

    pending = prepare()
    monkeypatch.setenv("G6_MCP_SENSITIVE", "agentic.run,cegis.synthesize")
    monkeypatch.setattr(kernel, "prepare", lambda *a, **k: pending)
    in_process_child(monkeypatch)

    async def forbidden(*a, **k):
        pytest.fail("per-override dialog requested")

    monkeypatch.setattr(consent, "request_consent", forbidden)
    registered = []
    server = SimpleNamespace(tool=lambda: registered.append)
    jobs = SimpleNamespace(start=lambda work: work())
    agentic.register(server, object, jobs)
    reply = asyncio.run(registered[0]("cegis", "synthesize", None))
    assert reply["agentic"]["outcome"] == "substituted" and "original" in reply


@pytest.mark.parametrize("module,name", [("g6.cegis.hole", "valid_float_grid"), ("g6.cegis.block", "recheck")])
def test_declared_code_identity_cannot_change_after_prepare(monkeypatch, module, name):
    import importlib
    import types

    started = no_child(monkeypatch)
    pending = prepare()
    owner = importlib.import_module(module)
    original = getattr(owner, name)
    changed = types.FunctionType(original.__code__, original.__globals__, original.__name__)
    monkeypatch.setattr(owner, name, changed)
    reply = kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, ""))
    assert reply["agentic"]["code"] == kernel.DIGEST_MISMATCH and started == []
