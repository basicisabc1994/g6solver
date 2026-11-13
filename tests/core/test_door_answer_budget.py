# SPDX-License-Identifier: Apache-2.0
"""A door reads an answer that reaches the same objects again only within a visit budget, before it copies it
(review-pr8fu3r4, finding 3; the certificate issuance window before 0.3.0).

The door's copy of a result (``g6.core.door.held_copy``) follows every reference, so an answer in which one object is
reached many times was copied once for each time: thirty lists, each holding the one before it twice, are two thousand
million values to the copy. The copy's work is now bounded by what its producer built: before anything is copied, the
door counts what the copy would reach under an object it had already reached, and past the budget
(``door.ANSWER_VISITS``) nothing is copied. The checked door then replies ``blocked-escalated`` with
``G6_E_RESOURCE_LIMIT`` and no result, so nothing expands the answer after it either. An answer that reaches nothing
twice, however large, and one that shares a little, are copied exactly as before.
"""
from __future__ import annotations

import pytest
from returns.result import Success

from g6.cognitive_bridge import snapshot
from g6.core import door
from g6.core.resources import RESOURCE_LIMIT

LIMIT = snapshot.MAX_ITEMS


class Stop(BaseException):
    """Ends a copy that would otherwise run for hours; no door may swallow it."""


def doubled(levels: int = 30, leaf: object = "leaf") -> object:
    """``levels`` lists, each holding the one before it twice: ``levels + 1`` objects, ``2 ** (levels + 1) - 1``
    values to a copy that follows every reference."""
    answer = leaf
    for _ in range(levels):
        answer = [answer, answer]
    return answer


@pytest.fixture
def copies(monkeypatch):
    """How many values the door's copy reached, counted at the copy itself; a runaway copy is stopped."""
    native, seen = door.held_copy, [0]

    def counted(value, state, *, _depth=0):
        seen[0] += 1
        if seen[0] > 4 * LIMIT:
            raise Stop
        return native(value, state, _depth=_depth)

    monkeypatch.setattr(door, "held_copy", counted)
    return seen


class _Registry:
    refs: dict = {}

    def __init__(self, value) -> None:
        self.value = value

    def invoke(self, component, op, params, **kwargs):  # noqa: ANN001, ANN201
        return Success(self.value)


def _reply(value):
    try:
        return door.invoke_checked("demo", "op", {}, allow=frozenset(), how_to_allow="", registry=_Registry(value))
    except Stop:
        return None


def test_the_budget_is_the_bridges_visit_budget():
    assert door.ANSWER_VISITS == LIMIT


@pytest.mark.parametrize("state", ["qualified-draft", "blocked-escalated", None, "verified"])
def test_the_checked_door_refuses_a_shared_answer_before_it_copies_it(copies, state):
    said = {} if state is None else {"completion_state": state}
    reply = _reply({**said, "answer": doubled()})
    assert copies[0] <= LIMIT, f"the door's copy reached {copies[0]} values: the visit budget is {LIMIT}"
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == RESOURCE_LIMIT and reply["error"].startswith(f"[{RESOURCE_LIMIT}] demo.op")
    # an answer the door did not read is never handed on: what stands as the result is the door's own word
    assert reply["result"] == {"completion_state": "blocked-escalated", "error": reply["error"]}


def test_every_reader_of_the_doors_reply_is_told_the_answer_was_not_read(copies):
    """The agents' tool relay and a navigator plan's steps read ``door_reply(...)["result"]``: it is there, and it
    declares the block, so neither takes an unread answer for a usable one."""
    from g6.workforce.contracts import outcome_of

    reply = None
    try:
        reply = door.door_reply("demo", "op", Success({"answer": doubled()}))
    except Stop:
        pass
    assert copies[0] <= LIMIT and reply["warning_card"]["code"] == RESOURCE_LIMIT and reply["ok"] is False
    assert reply["result"] == {"completion_state": "blocked-escalated", "error": reply["error"]}
    assert outcome_of(reply["result"]) == "blocked-escalated"


@pytest.mark.parametrize("copy", ["held_copy", "corrected_result"])
def test_the_copy_itself_refuses_for_every_caller(copies, copy):
    for value in (doubled(), {"rows": doubled()}, (doubled(20), doubled(20))):
        with pytest.raises(door.AnswerNotRead, match=RESOURCE_LIMIT):
            getattr(door, copy)(value, "qualified-draft")
    assert copies[0] <= LIMIT


def test_a_kept_verified_copies_nothing_and_counts_nothing(copies):
    shared = doubled()
    assert door.held_copy(shared, "verified") is shared


def test_a_cycle_that_branches_is_refused_and_one_that_does_not_is_cut_at_the_depth_as_before(copies):
    branching: list = []
    branching.extend([branching, branching])
    with pytest.raises(door.AnswerNotRead):
        door.held_copy(branching, "qualified-draft")
    assert copies[0] <= LIMIT
    line: list = ["verified"]
    line.append(line)
    held = door.held_copy(line, "qualified-draft")
    depth, note = 0, "(nested too deep for the door to read)"
    while isinstance(held[1], list):
        assert held[0] == "qualified-draft"
        held, depth = held[1], depth + 1
    assert held == [note, note] and depth == 32


def test_the_budget_counts_what_is_reached_again_and_nothing_else():
    """``inner`` is reached twice: the second time its values are reached again. Exactly the budget is read; one more
    is not. The same values reached once each are no charge at all."""
    inner = [0] * LIMIT
    assert door.held_copy([inner, inner], "qualified-draft") == [inner, inner]
    inner.append(0)
    with pytest.raises(door.AnswerNotRead):
        door.held_copy([inner, inner], "qualified-draft")
    assert door.held_copy([inner, list(inner)], "qualified-draft") == [inner, inner]


def test_a_large_answer_that_reaches_nothing_twice_is_copied_as_before(copies):
    rows = [{"id": index, "state": "verified", "cells": [index, str(index)]} for index in range(60_000)]
    value = {"completion_state": "qualified-draft", "rows": rows}
    reply = _reply(value)
    assert reply["ok"] is True and copies[0] > 2 * LIMIT, "the instrument counts the copy"
    assert len(reply["result"]["rows"]) == 60_000
    assert reply["result"]["rows"][59_999] == {"id": 59_999, "state": "qualified-draft", "cells": [59_999, "59999"]}
    assert rows[0]["state"] == "verified", "the answer itself is never changed"


def test_an_answer_that_shares_a_little_is_copied_as_before():
    unit = {"unit": "kg", "checked": "verified"}
    value = {"rows": [{"n": index, "unit": unit} for index in range(1000)], "unit": unit}
    held = door.corrected_result(value, "qualified-draft")
    assert held["unit"] == {"unit": "kg", "checked": "qualified-draft"}
    assert all(row == {"n": index, "unit": held["unit"]} for index, row in enumerate(held["rows"]))
    assert unit["checked"] == "verified"
