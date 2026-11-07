# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The task's question store under G6_HOME (G6 0.3 design, 3.2 steps 3 and 4; slice 3, items 3 and 4).

- One question per task by default, persistent across calls, keyed by the task's identity; the person can allow more
  or none; a pending question is shown again at no cost.
- Answers are accepted only for questions G6 asked, once each; a contradicting answer starts a superseding standard.
- Unreadable or malformed state is refused, never read as empty; a stop between any two writes leaves a store that
  reads back as what was written.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from g6.core.ledger import ChainLedger
from g6.harness import behaviour_question as bq
from g6.harness import question_store as qs
from g6.harness.confirmed_expectation import Answer, Unconfirmed

TASK = "Make total(prices) add the prices. A negative price is refused."
BASE = "b" * 64
AT = "2026-11-07T10:00:00Z"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(qs.BUDGET_ENV, raising=False)
    return tmp_path / "home"


def candidate(args=([2, -3],), rule="") -> bq.Candidate:
    [one] = bq.caller_candidates(TASK, [{
        "requirement_id": "R1", "function": "shop.prices.total", "args": list(args),
        "outcomes": {"A": {"returns": -1}, "B": {"raises": "ValueError"}}, "cited": "A negative price is refused.",
        "evidence": ["shop/prices.py: no check of the sign"], **({"rule": rule} if rule else {})}])
    return one


def store(task=TASK, root: Path | None = None) -> qs.TaskQuestions:
    return qs.TaskQuestions(qs.task_identity(root or Path("/repo"), task))


def ask(where: qs.TaskQuestions, one: bq.Candidate | None = None) -> qs.Asked:
    one = one or candidate()
    case, rule = one.questions(BASE)
    return where.ask(case, rule, one, at=AT)


def answer(asked: qs.Asked, option="B", *, rule=False, who="person:Ada") -> Answer:
    shown = asked.rule if rule else asked.question
    return Answer(question_sha256=shown.sha256, option=option, answered_by=who, answered_at=AT)


# --- item 3: the budget -------------------------------------------------------------------------------------------------


def test_the_task_s_identity_is_its_repository_and_text_not_its_base_commit(tmp_path):
    one = qs.task_identity(tmp_path, TASK)
    assert one == qs.task_identity(tmp_path, TASK) and one != qs.task_identity(tmp_path, TASK + " ")
    assert one != qs.task_identity(tmp_path / "other", TASK)
    assert store().path.parent.name == "questions" and store().path.name.endswith(".jsonl")


def test_one_question_per_task_by_default_persistent_across_calls():
    first = ask(store())
    assert store().state().left == 0 and store().state().pending == first   # a fresh store object: it persisted
    assert ask(store()) == first   # the same question again costs nothing
    with pytest.raises(qs.BudgetSpent):
        ask(store(), candidate(args=([1, -1],)))
    assert ask(store(TASK + " Also keep the order."), candidate()).number == 1   # another task has its own budget


def test_the_person_can_allow_one_more_or_none(monkeypatch):
    where = store()
    first = ask(where)
    where.allow_more(first, by="person:Ada", at=AT)
    assert where.state().allowance == 2 and ask(where, candidate(args=([1, -1],))).number == 2
    monkeypatch.setenv(qs.BUDGET_ENV, "0")
    other = store(TASK + " none")
    assert other.state().allowance == 0
    with pytest.raises(qs.BudgetSpent):
        ask(other)


@pytest.mark.parametrize("value", ["zero", "-1", "11", "1.5"])
def test_a_budget_setting_g6_cannot_read_allows_none_and_says_why(monkeypatch, value):
    monkeypatch.setenv(qs.BUDGET_ENV, value)
    state = store().state()
    assert state.allowance == 0 and qs.BUDGET_ENV in state.budget_note
    with pytest.raises(qs.BudgetSpent, match=qs.BUDGET_ENV):
        ask(store())


# --- item 4: answers only for questions G6 asked ---------------------------------------------------------------------


def test_an_answer_whose_question_g6_did_not_ask_is_refused():
    where = store()
    asked = ask(where)
    stranger = candidate(args=([7, -7],)).question(BASE)
    with pytest.raises(qs.AnswerRefused, match="did not ask"):
        where.answer(Answer(question_sha256=stranger.sha256, option="A", answered_by="person:Ada", answered_at=AT))
    with pytest.raises(qs.AnswerRefused, match="did not ask"):   # another task's store did not ask it either
        store(TASK + " elsewhere").answer(answer(asked))
    assert where.state().answers == ()


def test_a_question_is_answered_once_and_its_rule_counts_as_the_same_question():
    where = store()
    asked = ask(where, candidate(rule="total refuses every negative price"))
    where.answer(answer(asked, rule=True))
    with pytest.raises(qs.AnswerRefused, match="answered once"):
        where.answer(answer(asked, option="A"))
    state = where.state()
    assert state.pending is None and state.standard().predicates == (state.standard().expectations[0].expectation_id,)
    assert state.standard().expectations[0].scope.startswith("the rule, as shown in full")


