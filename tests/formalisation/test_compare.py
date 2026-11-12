# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Check 3: diverse candidate comparison. Agreement never accepts; a dispute is the observation itself."""
from __future__ import annotations

import pytest

z3 = pytest.importorskip("z3")

from g6.formal import z3_bridge  # noqa: E402
from g6.harness.formalisation import ADAPTERS  # noqa: E402
from g6.harness.formalisation.adapters import from_expectation  # noqa: E402
from g6.harness.formalisation.checks import assess  # noqa: E402
from g6.harness.formalisation.compare import SINGLE_FAMILY, both_permitted, compare  # noqa: E402
from g6.harness.formalisation.draft import Draft  # noqa: E402
from g6.harness.confirmed_expectation import ExpectationStandard  # noqa: E402
from tests.formalisation._cases import confirmed, relation  # noqa: E402

ADAPTER = ADAPTERS["smt.return.int_bool.v1"]


def draft(text, family, **over):
    return Draft(status="drafted", statement=relation(text, **over), family=family)


def test_equivalent_drafts_pass_and_the_note_says_what_that_is():
    found, disputed = compare(draft("(= result (+ x 1))", "anthropic"), draft("(= result (+ 1 x))", "openai"),
                              adapter=ADAPTER)
    assert found.status == "passed" and disputed is None and found.queries[-1].reading == "equivalent_under_assumptions"
    assert "never accepts" in found.detail["note"] and found.detail["families"] == ["anthropic", "openai"]


def test_same_wrong_formula_agrees_and_still_needs_person():
    """Two copies of result = x + 2 agree while both miss an intended x + 1."""
    a, b = draft("(= result (+ x 2))", "anthropic"), draft("(= result (+ x 2))", "openai")
    found, _ = compare(a, b, adapter=ADAPTER)
    assert found.status == "passed"
    assessed = assess(a.statement, ExpectationStandard(()), (), comparison=found)
    assert assessed.status == "unconfirmed"                 # no example: nothing binds it, whatever the comparison says
    first = confirmed([3], {"A": {"returns": 5}, "B": {"returns": 4}})
    ready = assess(a.statement, ExpectationStandard((first,)), (from_expectation(first),), comparison=found)
    assert ready.status == "ready_for_person" and "accept" not in ready.status   # ready to be shown, never accepted


def test_disputed_observation_not_invented_outputs():
    found, disputed = compare(draft("(>= result x)", "anthropic"), draft("(> result x)", "openai"), adapter=ADAPTER)
    assert found.status == "incomplete" and "disagree on an observation" in found.reasons[0]
    x = disputed.call["args"][0]
    assert disputed.call == {"function": "shop.f", "args": [x], "kwargs": {}}
    assert disputed.observation == {"returns": x} and disputed.permitted_by == ("anthropic",)
    assert disputed.unique is False                         # each draft permits several results there
    shared = both_permitted(draft("(>= result x)", "anthropic"), draft("(> result x)", "openai"), ADAPTER, disputed)
    assert shared["returns"] > x                            # a real outcome both permit, found by a witness query


def test_functional_drafts_dispute_is_unique():
    found, disputed = compare(draft("(= result (+ x 1))", "anthropic"), draft("(= result (+ x 2))", "openai"),
                              adapter=ADAPTER)
    assert found.status == "incomplete" and disputed.unique is True and len(disputed.permitted_by) == 1


def test_unconcretised_stays_model_disagreement():
    class Blind:
        id, fragment, requires_function = ADAPTER.id, ADAPTER.fragment, False
        supports, sorts = staticmethod(ADAPTER.supports), staticmethod(ADAPTER.sorts)

        def concretise(self, statement, assignment):
            return None

    found, disputed = compare(draft("(>= result x)", "anthropic"), draft("(> result x)", "openai"), adapter=Blind())
    assert found.status == "incomplete" and disputed is None and "model disagreement, not concretised" in found.reasons[0]


def test_same_family_is_not_diverse():
    found, disputed = compare(draft("(= result (+ x 1))", "anthropic"), draft("(= result (+ x 1))", "anthropic"),
                              adapter=ADAPTER)
    assert found.status == "incomplete" and disputed is None and "one voice" in found.reasons[0]


@pytest.mark.parametrize("status", ["withheld", "unavailable", "outside_fragment", "caps_spent", "model_failed"])
def test_one_draft_missing_is_incomplete(status):
    missing = Draft(status=status, statement=None, family="openai", reason="why")
    found, disputed = compare(draft("(= result (+ x 1))", "anthropic"), missing, adapter=ADAPTER)
    assert found.status == "incomplete" and disputed is None and status in found.reasons[0]
    assert SINGLE_FAMILY == "stop_unconfirmed"
    limited, _ = compare(draft("(= result (+ x 1))", "anthropic"), missing, adapter=ADAPTER,
                         single_family="accept_with_limit")       # the other answer to the owner's question: one switch
    assert limited.status == "passed" and "one model family" in limited.detail["limit"]
    neither, _ = compare(missing, missing, adapter=ADAPTER, single_family="accept_with_limit")
    assert neither.status == "incomplete"


def test_confirmed_example_settles_without_a_question():
    first = from_expectation(confirmed([3], {"A": {"returns": 4}, "B": {"returns": 3}}))
    found, disputed = compare(draft("(>= result x)", "anthropic"), draft("(> result x)", "openai"), adapter=ADAPTER,
                              cases=(first,))
    assert found.status == "passed" and disputed is None and found.detail["kept"] == "openai"
    assert "without a question" in found.detail["note"]
    other = from_expectation(confirmed([3], {"A": {"returns": 9}, "B": {"returns": 0}}))   # settles nothing
    again, disputed = compare(draft("(>= result x)", "anthropic"), draft("(> result x)", "openai"), adapter=ADAPTER,
                              cases=(other,))
    assert again.status == "incomplete" and disputed is not None


def test_drafts_of_different_scopes_are_not_compared():
    found, _ = compare(draft("(= result (+ x 1))", "anthropic"),
                       draft("(= result (+ x 1))", "openai", domain=("(>= x 1)",)), adapter=ADAPTER)
    assert found.status == "incomplete" and "one supported scope" in found.reasons[0]


def test_drafts_that_raise_on_different_inputs_dispute_the_raise():
    a = draft("(= result (+ x 1))", "anthropic", raises_when="(< x 2)", error="ValueError")
    b = draft("(= result (+ x 1))", "openai", raises_when="(< x 3)", error="ValueError")
    found, disputed = compare(a, b, adapter=ADAPTER)
    assert found.status == "incomplete" and disputed.call["args"] == [2]
    assert disputed.observation in ({"raises": "ValueError"}, {"returns": 3})


@pytest.mark.parametrize("why", ["incomplete", "timeout"])
def test_undecided_comparison_is_incomplete(monkeypatch, why):
    monkeypatch.setattr(z3_bridge, "run_query", lambda query, timeout: ("unknown", {}, why))
    found, disputed = compare(draft("(>= result x)", "anthropic"), draft("(> result x)", "openai"), adapter=ADAPTER)
    assert found.status == "incomplete" and disputed is None and "did not decide" in found.reasons[0]