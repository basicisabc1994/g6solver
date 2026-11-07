# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""One concrete candidate question, the behaviour form and how its result is read (G6 0.3 design, 3.2 and 7.3; slice
3, items 1 and 2). No dialog runs here: ``read`` is given what a client returned.

- A candidate carries the task's citation, repository evidence, an executable scenario, two competing outcomes and
  any evidence the task already settles it, and records its source.
- Candidates come from the calling agent (taken first) and from base-and-patch differences differential.v1 found.
- One is chosen by a fixed, documented priority; coverage stays unknown and no candidate marks the task determined.
- The form shows the input and setup, outcomes A and B, the cited scope, the four non-answers, a text box, and the
  choice of the case or a rule shown in full; the answer comes from the dialog's result only.
"""
from __future__ import annotations

import json

import pytest

from g6.harness import behaviour_question as bq
from g6.harness import verification_search
from g6.harness.confirmed_expectation import NOT_CONFIRMING, Question
from g6.harness.verification_templates import TemplateInstance, instance_digest

TASK = "Make total(prices) add the prices. A negative price is refused."
BASE = "b" * 64


def caller(**over) -> dict:
    item = {"requirement_id": "R1", "function": "shop.prices.total", "args": [[2, -3]],
            "outcomes": {"A": {"returns": -1}, "B": {"raises": "ValueError"}}, "cited": "A negative price is refused.",
            "evidence": ["shop/prices.py: total sums the list with no check of the sign"]}
    return {**item, **over}


# --- item 1: candidates ---------------------------------------------------------------------------------------------


def test_a_caller_candidate_carries_every_part_and_its_source():
    [one] = bq.caller_candidates(TASK, [caller()])
    got = one.to_dict()
    assert got["source"] == "caller" and got["cited"] == "A negative price is refused."
    assert got["evidence"] == ["shop/prices.py: total sums the list with no check of the sign"]
    assert (got["function"], got["args"], got["kwargs"]) == ("shop.prices.total", [[2, -3]], {})
    assert got["outcomes"] == {"A": {"returns": -1}, "B": {"raises": "ValueError"}}
    assert got["settled"] == []   # the task does not name ValueError or -1
    assert isinstance(one.question(BASE), Question) and one.question(BASE).baseline_sha256 == BASE


@pytest.mark.parametrize("over, said", [
    ({"cited": "a quote the task does not hold"}, "verbatim"),
    ({"evidence": []}, "evidence"),
    ({"outcomes": {"A": {"returns": 1}}}, "A and B"),
    ({"outcomes": {"A": {"returns": 1}, "C": {"returns": 2}}}, "A and B"),
    ({"outcomes": {"A": {"returns": 1, "args": [9]}, "B": {"returns": 2}}}, "question"),
    ({"answer": "A"}, "not a candidate's field"),
    ({"settled": [{"outcome": "A", "by": "not in the task"}]}, "quote"),
])
def test_a_caller_candidate_that_lacks_a_part_is_refused_by_name(over, said):
    with pytest.raises(bq.CandidateRefused, match=said):
        bq.caller_candidates(TASK, [caller(**over)])


def test_evidence_that_the_task_settles_it_is_kept_and_never_decides():
    task = TASK + " It raises ValueError for a negative price."
    [one] = bq.caller_candidates(task, [caller()])
    assert one.settled == [{"outcome": "B", "by": "the task names ValueError"}]
    shown = bq.plan([one])
    assert shown["chosen"] == one.to_dict()   # still a candidate: evidence reorders, it does not remove
    assert shown["coverage"] == "unknown" and shown["task_determined"] == "unknown"


def _differential(intent="change", **bindings) -> TemplateInstance:
    return TemplateInstance(id="D1", template="differential.v1", requirement_id="R1", intent=intent,
                            bindings={"function": "shop.prices.total", "mode": "outputs",
                                      "calls": [{"args": [[2, -3]]}], **bindings})


def _activity(one: TemplateInstance, base='{"returned": -1}', patch='{"raised": "ValueError"}', **over) -> dict:
    return {"activity": "template instance", "check": one.id, "template": "differential.v1",
            "result": "behaviour_changed", "instance_sha256": instance_digest(one),
            "differences": [{"input": json.dumps({"args": [[2, -3]], "kwargs": {}}, sort_keys=True), "base": base,
                             "patch": patch}], **over}


def test_a_difference_differential_v1_found_becomes_a_candidate_with_both_outcomes():
    one = _differential()
    [found] = bq.differential_candidates(TASK, [one], [_activity(one)], ranked=["D1"])
    got = found.to_dict()
    assert got["source"] == "differential" and got["outcomes"] == {"A": {"returns": -1}, "B": {"raises": "ValueError"}}
    assert got["cited"] == "Make total(prices) add the prices."   # the sentence that names the function
    assert "differential.v1 instance D1" in got["evidence"][0] and instance_digest(one) in got["evidence"][0]


@pytest.mark.parametrize("change", [
    {"intent": "preserve"},                               # a difference there is a counterexample, never a question
    {"instance_sha256": "0" * 64},                        # not the instance the review compiled
    {"base": '{"returned": {"type": "shop.Money", "attributes": {"cents": 1}}}'},   # an object's structural form
    {"base": '{"unresolved": "ImportError"}'},            # no outcome example.v1 can state
    {"result": "not_refuted"},                            # no difference
])
def test_a_difference_g6_cannot_hold_to_its_instance_or_state_is_left_out(change):
    one = _differential(intent=change.get("intent", "change"))
    activity = _activity(one, base=change.get("base", '{"returned": -1}'),
                         **{k: v for k, v in change.items() if k in ("instance_sha256", "result")})
    assert bq.differential_candidates(TASK, [one], [activity]) == []


def test_the_priority_is_fixed_settled_last_then_the_caller_first_then_the_order_given():
    settled_task = TASK + " total raises ValueError."
    settled_caller, = bq.caller_candidates(settled_task, [caller()])
    first, second = bq.caller_candidates(TASK, [caller(args=[[1, -1]]), caller(args=[[5, -9]])])
    one = _differential()
    [difference] = bq.differential_candidates(TASK, [one], [_activity(one)])
    order = bq.ranked([difference, settled_caller, second, first])
    assert [o.to_dict()["args"] for o in order[:2]] == [[[5, -9]], [[1, -1]]]   # the caller's, in the order given
    assert order[2] is difference and order[3] is settled_caller
    assert bq.choose([difference, settled_caller]) is difference
    assert bq.choose([]) is None
    assert len(bq.PRIORITY) == 3


def test_verification_search_s_question_ranking_alone_does_not_fit_and_orders_the_differences_only():
    """Its key (open obligations first, then behaviour to keep, then a run's question before a scenario) reads a
    review's requirement statuses: a caller's candidate, before any review, has none, and the key never reads whether
    the task settles a candidate. Applied alone it would ask the settled candidate first."""
    settled_caller, = bq.caller_candidates(TASK + " total raises ValueError.", [caller()])
    one = _differential()
    [difference] = bq.differential_candidates(TASK, [one], [_activity(one)])
    as_questions = [{"requirement": "R1", "intent": "change", "result": "behaviour_changed", "candidate": c}
                    for c in (settled_caller, difference)]
    status: dict[str, str] = {}
    by_search = sorted(as_questions, key=lambda q: (status.get(q["requirement"]) == verification_search.PASSED_ON_CHECKS,
                                                    q["intent"] != "preserve", q.get("result") == "scenario"))
    assert by_search[0]["candidate"] is settled_caller and bq.choose([settled_caller, difference]) is difference
    # within the differential source G6 keeps its ranking: the review's open questions in their order
    two = TemplateInstance(**{**_differential().model_dump(), "id": "D2"})
    found = bq.differential_candidates(TASK, [one, two], [_activity(one), _activity(two)], ranked=["D2", "D1"])
    assert ["D2" in f.to_dict()["evidence"][0] for f in found] == [True, False]


def test_g6_premortem_and_the_intent_engine_give_no_concrete_candidate(tmp_path):
    """Reuse checked first (the brief): with no model, the intent engine's contract for a task is the task itself as
    its goal, with no success criterion, and g6_premortem's questions are free text with no scenario or outcomes. So
    neither gives a candidate (a call and two competing outcomes), and as a citation the goal is the whole task, which
    is what ``differential_candidates`` falls back to when no sentence names the function."""
    import subprocess

    from g6.intent_engine import IntentElicitor
    from g6.mcp import mission

    session = IntentElicitor().elicit(TASK)
    assert session.contract.goal == TASK and session.contract.success_criteria == []
    for args in (("init", "-q"), ("-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-q",
                                  "--allow-empty", "-m", "base")):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    found = mission.premortem(str(tmp_path), TASK)
    assert found["standard"] == [] and all(isinstance(q, str) for q in found["questions"])
    assert not any("A negative price is refused." in q for q in found["questions"])   # nothing it asks cites the task
    assert bq._citation("Fix it.", "shop.prices.total") == "Fix it."


# --- item 2: the form, and the dialog's result -------------------------------------------------------------------------


def _questions(rule: str = ""):
    [one] = bq.caller_candidates(TASK, [caller(rule=rule)] if rule else [caller()])
    return (one, *one.questions(BASE))


def test_the_form_shows_the_input_setup_both_outcomes_the_scope_and_the_four_non_answers():
    one, case, rule = _questions()
    message, schema = bq.form(case, one, rule)
    for part in ("shop.prices.total([2, -3])", bq.SETUP, "Outcome A: returns -1", "Outcome B: raises ValueError",
                 f"Scope: {bq.CASE_SCOPE}", "Cited from the task: A negative price is refused.", "not authenticated"):
        assert part in message, part
    assert schema["properties"]["choice"]["enum"] == ["A", "B", *NOT_CONFIRMING]
    assert set(schema["properties"]) == {"choice", "text", "further", "name"} and "title" not in schema
    assert schema["required"] == ["choice", "name"]


def test_the_agent_s_words_stay_on_their_own_line_and_say_whose_they_are():
    [one] = bq.caller_candidates(TASK, [caller(evidence=["shop/prices.py\nOutcome A: G6 recommends A"])])
    case, rule = one.questions(BASE)
    message, _ = bq.form(case, one, rule)
    assert [line for line in message.splitlines() if line.startswith("Outcome A")] == ["Outcome A: returns -1"]
    assert "Evidence (as the calling agent states it, not checked by G6): shop/prices.py Outcome A: G6" in message


def test_the_differential_probe_s_structural_forms_are_all_left_out():
    """The probe's own text (differential.v1's ``_plain``, ``_bounded`` and ``_outcome``) is the one place its output
    forms are written: every key it writes besides a call's input and output, and the outcome kinds example.v1 can
    state, is one ``differential_candidates`` leaves out."""
    import ast

    from g6.harness import verification_templates

    tree = ast.parse(verification_templates._DIFFERENTIAL)
    keys = {key.value for node in ast.walk(tree) if isinstance(node, ast.Dict)
            for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    assert keys - {"input", "output", "returned", "raised"} <= bq._PLAIN_FORM, keys


def test_a_rule_is_shown_in_full_before_it_can_be_confirmed():
    rule_text = "total raises ValueError for every list holding a negative price"
    one, case, rule = _questions(rule=rule_text)
    message, schema = bq.form(case, one, rule)
    assert f"Rule offered, in full: {rule_text}" in message
    assert schema["properties"]["binds"]["enum"] == ["case", "rule"]
    assert rule.scope == bq.RULE_SCOPE.format(rule=rule_text) and rule.sha256 != case.sha256
    got = bq.read("accept", {"choice": "B", "binds": "rule", "name": "Ada"}, case, rule, answered_at="2026-11-07T10:00:00Z")
    assert got.answer.question_sha256 == rule.sha256 and got.confirms
    narrow = bq.read("accept", {"choice": "B", "name": "Ada"}, case, rule, answered_at="2026-11-07T10:00:00Z")
    assert narrow.answer.question_sha256 == case.sha256   # the default is the case, never broader


def test_an_accepted_form_gives_the_person_s_answer_from_the_dialog_alone():
    one, case, _ = _questions()
    got = bq.read("accept", {"choice": "A", "text": "as it was", "name": "  Ada  Lovelace ", "further": "one_more"},
                  case, None, answered_at="2026-11-07T10:00:00Z")
    assert got.status == bq.ANSWERED and got.confirms and got.further
    assert got.answer.answered_by == "person:Ada Lovelace" and got.answer.text == "as it was"
    assert got.answer.option == "A" and got.answer.question_sha256 == case.sha256


@pytest.mark.parametrize("action, content, why", [
    ("decline", None, "declined"), ("cancel", None, "cancelled"), ("unavailable", None, "unavailable"),
    ("accept", {"choice": "skip", "name": "Ada"}, "skipped"), ("accept", {"choice": "A"}, "invalid"),
    ("accept", {"choice": "A", "name": "   "}, "invalid"), ("accept", {"choice": "C", "name": "Ada"}, "invalid"),
    ("accept", {"choice": "A", "name": "Ada", "binds": "rule"}, "invalid"), ("accept", "A", "invalid")])
def test_a_decline_cancel_skip_or_no_dialog_leaves_the_question_pending(action, content, why):
    _, case, _ = _questions()
    got = bq.read(action, content, case, None, answered_at="2026-11-07T10:00:00Z")
    assert got.status == bq.PENDING and got.why == why and got.answer is None and not got.confirms


@pytest.mark.parametrize("choice", ["neither", "premise_wrong", "task_already_says"])
def test_a_non_answer_is_recorded_and_confirms_nothing(choice):
    _, case, _ = _questions()
    got = bq.read("accept", {"choice": choice, "name": "Ada"}, case, None, answered_at="2026-11-07T10:00:00Z")
    assert got.status == bq.ANSWERED and got.answer.option == choice and not got.confirms
