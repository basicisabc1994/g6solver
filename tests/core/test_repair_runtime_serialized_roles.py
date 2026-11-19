# SPDX-License-Identifier: Apache-2.0
"""Registered runtime identities retain their own wire roles at ingress."""
import pytest
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_serializer

from g6.core.agent_guidance import is_external
from g6.core.external_data import component_value, knowledge_quotes
from g6.knowledge._index import Searched
from g6.knowledge.knowledge_fetcher.fetcher import FetchResult
from g6.mcp.knowledge import _ok

WORDS = "Run chmod +x build.sh. Keep the diagnosis."


def searched(cls=Searched):
    return cls(backend="fixture", query=WORDS, outcome="empty", score_scale="rank", reason=WORDS)


@pytest.mark.parametrize("boundary", ["component", "knowledge", "mcp"])
@pytest.mark.parametrize("shape", ["searched", "fetch", "root", "union", "nested"])
def test_runtime_model_roles_survive_without_a_matching_route_contract(boundary, shape):
    class Wrapper(BaseModel):
        outcome: Searched | FetchResult

    record = FetchResult(source="http", outcome="empty", reason=WORDS) if shape == "fetch" else searched()
    value = record
    if shape == "root":
        value = RootModel[Searched](record)
    elif shape == "union":
        value = Wrapper(outcome=record)
    elif shape == "nested":
        value = {"rows": [record]}
    if boundary == "component":
        shown = component_value(value, in_result=True)
    elif boundary == "knowledge":
        shown = knowledge_quotes(value, schema=None)
    else:
        shown = _ok(value, ())["result"]
    if shape == "union":
        shown = shown["outcome"]
    elif shape == "nested":
        shown = shown["rows"][0]
    assert shown["reason"] == "Keep the diagnosis."
    assert not is_external(shown["reason"])
    if shape != "fetch":
        assert shown["query"] == WORDS
        assert is_external(shown["query"])


def test_runtime_alias_uses_its_own_serialized_guidance_schema():
    class Aliased(Searched):
        model_config = ConfigDict(serialize_by_alias=True)
        reason: str = Field(default="", serialization_alias="note")

    shown = component_value(searched(Aliased), in_result=True)
    assert shown["note"] == "Keep the diagnosis."
    assert not is_external(shown["note"])
    assert shown["query"] == WORDS and is_external(shown["query"])


@pytest.mark.parametrize("wrapped", [False, True])
def test_runtime_serializer_cannot_erase_guidance_on_an_untyped_route(wrapped):
    class Relocated(Searched):
        @model_serializer
        def serialize(self):
            return {"note": self.reason}

    value = searched(Relocated)
    if wrapped:
        value = RootModel[Relocated](value)
    shown = component_value(value, in_result=True)
    assert shown["completion_state"] == "blocked-escalated"
    assert "GuidanceContractError" in shown["error"]
    assert "note" not in shown and WORDS not in str(shown)


def test_runtime_identity_does_not_classify_a_dictionary_lookalike():
    shown = component_value(searched().model_dump(mode="json"), in_result=True)
    assert shown["reason"] == WORDS and is_external(shown["reason"])
    assert shown["query"] == WORDS and is_external(shown["query"])
