# SPDX-License-Identifier: Apache-2.0
"""Entry-path and numeric policies, inventoried from the bridge's code."""
from __future__ import annotations

import ast
import copy
import inspect
import math
import sys
from collections import UserDict
from pathlib import Path
from types import MappingProxyType

import pytest
from returns.result import Success

import g6.cognitive_bridge as bridge
from g6.cognitive_bridge import codes, snapshot
from g6.cognitive_bridge.block import CognitiveBridgeBlock
from g6.core.registry import ComponentRef, ComponentRegistry


def invoke(path, arguments):
    if path == "direct":
        return CognitiveBridgeBlock().infer(arguments).value
    if path == "screen":
        block = CognitiveBridgeBlock()
        early = block.screen_raw(arguments)
        return early.value if early else block.infer(arguments).value
    registry = ComponentRegistry()
    registry._refs = {"cognitive_bridge": ComponentRef("cognitive_bridge", "g6.cognitive_bridge", "builtin")}
    answer = registry.invoke("cognitive_bridge", arguments["op"], arguments)
    assert isinstance(answer, Success), answer
    return answer.unwrap()


class ForeignArguments(dict):
    pass


class DuckMapping:
    def __init__(self, values):
        self.values = values

    def keys(self):
        return self.values.keys()

    def __getitem__(self, key):
        return self.values[key]


class FalsyMapping(ForeignArguments):
    def __bool__(self):
        return False


PATHS = ("direct", "screen", "registry")
MAPPINGS = (dict, ForeignArguments, UserDict, MappingProxyType, DuckMapping)


def test_every_raw_block_entry_is_in_the_path_inventory():
    # Discover parameter-taking actions rather than selecting only infer.
    actions = {name for name, fn in inspect.getmembers(CognitiveBridgeBlock, inspect.isfunction)
               if not name.startswith("_") and fn.__module__ == CognitiveBridgeBlock.__module__}
    assert actions == {"infer", "screen_raw"}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("mapping", MAPPINGS)
@pytest.mark.parametrize("op", ["ops", "help", "assurance_schema", "plan_methods", "rank", "update", "rank_methods"])
def test_outer_mappings_follow_one_rule(path, mapping, op):
    from tests.cognitive_bridge.test_conversion_inventory import REQUESTS
    request = copy.deepcopy(REQUESTS.get(op, {"op": op}))
    if op == "rank":
        request = copy.deepcopy(REQUESTS["update"])
        request["op"] = op
    expected = invoke(path, request)
    answer = invoke(path, mapping(request))
    assert answer == expected
    assert codes.claims(answer) == []


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("mapping", MAPPINGS)
def test_outer_copy_does_not_convert_nested_mappings(path, mapping):
    arguments = mapping({"op": "plan_methods", "claims": [{
        "id": "c", "statement": "A", "evidence_needs": ["formal"],
        "models": ForeignArguments(formal={"formula": "A"}),
    }]})
    answer = invoke(path, arguments)
    assert answer["code"] == codes.INPUT
    assert "models" in answer["reason"]


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("mapping", MAPPINGS)
def test_outer_copy_does_not_authorize_a_claim(path, mapping):
    answer = invoke(path, mapping({"op": "plan_methods", "claims": [{
        "id": "c", "statement": "A", "evidence_needs": ["formal"],
        "models": {"formal": {"formula": "A", "verified": True}},
    }]}))
    assert answer["code"] == codes.INPUT


@pytest.mark.parametrize("path", PATHS)
def test_falsy_outer_mapping_has_the_same_absence_semantics_as_the_registry(path):
    assert invoke(path, FalsyMapping(op="help", budget=999))["op"] == "help"


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("mapping", MAPPINGS)
def test_outer_mapping_still_requires_text_keys(path, mapping):
    answer = invoke(path, mapping({"op": "ops", 1: "invalid"}))
    assert answer["code"] == codes.INPUT


def test_screened_mapping_is_detached_and_consumed_once():
    arguments = ForeignArguments(op="help")
    block = CognitiveBridgeBlock()
    assert block.screen_raw(arguments) is None
    arguments["op"] = "ops"
    assert block.infer(arguments).value["code"] == codes.INPUT
    # The refused call consumed its held input; a later call and a replacement instance start fresh.
    assert block.infer(arguments).value["ops"]
    assert CognitiveBridgeBlock().infer(arguments).value["ops"]


def numeric_readers():
    """Every explicit numeric reader and typed numeric field, from existing code inventories."""
    from tests.cognitive_bridge.test_boundary_inventory import MADE, _record_classes
    from tests.cognitive_bridge.test_conversion_inventory import PARAMETER_CALLS, PLANTS, leaf_parameters
    from g6.cognitive_bridge.snapshot import _kinds
    from g6.cognitive_bridge import engine, xai
    readers = {f"parameter:{name}.{parameter}": call for (name, parameter), call in PARAMETER_CALLS.items()
               if not leaf_parameters()[(name, parameter)].startswith("bool")}
    for key, (_, places) in PLANTS.items():
        if key[2] in ("float", "struct.pack", "tolist"):
            readers.update({f"conversion:{name}": call for name, call in places.items()})
    readers.update({
        "collaborator:predictor-scores": lambda value: snapshot.returned([value], engine._scores, 1),
        "collaborator:selector-bits": lambda value: snapshot.returned(
            [{"id": "p", "expected_information_bits": value}], engine._selection,
            (bridge.Probe("p", "q", "read", {"a": True}, .8),)),
        "collaborator:regression-predictions": lambda value: snapshot.returned(
            [value], xai._predictions, 1, "regression"),
        "estimator:numeric-array": lambda value: xai._numbers([value], where="scores"),
    })
    for name, cls in _record_classes().items():
        for field, kind in _kinds(cls).items():
            if kind[0] not in ("number", "integer"):
                continue
            def read(value, name=name, field=field, cls=cls):
                import dataclasses
                return dataclasses.replace(MADE[name](), **{field: value})
            readers[f"field:{name}.{field}"] = read
    return readers


