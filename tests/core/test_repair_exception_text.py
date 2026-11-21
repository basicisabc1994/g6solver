# SPDX-License-Identifier: Apache-2.0
"""An exception's text rendered for a reply survives a raising ``__str__`` or ``__repr__``."""
import json

import pytest
from pydantic import BaseModel
from returns.result import Failure

from g6.core import external_data
from g6.core.ai_block import AIBlock
from g6.core.block_contract import BlockContract
from g6.core.result import Result


class Unprintable(Exception):
    def __str__(self):
        raise ValueError("synthetic unprintable")


class Unreadable(Unprintable):
    def __repr__(self):
        raise ValueError("synthetic unrepresentable")


class Odd(str):
    def __str__(self):
        raise ValueError("synthetic subclass text")


class OddText(Exception):
    def __str__(self):
        return Odd("synthetic odd text")


class FailureValue:
    def __str__(self):
        raise ValueError("synthetic unprintable failure")

    __repr__ = __str__


def _bounded(reply):
    shown, _, _ = external_data.detached(reply)
    assert '"blocked-escalated"' in json.dumps(shown), shown
    return shown


@pytest.mark.parametrize("error,want", [
    (ValueError("plain words"), "plain words"),
    (OddText(), "synthetic odd text"),
    (Unprintable(), "Unprintable()"),
])
def test_exception_text_reads_what_can_be_read(error, want):
    text = external_data.exception_text(error)
    assert text == want and type(text) is str


@pytest.mark.parametrize("error", [Unreadable(), FailureValue()])
def test_exception_text_never_raises(error):
    text = external_data.exception_text(error)
    assert type(text) is str and type(error).__name__ in text


def test_component_call_contains_an_unprintable_exception():
    def fn():
        raise Unreadable()

    outcome = external_data.component_call(fn)
    assert outcome.is_fail()


@pytest.mark.parametrize("raised", [Unprintable, Unreadable])
def test_invoke_exception_with_unprintable_text_is_a_bounded_refusal(raised):
    from g6.mcp import invoke_reply

    class Registry:
        def invoke(self, *args, **kwargs):
            raise raised()

    _bounded(invoke_reply("extension", "check", registry=Registry()))


def test_invoke_failure_with_unprintable_value_is_a_bounded_refusal():
    from g6.mcp import invoke_reply

    class Registry:
        def invoke(self, *args, **kwargs):
            return Failure(FailureValue())

    _bounded(invoke_reply("extension", "check", registry=Registry()))


def test_compose_check_with_unprintable_exception_is_a_bounded_refusal():
    from g6.core.compose import Stage, compose

    class Input(BaseModel):
        op: str
        number: int

    def precondition(context):
        raise Unreadable()

    class Guarded(AIBlock):
        __g6_block_contract__ = BlockContract(
            name="guarded", inputs_required=("number",), outputs=("number",),
            preconditions=(precondition,), verification_method="unit_tests_only")

        def __init__(self):
            super().__init__("guarded")

        def infer(self, data: Input):
            return Result.ok({"number": data.number})

    flow = compose(Stage(Guarded(), "run"), caller_inputs=("number",))
    _bounded(flow.infer({"number": 1}).value)


def test_adaptation_guard_with_unprintable_exception_is_unknown():
    from g6.core import adaptation_gate as gate

    controls = [gate.Control("bad", "negative", {"x": 1}, "reviewer"),
                gate.Control("good", "positive", {"x": 2}, "reviewer")]
    proposal = gate.AdaptationProposal(
        proposal_id="p1", proposer="proposer", source_case_ids=("c1",), target_component="guard",
        intended_change="flag x", predicted_benefit="fewer misses", falsifiers=("a miss",), test_plan="controls",
        candidate_hash="a" * 64, baseline_hash="b" * 64, transfer_hypothesis="same class", cost_cap=1.0,
        rollback="restore baseline")

    def guard(case):
        raise Unreadable()

    result = gate.validate_locally(proposal, guard, controls)
    assert {o["result"] for o in result.outcomes} == {"unknown"}
    assert all("Unreadable" in o["error"] for o in result.outcomes)
