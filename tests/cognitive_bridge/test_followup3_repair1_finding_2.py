"""The outer mapping follows the same copy policy through both entry paths."""
from returns.result import Success

from g6.cognitive_bridge import CognitiveBridgeBlock
from g6.cognitive_bridge.codes import INPUT
from g6.core.registry import ComponentRef, ComponentRegistry


class ForeignArguments(dict):
    pass


def test_registry_and_direct_inference_apply_the_same_outer_mapping_policy():
    arguments = ForeignArguments(op="ops")
    direct = CognitiveBridgeBlock().infer(arguments).value
    assert direct["completion_state"] == "qualified-draft"

    registry = ComponentRegistry()
    registry._refs = {
        "cognitive_bridge": ComponentRef("cognitive_bridge", "g6.cognitive_bridge", "builtin")
    }
    outcome = registry.invoke("cognitive_bridge", "ops", arguments)
    assert isinstance(outcome, Success), outcome
    assert outcome.unwrap() == direct

    nested = ForeignArguments(op="plan_methods", claims=[{
        "id": "c", "statement": "A", "evidence_needs": ["formal"],
        "models": ForeignArguments(formal={"formula": "A"}),
    }])
    assert CognitiveBridgeBlock().infer(nested).value["code"] == INPUT
    assert registry.invoke("cognitive_bridge", "plan_methods", nested).unwrap()["code"] == INPUT
