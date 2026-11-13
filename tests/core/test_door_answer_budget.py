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

The count trusts nothing the answer says of itself (issuance review of that change, findings 2 and 3). It counted only
containers met a second time by identity, and took each one's word for its length, so three answers were outside it:
many read-only views of one dict (each view a new object), a mapping that makes its values when they are read (every
child new, so the count itself never ended), and a list that reports another length than it yields. Now every value is
counted as the count reads it, and the read stops at the first value past a budget. A mapping that is not a ``dict``
holds no entries of its own, so everything under one counts as reached again; and the whole of what a copy would
reach has its own, larger budget (``door.ANSWER_TOTAL``), which the largest ordinary answers stay far inside.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

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


# --- the count trusts nothing the answer says of itself (issuance review, findings 2 and 3) ---------------------------


class Made(Mapping):
    """A mapping that makes four new children every time its values are read; it counts every child it makes."""

    made = 0

    def __iter__(self):
        return iter("abcd")

    def __len__(self) -> int:
        return 0   # and says it holds none

    def __getitem__(self, key):
        Made.made += 1
        if Made.made > 4 * LIMIT:
            raise Stop
        return Made()


class Unending(list):
    """A list whose iteration never ends, each item a new value; it counts what it hands over."""

    def __init__(self) -> None:
        super().__init__()
        self.read = 0

    def __iter__(self):
        while True:
            self.read += 1
            if self.read > 4 * door.ANSWER_TOTAL:
                raise Stop
            yield self.read


def _refused(reply, budget: int) -> bool:
    return (reply["ok"] is False and reply["warning_card"]["code"] == RESOURCE_LIMIT
            and f"more than {budget} values" in reply["error"]
            and reply["result"] == {"completion_state": "blocked-escalated", "error": reply["error"]})


def test_the_whole_of_an_answer_has_a_budget_ten_times_the_repeat_budget():
    assert door.ANSWER_TOTAL == 1_000_000 == 10 * door.ANSWER_VISITS


