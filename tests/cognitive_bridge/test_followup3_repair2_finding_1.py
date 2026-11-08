"""Exact-type admission must not delegate equality to an input's metaclass."""
import pytest
from returns.result import Success

from g6.cognitive_bridge import ClaimIR, CognitiveBridgeBlock
from g6.cognitive_bridge import codes
from g6.cognitive_bridge.codes import INPUT, NotPlainData
from g6.cognitive_bridge.snapshot import plain
from g6.core.registry import ComponentRef, ComponentRegistry


class MappingIdentity(type):
    def __eq__(cls, other):
        return other is dict or other is int or other is cls

    __hash__ = type.__hash__


class ForeignModel(dict, metaclass=MappingIdentity):
    pass


@pytest.mark.parametrize("entry", ["plain", "record", "direct", "registry", "refusal", "settled"])
def test_nested_subclass_cannot_choose_its_own_admitted_kind(entry):
    model = ForeignModel(formula="A")
    assert type(model) is not dict
    if entry in {"refusal", "settled"}:
        payload = ForeignModel(verified=True, reliability_label="verified")
        try:
            answer = (codes.refusal(INPUT, "bad input", payload=payload) if entry == "refusal"
                      else codes.settled(None, payload=payload))
        except (TypeError, ValueError):
            return
        assert codes.claims(answer) == [], "the maker returned a caller's verified flag and reliability label"
        return
    request = {"op": "plan_methods", "claims": [{
        "id": "c", "statement": "check A", "evidence_needs": ["formal"],
        "models": {"formal": model},
    }]}
    if entry in {"plain", "record"}:
        with pytest.raises(NotPlainData) as caught:
            if entry == "plain":
                plain(request, where="request")
            else:
                ClaimIR("c", "check A", ("formal",), {"formal": model})
        assert caught.value.code == INPUT
        return
    if entry == "direct":
        answer = CognitiveBridgeBlock().infer(request).value
    else:
        registry = ComponentRegistry()
        registry._refs = {"cognitive_bridge": ComponentRef(
            "cognitive_bridge", "g6.cognitive_bridge", "builtin")}
        outcome = registry.invoke("cognitive_bridge", "plan_methods", request)
        assert isinstance(outcome, Success), outcome
        answer = outcome.unwrap()
    assert answer.get("code") == INPUT, "a nested subclass was accepted as exact dict data"
