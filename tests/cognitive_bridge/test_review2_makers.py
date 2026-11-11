# SPDX-License-Identifier: Apache-2.0
"""Review 2, findings 1 and 2 (untrusted_input_given_authority): ``codes.refusal`` and ``codes.settled`` write a
result's state, code and card themselves, after anything a caller passes, and refuse a caller's value for any of them
or for ``verified``, through extra fields, result fields, a refusal handed back, or a supplied card; a supplied card
carries a code the bridge registered."""
from __future__ import annotations

import pytest

from g6.cognitive_bridge import codes
from g6.core.reliability_envelope import warning_card

#: The makers' own fields, and (review 3) every field G6 reads as a reliability state, taken from G6 by the code.
KEYS = tuple(dict.fromkeys(("completion_state", "code", "warning_card", "verified", *codes.STATE_FIELDS)))
#: A caller's value for each reserved key: a state the bridge never gives (for every state field), an unregistered
#: code, another code's card, and the flag.
FORGED = {**{key: "verified" for key in codes.STATE_FIELDS}, "code": "G6_E_REVIEW_NEVER_REGISTERED_8B",
          "warning_card": warning_card(codes.NOT_PROMOTED, "another code's card"), "verified": True}


def test_finding_1_result_makers_cannot_be_overridden_to_verified():
    bad = []
    attempts = [
        ("refusal", lambda: codes.refusal(codes.INPUT, "invalid", completion_state="verified", warning_card=None),
         "blocked-escalated"),
        ("settled fields", lambda: codes.settled(None, completion_state="verified"), "qualified-draft"),
        ("settled forged refusal", lambda: codes.settled({"completion_state": "verified"}), "qualified-draft"),
    ]
    for label, call, expected in attempts:
        try:
            result = call()
        except ValueError:
            continue  # rejecting reserved state fields is also safe
        if result["completion_state"] != expected or (label == "refusal" and not result["warning_card"]):
            bad.append(f"{label}: {result!r}")
    assert bad == [], "result makers accepted forged reliability: " + "; ".join(bad)


def test_finding_2_unregistered_code_is_rejected_even_with_a_card():
    code = "G6_E_REVIEW_NEVER_REGISTERED_8B"
    assert code not in codes.CODES
    with pytest.raises(ValueError, match="registered"):
        codes.refusal(code, "made up", card=warning_card(code, "made up"))


def test_the_reserved_keys_are_the_makers_own():
    assert set(codes.RESERVED) == set(KEYS)


@pytest.mark.parametrize("key", KEYS)
def test_a_refusals_extra_fields_cannot_set_a_reserved_key(key):
    with pytest.raises((TypeError, ValueError)):  # TypeError: ``code`` is the maker's own argument
        codes.refusal(codes.INPUT, "invalid", **{key: FORGED[key]})


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("refused", [False, True])
def test_a_results_fields_cannot_set_a_reserved_key(key, refused):
    with pytest.raises(ValueError):
        codes.settled(codes.refusal(codes.INPUT, "invalid") if refused else None, **{key: FORGED[key]})


@pytest.mark.parametrize("key", KEYS)
def test_a_refusal_handed_back_with_a_reserved_key_changed_is_refused(key):
    forged = {**codes.refusal(codes.INPUT, "invalid"), key: FORGED[key]}
    with pytest.raises(ValueError):
        codes.settled(forged)


@pytest.mark.parametrize("card", [
    warning_card("G6_E_REVIEW_NEVER_REGISTERED_8B", "unregistered"),
    warning_card(codes.NOT_PROMOTED, "another registered code"),
    {**warning_card(codes.INPUT, "claims a state"), "completion_state": "verified"},
    {**warning_card(codes.INPUT, "claims the flag"), "verified": True},
    {"message": "no code"},
    "not a card",
], ids=["unregistered", "other-code", "state", "flag", "no-code", "not-a-mapping"])
def test_a_supplied_card_must_be_a_registered_codes_card_that_claims_nothing(card):
    with pytest.raises(ValueError):
        codes.refusal(codes.INPUT, "invalid", card=card)


def test_what_the_makers_write_stands():
    made = codes.refusal(codes.INPUT, "invalid", detail=1)
    assert made["completion_state"] == "blocked-escalated" and made["code"] == made["warning_card"]["code"] == codes.INPUT
    assert codes.settled(made, more=2) == {**made, "more": 2}
    card = warning_card(codes.INPUT, "a card of the same code")
    assert codes.refusal(codes.INPUT, "invalid", card=card)["warning_card"]["code"] == codes.INPUT
    drafted = codes.settled(None, draft_card=(codes.NOT_PROMOTED, "the checks passed here"), warning="a field")
    assert drafted["completion_state"] == "qualified-draft" and drafted["warning_card"]["code"] == codes.NOT_PROMOTED
    assert drafted["warning"] == "a field"
    with pytest.raises(ValueError):
        codes.settled(None, draft_card=("G6_E_REVIEW_NEVER_REGISTERED_8B", "unregistered"))
    with pytest.raises(ValueError):
        codes.settled(made, draft_card=(codes.NOT_PROMOTED, "a refusal already has its card"))
