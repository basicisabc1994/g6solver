# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811 - imported pytest fixtures are also test parameters
"""Authority at the send, callable mutation refusal, and single-use preparation lifetime."""
from __future__ import annotations

import gc
import weakref

import pytest

from g6._agentic import kernel
from g6.cegis import block, hole, runner
from g6.core import cloud_consent, host_models, llm
from g6.core.model_calls import note_model_call
from g6.self_maintenance import usage
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, in_process_child, no_child
from tests.agentic.test_followup2 import entries, offline  # noqa: F401 - shared offline fixtures

GRID = '{"value":[0.5]}'


@pytest.fixture(autouse=True)
def state_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))


def prepare(model=None):
    return kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                          llm=model if model is not None else llm.FakeLLM(default=GRID))


def test_local_admission_records_local_model_everywhere(monkeypatch, entries):
    in_process_child(monkeypatch)
    monkeypatch.setattr(llm, "_post_json", lambda *a, **k: {"message": {"content": GRID}})
    pending = prepare(llm.OllamaCaller("fixture", "http://127.0.0.1:9"))
    assert "local model" in pending.binding["Authority"]
    reply = kernel.override(pending)
    assert reply["agentic"]["outcome"] == "substituted"
    assert entries[0]["person"] == reply["agentic"]["person"] == {
        "answer": "local model", "provider": "local:loopback", "name": "", "authenticated": False}
    assert "local model (local:loopback)" in reply["warning_card"]["message"]
    assert reply["agentic"]["summary"] == "Authority: local model (local:loopback)"


def test_consent_refusal_is_recorded_without_a_substitute(monkeypatch, entries):
    in_process_child(monkeypatch)
    pending = prepare(host_models.CALLERS["claude_code"]())
    reply = kernel.override(pending)
    assert reply["agentic"]["reason"] == "model_failed"
    assert reply["agentic"]["substituted"] is False
    assert entries[0]["person"]["answer"] == "refused by the consent check"
    assert entries[0]["person"]["provider"] == "anthropic:claude_code"
    assert "refused by the consent check" in reply["warning_card"]["message"]


@pytest.mark.parametrize("change", ["code", "defaults", "kwdefaults", "kwdefault_item", "closure"])
def test_mutated_responder_refuses_before_any_child(monkeypatch, entries, change):
    caller_data = change in {"closure", "kwdefault_item"}
    if caller_data:
        in_process_child(monkeypatch)
        started = None
    else:
        started = no_child(monkeypatch)
    closed = object()
    observed = []

    def answer(system, user, extra=None, *, option=None):
        assert closed is not None
        observed.append((closed, option))
        return GRID

    pending = prepare(llm.FakeLLM(answer))
    if change == "code":
        def replacement(system, user, extra=None, *, option=None):
            assert closed is not None
            assert observed == []
            pytest.fail("changed responder ran")
        answer.__code__ = replacement.__code__
    elif change == "defaults":
        answer.__defaults__ = (object(),)
    elif change == "kwdefaults":
        answer.__kwdefaults__ = {"option": object()}
    elif change == "kwdefault_item":
        answer.__kwdefaults__["option"] = object()
    else:
        closed = object()
    reply = kernel.override(pending)
    if caller_data:
        assert reply["agentic"]["outcome"] == "substituted"
        assert reply["agentic"]["substituted"] is True
        assert reply["completion_state"] == "qualified-draft"
        assert reply["original"] == pending.original
        assert observed == [(closed, answer.__kwdefaults__["option"])]
        assert reply["agentic"]["checks"] and reply["agentic"]["isolation"]["backend"] == "test-in-process"
        assert len(entries) == 1 and entries[0]["person"] == reply["agentic"]["person"]
        assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    else:
        assert reply["agentic"]["code"] == kernel.DIGEST_MISMATCH
        assert started == [] and entries == []


@pytest.mark.parametrize(("module", "target"), [(hole, "valid_float_grid"), (hole, "float_grid"),
                                               (block, "recheck"), (runner, "inside_kernel_child")])
