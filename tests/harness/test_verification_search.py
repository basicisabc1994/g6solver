# SPDX-License-Identifier: Apache-2.0
"""Verification search, part 1: an intent contract the caller passes, mapped onto the patch review's requirements.

Its items count as caller-stated: success criteria become stated requirements, non-goals and failure criteria become
stated preservation obligations, and assumptions keep their source and become no requirement. No model is called, and
the deterministic elicitor is not used. Development fixtures only."""
from __future__ import annotations

import pytest

from g6.harness.patch_review import Requirement
from g6.harness.verification_search import intent_contract, requirements_from_intent
from g6.intent_engine.schema import Assumption, IntentContract

ADD = "test_calc.py::test_add"
POSITIVE = Requirement(id="R1", text="add returns the sum of two positive integers", checks=[ADD])
INTENT = IntentContract(
    goal="Repair addition for positive and negative integers.",
    success_criteria=["add returns the sum of two negative integers", "add returns 0 for 0 and 0"],
    non_goals=["change the signature of add"],
    failure_criteria=["add raises an error for an integer input"],
    assumptions=[Assumption(statement="inputs are integers", source="explicit"),
                 Assumption(statement="callers use Python 3.12", source="inferred"),
                 Assumption(statement="nothing calls add concurrently")])


def _by_id(mapped) -> dict[str, Requirement]:
    return {req.id: req for req in mapped.requirements}


def test_the_callers_requirements_stay_first_and_stated():
    mapped = requirements_from_intent(INTENT, [POSITIVE])
    assert mapped.requirements[0] == POSITIVE
    assert (POSITIVE.provenance, POSITIVE.kind) == ("stated", "requirement")


def test_success_criteria_become_stated_requirements_with_no_check():
    found = _by_id(requirements_from_intent(INTENT, [POSITIVE]))
    for index, text in enumerate(INTENT.success_criteria):
        req = found[f"intent.success_criteria.{index}"]
        assert (req.text, req.provenance, req.kind, req.checks) == (text, "stated", "requirement", [])


def test_non_goals_and_failure_criteria_become_stated_preservation_obligations():
    found = _by_id(requirements_from_intent(INTENT, []))
    for ident, text in (("intent.non_goals.0", INTENT.non_goals[0]),
                        ("intent.failure_criteria.0", INTENT.failure_criteria[0])):
        assert (found[ident].text, found[ident].provenance, found[ident].kind, found[ident].checks) == (
            text, "stated", "preserve", [])


def test_assumptions_keep_their_source_only_explicit_is_normative_and_none_becomes_a_requirement():
    mapped = requirements_from_intent(INTENT, [])
    assert [(a["id"], a["statement"], a["source"], a["normative"]) for a in mapped.assumptions] == [
        ("intent.assumptions.0", "inputs are integers", "explicit", True),
        ("intent.assumptions.1", "callers use Python 3.12", "inferred", False),
        ("intent.assumptions.2", "nothing calls add concurrently", "fallback", False)]
    statements = {a.statement for a in INTENT.assumptions}
    assert not statements & {req.text for req in mapped.requirements}
    assert not any(req.id.startswith("intent.assumptions") for req in mapped.requirements)


def test_a_duplicate_of_a_callers_requirement_is_merged_into_it_never_doubled():
    """Text that differs only in case, spacing or a closing full stop is the same requirement: the caller's keeps its
    id and checks, and records the intent item merged into it."""
    negative = Requirement(id="R2", text="Add returns the sum of two  negative integers.", checks=["test_calc.py"])
    mapped = requirements_from_intent(INTENT, [POSITIVE, negative])
    found = _by_id(mapped)
    assert "intent.success_criteria.0" not in found
    assert found["R2"].checks == ["test_calc.py"] and found["R2"].merged_from == ["intent.success_criteria.0"]
    assert sum(req.text.casefold().startswith("add returns the sum of two negative") for req in mapped.requirements) == 0
    assert len([req for req in mapped.requirements if "negative" in req.text.casefold()]) == 1
    # within the intent too: a non-goal repeated as a failure criterion is one obligation
    twice = IntentContract(goal="g", non_goals=["keep the API"], failure_criteria=["Keep the API."])
    [kept] = requirements_from_intent(twice, []).requirements
    assert (kept.id, kept.kind, kept.merged_from) == ("intent.non_goals.0", "preserve", ["intent.failure_criteria.0"])


