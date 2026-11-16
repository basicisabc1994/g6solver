# SPDX-License-Identifier: Apache-2.0
"""The kernel's bounded decode (``g6._agentic.decode``): model text and client text are decoded only within a size
and a nesting depth, and every decode error comes back as one refusal, never as an exception of its own."""
from __future__ import annotations

import pytest

from g6._agentic import decode


def test_a_value_within_the_bounds_decodes():
    assert decode.loads('{"a": [1, {"b": "[[["}]}', max_chars=100) == {"a": [1, {"b": "[[["}]}
    assert decode.extract('the grid: {"value": [0.5]} as asked', max_chars=100) == {"value": [0.5]}
    assert decode.loads("[" * 64 + "]" * 64, max_chars=1000) is not None


@pytest.mark.parametrize("text", [
    "[" * 65 + "]" * 65,
    "[" * 3000 + "]" * 3000,
    "[" * 20_000,
    '{"a": ' * 100 + "1" + "}" * 100,
])
def test_nesting_past_the_depth_is_refused_before_it_is_decoded(text):
    with pytest.raises(decode.Undecodable, match="nest"):
        decode.loads(text, max_chars=100_000)
    with pytest.raises(decode.Undecodable, match="nest"):
        decode.extract("prose " + text, max_chars=100_000)


@pytest.mark.parametrize(("text", "why"), [
    ("x" * 101, "characters"),
    ("not json", "JSON"),
    ('{"a": 1', "JSON"),
    (None, "text"),
    (b"{}", "text"),
])
def test_every_other_decode_failure_is_one_refusal(text, why):
    with pytest.raises(decode.Undecodable, match=why):
        decode.loads(text, max_chars=100)


def test_a_recursion_error_from_the_decoder_itself_is_a_refusal(monkeypatch):
    def deep(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(decode.json, "loads", deep)
    with pytest.raises(decode.Undecodable, match="RecursionError"):
        decode.loads("[]", max_chars=100)
