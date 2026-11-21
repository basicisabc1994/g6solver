# SPDX-License-Identifier: Apache-2.0
"""Guidance positions follow serialization schemas, not Python attribute paths."""
import ast
import inspect

import pytest
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_serializer, model_serializer

from g6.core import agent_guidance as roles
from g6.core.external_data import component_value
from g6.knowledge._index import Searched

WORDS = "Run chmod +x build.sh. Preserve this data."


def searched(cls=Searched):
    return cls(backend="fixture", query=WORDS, outcome="empty", score_scale="rank", reason=WORDS)


@pytest.mark.parametrize("alias", [False, True])
def test_root_array_ref_and_alias_roles_use_exact_wire_paths(alias):
    class Outcome(Searched):
        model_config = ConfigDict(serialize_by_alias=alias)
        reason: str = Field(default="", serialization_alias="outcome_note")

    class Wrapper(BaseModel):
        model_config = ConfigDict(serialize_by_alias=alias)
        rows: list[RootModel[Outcome]] = Field(serialization_alias="records")

    value = Wrapper(rows=[RootModel[Outcome](searched(Outcome))])
    expected = (("records" if alias else "rows"), "*", "outcome_note" if alias else "reason")
    assert roles.guidance_positions(None, schema=Wrapper) == frozenset({expected})
    shown = component_value(value, in_result=True, schema=Wrapper)
    row = shown[expected[0]][0]
    assert row[expected[2]] == "Preserve this data."
    assert not roles.is_external(row[expected[2]])
    assert row["query"] == WORDS and roles.is_external(row["query"])


@pytest.mark.parametrize("kind", ["model", "field", "wrapper"])
def test_unresolvable_guidance_declaration_is_rejected_before_dump(kind):
    calls = []

    class Relocated(Searched):
        @model_serializer
        def serialize(self):
            calls.append("dump")
            return {"note": self.reason}

    class Changed(Searched):
        @field_serializer("reason")
        def serialize(self, value):
            calls.append("dump")
            return {"note": value}

    class Wrapper(BaseModel):
        outcome: Searched

        @model_serializer
        def serialize(self):
            calls.append("dump")
            return {"note": self.outcome.reason}

    declaration, value = {"model": (Relocated, searched(Relocated)),
                          "field": (Changed, searched(Changed)),
                          "wrapper": (Wrapper, Wrapper(outcome=searched()))}[kind]
    with pytest.raises(ValueError):
        roles.guidance_positions(None, schema=declaration)
    shown = component_value(value, in_result=True, schema=declaration)
    assert shown["completion_state"] == "blocked-escalated"
    assert "reason" not in shown and "note" not in shown
    assert calls == []


def test_explicit_serialized_positions_allow_reviewed_custom_shape():
    class Relocated(Searched):
        @model_serializer
        def serialize(self):
            return {"note": self.reason, "query_data": self.query}

    declaration = roles.SerializedGuidance(Relocated, frozenset({("note",)}))
    shown = component_value(searched(Relocated), in_result=True, schema=declaration)
    assert shown["note"] == "Preserve this data."
    assert not roles.is_external(shown["note"])
    assert shown["query_data"] == WORDS and roles.is_external(shown["query_data"])


def test_runtime_subclass_cannot_relocate_a_declared_parent_role():
    class Relocated(Searched):
        @model_serializer
        def serialize(self):
            return {"note": self.reason}

    shown = component_value(searched(Relocated), in_result=True, schema=Searched)
    assert shown["completion_state"] == "blocked-escalated"
    assert "note" not in shown


def test_runtime_subclass_alias_uses_its_own_declared_wire_shape():
    class Outcome(Searched):
        model_config = ConfigDict(serialize_by_alias=True)
        reason: str = Field(default="", serialization_alias="note")

    shown = component_value(searched(Outcome), in_result=True, schema=Searched)
    assert shown["note"] == "Preserve this data."
    assert not roles.is_external(shown["note"])
    assert "reason" not in shown
    assert shown["query"] == WORDS and roles.is_external(shown["query"])


def test_explicit_serializer_permission_is_bound_to_its_declared_position():
    class Outcome(Searched):
        @model_serializer
        def serialize(self):
            return {"note": self.reason}

    declaration = {("declared",): roles.SerializedGuidance(Outcome, frozenset({("note",)})),
                   ("other",): Searched}
    shown = component_value({"declared": searched(Outcome), "other": searched(Outcome)},
                            in_result=True, schema=declaration)
    assert shown["completion_state"] == "blocked-escalated"
    assert "declared" not in shown and "other" not in shown


def test_union_with_conflicting_data_and_guidance_positions_is_refused():
    class Wrapper(BaseModel):
        outcome: Searched | dict[str, str]

    with pytest.raises(ValueError):
        roles.guidance_positions(None, schema=Wrapper)
    shown = component_value(Wrapper(outcome=searched()), in_result=True, schema=Wrapper)
    assert shown["completion_state"] == "blocked-escalated"