def test_a_stated_item_is_never_merged_into_a_requirement_no_one_stated():
    hypothesis = Requirement(id="H1", text=INTENT.success_criteria[0], provenance="model_hypothesis")
    found = _by_id(requirements_from_intent(INTENT, [hypothesis]))
    assert found["H1"].merged_from == [] and found["intent.success_criteria.0"].provenance == "stated"


def test_ids_come_from_the_field_and_the_index_and_are_the_same_on_every_call():
    first = requirements_from_intent(INTENT, [POSITIVE])
    second = requirements_from_intent(IntentContract.model_validate(INTENT.model_dump()), [POSITIVE])
    assert [req.model_dump() for req in first.requirements] == [req.model_dump() for req in second.requirements]
    assert first.assumptions == second.assumptions and first.unknowns == second.unknowns
    assert [req.id for req in first.requirements] == [
        "R1", "intent.success_criteria.0", "intent.success_criteria.1", "intent.non_goals.0",
        "intent.failure_criteria.0"]


def test_success_and_failure_checks_check_an_answers_text_so_none_becomes_a_check_and_each_is_an_unknown():
    """The ruling: an intent's ``Check`` is a predicate on the text of an answer (contains, absent, regex or word
    count). It names no repository test, so none becomes a check of the patch, and each is said in an unknown."""
    intent = IntentContract(goal="g", success_criteria=["the summary names the fix"],
                            success_checks={"the summary names the fix": {"kind": "contains", "value": ADD}},
                            failure_checks={"anything": {"kind": "word_count", "max_words": 5}})
    mapped = requirements_from_intent(intent, [])
    assert [req.checks for req in mapped.requirements] == [[]]
    assert len(mapped.unknowns) == 2
    assert "success_checks" in mapped.unknowns[0] and "contains" in mapped.unknowns[0]
    assert "failure_checks" in mapped.unknowns[1] and "word_count" in mapped.unknowns[1]
    assert all("names no repository test" in unknown for unknown in mapped.unknowns)


def test_intent_fields_that_are_not_mapped_are_said_in_an_unknown():
    intent = IntentContract(goal="g", constraints=["no new dependency"], declared_risks=["data loss"])
    mapped = requirements_from_intent(intent, [])
    assert mapped.requirements == []
    assert any("intent.constraints" in unknown for unknown in mapped.unknowns)
    assert any("intent.declared_risks" in unknown for unknown in mapped.unknowns)
    assert requirements_from_intent(IntentContract(goal="g"), []).unknowns == []


def test_an_intent_is_validated_as_an_intent_contract():
    assert intent_contract({"goal": "g", "non_goals": ["keep the API"]}) == IntentContract(
        goal="g", non_goals=["keep the API"])
    assert intent_contract(INTENT) is INTENT


@pytest.mark.parametrize("value, said", [
    ("repair addition", "object"),
    ({"success_criteria": ["x"]}, "goal"),
    ({"goal": "g", "non_goal": ["typo"]}, "non_goal"),
    ({"goal": "g", "assumptions": [{"statement": "s", "sorce": "explicit"}]}, "sorce"),
    ({"goal": "g", "assumptions": [{"statement": "s", "source": "certain"}]}, "source"),
    ({"goal": "g", "success_criteria": "not a list"}, "success_criteria"),
    ({"goal": "g", "success_criteria": ["   "]}, "success_criteria"),
    ({"goal": "g", "success_checks": {"x": {"kind": "pytest", "value": "t"}}}, "success_checks"),
])
def test_an_invalid_intent_is_a_value_error_that_says_what_is_wrong(value, said):
    with pytest.raises(ValueError) as raised:
        mapped = intent_contract(value)
        requirements_from_intent(mapped, [])
    assert said in str(raised.value)
