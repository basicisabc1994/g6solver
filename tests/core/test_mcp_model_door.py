# SPDX-License-Identifier: Apache-2.0
"""The MCP door passes the person's model to the components whose block takes one. ``g6_invoke`` builds the model the
person configured (``g6.core.llm.provider_from_env``) once per call and gives it only to a component that declares
``__g6_model__`` (its block's constructor keyword); with none configured, a component that needs one says how to
configure one. A sensitive op stays refused over MCP whatever model is set. No network, no real program: a
``FakeLLM`` stands in for the configured model."""
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from g6.core import llm
from g6.core.registry import ComponentRef, ComponentRegistry, get_registry
from g6.mcp import invoke_reply

#: Each component whose block takes a model, and the constructor keyword it takes it by.
TAKES_A_MODEL = {"adapt_healing": "caller", "adapt_library_synthesizer": "caller", "component_creator": "caller",
                 "cog_arch_gps": "caller", "ctx_langextract": "caller", "ctx_vision": "caller", "lean": "llm",
                 "adapt_synth": "llm"}
PROBLEM = json.dumps({"initial": ["at_home"], "goals": ["at_shop"],
                      "operators": [{"name": "walk", "preconditions": ["at_home"], "add_list": ["at_shop"],
                                     "delete_list": ["at_home"]}], "plan": ["walk"]})
ENCODE = json.dumps({"description": "Get from home to the shop with no car."})


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> llm.FakeLLM:
    """The person's configured model, as provider_from_env builds it, stood in for by a FakeLLM."""
    fake = llm.FakeLLM(lambda system, user: PROBLEM)
    monkeypatch.setattr(llm, "provider_from_env", lambda *args, **kwargs: fake)
    return fake


@pytest.fixture
def none_configured(key_file, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file("")
    for name in ("G6_LLM_PROVIDER", "G6_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_an_op_that_needs_a_model_runs_on_the_one_the_person_configured(configured: llm.FakeLLM) -> None:
    reply = invoke_reply("cog_arch_gps", "encode", ENCODE)
    assert reply["ok"], reply
    result = reply["result"]
    assert result["code"] != "G6_E_GPS_NO_MODEL" and result["plan"] == ["walk"], result
    assert len(configured.calls) == 1 and "Get from home to the shop" in configured.calls[0][1]
    assert reply["completion_state"] == "qualified-draft"   # a model's encoding is a draft, never more
    # Removing the llm hand-over in invoke_reply, or the __g6_model__ branch in ComponentRegistry.invoke, makes this
    # fail with G6_E_GPS_NO_MODEL.


def test_with_no_model_configured_the_refusal_says_how_to_configure_one(none_configured) -> None:
    reply = invoke_reply("cog_arch_gps", "encode", ENCODE)
    assert reply["ok"] and reply["result"]["code"] == "G6_E_GPS_NO_MODEL"
    said = json.dumps(reply["result"])
    assert "none over MCP" not in said
    assert "G6_LLM_PROVIDER=claude_code" in said and "G6_LLM_PROVIDER=codex" in said and "ollama" in said
    assert ".g6" in said   # the key file
    # Restoring the old "there is none over MCP" wording in cog_arch_gps makes this fail.


def test_a_setting_g6_cannot_use_is_said_when_the_model_is_asked(key_file, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file("")
    monkeypatch.setenv("G6_LLM_PROVIDER", "claude_code")   # an agent's environment, not the key file
    reply = invoke_reply("cog_arch_gps", "encode", ENCODE)
    assert reply["ok"] and reply["result"]["code"] == "G6_E_GPS_MODEL_FAILED"
    assert "G6_LLM_PROVIDER=claude_code" in json.dumps(reply["result"])
    monkeypatch.setenv("G6_LLM_PROVIDER", "no-such-provider")
    reply = invoke_reply("cog_arch_gps", "encode", ENCODE)
    assert reply["ok"] and "unknown G6_LLM_PROVIDER" in json.dumps(reply["result"])
    # Letting provider_from_env's LLMError escape invoke_reply (no try in _person_model) makes the second half fail.


def test_a_sensitive_op_is_still_refused_over_mcp_with_a_model(configured: llm.FakeLLM) -> None:
    reply = invoke_reply("adapt_healing", "heal", json.dumps({}))
    assert not reply["ok"] and reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    assert configured.calls == []
    # Moving the sensitive-op refusal in ComponentRegistry.invoke after the block is built with the model makes the
    # model reachable; removing it makes this fail.


def _registry(module: object) -> ComponentRegistry:
    registry = ComponentRegistry()
    registry._refs = {"fake": ComponentRef("fake", "fake_module", "builtin")}
    registry.get_module = lambda name: module   # type: ignore[method-assign]
    return registry


class _Recorded:
    built: list[dict] = []

    def __init__(self, **kwargs: object) -> None:
        type(self).built.append(kwargs)

    def infer(self, data: dict):  # noqa: ANN201 - untyped input: the registry hands over the dict
        return SimpleNamespace(is_fail=lambda: False, value={"op": data["op"]})


@pytest.mark.parametrize("declared", [None, "caller"])
def test_only_a_component_that_declares_its_model_keyword_is_built_with_the_model(declared: str | None) -> None:
    _Recorded.built = []
    module = SimpleNamespace(__g6_block__="_Recorded", _Recorded=_Recorded, __g6_sensitive_ops__=())
    if declared:
        module.__g6_model__ = declared
    model = llm.FakeLLM()
    assert _registry(module).invoke("fake", "ops", {}, llm=model).__class__.__name__ == "Success"
    assert _registry(module).invoke("fake", "ops", {}).__class__.__name__ == "Success"
    assert _Recorded.built == ([{"caller": model}, {}] if declared else [{}, {}])
    # Building every block with the model (cls(**{...}) without the __g6_model__ check) makes the None case fail.


@pytest.mark.parametrize(("component", "keyword"), sorted(TAKES_A_MODEL.items()))
def test_each_component_that_takes_a_model_declares_the_keyword_its_block_takes(component: str, keyword: str) -> None:
    registry = get_registry()
    assert getattr(registry.get_module(component), "__g6_model__", None) == keyword
    assert keyword in inspect.signature(registry.get_class(component).__init__).parameters
    # Removing a component's __g6_model__ declaration makes its case fail.


def test_a_component_whose_block_takes_no_model_caller_declares_none() -> None:
    registry = get_registry()
    for name in ("ctx_search", "agent_langgraph", "adapt_image", "grounding"):
        assert getattr(registry.get_module(name), "__g6_model__", None) is None, name


def test_ctx_vision_says_plainly_that_a_model_that_takes_no_images_was_not_shown_the_picture(
        monkeypatch: pytest.MonkeyPatch) -> None:
    class TextOnly:
        def complete(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
            raise AssertionError("a model that takes no images is never asked about a picture")

    monkeypatch.setattr(llm, "provider_from_env", lambda *args, **kwargs: TextOnly())
    png = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    reply = invoke_reply("ctx_vision", "describe", json.dumps({"image_base64": png}))
    assert reply["ok"] and reply["result"]["code"] == "G6_E_VISION_NO_IMAGES", reply
    assert "takes no images" in json.dumps(reply["result"])
    # Restoring the old NO_IMAGES wording ("cannot send images to its model") makes this fail.
