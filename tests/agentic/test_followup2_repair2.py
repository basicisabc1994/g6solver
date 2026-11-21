# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811 - imported pytest fixtures are also test parameters
"""Follow callable code without freezing caller data; keep single-use seals private."""
from __future__ import annotations

import functools
import gc
import weakref
from dataclasses import replace

import pytest

from g6._agentic import kernel, ledger
from g6.core import cloud_consent, host_models, llm
from g6.core.model_calls import note_model_call
from g6.self_maintenance import usage
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, in_process_child, no_child
from tests.agentic.test_followup2 import entries, offline  # noqa: F401

GRID = '{"value":[0.5]}'


@pytest.fixture(autouse=True)
def state_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))


def prepare(answer=None):
    return kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                          llm=llm.FakeLLM(answer, default=GRID))


def test_nonlocal_counter_is_state_not_a_code_change(monkeypatch, entries):
    in_process_child(monkeypatch)
    count = 0

    def answer(system, user):
        nonlocal count
        count += 1
        return GRID

    reply = kernel.override(prepare(answer))
    assert reply["agentic"]["outcome"] == "substituted"
    assert count == 1


def test_partial_responder_executes(monkeypatch, entries):
    in_process_child(monkeypatch)

    def answer(text, system, user):
        return text

    pending = prepare(functools.partial(answer, GRID))
    assert type(pending) is kernel.Pending
    assert kernel.override(pending)["agentic"]["outcome"] == "substituted"


@pytest.mark.parametrize("edge", ["closure", "wrapped", "method", "partial", "instance", "nested",
                                  "partial_wrapped", "instance_wrapped"])
def test_reachable_function_code_swap_refuses_before_execution(monkeypatch, entries, edge):
    started = no_child(monkeypatch)

    def step(system, user):
        return GRID

    if edge == "closure":
        def answer(system, user):
            return step(system, user)
    elif edge == "wrapped":
        def answer(system, user):
            return GRID
        answer.__wrapped__ = step
    elif edge == "method":
        class Session:
            def answer(self, system, user):
                return GRID
        method = Session().answer
        step = method.__func__

        def answer(system, user):
            return method(system, user)
    elif edge == "partial":
        answer = functools.partial(step)
    elif edge in ("instance", "instance_wrapped"):
        class Session:
            def __call__(self, system, user):
                return GRID
        answer = Session()
        if edge == "instance":
            step = Session.__call__
        else:
            answer.__wrapped__ = step
    elif edge == "partial_wrapped":
        answer = functools.partial(lambda system, user: GRID)
        answer.__wrapped__ = step
    else:
        def middle(system, user):
            return step(system, user)

        def answer(system, user):
            return middle(system, user)

    pending = prepare(answer)
    assert type(pending) is kernel.Pending

    def changed(*args, **kwargs):
        pytest.fail("changed reachable code ran")

    monkeypatch.setattr(step, "__code__", changed.__code__)
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == [] and entries == []


@pytest.mark.parametrize("change", ["defaults", "kwdefaults", "closure_function", "wrapped_function"])
def test_reachable_bindings_are_rechecked(monkeypatch, entries, change):
    started = no_child(monkeypatch)

    def step(system, user, extra=None, *, option=None):
        return GRID

    def answer(system, user):
        return step(system, user)

    answer.__wrapped__ = step
    pending = prepare(answer)
    if change == "defaults":
        step.__defaults__ = (object(),)
    elif change == "kwdefaults":
        step.__kwdefaults__ = {"option": object()}
    elif change == "closure_function":
        def step(system, user):
            return GRID
    else:
        answer.__wrapped__ = lambda system, user: GRID
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == []


@pytest.mark.parametrize("change", ["func", "args", "keywords"])
def test_partial_bindings_are_rechecked(monkeypatch, entries, change):
    started = no_child(monkeypatch)

    def step(system, user, *, text=GRID):
        return text

    answer = functools.partial(step, text=GRID)
    pending = prepare(answer)
    assert type(pending) is kernel.Pending
    function, args, keywords = step, answer.args, answer.keywords
    if change == "func":
        def function(system, user, **kwargs):
            return GRID
    elif change == "args":
        args = ("changed",)
    else:
        keywords = {"text": GRID}
    answer.__setstate__((function, args, keywords, None))
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == []


def test_callable_cycles_are_followed_once(monkeypatch, entries):
    in_process_child(monkeypatch)

    def answer(system, user):
        assert answer is not None
        return GRID

    answer.__wrapped__ = answer
    assert kernel.override(prepare(answer))["agentic"]["outcome"] == "substituted"


def test_replaced_instance_call_implementation_refuses(monkeypatch, entries):
    started = no_child(monkeypatch)

    class Session:
        def __call__(self, system, user):
            return GRID

    pending = prepare(Session())

    def changed(*args, **kwargs):
        pytest.fail("replacement call implementation ran")

    monkeypatch.setattr(Session, "__call__", changed)
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == []


