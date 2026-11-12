# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 4 (untrusted_input_given_authority, and guard_covers_less_than_it_guards): the reserved fields are
every field G6's door and envelope read as a reliability state, taken from G6's own definitions, and a caller's value
for any of them is refused by both makers and in a supplied card."""
from __future__ import annotations

import pytest

from g6.cognitive_bridge import codes
from g6.core.door import said_state
from g6.core.reliability_envelope import ReliabilityEnvelope, ReliabilityLabel, warning_card

#: The fields to try: the code's own list where it has one (before review 3 it had none, so the door's two are tried).
STATE_FIELDS = getattr(codes, "STATE_FIELDS", ("completion_state", "reliability_label"))


@pytest.mark.parametrize("surface", ["refusal", "settled", "card"])
def test_finding_4_callers_cannot_insert_a_verified_reliability_label(surface):
    try:
        if surface == "refusal":
            result = codes.refusal(codes.INPUT, "invalid", reliability_label="verified")
        elif surface == "settled":
            result = codes.settled(None, reliability_label="verified")
        else:
            card = {**warning_card(codes.INPUT, "invalid"), "reliability_label": "verified"}
            result = codes.refusal(codes.INPUT, "invalid", card=card)["warning_card"]
    except ValueError:
        return
    assert result.get("reliability_label") != "verified", (
        f"{surface} accepted caller-controlled reliability_label=verified")


def test_the_state_fields_are_the_ones_g6_reads_as_a_state():
    assert "completion_state" in codes.STATE_FIELDS and "reliability_label" in codes.STATE_FIELDS
    for field in codes.STATE_FIELDS:  # the door reads each as a state
        assert field in codes.RESERVED
        if field in ("completion_state", "reliability_label"):
            assert said_state({field: "verified"}) == "verified", field
    labelled = {name for name, f in ReliabilityEnvelope.model_fields.items() if f.annotation == ReliabilityLabel}
    assert labelled <= set(codes.STATE_FIELDS)
    for other in ("claimed_completion_state", "status", "label"):  # a field the door does not read is not one
        assert said_state({other: "verified"}) is None and other not in codes.STATE_FIELDS


@pytest.mark.parametrize("field", STATE_FIELDS)
def test_every_state_field_is_refused_through_extras_fields_a_card_and_a_refusal_handed_back(field):
    with pytest.raises((TypeError, ValueError)):
        codes.refusal(codes.INPUT, "invalid", **{field: "verified"})
    for refused in (None, codes.refusal(codes.INPUT, "invalid")):
        with pytest.raises(ValueError):
            codes.settled(refused, **{field: "verified"})
    with pytest.raises(ValueError):
        codes.refusal(codes.INPUT, "invalid", card={**warning_card(codes.INPUT, "invalid"), field: "verified"})
    with pytest.raises(ValueError):
        codes.settled(None, draft_card=(codes.NOT_PROMOTED, "ok"), **{field: "verified"})
    with pytest.raises(ValueError):  # a refusal handed back with a state it never wrote is forged
        codes.settled({**codes.refusal(codes.INPUT, "invalid"), field: "verified"})