def test_the_store_takes_only_a_person_s_answer_record():
    with pytest.raises(Unconfirmed):
        Answer(question_sha256="a" * 64, option="A", answered_by="simulated:maintainer", answered_at=AT)
    with pytest.raises(qs.AnswerRefused):
        store().answer({"question_sha256": "a" * 64, "option": "A", "answered_by": "person:Ada"})


def test_a_contradicting_answer_starts_a_superseding_standard():
    where = store()
    first = ask(where)
    where.allow_more(first, by="person:Ada", at=AT)
    where.answer(answer(first, "A"))
    one = candidate()
    other = one.question(BASE, scope="the same case, asked again")   # same scenario, another question
    again = where.ask(other, None, one, at=AT)
    where.answer(answer(again, "B"))
    state = where.state()
    [held] = state.standard().expectations
    assert held.option == "B" and state.standard().replaced and len(state.superseded) == 1
    assert state.superseded[0]["replaced"] == list(state.standard().replaced)


def test_a_non_answer_closes_the_question_and_confirms_nothing():
    where = store()
    asked = ask(where)
    where.answer(answer(asked, "task_already_says"))
    state = where.state()
    assert state.pending is None and state.standard().expectations == ()


# --- unreadable state, and a stop between two writes ----------------------------------------------------------------


def _written(where: qs.TaskQuestions) -> list[str]:
    return where.path.read_text(encoding="utf-8").splitlines(keepends=True)


@pytest.mark.parametrize("damage", ["torn", "edited", "garbage", "reordered"])
def test_a_store_that_does_not_check_out_is_unreadable_never_empty(damage):
    where = store()
    asked = ask(where)
    where.answer(answer(asked))
    lines = _written(where)
    if damage == "torn":
        where.path.write_text("".join(lines)[:-20], encoding="utf-8")
    elif damage == "edited":   # the answer's option, inside its record's text
        edited = "".join(lines).replace('\\"option\\":\\"B\\"', '\\"option\\":\\"A\\"')
        assert edited != "".join(lines)
        where.path.write_text(edited, encoding="utf-8")
    elif damage == "garbage":
        where.path.write_text("not a ledger\n", encoding="utf-8")
    else:
        where.path.write_text("".join(reversed(lines)), encoding="utf-8")
    with pytest.raises(qs.StoreUnreadable, match=qs.UNREADABLE):
        where.state()
    with pytest.raises(qs.StoreUnreadable):
        ask(where, candidate(args=([1, -1],)))


def test_an_entry_the_store_s_rules_refuse_makes_the_store_unreadable_on_read():
    """An answer appended without the store's rules (a hand edit that keeps the chain) is caught when read back."""
    where = store()
    ask(where)
    ledger = ChainLedger(where.path)   # no rules: as an edit would write it
    stranger = candidate(args=([7, -7],)).question(BASE)
    forged = Answer(question_sha256=stranger.sha256, option="A", answered_by="person:Ada", answered_at=AT)
    ledger.append(qs.ANSWER, forged.sha256, {"answer": forged.record.decode("ascii")})
    with pytest.raises(qs.StoreUnreadable, match="did not ask"):
        where.state()


class Stop(Exception):
    pass


@pytest.mark.parametrize("stop_before", [qs.ASKED, qs.ANSWER, qs.SHOWN, qs.MORE])
def test_a_stop_between_two_writes_leaves_what_was_written_and_nothing_else(monkeypatch, stop_before):
    real = ChainLedger.append

    def append(self, kind, ident, body):
        if kind == stop_before:
            raise Stop(kind)
        return real(self, kind, ident, body)

    monkeypatch.setattr(ChainLedger, "append", append)
    where = store()
    with pytest.raises(Stop):   # the steps a call takes, in order: ask, answer, show, allow one more
        asked = ask(where)
        where.answer(answer(asked))
        where.show(asked, bq.ANSWERED, shown_at=AT, back_at=AT, seconds=1.0)
        where.allow_more(asked, by="person:Ada", at=AT)
    monkeypatch.setattr(ChainLedger, "append", real)
    state = where.state()   # reads back, after a restart, as what was written
    steps = [qs.ASKED, qs.ANSWER, qs.SHOWN, qs.MORE]
    written = steps[:steps.index(stop_before)]
    assert (len(state.asked) == 1) == (qs.ASKED in written)
    assert (len(state.answers) == 1) == (qs.ANSWER in written)
    assert (len(state.shown) == 1) == (qs.SHOWN in written) and state.more == 0
    if qs.ASKED in written and qs.ANSWER not in written:
        assert state.pending is not None and ask(where) == state.pending   # shown again, at no further cost


def test_two_calls_racing_for_the_last_question_spend_it_once():
    barrier, results = threading.Barrier(2), []

    def one(args):
        where = store()
        barrier.wait()
        try:
            results.append(ask(where, candidate(args=(args,))).number)
        except qs.BudgetSpent:
            results.append("spent")

    threads = [threading.Thread(target=one, args=(args,)) for args in ([1, -1], [2, -2])]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results, key=str) == [1, "spent"] and len(store().state().asked) == 1