def test_excessive_callable_graph_refuses_before_execution(monkeypatch, entries):
    started = no_child(monkeypatch)

    def wrap(inner):
        def answer(system, user):
            return inner(system, user)
        return answer

    answer = lambda system, user: GRID  # noqa: E731 - seed for a callable graph, never invoked
    for _ in range(4097):
        answer = wrap(answer)
    reply = prepare(answer)
    assert type(reply) is dict
    assert reply["agentic"]["code"] == "G6_E_INPUT_VALIDATION"
    assert started == [] and entries == []


@pytest.mark.parametrize("state", ["closure", "kwdefault", "self", "partial_keywords"])
def test_plain_state_changes_remain_usable(monkeypatch, entries, state):
    in_process_child(monkeypatch)
    data = object()

    def answer(system, user, *, option=None):
        assert data is not None
        return GRID

    if state == "self":
        class Session:
            count = 0

            def answer(self, system, user):
                self.count += 1
                return GRID
        answer = Session().answer
    elif state == "partial_keywords":
        answer = functools.partial(answer, option=1)
    pending = prepare(answer)
    if state == "closure":
        data = object()
    elif state == "kwdefault":
        answer.__kwdefaults__["option"] = object()
    elif state == "partial_keywords":
        answer.keywords["option"] = 2
    assert kernel.override(pending)["agentic"]["outcome"] == "substituted"


def test_pin_inspection_never_dispatches_data_attribute_hooks(monkeypatch, entries):
    in_process_child(monkeypatch)

    class Data:
        def __getattribute__(self, name):
            pytest.fail("pinning dispatched a data object's attribute hook")

    data = Data()

    def answer(system, user):
        assert data is not None
        return GRID

    assert kernel.override(prepare(answer))["agentic"]["outcome"] == "substituted"


@pytest.mark.parametrize("value", ["0" * 64, 1, type("Text", (str,), {})("wrong")])
def test_public_digest_must_match_the_private_seal(monkeypatch, entries, value):
    started = no_child(monkeypatch)
    pending = prepare()
    object.__setattr__(pending, "digest", value)
    reply = kernel.override(pending)
    assert reply["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == [] and entries == []


def test_pending_cannot_supply_a_replacement_seal(monkeypatch, entries):
    started = no_child(monkeypatch)

    def answer(system, user):
        return GRID

    pending = prepare(answer)
    assert "_retained" not in vars(pending)
    object.__setattr__(pending, "_retained", object())

    def changed(system, user):
        pytest.fail("a forged seal admitted changed code")

    monkeypatch.setattr(answer, "__code__", changed.__code__)
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == []


@pytest.mark.parametrize("confirmation", [kernel.Confirmation("wrong", ""), object(),
                                         kernel.Confirmation(type("Text", (str,), {})("wrong"), "")])
def test_failed_legacy_confirmation_does_not_consume_pending(monkeypatch, entries, confirmation):
    served = in_process_child(monkeypatch)
    pending = prepare()
    reply = kernel.override(pending, confirmation=confirmation)
    assert reply["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert reply["original"] == pending.original
    assert served == [] and entries == []
    reply = kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, ""))
    assert reply["agentic"]["outcome"] == "substituted"
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH


def test_copy_has_no_seal_and_does_not_consume_the_original(monkeypatch, entries):
    in_process_child(monkeypatch)
    pending = prepare()
    assert kernel.override(replace(pending))["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert kernel.override(pending)["agentic"]["outcome"] == "substituted"


def test_eviction_releases_a_responder_pending_cycle(monkeypatch, entries):
    no_child(monkeypatch)

    class Session:
        def answer(self, system, user):
            return GRID

    session = Session()
    session.pending = prepare(session.answer)
    gone = weakref.ref(session.pending)
    del session
    kept = [prepare() for _ in range(256)]
    gc.collect()
    assert gone() is None
    assert len(kernel._PREPARED) <= 256
    for pending in kept:
        kernel.declined(pending, "cancelled")


def test_mixed_authorities_survive_ledger_projection(monkeypatch, entries, accepted_cloud_dialog):
    in_process_child(monkeypatch)
    provider = "anthropic:claude_code"
    refs = accepted_cloud_dialog("fixture:repair2", provider, text="fixture")
    requests = []

    def complete(*args, **kwargs):
        requests.append(1)
        if len(requests) == 1:
            with cloud_consent.admission(provider, refs, "0" * 64):
                note_model_call()
                return ""
        return GRID

    monkeypatch.setattr(usage, "_complete_host", complete)
    reply = kernel.override(kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                                          llm=host_models.CALLERS["claude_code"]()))
    record = reply["agentic"]
    assert record["outcome"] == "substituted"
    assert entries[0]["person"]["answer"] == "allowed in code"
    assert entries[0]["authority_sends"] == record["authority_sends"] == [
        {"answer": "provider consent", "provider": provider}, {"answer": "allowed in code", "provider": ""}]


def test_ledger_send_authority_rejects_free_text():
    with pytest.raises(ValueError):
        ledger.SendAuthority.model_validate({"answer": "provider consent", "provider": "private text"})
    with pytest.raises(ValueError):
        ledger.SendAuthority.model_validate({"answer": "private text", "provider": ""})
