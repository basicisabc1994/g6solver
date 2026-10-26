# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A request's own model calls, counted beside the process-wide count (Phase E Part 1, E1a; Ledger §21).

Pinned: inside ``counting()`` the calls noted in that context count for it and for every scope around it; two
requests on two threads never count each other's calls; a thread the request did not hand its context to is not
charged to the request and is still counted process-wide; the process-wide count is unchanged. Offline.
"""
from __future__ import annotations

import contextvars
import sys
import threading

import pytest
from g6.core import model_calls as mc


def test_a_scope_counts_the_calls_noted_in_its_context_and_the_process_count_still_rises():
    before = mc.model_calls()
    with mc.counting() as mine:
        mc.note_model_call()
        mc.note_model_call()
        assert mine() == 2
    assert mc.model_calls() - before == 2
    mc.note_model_call()
    assert mine() == 2  # once the scope closed, nothing more counts for it


def test_a_nested_scope_counts_for_each_scope_it_sits_in():
    with mc.counting() as outer:
        mc.note_model_call()
        with mc.counting() as inner:
            mc.note_model_call()
        assert (outer(), inner()) == (2, 1)


def test_two_requests_on_two_threads_never_count_each_others_calls():
    barrier, seen = threading.Barrier(2, timeout=20), {}

    def request(name: str, calls: int) -> None:
        with mc.counting() as mine:
            for _ in range(calls):
                mc.note_model_call()
            barrier.wait()  # both have noted their calls before either reads its count
            seen[name] = mine()

    before = mc.model_calls()
    threads = [threading.Thread(target=request, args=("a", 3)), threading.Thread(target=request, args=("b", 5))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert seen == {"a": 3, "b": 5}
    assert mc.model_calls() - before == 8


def test_a_thread_the_request_did_not_hand_its_context_to_is_counted_only_process_wide():
    """Review Focus 5. A pool or bare thread starts with an empty context here (``bind_billing_context`` binds the
    customer's identity only), so its model call is not charged to the request: under-reported, never another
    tenant's. An interpreter that hands threads their creator's context counts it, which is also right."""
    inherits = bool(getattr(sys.flags, "thread_inherit_context", False))
    before = mc.model_calls()
    with mc.counting() as mine:
        stray = threading.Thread(target=mc.note_model_call)
        stray.start()
        stray.join(timeout=20)
        handed = threading.Thread(target=contextvars.copy_context().run, args=(mc.note_model_call,))
        handed.start()
        handed.join(timeout=20)
        assert mine() == (2 if inherits else 1)
    assert mc.model_calls() - before == 2


def test_child_counts_preserve_scoped_and_process_totals_through_nested_scopes():
    before = mc.model_calls()
    with mc.counting() as outer:
        with mc.counting() as inner:
            mc.note_child_model_calls(scoped=2, process=5)
        mc.note_child_model_calls(scoped=1, process=3)
    assert (outer(), inner(), mc.model_calls() - before) == (3, 2, 8)


@pytest.mark.parametrize("scoped,process", [(True, 1), (0, True), (-1, 0), (1, 0), (0, -1), (0, 1.5)])
def test_invalid_child_counts_cannot_mutate_either_counter(scoped, process):
    before = mc.model_calls()
    with mc.counting() as mine:
        with pytest.raises(ValueError):
            mc.note_child_model_calls(scoped=scoped, process=process)
        assert mine() == 0
    assert mc.model_calls() == before
