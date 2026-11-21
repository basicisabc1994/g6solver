# SPDX-License-Identifier: Apache-2.0
"""Marked reply text is for filtering what the agent is shown; it never changes what data a validator accepts.

Since 0b400900 text that entered from outside (a tool's arguments, a component's result) and text G6 marked as guidance
are private ``str`` subclasses. Where G6 takes such a value as data (a template instance a review or a question is built
from, a run's isolation record, the input a later stage of a composition is given) it holds plain ``str``; a ``str``
subclass from outside G6 is still refused where exact built-in types are required.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from g6.core import confine
from g6.core.ai_block import AIBlock
from g6.core.block_contract import BlockContract
from g6.core.compose import Stage, compose
from g6.core.external_data import caller_echo
from g6.core.guidance_builders import confine_not_started, confine_record, confine_summary
from g6.core.result import Result
from g6.harness import verification_templates as vt
from g6.workforce import repo_tests as rt

INSTANCE = {"id": "D1", "template": "differential.v1", "requirement_id": "R1", "intent": "change",
            "bindings": {"function": "pkg.add", "mode": "outputs", "calls": [{"args": ["a", "z"], "kwargs": {"k": "v"}}]}}


def _texts(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _texts(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _texts(child)
    elif isinstance(value, str):
        yield value


class Foreign(str):
    """A str subclass G6 did not make."""


def test_template_instances_take_caller_text_that_crossed_the_mcp_ingress_as_plain_data():
    [one] = vt.template_instances(caller_echo([INSTANCE]), {"R1"})
    assert one.bindings == INSTANCE["bindings"]
    assert all(type(text) is str for text in _texts(one.bindings))
    assert vt.instance_digest(one) == vt.instance_digest(vt.template_instances([INSTANCE], {"R1"})[0])


def test_template_instances_still_refuse_a_str_subclass_from_outside_g6():
    foreign = {**INSTANCE, "bindings": {**INSTANCE["bindings"], "calls": [{"args": [Foreign("a")]}]}}
    with pytest.raises(ValueError, match="bindings must be JSON data: a Foreign is not"):
        vt.template_instances([foreign], {"R1"})


@pytest.mark.parametrize("build", [
    lambda plan: confine_record(plan),
    lambda plan: confine_not_started(plan.record(), OSError("the child did not start")),
    lambda plan: confine_summary([plan.record()]),
], ids=["record", "not_started", "summary"])
def test_a_runs_isolation_record_is_plain_data_its_report_schema_accepts(build):
    record = build(confine.Plan("recorded-process", "no backend here; G6_ALLOW_UNCONFINED is not set"))
    assert all(type(text) is str for text in _texts(record)), record
    assert rt.REPORT_FIELDS["isolation"].accepts(record), record
    # the record is still filtered for the agent: no setting name G6 documents stays in it
    assert "G6_ALLOW_UNCONFINED" not in str(record), record


class _Text(BaseModel):
    op: str
    items: list


class Emit(AIBlock):
    __g6_block_contract__ = BlockContract(
        name="emit", inputs_required_per_op={"run": ("items",)}, outputs_per_op={"run": ("items",)},
        verification_method="unit_tests_only")

    def __init__(self):
        super().__init__("emit")

    def infer(self, data: _Text):
        return Result.ok({"items": ["from the first stage", {"key": "value"}], "completion_state": "qualified-draft"})


class Take(AIBlock):
    __g6_block_contract__ = BlockContract(
        name="take", inputs_required_per_op={"run": ("items",)}, outputs_per_op={"run": ("kinds",)},
        verification_method="unit_tests_only")

    def __init__(self):
        super().__init__("take")

    def infer(self, data: _Text):
        return Result.ok({"kinds": sorted({type(text).__name__ for text in _texts(data.items)}),
                          "completion_state": "qualified-draft"})


def test_a_later_stage_takes_an_earlier_stage_output_as_plain_data():
    flow = compose(Stage(Emit(), "run"), Stage(Take(), "run", inputs={"items": "0.items"}), caller_inputs=("items",))
    got = flow.infer({"items": []}).value
    assert got["result"]["kinds"] == ["str"], got
