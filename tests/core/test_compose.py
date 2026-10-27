# SPDX-License-Identifier: Apache-2.0
import pytest
from pydantic import BaseModel

from g6.core.ai_block import AIBlock
from g6.core.block_contract import BlockContract
from g6.core.result import Result
from g6.core.compose import Stage, compose, CompositionError


class Input(BaseModel):
    op: str
    number: int


class Increment(AIBlock):
    __g6_block_contract__ = BlockContract(
        name="increment", inputs_required_per_op={"run": ("number",)},
        outputs_per_op={"run": ("number",)}, verification_method="unit_tests_only")

    def __init__(self):
        super().__init__("increment")

    def infer(self, data: Input):
        return Result.ok({"number": data.number + 1, "completion_state": "verified",
                          "evidence": [{"kind": "assertion", "summary": "producer says correct"}]})


def test_explicit_composition_builds_typed_inputs_and_never_promotes_claims():
    flow = compose(Stage(Increment(), "run"),
                   Stage(Increment(), "run", inputs={"number": "0.number"}), caller_inputs=("number",))
    got = flow.infer({"number": "3"}).value
    assert got["result"]["number"] == 5
    assert got["completion_state"] == "qualified-draft"
    report = got["assurance"]
    assert report["status"] == "contract-checked"
    assert len(report["stages"]) == 2 and len(report["evidence"]) == 2
    assert all(e["authority"] == "producer-declared" for e in report["evidence"])
    assert report["digest"]


def test_downstream_fields_are_not_implicitly_wired():
    with pytest.raises(CompositionError, match="UNMET_INPUT"):
        compose(Stage(Increment(), "run"), Stage(Increment(), "run"), caller_inputs=("number",))


def test_default_contract_is_refused():
    with pytest.raises(CompositionError, match="NO_CONTRACT"):
        compose(Stage(AIBlock("uncontracted"), "run"), caller_inputs=())


def test_invalid_typed_input_stops_before_execution():
    flow = compose(Stage(Increment(), "run"), caller_inputs=("number",))
    got = flow.infer({"number": "not a number"}).value
    assert got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


def test_precondition_and_missing_output_fail_closed():
    class Guarded(Increment):
        __g6_block_contract__ = BlockContract(
            name="guarded", inputs_required=("number",), outputs=("absent",),
            preconditions=(lambda ctx: ctx["context"]["number"] > 0,), verification_method="unit_tests_only")

    flow = compose(Stage(Guarded(), "run"), caller_inputs=("number",))
    assert flow.infer({"number": -1}).value["warning_card"]["code"] == "G6_E_COMPOSITION_PRECONDITION_FAILED"
    assert flow.infer({"number": 1}).value["warning_card"]["code"] == "G6_E_COMPOSITION_OUTPUT_MISSING"


def test_incompatible_evidence_types_are_refused_before_running():
    class NeedsTests(Increment):
        __g6_block_contract__ = BlockContract(
            name="needs-tests", inputs_required=("number",), outputs=("number",),
            evidence_inputs_per_op={"run": {"number": "executed-tests"}}, verification_method="unit_tests_only")
    with pytest.raises(CompositionError, match="EVIDENCE_MISMATCH"):
        compose(Stage(Increment(), "run"), Stage(NeedsTests(), "run", inputs={"number": "0.number"}),
                caller_inputs=("number",))


def test_contract_change_changes_assurance_digest():
    before = compose(Stage(Increment(), "run"), caller_inputs=("number",)).infer({"number": 1}).value
    class Revised(Increment):
        __g6_block_contract__ = BlockContract(name="increment", inputs_required=("number",), outputs=("number",),
                                             version=2, verification_method="unit_tests_only")
    after = compose(Stage(Revised(), "run"), caller_inputs=("number",)).infer({"number": 1}).value
    assert before["assurance"]["digest"] != after["assurance"]["digest"]
