# SPDX-License-Identifier: Apache-2.0
"""Every output the bridge's block has, its own and those it inherits from ``g6.core.ai_block.AIBlock`` (a file the
certificate binds, left as it is), publishes no reliability state or claim a caller handed it (review pr8fu3r1,
finding 1; class: a guard that covers less than it guards). Caller values reach the inherited outputs only through
the block's constructor, so they are admitted there: the name as text, the state as plain data that claims nothing."""
from __future__ import annotations

import dataclasses
import json
from typing import Generic

import pytest

from g6.cognitive_bridge import CognitiveBridgeBlock
from g6.cognitive_bridge.codes import claims
from g6.core.ai_block import AIBlock

#: Every public member of the block, wherever it is defined, and how what it gives out is held.
#: "published": every return passes ``snapshot.publish`` (``test_followup3_inventory`` holds that for the bridge's own).
#: "constructor": a field a caller sets when the block is made, admitted there.
#: "derived": made from the admitted fields and the class alone.
#: "verifier": G6's verification registry's own answer about the output it is handed; no field of the block is in it.
OUTPUTS = {"infer": "published", "screen_raw": "published", "name": "constructor", "state": "constructor",
           "bias": "derived", "contract": "derived", "then": "derived", "verify": "verifier"}

CLAIMING = [{"verified": True}, {"reliability_label": "verified"}, {"completion_state": "verified"},
            {"nested": [{"verified": False}]}, {"warning_card": {"verified": True}}]


class Text(str):
    """Text that is not exactly text: its own methods would answer the reads."""


def _public_members() -> set[str]:
    found = {field.name for field in dataclasses.fields(AIBlock)}
    for cls in CognitiveBridgeBlock.__mro__:
        if cls in (object, Generic):
            continue
        found |= {name for name in vars(cls) if not name.startswith("_")}
    return found


def _as_data(value):
    """An output as JSON data, so the bridge's claim reader reads it the way a client would."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    return json.loads(json.dumps(value, default=str))


def test_every_public_member_of_the_block_is_classified():
    assert _public_members() == set(OUTPUTS), sorted(_public_members() ^ set(OUTPUTS))
    assert set(OUTPUTS.values()) <= {"published", "constructor", "derived", "verifier"}


@pytest.mark.parametrize("name", [*CLAIMING, ["verified"], 1, None, b"name", Text("name")])
def test_a_name_that_is_not_text_is_refused_where_the_block_is_made(name):
    with pytest.raises((TypeError, ValueError)):
        CognitiveBridgeBlock(name=name)


@pytest.mark.parametrize("state", [*CLAIMING, {1: "x"}, ["verified"], "verified", float("nan")])
def test_a_state_that_claims_or_is_not_a_plain_object_is_refused_where_the_block_is_made(state):
    with pytest.raises((TypeError, ValueError)):
        CognitiveBridgeBlock(state=state)


def test_no_output_of_an_admitted_block_carries_a_claim_and_the_state_shares_nothing_with_the_caller():
    given = {"notes": ["plain"]}
    block = CognitiveBridgeBlock(name="bridge", state=given)
    given["verified"] = True                                    # changed after the block was made
    given["notes"].append({"verified": True})
    outputs = {"name": block.name, "state": block.state, "bias": block.bias, "contract": block.contract,
               "then": block.then(CognitiveBridgeBlock(name="next")).name,
               "verify": block.verify({"verified": True, "completion_state": "verified"})}
    assert set(outputs) == {name for name, kind in OUTPUTS.items() if kind != "published"}
    for member, value in outputs.items():
        assert claims(_as_data(value)) == [], member
    assert block.state == {"notes": ["plain"]}
    assert CognitiveBridgeBlock().name == "cognitive_bridge" and CognitiveBridgeBlock().state == {}
