# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""The person's acceptance: only their answer to the exact rule shown makes one, and every digest it binds resolves."""
from __future__ import annotations

import dataclasses

import pytest

z3 = pytest.importorskip("z3")

from g6.evidence import mapping as m  # noqa: E402
from g6.harness import behaviour_question as bq  # noqa: E402
from g6.harness import question_store as qs  # noqa: E402
from g6.harness.formalisation import receipts  # noqa: E402
from g6.harness.formalisation.acceptance import (  # noqa: E402
    NOT_ACCEPTED, RULE_LABELS, AcceptanceRefused, AcceptanceStore, FormalAcceptance, accept, rule_text)
from g6.harness.formalisation.adapters import from_expectation  # noqa: E402
from g6.harness.formalisation.checks import CheckOutcome, assess  # noqa: E402
from g6.harness.formalisation.draft import Draft  # noqa: E402
from g6.harness.formalisation.render import render  # noqa: E402
from g6.harness.formalisation.scope import bind_scope  # noqa: E402
from g6.harness.confirmed_expectation import ExpectationStandard  # noqa: E402
from tests.formalisation._cases import BASE, relation  # noqa: E402

STATEMENT = relation("(= result (+ x 1))")
AGREED = CheckOutcome("comparison", "passed", ())
DRAFTS = (Draft(status="drafted", statement=STATEMENT, family="anthropic", model="a"),
          Draft(status="drafted", statement=STATEMENT, family="openai", model="b"))


def shown_rule(statement=STATEMENT) -> str:
    shown = render(statement)
    return rule_text(shown, bind_scope(statement, shown, ExpectationStandard(()), ()), DRAFTS)


def asked_and_answered(tmp_path, *, binds="rule", choice="A", rule=None, name="owner"):
    """Ask the one question of a task with the rule shown in full, and record the person's answer to it."""
    store = qs.TaskQuestions(qs.task_identity(tmp_path, "f adds one"))
    candidate = bq.Candidate.make(source="caller", requirement_id="R1", function="shop.f", args=[3], kwargs={},
                                  outcomes={"A": {"returns": 4}, "B": {"returns": 5}}, cited="the task: f adds one",
                                  evidence=["shop/f.py defines f"], rule=shown_rule() if rule is None else rule)
    question, rule_question = candidate.questions(BASE)
    asked = store.ask(question, rule_question, candidate)
    reading = bq.read("accept", {"choice": choice, "binds": binds, "name": name}, asked.question, asked.rule,
                      answered_at=qs.now())
    state = store.answer(reading.answer)
    standard = state.standard()
    cases = tuple(from_expectation(one) for one in standard.expectations)
    return store, asked, reading, state, assess(STATEMENT, standard, cases, comparison=AGREED)


def test_rule_text_is_one_line_of_labelled_fields_in_order():
    text = shown_rule()
    assert "\n" not in text and text.isascii() and [text.index(label) for label in RULE_LABELS] == sorted(
        text.index(label) for label in RULE_LABELS)
    assert STATEMENT.text in text and STATEMENT.sha256 in text and render(STATEMENT).sha256 in text
    assert "anthropic (a), openai (b)" in text and "effects on arguments" in text
    assert shown_rule(relation("(= result (+ x 2))")) != text


def test_binds_rule_records_acceptance_bound_to_digests(tmp_path):
    _, asked, reading, state, assessed = asked_and_answered(tmp_path)
    assert assessed.status == "ready_for_person"
    made = accept(asked, reading, assessed, DRAFTS)
    assert made.question_sha256 == asked.rule.sha256 and made.answer_sha256 == reading.answer.sha256
    assert made.scope == assessed.scope and made.scope.standard_sha256 == state.standard().sha256
    assert made.assessment_sha256 == assessed.sha256 and made.accepted_by == "person:owner"
    assert [one["family"] for one in made.drafts] == ["anthropic", "openai"] and not made.drafts[0]["saw_other"]
    assert FormalAcceptance.from_record(made.record()) == made and len(made.sha256) == 64


def test_binds_case_leaves_formalisation_unconfirmed(tmp_path):
    _, asked, reading, state, assessed = asked_and_answered(tmp_path, binds="case")
    assert state.standard().predicates and assessed.status == "ready_for_person"   # the example is still enforced
    with pytest.raises(AcceptanceRefused, match="the case, not the rule") as refused:
        accept(asked, reading, assessed, DRAFTS)
    assert refused.value.code == NOT_ACCEPTED and NOT_ACCEPTED in str(refused.value)


def test_the_rule_shown_must_be_this_assessments_rule(tmp_path):
    other = shown_rule(relation("(= result (+ 1 x))"))            # another statement's rule was shown
    _, asked, reading, _, assessed = asked_and_answered(tmp_path, rule=other)
    with pytest.raises(AcceptanceRefused, match="not this assessment's rule"):
        accept(asked, reading, assessed, DRAFTS)
    _, asked, reading, _, assessed = asked_and_answered(tmp_path / "b")
    with pytest.raises(AcceptanceRefused, match="not this assessment's rule"):
        accept(asked, reading, assessed, DRAFTS[:1])               # other drafts than the form named


