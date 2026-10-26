# SPDX-License-Identifier: Apache-2.0
"""A block's optional ``screen_raw`` hook (``g6.core.registry.ComponentRegistry.invoke``): it sees the raw parameters
before typed validation, so a safety check can answer even when the input would be refused; a result it returns ends
the call; ``None`` lets the call go on to typed validation exactly as before; and a hook that raises refuses the call
with a stable code. The hook can never feed ``infer``: raw input reaches ``infer`` only through typed validation."""
from __future__ import annotations

import pytest

from g6.core.errors import SCREEN_RAW_FAILED
from g6.core.registry import ComponentRef, ComponentRegistry

DOUBLE = '''
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict
from g6.core.ai_block import AIBlock
from g6.core.result import Result

SEEN: list = []
INFERRED: list = []


class DoubleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["run", "ops", "help"] = "help"
    text: str = ""


class DoubleBlock(AIBlock[DoubleInput, dict, dict]):
    def __init__(self, name: str = "double", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def screen_raw(self, raw: dict[str, Any]) -> Result | None:
        SEEN.append(dict(raw))
        if raw.get("boom"):
            raise RuntimeError("the hook broke")
        if raw.get("stop"):
            return Result.ok({"stopped": True})
        if raw.get("refuse"):
            return Result.fail("[G6_E_DOUBLE_REFUSED] refused before validation")
        if raw.get("odd"):
            return "not a Result"
        return None

    def infer(self, data: DoubleInput) -> Result[dict]:
        INFERRED.append(data)
        return Result.ok({"text": data.text})


__g6_block__ = "DoubleBlock"
'''


@pytest.fixture
def double(tmp_path, monkeypatch):
    package = tmp_path / "screen_raw_double"
    package.mkdir()
    (package / "__init__.py").write_text(DOUBLE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = ComponentRegistry()
    registry._refs = {"double": ComponentRef("double", "screen_raw_double", "builtin")}
    module = registry.get_module("double")
    module.SEEN.clear()
    module.INFERRED.clear()
    return registry, module


def test_the_hook_sees_the_raw_parameters_unknown_keys_included(double) -> None:
    registry, module = double
    registry.invoke("double", "run", {"text": "hi", "unknown": [1, {"deep": True}]})
    assert module.SEEN == [{"text": "hi", "unknown": [1, {"deep": True}], "op": "run"}]


def test_a_result_from_the_hook_ends_the_call_and_infer_never_runs(double) -> None:
    # Removing the hook call in ComponentRegistry.invoke makes the unknown key "stop" an input error instead.
    registry, module = double
    found = registry.invoke("double", "run", {"stop": True})
    assert found.__class__.__name__ == "Success" and found.unwrap() == {"stopped": True}
    refused = registry.invoke("double", "run", {"refuse": True})
    assert refused.__class__.__name__ == "Failure" and str(refused.failure()).startswith("[G6_E_DOUBLE_REFUSED]")
    assert module.INFERRED == []


def test_none_goes_on_to_typed_validation_which_still_refuses_an_unknown_key(double) -> None:
    registry, module = double
    refused = registry.invoke("double", "run", {"text": "hi", "unknown": 1})
    assert str(refused.failure()).startswith("[G6_E_INPUT_VALIDATION]") and module.INFERRED == []
    ok = registry.invoke("double", "run", {"text": "hi"})
    assert ok.unwrap() == {"text": "hi"} and len(module.INFERRED) == 1


@pytest.mark.parametrize("params", [{"boom": True}, {"odd": True}])
def test_a_hook_that_raises_or_answers_wrongly_refuses_the_call_and_infer_never_runs(double, params) -> None:
    # G4: removing the except branch (or the Result check) around the hook would raise, or fall through to infer.
    registry, module = double
    refused = registry.invoke("double", "run", {"text": "hi", **params})
    assert refused.__class__.__name__ == "Failure" and str(refused.failure()).startswith(f"[{SCREEN_RAW_FAILED}]")
    assert module.INFERRED == []


MODEL_DOUBLE = '''
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict
from g6.core.ai_block import AIBlock
from g6.core.result import Result

SEEN: list = []
BUILT: list = []


class ModelDoubleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["run", "write", "ops", "help"] = "help"


class ModelDoubleBlock(AIBlock[ModelDoubleInput, dict, dict]):
    def __init__(self, name: str = "model_double", state: dict | None = None, caller: Any = None) -> None:
        super().__init__(name=name, state=state or {})
        self.caller = caller
        BUILT.append(caller)

    def screen_raw(self, raw: dict[str, Any]) -> Result | None:
        SEEN.append((raw.get("op"), self.caller))
        return None

    def infer(self, data: ModelDoubleInput) -> Result[dict]:
        return Result.ok({"caller": self.caller})


__g6_block__ = "ModelDoubleBlock"
__g6_model__ = "caller"
__g6_sensitive_ops__ = ("write",)
'''


def test_the_hook_runs_on_the_block_built_with_its_model_and_never_for_a_refused_sensitive_op(tmp_path,
                                                                                            monkeypatch) -> None:
    # The model door (llm=) merged with this hook: the block is built with its model, then the hook, then typed
    # validation. Building the block with cls() before the hook fails the first assertion; moving the block's
    # building or the hook above the sensitive-op refusal fails the last.
    package = tmp_path / "screen_raw_model_double"
    package.mkdir()
    (package / "__init__.py").write_text(MODEL_DOUBLE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    registry = ComponentRegistry()
    registry._refs = {"model_double": ComponentRef("model_double", "screen_raw_model_double", "builtin")}
    module = registry.get_module("model_double")
    found = registry.invoke("model_double", "run", {}, llm="the model")
    assert module.SEEN == [("run", "the model")] and found.unwrap() == {"caller": "the model"}
    refused = registry.invoke("model_double", "write", {}, block_sensitive=True, llm="the model")
    assert str(refused.failure()).startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]")
    assert module.BUILT == ["the model"] and module.SEEN == [("run", "the model")]
