# SPDX-License-Identifier: Apache-2.0
"""Optional extraction resolution cannot bypass grounding's advisory boundary."""
import importlib
import json
from types import SimpleNamespace

import pytest

from g6.core import semantic
from g6.core.llm import FakeLLM
from g6.core.registry import get_registry
from g6.grounding import GroundingBlock, GroundingInput
from tests.grounding.test_grounding_engine_opt_in import google_library

CLAIM = "Customers have a month to return the product"
SOURCE = "You can return items within thirty days of purchasing them."


@pytest.mark.parametrize("failure", [KeyError, ModuleNotFoundError, RuntimeError, AttributeError, TypeError])
def test_unavailable_component_abstains_even_with_injected_library(monkeypatch, failure):
    registry = get_registry()
    original = registry.get_module
    resolved = []

    def unavailable(name):
        if name != "ctx_langextract":
            return original(name)
        resolved.append(name)
        if failure is AttributeError:
            return SimpleNamespace()
        if failure is TypeError:
            return SimpleNamespace(grounding_backend=None)
        raise failure("unavailable extraction component")

    monkeypatch.setenv("G6_GROUNDING_ENGINE", "auto")
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    monkeypatch.setattr(registry, "get_module", unavailable)
    fake = FakeLLM(default='{"value":{"extractions":[]}}')
    with semantic.request(llm=fake):
        out = GroundingBlock(langextract_module=google_library()).infer(GroundingInput(
            claim=CLAIM, sources={"policy": SOURCE}, judge="model")).unwrap()
    assert resolved == ["ctx_langextract"]
    assert not fake.calls
    assert out["check"]["status"] == "not_found"
    assert out["completion_state"] == "blocked-escalated"
    assert out["check"]["model_reading"] == {
        "reading": "unclear", "engine": "ctx_langextract", "model_ran": False,
        "span_engine": "ctx_langextract", "passages": [], "patterns": [], "windows": [],
        "errors": [{"code": "extraction_failed", "source": "", "type": failure.__name__}],
        "quote": "", "quote_found": False, "lexical_on_quote": None, "source": "",
        "reason": "extraction incomplete"}
    assert out["warning_card"]["code"] == "G6_E_GROUNDING_NOT_FOUND"
    assert any(card["code"] == "G6_E_GROUNDING_LEXICAL_ONLY"
               for card in out["warning_card"]["warnings"])


def test_registered_backend_uses_the_existing_semantic_caller(monkeypatch):
    registry = get_registry()
    component = registry.get_module("ctx_langextract")
    backend = component.grounding_backend
    monkeypatch.setattr(backend, "load_library", lambda: None)
    original = registry.get_module
    resolved = []

    def resolve(name):
        resolved.append(name)
        return original(name)

    monkeypatch.setenv("G6_GROUNDING_ENGINE", "auto")
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    monkeypatch.setattr(registry, "get_module", resolve)
    fake = FakeLLM(default=json.dumps({"value": {
        "supports": SOURCE, "contradicts": None, "unrelated": None}}))
    reader = importlib.import_module("g6.grounding.model_reading")
    with semantic.request(llm=fake):
        reading = reader.model_reading(CLAIM, {"policy": SOURCE})
    assert resolved == ["ctx_langextract"]
    assert reader._Caller.__module__ == "g6.grounding.model_reading"
    assert len(fake.calls) == 1
    assert reading["engine"] == "ctx_langextract" and reading["reading"] == "supports"
    assert reading["model_ran"] is True and reading["passages"][0]["grounded"] is True
    assert reading["lexical_on_quote"]["status"] == "not_found"


def test_disabled_reader_does_not_resolve_the_backend(monkeypatch):
    def forbidden(name):
        raise AssertionError("disabled extraction must not resolve a component")

    monkeypatch.setenv("G6_GROUNDING_ENGINE", "lexical")
    monkeypatch.setattr(get_registry(), "get_module", forbidden)
    reader = importlib.import_module("g6.grounding.model_reading")
    fake = FakeLLM(default='{"value":{"extractions":[]}}')
    with semantic.request(llm=fake):
        reading = reader.model_reading(CLAIM, {"policy": SOURCE}, langextract_module=google_library())
    assert reading == {
        "reading": "unclear", "engine": "lexical", "model_ran": False,
        "span_engine": "ctx_langextract", "passages": [], "patterns": [], "windows": [], "errors": [],
        "quote": "", "quote_found": False, "lexical_on_quote": None, "source": "",
        "reason": "grounding model engine disabled"}
    assert not fake.calls
