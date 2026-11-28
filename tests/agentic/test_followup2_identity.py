# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811 - imported pytest fixtures are also test parameters
"""Identity reuse must never admit replacement preparation code or caller metadata."""
from __future__ import annotations

import builtins
import functools
import gc
import weakref

import pytest

from g6._agentic import kernel
from tests.agentic.kit import no_child
from tests.agentic.test_followup2 import entries, offline  # noqa: F401
from tests.agentic.test_followup2_repair2 import GRID, prepare, state_folder  # noqa: F401


@pytest.mark.parametrize("consume", ["override", "declined"])
def test_consumed_seal_keeps_original_responder_until_pending_collection(monkeypatch, entries, consume):
    started = no_child(monkeypatch)

    def answer(system, user):
        return GRID

    kept = weakref.ref(answer)
    pending = prepare(answer)
    del answer
    object.__setattr__(pending.caller, "responder", lambda system, user: GRID)
    gc.collect()
    assert kept() is not None
    if consume == "override":
        assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    else:
        assert kernel.declined(pending, "cancelled")["agentic"]["outcome"] == "not_run"
    assert kept() is not None
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == [] and entries == []
    del pending
    gc.collect()
    assert kept() is None


def test_consumed_seal_is_released_at_capacity_and_cannot_run_again(monkeypatch, entries):
    started = no_child(monkeypatch)

    def answer(system, user):
        return GRID

    kept = weakref.ref(answer)
    pending = prepare(answer)
    del answer
    object.__setattr__(pending.caller, "responder", None)
    # As the kf2r1 control reads it: a removed responder is refused as a digest mismatch or as an unmetered caller.
    assert kernel.override(pending)["agentic"]["code"] in (kernel.DIGEST_MISMATCH, kernel.CALLER_UNMETERED)
    assert kept() is not None
    held = [prepare() for _ in range(256)]
    gc.collect()
    assert kept() is None and len(kernel._PREPARED) <= 256
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == [] and entries == []
    for other in held:
        kernel.declined(other, "cancelled")


@pytest.mark.parametrize("change", ["responder", "code", "defaults", "kwdefaults", "wrapped",
                                   "closure", "partial_func", "partial_args", "partial_keywords"])
def test_simulated_reused_identity_refuses_before_any_child(monkeypatch, entries, change):
    started = no_child(monkeypatch)

    def step(system, user, extra=None, *, option=None):
        return GRID

    def answer(system, user):
        return step(system, user)

    answer.__wrapped__ = step
    if change.startswith("partial_"):
        answer = functools.partial(step, option=None)
    pending = prepare(answer)
    if change == "responder":
        old = answer
        replacement = lambda system, user: GRID  # noqa: E731 - replacement identity under test
        object.__setattr__(pending.caller, "responder", replacement)
    elif change == "code":
        old = step.__code__
        replacement = (lambda *args, **kwargs: GRID).__code__
        step.__code__ = replacement
    elif change == "defaults":
        old = step.__defaults__
        replacement = (object(),)
        step.__defaults__ = replacement
    elif change == "kwdefaults":
        old = step.__kwdefaults__
        replacement = {"option": object()}
        step.__kwdefaults__ = replacement
    elif change in ("wrapped", "closure"):
        old = step
        replacement = lambda system, user: GRID  # noqa: E731 - replacement identity under test
        if change == "wrapped":
            answer.__wrapped__ = replacement
        else:
            step = replacement
    else:
        function, args, keywords = answer.func, answer.args, answer.keywords
        if change == "partial_func":
            old = function
            replacement = lambda *args, **kwargs: GRID  # noqa: E731 - replacement identity under test
            function = replacement
        elif change == "partial_args":
            old = args
            replacement = ("changed",)
            args = replacement
        else:
            old = keywords
            replacement = {"option": object()}
            keywords = replacement
        answer.__setstate__((function, args, keywords, None))
    old_id = builtins.id(old)
    del old, answer
    gc.collect()

    # Simulate allocator reuse deterministically without changing Python's own identity semantics.
    monkeypatch.setattr(kernel, "id", lambda value: old_id if value is replacement else builtins.id(value),
                        raising=False)
    assert kernel.override(pending)["agentic"]["code"] == kernel.DIGEST_MISMATCH
    assert started == [] and entries == []