def test_numeric_inventory_covers_all_number_reads():
    from tests.cognitive_bridge import test_conversion_inventory as conversion
    conversion.test_the_public_parameters_that_declare_a_number_or_a_flag_are_listed_from_the_code()
    conversion.test_the_record_fields_that_declare_a_number_or_a_flag_are_listed_from_the_code()
    conversion.test_the_conversions_are_listed_from_the_code()
    conversion.test_every_conversion_a_callers_value_reaches_is_planted_here()
    assert numeric_readers()


NUMERIC_OWNERS = {
    "adaptation:AdaptationPromotionGate.__init__", "codes:_read_as",
    "critique:LLMMetacognitiveCritic.__init__", "engine:_beliefs", "engine:_validate_probe",
    "engine:InformationGainSelector.__init__", "engine:InformationGainSelector.rank",
    "engine:LLMProposer.__init__", "engine:_scores", "engine:MLAcquisitionSelector.__init__",
    "engine:MLAcquisitionSelector.rank", "engine:_selection", "engine:CognitiveAgent.run",
    "engine:LLMIdeaGenerator.__init__", "metacognition:ProblemSignature.__post_init__",
    "metacognition:MethodVariant.__post_init__", "metacognition:EvaluatedTrial.__post_init__",
    "metacognition:MetacognitiveMethodSelector.__init__", "metacognition:MetacognitiveMethodSelector.select",
    "mixed_methods:LLMClaimCompiler.__init__", "orchestration:CognitiveAssuranceOrchestrator.compile",
    "quantitative:evaluated_trace_rows", "quantitative:sklearn_payload", "snapshot:_leaf",
    "xai:_estimated", "xai:_numbers", "xai:_float32", "xai:extract_tree.visit", "xai:distill_blackbox",
}


def numeric_owners():
    """Every function that reads or converts a number; fail when a new consumer is not classified."""
    found = set()
    readers = {"number", "math.isfinite", "float", "struct.pack"}

    def walk(node, module, prefix="", owner=""):
        if isinstance(node, ast.ClassDef):
            prefix += node.name + "."
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = prefix + node.name
            prefix = owner + "."
        elif isinstance(node, ast.Call) and ast.unparse(node.func) in readers:
            found.add(f"{module}:{owner}")
        for child in ast.iter_child_nodes(node):
            walk(child, module, prefix, owner)

    for path in Path(codes.__file__).parent.glob("*.py"):
        walk(ast.parse(path.read_text(encoding="utf-8")), path.stem)
    return found


def test_every_numeric_consumer_is_classified_from_the_code():
    assert numeric_owners() == NUMERIC_OWNERS


@pytest.mark.parametrize("value", [10 ** 400, -(10 ** 400), 10 ** 4000, math.inf, -math.inf, math.nan,
                                  int(sys.float_info.max) + 1, int(sys.float_info.max) + 2 ** 970 - 1,
                                  -int(sys.float_info.max) - 1],
                         ids=["huge-positive", "huge-negative", "encoder-length", "inf", "negative-inf", "nan",
                              "max-plus-one", "last-rounding-integer", "min-minus-one"])
def test_encoder_and_every_numeric_reader_refuse_unrepresentable_numbers(value):
    with pytest.raises(snapshot.NotPlainData) as caught:
        snapshot.canonical_json(value)
    assert caught.value.code == codes.INPUT
    for name, read in numeric_readers().items():
        try:
            answer = read(value)
        except snapshot.NotPlainData as exc:
            assert exc.code == codes.INPUT, name
            continue
        # Some readers return the block's registered refusal rather than raising.
        answer = answer.value if hasattr(answer, "is_fail") else answer
        assert isinstance(answer, dict) and answer.get("code") == codes.INPUT, (name, answer)


@pytest.mark.parametrize("value", [0, 1, 10 ** 300, int(sys.float_info.max), sys.float_info.max])
def test_encoder_numeric_reader_and_cost_records_agree_at_supported_limits(value):
    assert snapshot.number(value, where="number") == value
    assert snapshot.record_data(value) == value
    assert bridge.ProblemSignature("d", "u", "c", 1, value).budget == value
    assert bridge.MethodVariant("v", "formal", "smt", "formal", "check_validity", value).estimated_cost == value
    assert bridge.InformationGainSelector().rank({"a": .5, "b": .5}, [], value) == []


def readable_members():
    """Resolve public members through each class's MRO, including inherited descriptors."""
    for name in bridge.__all__:
        cls = getattr(bridge, name)
        if not inspect.isclass(cls):
            continue
        fields = set()
        for base in cls.__mro__:
            fields.update(getattr(base, "__annotations__", {}))
            try:
                tree = ast.parse(inspect.getsource(base))
            except (OSError, TypeError, IndentationError):
                continue
            fields.update(node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                          and isinstance(node.value, ast.Name) and node.value.id == "self"
                          and isinstance(node.ctx, ast.Store))
        for attr in sorted(set(dir(cls)) | fields):
            if not attr.startswith("_"):
                yield name, attr, inspect.getattr_static(cls, attr, None)


def test_readable_inventory_includes_inherited_properties_and_base_attributes():
    members = {(name, attr) for name, attr, _ in readable_members()}
    assert {("CognitiveBridgeBlock", attr) for attr in
            ("name", "state", "bias", "contract", "verify", "then")} <= members
    assert ("CognitiveInput", "model_extra") in members
    assert ("LLMProposer", "llm") in members