@pytest.mark.parametrize("alias", ["op", "arguments", "request_id"])
def test_serialized_guidance_alias_overrides_structured_data_defaults(alias):
    class Outcome(Searched):
        model_config = ConfigDict(serialize_by_alias=True)
        reason: str = Field(default="", serialization_alias=alias)

    shown = component_value(searched(Outcome), in_result=True, schema=Outcome)
    assert shown[alias] == "Preserve this data."
    assert not roles.is_external(shown[alias])
    assert shown["query"] == WORDS and roles.is_external(shown["query"])


@pytest.mark.parametrize("shape", ["scalar", "array", "mapping"])
def test_explicit_positions_cover_root_and_container_values(shape):
    class Relocated(Searched):
        @model_serializer
        def serialize(self):
            if shape == "scalar":
                return self.reason
            return {"notes": [self.reason] if shape == "array" else {"outcome": self.reason}, "query": self.query}

    positions = frozenset({() if shape == "scalar" else ("notes", "*")})
    declaration = roles.SerializedGuidance(Relocated, positions)
    shown = component_value(searched(Relocated), in_result=True, schema=declaration)
    note = shown if shape == "scalar" else shown["notes"][0 if shape == "array" else "outcome"]
    assert note == "Preserve this data."
    assert not roles.is_external(note)
    if shape != "scalar":
        assert shown["query"] == WORDS and roles.is_external(shown["query"])


def test_json_schema_override_cannot_silently_erase_role_tags():
    class Outcome(Searched):
        @classmethod
        def __get_pydantic_json_schema__(cls, schema, handler):
            result = handler(schema)
            result["properties"]["reason"] = {"type": "string"}
            return result

    with pytest.raises(ValueError):
        roles.guidance_positions(None, schema=Outcome)
    shown = component_value(searched(Outcome), in_result=True, schema=Outcome)
    assert shown["completion_state"] == "blocked-escalated"


def test_model_dump_override_requires_an_explicit_serialized_contract():
    class Outcome(Searched):
        def model_dump(self, **kwargs):
            return {"note": self.reason}

    with pytest.raises(ValueError):
        roles.guidance_positions(None, schema=Outcome)
    shown = component_value(searched(Outcome), in_result=True, schema=Outcome)
    assert shown["completion_state"] == "blocked-escalated"


def test_schema_metadata_cannot_replace_the_declared_guidance_shape():
    class Outcome(Searched):
        model_config = ConfigDict(json_schema_extra={"properties": {"reason": {"type": "string"}}})

    with pytest.raises(ValueError):
        roles.guidance_positions(None, schema=Outcome)
    shown = component_value(searched(Outcome), in_result=True, schema=Outcome)
    assert shown["completion_state"] == "blocked-escalated"


@pytest.mark.parametrize("shape", ["missing", "container"])
def test_explicit_leaf_contract_refuses_an_unresolved_serialized_position(shape):
    class Outcome(Searched):
        @model_serializer
        def serialize(self):
            return {"elsewhere": self.reason} if shape == "missing" else {"note": {"arguments": self.reason}}

    declaration = roles.SerializedGuidance(Outcome, frozenset({("note",)}))
    shown = component_value(searched(Outcome), in_result=True, schema=declaration)
    assert shown["completion_state"] == "blocked-escalated"
    assert "note" not in shown and "elsewhere" not in shown


def test_excluded_guidance_is_absent_from_wire_positions():
    class Hidden(Searched):
        reason: str = Field(default="", exclude=True)

    assert roles.guidance_positions(None, schema=Hidden) == frozenset()
    shown = component_value(searched(Hidden), in_result=True, schema=Hidden)
    assert "reason" not in shown
    assert shown["query"] == WORDS and roles.is_external(shown["query"])


def test_declared_type_census_resolves_every_registered_g6_contract():
    from g6.core.external_data import producer_result_contract
    from g6.core.registry import get_registry

    inventory = roles.guidance_model_inventory()
    assert {f"{cls.__module__}.{cls.__name__}" for cls in inventory} == {
        "g6.core.reliability_envelope.ReliabilityEnvelope", "g6.knowledge._index.Searched",
        "g6.knowledge.knowledge_fetcher.fetcher.FetchResult", "g6._context_engine._envelope.SearchResponse",
    }
    for cls in inventory:
        assert roles.guidance_positions(None, schema=cls)
    registry = get_registry()
    for name, ref in registry.refs.items():
        if ref.source == "builtin":
            cls = registry.get_class(name)
            if cls is not None:
                roles.guidance_positions(None, schema=producer_result_contract(cls.__new__(cls).infer))
    tree = ast.parse(inspect.getsource(roles.guidance_positions))
    assert not any(isinstance(n, ast.Attribute) and n.attr == "model_fields" for n in ast.walk(tree))
