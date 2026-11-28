# SPDX-License-Identifier: Apache-2.0
"""Caller text keeps G6's external receipt for replies; a validator admits it and stores plain str.

Lead ruling 2026-11-21 (rc-wrp): G6's validators accept G6's own receipt class on caller text and normalise it at
the validator boundary, so no receipt type reaches a stored candidate record.
"""
from __future__ import annotations

import pytest

from g6.core.agent_guidance import is_external
from g6.core.external_data import caller_echo, plain_text
from g6.harness import behaviour_question as bq

TASK = "Make total(prices) add the prices. A negative price is refused."
CANDIDATE = {"requirement_id": "R1", "function": "shop.prices.total", "args": [[2, -3], "text"],
             "outcomes": {"A": {"returns": -1}, "B": {"raises": "ValueError"}}, "cited": "A negative price is refused.",
             "evidence": ["shop/prices.py: total sums the list with no check of the sign"],
             "rule": "total raises ValueError for every list that holds a negative price"}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _strings(child)


def test_a_caller_candidate_that_crossed_the_ingress_is_admitted_and_stored_as_plain_text():
    echoed = caller_echo(CANDIDATE)
    assert is_external(echoed["requirement_id"]) and is_external(echoed["cited"])
    made, = bq.caller_candidates(TASK, [echoed])
    assert type(made.data) is str
    record = made.to_dict()
    assert record["source"] == "caller"                      # the provenance still says the caller wrote it
    assert record["requirement_id"] == "R1" and record["cited"] == CANDIDATE["cited"]
    assert all(type(text) is str for text in _strings(record))
    assert made.data == bq.caller_candidates(TASK, [CANDIDATE])[0].data


def test_plain_text_copies_only_g6_receipts():
    class Other(str):
        pass

    other = Other("R1")
    shown = plain_text({"a": [caller_echo("x"), (caller_echo("y"),)], "b": other})
    assert type(shown["a"][0]) is str and type(shown["a"][1][0]) is str
    assert shown["b"] is other


def test_a_str_subclass_that_is_not_g6s_receipt_is_still_refused():
    class Other(str):
        pass

    with pytest.raises(bq.CandidateRefused, match="requirement_id is text"):
        bq.caller_candidates(TASK, [{**CANDIDATE, "requirement_id": Other("R1")}])
