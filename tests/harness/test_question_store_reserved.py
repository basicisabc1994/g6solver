# SPDX-License-Identifier: Apache-2.0
"""The one addition section 17 makes to the question store (design 17.3: the optional feedback question draws on A's
one budget): a reserved slot counts against what is left, is bound to its task like every entry, and expands nothing.
Every rule the store had before holds as it did."""
from __future__ import annotations

import pytest

from g6.core.ledger import ChainLedger
from g6.harness import question_store as qs
from tests.harness.test_question_store import TASK, answer, ask, candidate, store

AT = "2026-11-07T10:00:00Z"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(qs.BUDGET_ENV, raising=False)
    return tmp_path / "home"


def test_reserved_entry_counts_against_left():
    where = store()
    assert where.state().left == 1
    ident = where.reserve("feedback", at=AT)
    state = store().state()                                   # a fresh object: it persisted
    assert len(ident) == 64 and state.left == 0 and state.asked == () and state.pending is None
    assert state.reserved == ({"purpose": "feedback", "reserved_at": AT, "task": where.identity},)
    with pytest.raises(qs.BudgetSpent):
        ask(store())                                          # the behaviour question shares the one slot
    with pytest.raises(qs.BudgetSpent):
        store().reserve("feedback", at=AT)
    with pytest.raises(ValueError):
        store().reserve("anything else")


def test_a_question_asked_first_leaves_no_slot_to_reserve():
    asked = ask(store())
    with pytest.raises(qs.BudgetSpent):
        store().reserve("feedback", at=AT)
    assert store().state().reserved == () and store().state().pending == asked


def test_reserved_entry_bound_to_another_task_is_refused():
    mine, other = store(), store(TASK + " Also keep the order.")
    mine.reserve("feedback", at=AT)
    other.path.parent.mkdir(parents=True, exist_ok=True)
    other.path.write_bytes(mine.path.read_bytes())
    other.head.write_bytes(mine.head.read_bytes())
    with pytest.raises(qs.StoreUnreadable):
        other.state()
    ledger = ChainLedger(validate=mine._validate)
    with pytest.raises(ValueError):
        ledger.append(qs.RESERVED, "x" * 64, {"purpose": "feedback", "reserved_at": AT, "task": other.identity})
    for body in ({"purpose": "other", "reserved_at": AT}, {"purpose": "feedback"}, {"reserved_at": AT}):
        with pytest.raises(ValueError):
            ledger.append(qs.RESERVED, "y" * 64, {**body, "task": mine.identity})


def test_only_allow_more_expands_the_budget(monkeypatch):
    where = store()
    asked = ask(where)
    where.answer(answer(asked))
    assert where.state().left == 0 and where.state().allowance == 1
    with pytest.raises(qs.BudgetSpent):
        where.reserve("feedback", at=AT)
    where.allow_more(asked, by="person:Ada", at=AT)           # the person's own request, in a form G6 showed
    assert where.state().left == 1 and where.state().allowance == 2
    where.reserve("feedback", at=AT)
    assert where.state().left == 0 and where.state().allowance == 2       # a reservation expands nothing
    monkeypatch.setenv(qs.BUDGET_ENV, "0")
    with pytest.raises(qs.BudgetSpent):
        store(TASK + " another").reserve("feedback", at=AT)   # the person asked for no questions


def test_existing_stores_without_reserved_entries_read_as_before():
    where = store()
    asked = ask(where)
    before = where.state()
    assert before.reserved == () and before.left == 0 and before.pending == asked
    state = where.answer(answer(asked))
    assert state.reserved == () and state.answers and state.standard().expectations
    assert qs.State().reserved == () and qs.State().left == qs.DEFAULT_BUDGET
    assert ask(store(TASK + " x"), candidate()).number == 1
