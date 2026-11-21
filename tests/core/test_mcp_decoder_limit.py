# SPDX-License-Identifier: Apache-2.0
"""A tool argument whose JSON is past the decoder's limits is refused with the tool's own input refusal, never raised."""
import asyncio

import pytest

from g6.mcp import build_server

DEEP = "[" * 5000 + "0" + "]" * 5000


def _call(name, **arguments):
    handler = asyncio.run(build_server().get_tools())[name].fn
    got = handler(**arguments)
    return asyncio.run(got) if asyncio.iscoroutine(got) else got


def _refused(got, code):
    assert got["ok"] is False
    assert got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == code, got.get("error")
    assert "exceeds decoder limits" in got["error"]


@pytest.mark.parametrize("where", ["constant", "input"])
def test_g6_compose_refuses_a_value_past_the_decoder_limits(where):
    stage = ('{"component": "grounding", "op": "check_claims", "params": {"text": "The limit is 42 units.", '
             '"sources": ' + DEEP + '}}') if where == "constant" else '{"component": "grounding", "op": "check_claims"}'
    inputs = '{}' if where == "constant" else '{"text": "The limit is 42 units.", "sources": ' + DEEP + '}'
    _refused(_call("g6_compose", composition_json='{"stages": [' + stage + '], "inputs": ' + inputs + '}'),
             "G6_E_INPUT_VALIDATION")


def test_g6_invoke_refuses_params_past_the_decoder_limits():
    _refused(_call("g6_invoke", component="grounding", op="check_claims",
                   params_json='{"text": "x", "sources": ' + DEEP + '}'), "G6_E_INPUT_VALIDATION")


def test_metaharness_start_refuses_a_specification_past_the_decoder_limits():
    _refused(_call("metaharness_start", specification_json='{"name": ' + DEEP + '}'), "G6_E_INPUT_VALIDATION")


def test_metaharness_propose_refuses_a_profile_past_the_decoder_limits():
    # the meta-harness envelope, as its own JSON refusal there: no ``ok``, the text under ``message``
    got = _call("metaharness_propose", workflow="w", ctx=None, profile_json='{"p": ' + DEEP + '}')
    assert got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == "G6_E_META_SCHEMA", got.get("message")
    assert "exceeds decoder limits" in got["message"]
