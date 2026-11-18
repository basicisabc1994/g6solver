# SPDX-License-Identifier: Apache-2.0
import importlib

from g6.core.llm import FakeLLM
from pydantic import BaseModel, ConfigDict


def test_measurement_records_changed_decisions_without_accuracy_claim():
    runner = importlib.import_module("validation.semantic.run")
    cases = [{"id": "a", "text": "Unpick the Byzantine generals puzzle"},
             {"id": "b", "text": "Build distributed consensus"}]
    llm = FakeLLM(default='{"value":"complex"}')
    result = runner.measure("effort", cases, llm=llm)
    assert result["cases"] == 2 and result["changed_decisions"] == 2
    assert result["agreement"] == 0 and len(llm.calls) == 2
    assert result["readings"][0]["floor_value"] == "simple"
    assert "accuracy" not in result


def test_measurement_no_model_and_invalid_reply_keep_floor():
    runner = importlib.import_module("validation.semantic.run")
    cases = [{"id": "a", "text": "delete production data"}]
    for llm in (None, FakeLLM(default='{"value":"outside"}')):
        result = runner.measure("risk", cases, llm=llm)
        assert result["agreement"] == 1 and result["changed_decisions"] == 0
        assert result["readings"][0]["value"] == "R3"
        assert result["readings"][0]["by"] == "floor"


def test_measurement_supports_a_declared_structured_reply_schema():
    runner = importlib.import_module("validation.semantic.run")

    class Intent(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        is_relation_question: bool

    class Reply(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        value: Intent

    result = runner.measure("query_intent", [{"text": "Show the ripples", "floor": {"is_relation_question": False}}],
                            llm=FakeLLM(default='{"value":{"is_relation_question":true}}'), schema=Reply)
    assert result["changed_decisions"] == 1 and result["model_readings"] == 1
