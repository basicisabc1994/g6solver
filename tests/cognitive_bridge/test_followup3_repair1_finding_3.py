"""The encoder and budget readers share the finite float range refusal."""
import pytest

from g6.cognitive_bridge import InformationGainSelector, ProblemSignature
from g6.cognitive_bridge.snapshot import NotPlainData, canonical_json, number
from g6.cognitive_bridge.codes import INPUT


def test_unrepresentable_integer_budget_has_one_registered_refusal():
    budget = 10 ** 400
    readers = [canonical_json, lambda value: number(value, where="budget"),
               lambda value: InformationGainSelector().rank({"a": .5, "b": .5}, [], value),
               lambda value: ProblemSignature("domain", "uncertainty", "claim", 1, value)]
    for read in readers:
        with pytest.raises(NotPlainData) as caught:
            read(budget)
        assert caught.value.code == INPUT
