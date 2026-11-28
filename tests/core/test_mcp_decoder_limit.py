# SPDX-License-Identifier: Apache-2.0
"""A tool argument whose JSON is past the decoder's limits is refused with the tool's own input refusal, never raised."""
import asyncio
import sys

import pytest

from g6.mcp import build_server

# Each value json.loads cannot read but whose text is not malformed: too deep (RecursionError), and an integer longer
# than the interpreter converts (a plain ValueError, not a JSONDecodeError), positive or negative. The depth is past
# CPython's C recursion limit on every platform: 5,000 levels decode on Linux, whose limit is higher than Windows's.
DIGITS = sys.get_int_max_str_digits() or 4300
DEPTH = 100_000
PAST = {"deep": "[" * DEPTH + "0" + "]" * DEPTH,
        "integer": "1" * (DIGITS + 1),
        "negative_integer": "-" + "9" * (DIGITS + 1)}
KINDS = pytest.mark.parametrize("kind", list(PAST))


def _call(name, **arguments):
    handler = asyncio.run(build_server().get_tools())[name].fn
    got = handler(**arguments)
    return asyncio.run(got) if asyncio.iscoroutine(got) else got


def _refused(got, code):
    assert got["ok"] is False
    assert got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == code, got.get("error")
    assert "exceeds decoder limits" in got["error"]


@KINDS
@pytest.mark.parametrize("where", ["constant", "input"])
def test_g6_compose_refuses_a_value_past_the_decoder_limits(where, kind):
    if kind != "deep" and not sys.get_int_max_str_digits():
        pytest.skip("this interpreter converts integers of any length")
    value = PAST[kind]
    stage = ('{"component": "grounding", "op": "check_claims", "params": {"text": "The limit is 42 units.", '
             '"sources": ' + value + '}}') if where == "constant" else '{"component": "grounding", "op": "check_claims"}'
    inputs = '{}' if where == "constant" else '{"text": "The limit is 42 units.", "sources": ' + value + '}'
    _refused(_call("g6_compose", composition_json='{"stages": [' + stage + '], "inputs": ' + inputs + '}'),
             "G6_E_INPUT_VALIDATION")


@KINDS
def test_g6_invoke_refuses_params_past_the_decoder_limits(kind):
    if kind != "deep" and not sys.get_int_max_str_digits():
        pytest.skip("this interpreter converts integers of any length")
    _refused(_call("g6_invoke", component="grounding", op="check_claims",
                   params_json='{"text": "x", "sources": ' + PAST[kind] + '}'), "G6_E_INPUT_VALIDATION")


@KINDS
def test_metaharness_start_refuses_a_specification_past_the_decoder_limits(kind):
    if kind != "deep" and not sys.get_int_max_str_digits():
        pytest.skip("this interpreter converts integers of any length")
    _refused(_call("metaharness_start", specification_json='{"name": ' + PAST[kind] + '}'), "G6_E_INPUT_VALIDATION")


@KINDS
def test_metaharness_propose_refuses_a_profile_past_the_decoder_limits(kind):
    if kind != "deep" and not sys.get_int_max_str_digits():
        pytest.skip("this interpreter converts integers of any length")
    # the meta-harness envelope, as its own JSON refusal there: no ``ok``, the text under ``message``
    got = _call("metaharness_propose", workflow="w", ctx=None, profile_json='{"p": ' + PAST[kind] + '}')
    assert got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == "G6_E_META_SCHEMA", got.get("message")
    assert "exceeds decoder limits" in got["message"]