def test_an_assessment_that_is_not_ready_or_an_answer_that_confirms_nothing_accepts_nothing(tmp_path):
    _, asked, reading, state, assessed = asked_and_answered(tmp_path)
    cases = tuple(from_expectation(one) for one in state.standard().expectations)
    unready = assess(STATEMENT, state.standard(), cases)           # no comparison was run
    with pytest.raises(AcceptanceRefused, match="not ready"):
        accept(asked, reading, unready, DRAFTS)
    nothing = bq.read("accept", {"choice": "neither", "binds": "rule", "name": "owner"}, asked.question, asked.rule,
                      answered_at=qs.now())
    with pytest.raises(AcceptanceRefused, match="confirmed nothing"):
        accept(asked, nothing, assessed, DRAFTS)
    with pytest.raises(AcceptanceRefused, match="confirmed nothing"):
        accept(asked, bq.read("decline", None, asked.question, asked.rule, answered_at=qs.now()), assessed, DRAFTS)


def kept(tmp_path):
    store, asked, reading, state, assessed = asked_and_answered(tmp_path)
    made = accept(asked, reading, assessed, DRAFTS)
    held = AcceptanceStore(store.identity)
    held.record(made, parts=receipts.held_parts(STATEMENT, assessed.rendering, assessed.scope, state.standard()),
                answers=state.answers)
    return held, made, assessed, state


def test_every_digest_the_acceptance_binds_resolves_from_the_store(tmp_path):
    held, made, assessed, state = kept(tmp_path)
    assert held.resolve(made.sha256) == made.record() and held.resolve(STATEMENT.sha256) == STATEMENT.record()
    assert held.resolve("0" * 64) is None and held.current(state.standard()) == (made,)
    from tests.formalisation.test_receipts import evidence

    found = evidence(acceptance_sha256=made.sha256, scope=assessed.scope, assessment_sha256=assessed.sha256,
                     standard_sha256=state.standard().sha256, controls=(), predicates=state.standard().predicates)
    codes = {code for code, _ in receipts.problems(found, resolve=held.resolve)}
    assert m.NO_ACCEPTANCE not in codes and codes == {m.NO_CONTROL}       # only the control is missing here


def test_changed_statement_byte_unresolvable(tmp_path):
    held, made, assessed, state = kept(tmp_path)
    from tests.formalisation.test_receipts import evidence

    changed = dataclasses.replace(STATEMENT, text="(= result (+ x 2))")
    found = evidence(acceptance_sha256=made.sha256, statement=changed, scope=assessed.scope,
                     assessment_sha256=assessed.sha256, standard_sha256=state.standard().sha256)
    why = [text for code, text in receipts.problems(found, resolve=held.resolve) if code == m.NO_ACCEPTANCE]
    assert any(text.startswith("statement:") for text in why)
    assert held.resolve(changed.sha256) is None


def test_contradicting_answer_invalidates_and_reports_dependents(tmp_path):
    """The person changes their mind: the old acceptance stops counting at once, and the store says which."""
    held, made, _, state = kept(tmp_path)
    from tests.formalisation._cases import confirmed

    newer = state.standard().superseding(confirmed([3], {"A": {"returns": 4}, "B": {"returns": 5}}, chosen="B"))
    assert held.current(newer) == () and held.dropped(newer) == (made,)
    assert held.current(state.standard()) == (made,) and held.dropped(state.standard()) == ()


def test_agent_cannot_write_an_acceptance(tmp_path):
    store, asked, reading, state, assessed = asked_and_answered(tmp_path)
    made = accept(asked, reading, assessed, DRAFTS)
    held = AcceptanceStore(store.identity)
    parts = receipts.held_parts(STATEMENT, assessed.rendering, assessed.scope, state.standard())
    with pytest.raises(AcceptanceRefused, match="only a person"):
        held.record(dataclasses.replace(made, accepted_by="agent:claude"), parts=parts, answers=state.answers)
    with pytest.raises(AcceptanceRefused, match="does not hold"):
        held.record(dataclasses.replace(made, answer_sha256="0" * 64), parts=parts, answers=state.answers)
    with pytest.raises(AcceptanceRefused, match="does not hold"):
        held.record(made, parts=parts, answers=())
    with pytest.raises(AcceptanceRefused, match="digest of its own bytes"):
        held.record(made, parts={"0" * 64: b"x"}, answers=state.answers)
    assert held.all() == ()


def test_store_chain_break_refuses(tmp_path):
    held, made, _, state = kept(tmp_path)
    path = held._chain.path
    path.write_text("", encoding="ascii")                      # emptied behind G6's back
    with pytest.raises(qs.StoreUnreadable):
        held.current(state.standard())
    with pytest.raises(qs.StoreUnreadable):
        held.resolve(made.sha256)