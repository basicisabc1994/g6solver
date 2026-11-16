# SPDX-License-Identifier: Apache-2.0
"""Authority follows admission; public metadata cannot release sealed responders."""
from __future__ import annotations

import copy
import gc
import socket
import sys
import weakref
from pathlib import Path

import pytest

import g6
from g6._agentic import kernel, ledger
from g6.core import cloud_consent, host_models, llm
from g6.core.model_calls import note_model_call
from g6.self_maintenance import usage
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, INT_PARAMS, cegis_in_process, in_process_child, no_child

GRID = '{"value":[0.5]}'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect = socket.socket.connect

    def refuse(self, address, *args, **kwargs):
        frame = sys._getframe(1)
        if frame.f_globals.get("__name__") == "socket" and "socketpair" in frame.f_code.co_name:
            return connect(self, address, *args, **kwargs)
        raise AssertionError("no connection allowed")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


@pytest.fixture
def entries(monkeypatch):
    seen = []

    def append(entry):
        ledger.Entry.model_validate(entry)
        seen.append(entry)
        return "fixture-ledger"

    monkeypatch.setattr(ledger, "append", append)
    return seen


@pytest.mark.parametrize("responder", [None, lambda s, u: GRID])
def test_process_authority_is_its_code_allowance(monkeypatch, entries, responder):
    in_process_child(monkeypatch)
    pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                             llm=llm.FakeLLM(responder, default=GRID))
    assert "allowed in code" in pending.question
    reply = kernel.override(pending)
    assert reply["agentic"]["outcome"] == "substituted"
    assert reply["agentic"]["person"] == {
        "answer": "allowed in code", "name": "", "authenticated": False, "provider": ""}
    assert entries[0]["person"] == reply["agentic"]["person"]
    assert "allowed in code" in reply["warning_card"]["message"]


@pytest.mark.parametrize("noted", [True, False])
def test_endpoint_records_the_provider_of_the_actual_admission(monkeypatch, entries, noted):
    in_process_child(monkeypatch)
    monkeypatch.setattr(llm, "_post_json", lambda *a, **k: {"message": {"content": GRID}})
    if not noted:
        monkeypatch.setattr(llm.OllamaCaller, "complete", lambda *a, **k: GRID)
    reply = kernel.run("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                       llm=llm.OllamaCaller("fixture", "http://127.0.0.1:9"))
    assert reply["agentic"]["outcome"] == "substituted"
    assert entries[0]["person"] == {
        "answer": "local model", "name": "", "authenticated": False, "provider": "local:loopback"}
    assert "local model (local:loopback)" in reply["warning_card"]["message"]


def test_host_label_without_an_admission_records_only_code_allowance(monkeypatch, entries):
    in_process_child(monkeypatch)
    monkeypatch.setattr(usage, "_complete_host", lambda *a, **k: GRID)
    reply = kernel.run("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                       llm=host_models.CALLERS["claude_code"]())
    assert reply["agentic"]["outcome"] == "substituted"
    assert entries[0]["person"]["answer"] == "allowed in code"
    assert entries[0]["person"]["provider"] == ""


def test_host_observes_admission_inside_its_send(monkeypatch, entries, accepted_cloud_dialog):
    in_process_child(monkeypatch)
    provider = "anthropic:claude_code"
    refs = accepted_cloud_dialog("fixture:kernel-fu2", provider, text="fixture")

    def complete(*args, **kwargs):
        with cloud_consent.admission(provider, refs, "0" * 64):
            note_model_call()
            return GRID

    monkeypatch.setattr(usage, "_complete_host", complete)
    reply = kernel.run("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                       llm=host_models.CALLERS["claude_code"]())
    assert reply["agentic"]["outcome"] == "substituted"
    assert entries[0]["person"]["answer"] == "provider consent"
    assert entries[0]["person"]["provider"] == provider


def test_a_public_snapshot_cannot_release_the_sealed_responder(monkeypatch):
    no_child(monkeypatch)

    def answer(s, u):
        return GRID

    retained = weakref.ref(answer)
    pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=llm.FakeLLM(answer))
    del answer
    object.__setattr__(pending.caller, "responder", None)
    gc.collect()
    assert retained() is not None
    reply = kernel.override(pending)
    assert reply["agentic"]["code"] in (kernel.CALLER_UNMETERED, kernel.DIGEST_MISMATCH)
    del pending
    gc.collect()
    assert retained() is None


def test_nested_tuples_keep_the_original_answer_when_no_override_is_requested(monkeypatch):
    cegis_in_process(monkeypatch)
    no_child(monkeypatch)
    params = copy.deepcopy(INT_PARAMS)
    params["cases"] = tuple(params["cases"])
    params["domain"] = {"x": tuple(case["input"]["x"] for case in params["cases"])}
    original = g6.invoke("cegis", "synthesize", params, allow=ALLOW)
    observed = []
    invoke_checked = kernel.invoke_checked

    def invoke(*args, **kwargs):
        answer = invoke_checked(*args, **kwargs)
        observed.append(answer)
        return answer

    monkeypatch.setattr(kernel, "invoke_checked", invoke)
    reply = kernel.run("cegis", "synthesize", params, allow=ALLOW)
    assert len(observed) == 1 and reply is observed[0]
    assert reply["ok"] is True
    # Door-generated request/run identifiers are intentionally different per invocation.
    assert reply["result"]["program"] == original["result"]["program"]
    assert reply["result"]["control"] == original["result"]["control"]
    assert reply["completion_state"] == original["completion_state"]
    assert "agentic" not in reply


def test_nested_tuples_are_canonical_lists():
    assert kernel._json_copy({"items": ((1, 2), (3, (4, 5)))}) == {"items": [[1, 2], [3, [4, 5]]]}


@pytest.mark.parametrize("value", [object(), {1, 2}, float("nan")])
def test_tuples_do_not_admit_non_json_values(monkeypatch, value):
    monkeypatch.setattr(kernel, "invoke_checked", lambda *a, **k: pytest.fail("invalid input reached original"))
    reply = kernel.run("cegis", "synthesize", {"items": (value,)}, allow=ALLOW)
    assert reply["agentic"]["code"] == "G6_E_INPUT_VALIDATION"


def test_documented_python_entry_point_is_callable():
    from g6._agentic.kernel import run

    text = (Path(__file__).resolve().parents[2] /
            "docs/superpowers/specs/2026-10-29-agentic-override-kernel-design.md").read_text(encoding="utf-8")
    assert "from g6._agentic.kernel import run" in text
    assert callable(run)