@pytest.mark.parametrize("change", ["code", "defaults", "kwdefaults"])
def test_component_mutations_before_execution_refuse(monkeypatch, module, target, change):
    started = no_child(monkeypatch)
    pending = prepare()
    function = getattr(module, target)
    attribute = {"code": "__code__", "defaults": "__defaults__", "kwdefaults": "__kwdefaults__"}[change]
    if change == "code":
        def replacement(*args, **kwargs):
            raise AssertionError("changed component ran")
        value = replacement.__code__
    else:
        value = (object(),) if change == "defaults" else {"extra": object()}
    monkeypatch.setattr(function, attribute, value)
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == []


def test_validator_mutation_by_responder_refuses_before_validation(monkeypatch, entries):
    in_process_child(monkeypatch)

    def replacement(*args, **kwargs):
        pytest.fail("changed validator ran")

    def answer(system, user):
        monkeypatch.setattr(hole.valid_float_grid, "__code__", replacement.__code__)
        return GRID

    reply = kernel.override(prepare(llm.FakeLLM(answer)))
    assert reply["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert reply["agentic"]["substituted"] is False
    assert not any(c["check"].endswith(":valid_float_grid") for c in reply["agentic"]["checks"])


@pytest.mark.parametrize("replace", [False, True])
def test_checker_mutated_by_responder_never_runs(monkeypatch, entries, replace):
    in_process_child(monkeypatch)
    launches = []
    launch = kernel._child.launch

    def observed(request, **kwargs):
        launches.append(request["kind"])
        return launch(request, **kwargs)

    monkeypatch.setattr(kernel._child, "launch", observed)

    def changed(*args, **kwargs):
        pytest.fail("changed checker ran")

    def answer(system, user):
        if replace:
            monkeypatch.setattr(block, "recheck", changed)
        else:
            monkeypatch.setattr(block.recheck, "__code__", changed.__code__)
        return GRID

    reply = kernel.override(prepare(llm.FakeLLM(answer)))
    if replace:
        # rebinding the module attribute changes nothing that runs: the override calls only the sealed checker, so it
        # either goes on with that one or refuses; the changed checker never runs (it would fail the test above)
        assert reply["agentic"].get("code") in (None, kernel.DIGEST_MISMATCH), reply["agentic"]
        return
    assert reply["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert reply["agentic"]["substituted"] is False
    assert "check" not in launches


@pytest.mark.parametrize("cancel", [False, True])
def test_preparation_is_consumed_even_on_cancellation_or_failure(monkeypatch, cancel):
    no_child(monkeypatch)
    pending = prepare()
    if cancel:
        kernel.declined(pending, "cancelled")
    else:
        kernel.override(pending)
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH


def test_abandoned_preparation_reachable_from_responder_is_collected():
    class Session:
        def answer(self, system, user):
            return GRID

    session = Session()
    session.pending = prepare(llm.FakeLLM(session.answer))
    gone = weakref.ref(session.pending)
    del session
    gc.collect()
    assert gone() is not None
    # The private single-use table retains identities, including cycles, up to its bound.
    held = []
    for _ in range(256):
        held.append(prepare())
        assert len(kernel._PREPARED) <= 256
    gc.collect()
    assert gone() is None
    for pending in held:
        kernel.declined(pending, "cancelled")


def test_oldest_live_preparation_is_evicted_and_refuses(monkeypatch):
    no_child(monkeypatch)
    pending = [prepare() for _ in range(257)]
    assert len(kernel._PREPARED) <= 256
    assert kernel.override(pending[0])["agentic"]["code"] == kernel.DIGEST_MISMATCH
    for item in pending[1:]:
        kernel.declined(item, "cancelled")


def test_mixed_checked_and_unchecked_host_sends_keep_their_authorities(monkeypatch, entries, accepted_cloud_dialog):
    in_process_child(monkeypatch)
    provider = "anthropic:claude_code"
    refs = accepted_cloud_dialog("fixture:repair1", provider, text="fixture")
    requests = []

    def complete(*args, **kwargs):
        requests.append(1)
        if len(requests) == 1:
            with cloud_consent.admission(provider, refs, "0" * 64):
                note_model_call()
                return ""
        return GRID

    monkeypatch.setattr(usage, "_complete_host", complete)
    reply = kernel.override(prepare(host_models.CALLERS["claude_code"]()))
    assert reply["agentic"]["outcome"] == "substituted"
    assert reply["agentic"]["person"]["answer"] == "allowed in code"
    assert reply["agentic"]["authority_sends"] == [
        {"answer": "provider consent", "provider": provider}, {"answer": "allowed in code", "provider": ""}]