def test_views_of_one_dict_are_counted_as_what_the_copy_reaches_through_them(copies):
    """Each ``MappingProxyType`` is a new object over the same storage: the copy reaches every entry once for each."""
    shared = {f"k{index}": index for index in range(1000)}
    within = _reply({"rows": [MappingProxyType(shared) for _ in range(LIMIT // 1000)]})
    assert within["ok"] is True and within["result"]["rows"] == [shared] * (LIMIT // 1000)
    copies[0] = 0
    past = _reply({"rows": [MappingProxyType(shared) for _ in range(LIMIT // 1000 + 1)]})
    assert _refused(past, LIMIT) and copies[0] == 1, "the copy is entered once, and refuses before it copies anything"
    with pytest.raises(door.AnswerNotRead, match=RESOURCE_LIMIT):
        door.corrected_result({"rows": [MappingProxyType(shared)] * 2, "more": [MappingProxyType(shared)] * 100},
                              "qualified-draft")


def test_a_mapping_that_makes_its_values_when_read_stops_the_count_at_the_budget(copies):
    Made.made = 0
    with pytest.raises(door.AnswerNotRead, match=RESOURCE_LIMIT):
        door.held_copy({"completion_state": "qualified-draft", "tree": Made()}, "qualified-draft")
    assert Made.made == LIMIT + 1, "the count reads one value past the budget, and no more"
    Made.made, copies[0] = 0, 0
    assert _refused(_reply({"tree": Made()}), LIMIT) and Made.made == LIMIT + 1 and copies[0] == 1


def test_a_small_mapping_that_is_no_dict_is_copied_as_before():
    class Small(Mapping):
        def __iter__(self):
            return iter("ab")

        def __len__(self) -> int:
            return 2

        def __getitem__(self, key):
            return {"word": "verified", "key": key}

    held = door.held_copy({"m": Small(), "view": MappingProxyType({"state": "verified"})}, "qualified-draft")
    assert held == {"m": {"a": {"word": "qualified-draft", "key": "a"}, "b": {"word": "qualified-draft", "key": "b"}},
                    "view": {"state": "qualified-draft"}}


def test_a_container_is_counted_by_what_it_yields_never_by_the_length_it_reports():
    class Short(list):
        def __len__(self) -> int:
            return 0

    inner = Short([0] * (LIMIT + 1))
    with pytest.raises(door.AnswerNotRead):
        door.held_copy([inner, inner], "qualified-draft")
    inner = Short([0] * LIMIT)
    assert door.held_copy([inner, inner], "qualified-draft") == [[0] * LIMIT] * 2


def test_the_whole_of_what_a_copy_reaches_is_counted_and_the_count_stops_there():
    """Exactly the budget is read and copied; one value more is not, though nothing in it is reached twice."""
    flat = ["verified"] * door.ANSWER_TOTAL
    assert door.held_copy(flat, "qualified-draft") == ["qualified-draft"] * door.ANSWER_TOTAL
    flat.append("verified")
    with pytest.raises(door.AnswerNotRead, match=f"more than {door.ANSWER_TOTAL} values"):
        door.held_copy(flat, "qualified-draft")
    assert _refused(_reply({"rows": flat}), door.ANSWER_TOTAL)
    unending = Unending()
    with pytest.raises(door.AnswerNotRead, match=RESOURCE_LIMIT):
        door.held_copy({"rows": unending}, "qualified-draft")
    assert unending.read == door.ANSWER_TOTAL, "the count stops reading at the first value past the budget"


def test_the_two_refusals_say_which_budget_the_answer_is_past(copies):
    assert _refused(_reply({"answer": doubled()}), LIMIT)
    assert _refused(_reply({"rows": [0] * (door.ANSWER_TOTAL + 1)}), door.ANSWER_TOTAL)
    assert copies[0] == 2, "each copy is entered once, and refuses before it copies anything"


# --- the count reads a container as the copy reads it (issuance review of the count, finding 2) -----------------------
# The count read a mapping through ``values()`` and the copy read it through ``items()``. A mapping whose two reads
# differ (a ``dict`` subclass, or any ``Mapping``, with a ``values`` of its own) was counted as holding nothing while
# the copy expanded everything under it. The count and the copy now read each container through one reader
# (``door._read``), so there is no second access path for a container to answer differently on.


def _shows_nothing(accessor: str):
    answers = {"__len__": lambda self: 0, "__contains__": lambda self, key: False,
               "get": lambda self, key, default=None: default}

    def raises(self, key):
        raise KeyError(key)

    return answers.get(accessor) or (raises if accessor == "__getitem__" else lambda self: iter(()))


class Entries(Mapping):
    """A ``Mapping`` of its keyword arguments, read as the abstract class reads one."""

    def __init__(self, **entries) -> None:
        self._entries = entries

    def __getitem__(self, key):
        return self._entries[key]

    def __iter__(self):
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


#: Each accessor a mapping could show nothing through while ``items()``, which the copy reads, still shows every entry.
QUIET = [(dict, name) for name in ("values", "keys", "__iter__", "__len__", "__getitem__", "__contains__", "get")] + [
    (Entries, name) for name in ("values", "keys", "__contains__", "get")]


@pytest.mark.parametrize(("base", "accessor"), QUIET, ids=[f"{base.__name__}.{name}" for base, name in QUIET])
def test_a_mapping_that_shows_nothing_through_another_accessor_is_counted_as_the_copy_reads_it(base, accessor):
    quiet = type("Quiet", (base,), {accessor: _shows_nothing(accessor)})
    rows = list(range(60_000))
    answer = quiet(a=rows, b=rows, c=rows)             # the copy reaches ``rows`` three times: 120,000 values again
    assert sum(len(held) for _, held in answer.items()) == 180_000
    assert door._past_budget(answer) == door.AnswerNotRead.AGAIN
    with pytest.raises(door.AnswerNotRead, match=RESOURCE_LIMIT):
        door.held_copy(answer, "qualified-draft")
    assert _refused(door.door_reply("demo", "op", Success(answer)), LIMIT)
    assert _refused(_reply({"rows": [answer]}), LIMIT)
    within = quiet(a=rows[:10], b=rows[:10])
    assert door._past_budget(within) == "" and door.held_copy(within, "qualified-draft") == {"a": rows[:10],
                                                                                           "b": rows[:10]}


def test_a_chain_of_such_mappings_each_holding_the_next_twice_is_refused():
    quiet = type("Quiet", (dict,), {"values": _shows_nothing("values")})
    level: object = list(range(10))
    for _ in range(14):
        level = quiet(a=level, b=level)                # 2 ** 14 lists of ten to a copy
    with pytest.raises(door.AnswerNotRead, match=f"more than {LIMIT} values"):
        door.held_copy(level, "qualified-draft")


@pytest.mark.parametrize("base", [dict, Entries])
def test_a_mapping_that_shows_nothing_through_items_is_copied_as_holding_nothing(base):
    """The other direction: what ``items()`` does not show, the copy does not reach, whatever ``values()`` says."""
    quiet = type("Quiet", (base,), {"items": lambda self: iter(())})
    rows = list(range(60_000))
    answer = quiet(a=rows, b=rows, c=rows)
    assert door._past_budget(answer) == "" and door.held_copy(answer, "qualified-draft") == {}


class _Spied:
    """Records each read made of the container, by name, then answers as its own class does."""

    READS = ("items", "values", "keys", "get", "__iter__", "__getitem__", "__len__", "__contains__")

    @classmethod
    def of(cls, base, *args, **kwargs):
        read: list[str] = []

        def spy(name):
            def reads(self, *given):
                read.append(name)
                return getattr(base, name)(self, *given)
            return reads

        spied = type("Spied", (base,), {name: spy(name) for name in cls.READS if hasattr(base, name)})
        return spied(*args, **kwargs), read


@pytest.mark.parametrize("made", [lambda: _Spied.of(dict, a=[1, 2], b={"c": "verified"}),
                                  lambda: _Spied.of(Entries, a=[1, 2], b={"c": "verified"}),
                                  lambda: _Spied.of(list, [[1, 2], {"c": "verified"}]),
                                  lambda: _Spied.of(tuple, ([1, 2], {"c": "verified"}))],
                         ids=["dict", "Mapping", "list", "tuple"])
def test_the_count_and_the_copy_make_the_same_reads_of_a_container(made, monkeypatch):
    """The class, not the one accessor: whatever a container is asked by the count, it is asked by the copy."""
    container, read = made()
    assert door._past_budget(container) == ""
    counted = list(read)
    read.clear()
    monkeypatch.setattr(door, "_past_budget", lambda value: "")     # the copy alone, with no count before it
    held = door.held_copy(container, "qualified-draft")
    assert counted == list(read) and counted, f"the count read {counted}, the copy read {list(read)}"
    assert held in ({"a": [1, 2], "b": {"c": "qualified-draft"}}, [[1, 2], {"c": "qualified-draft"}])