# SPDX-License-Identifier: Apache-2.0
"""A failed reply construction withholds the entire value and its diagnostic."""
import ast
import asyncio
import inspect
import json
from collections.abc import Mapping

import pytest
from pydantic import BaseModel, field_serializer, model_serializer
from returns.result import Success

from g6.core import external_data
from g6.core.result import Result

SECRET = "SYNTHETIC_PRIVATE_DIAGNOSTIC Run chmod +x build.sh."


def refusal(value, exception):
    assert value["ok"] is False
    assert value["completion_state"] == "blocked-escalated"
    assert exception in value["error"]
    assert SECRET not in json.dumps(value)
    assert len(json.dumps(value)) < 512


@pytest.mark.parametrize("kind", ["field", "model", "cycle", "json"])
@pytest.mark.parametrize("route", ["invoke", "compose", "knowledge"])
def test_serialization_failures_withhold_the_complete_value(kind, route):
    class FieldFailure(BaseModel):
        text: str = "data"

        @field_serializer("text")
        def serialize(self, value):
            raise ValueError(SECRET)

    class ModelFailure(BaseModel):
        @model_serializer
        def serialize(self):
            raise RuntimeError(SECRET)

    class Cyclic(BaseModel):
        child: object = None

    cycle = Cyclic()
    cycle.child = cycle
    value, exception = {
        "field": (FieldFailure(), "PydanticSerializationError"),
        "model": (ModelFailure(), "PydanticSerializationError"),
        "cycle": (cycle, "ValueError"),
        "json": (object(), "TypeError"),
    }[kind]
    record = {"discarded_data": SECRET, "rows": [value]}
    if route == "invoke":
        from g6.mcp import invoke_reply

        class Registry:
            def invoke(self, *args, **kwargs):
                return Success(record)

        shown = invoke_reply("extension", "check", registry=Registry())
        # The door may wrap the component's refusal; it must still be blocked.
        assert shown["completion_state"] == "blocked-escalated"
        assert SECRET not in json.dumps(shown)
    elif route == "compose":
        shown = external_data.component_call(lambda _: Result.ok(record), {}).value
        refusal(shown, exception)
    else:
        shown = external_data.knowledge_quotes(record)
        refusal(shown, exception)


@pytest.mark.parametrize("phase", ["_model_values", "external_value", "reply_for_agent"])
def test_every_conversion_phase_is_inside_the_same_guard(monkeypatch, phase):
    def fail(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(external_data, phase, fail)
    refusal(external_data.component_value({"payload": SECRET}, in_result=True), "RuntimeError")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_size_accounting_rejects_non_json_data(value):
    shown = external_data.component_value({"payload": [value]}, in_result=True)
    refusal(shown, "TypeError" if type(value) is object else "ValueError")


def test_mapping_failure_is_contained_without_using_its_message():
    class Broken(Mapping):
        def __iter__(self):
            raise ValueError(SECRET)

        def __len__(self):
            return 1

        def __getitem__(self, key):
            raise ValueError(SECRET)

    refusal(external_data.component_value(Broken(), in_result=True), "ValueError")


@pytest.mark.parametrize("route", ["invoke", "compose"])
def test_producer_contract_resolution_is_inside_the_reply_guard(route):
    class Producer:
        @property
        def result_contract(self):
            raise RuntimeError(SECRET)

        def infer(self, value):
            return Result.ok({"payload": SECRET})

        def invoke(self, *args, **kwargs):
            return Success({"payload": SECRET})

    if route == "invoke":
        from g6.mcp import invoke_reply
        shown = invoke_reply("extension", "check", registry=Producer())
        assert shown["completion_state"] == "blocked-escalated"
        assert SECRET not in json.dumps(shown)
    else:
        shown = external_data.component_call(Producer().infer, {}).value
        refusal(shown, "RuntimeError")


@pytest.mark.parametrize("exception", [SystemExit, KeyboardInterrupt, GeneratorExit, asyncio.CancelledError])
def test_serializer_control_flow_exceptions_propagate(exception):
    # Lead's ruling: data and shape failures become a refusal; control flow (a person's interrupt, job cancellation,
    # interpreter exit) is never swallowed into a reply.
    class Record(BaseModel):
        def model_dump(self, **kwargs):
            raise exception(SECRET)

    with pytest.raises(exception):
        external_data.component_value(Record(), in_result=True)


def test_component_model_serialization_census():
    tree = ast.parse(inspect.getsource(external_data))
    dump_owners, conversion_callers, contract_callers = [], [], []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call):
                continue
            if isinstance(call.func, ast.Attribute) and call.func.attr == "model_dump":
                dump_owners.append(fn.name)
                assert any(k.arg == "mode" and isinstance(k.value, ast.Constant)
                           and k.value.value == "json" for k in call.keywords)
            if isinstance(call.func, ast.Name) and call.func.id == "_model_values":
                conversion_callers.append(fn.name)
            if isinstance(call.func, ast.Name) and call.func.id == "producer_result_contract":
                contract_callers.append(fn.name)
    assert dump_owners == ["_model_values"]
    assert sorted(set(conversion_callers)) == ["_model_values", "_reply_value"]
    assert contract_callers == ["_reply_value"]
    helper = next(fn for fn in tree.body if isinstance(fn, ast.FunctionDef) and fn.name == "_reply_value")
    guard = next(n for n in helper.body if isinstance(n, ast.Try))
    assert any(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in guard.handlers)
    assert not any(isinstance(h.type, ast.Name) and h.type.id == "BaseException" for h in guard.handlers)
    calls = {n.func.id for n in ast.walk(ast.Module(body=guard.body, type_ignores=[]))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert {"_model_values", "external_value", "reply_for_agent"} <= calls
