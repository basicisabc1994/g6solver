# SPDX-License-Identifier: Apache-2.0
"""Identity classification must preserve the chosen outer mapping protocol."""
import pytest

from g6.cognitive_bridge import CognitiveBridgeBlock, codes
from tests.cognitive_bridge.test_followup3_repair1_inventory import PATHS, invoke


class InstanceKeys:
    def __init__(self, data):
        self.data = data
        self.keys = data.keys

    def __getitem__(self, key):
        return self.data[key]


class DynamicKeys:
    def __init__(self, data):
        self.data = data

    def __getattr__(self, name):
        if name == "keys":
            return self.data.keys
        raise AttributeError(name)

    def __getitem__(self, key):
        return self.data[key]


class FalsyInstanceKeys(InstanceKeys):
    def __bool__(self):
        return False


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("mapping", [InstanceKeys, DynamicKeys, FalsyInstanceKeys])
@pytest.mark.parametrize("op", ["ops", "help", "plan_methods"])
def test_outer_mapping_copy_does_not_guess_capability_from_class_attributes(path, mapping, op):
    request = {"op": op}
    answer = invoke(path, mapping(request))
    assert answer == invoke(path, request)
    assert codes.claims(answer) == []


@pytest.mark.parametrize("entry", ["infer", "screen_raw"])
def test_malformed_outer_protocol_still_has_a_registered_refusal(entry):
    class Malformed:
        def __bool__(self):
            raise RuntimeError("unreadable outer protocol")

    answer = getattr(CognitiveBridgeBlock(), entry)(Malformed()).value
    assert answer["code"] == codes.INPUT
    assert codes.claims(answer) == []
