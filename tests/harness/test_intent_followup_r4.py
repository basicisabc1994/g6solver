# SPDX-License-Identifier: Apache-2.0
"""Repair 4 of claude/wire-intent-fu (Codex review 4): a critic's stated verdict is read by one escape-aware scan
that never depends on the decoder succeeding, so anything that might be a stated verdict G6 cannot read makes the
reply unreadable. FakeLLM and synthetic inputs only."""
from __future__ import annotations

import json

import pytest

from g6.core import semantic
from g6.harness import completion_loop as loop

TOKEN = "review-intfu4-fixture"
DEEP = "[" * 6000 + "0" + "]" * 6000
PROTOCOL = {"verdict": "defect", "rule": "r", "why": "actual regression", "test": "", "error": "", "read": TOKEN}


@pytest.fixture(autouse=True)
def online_reader(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)


def _report(said):
    with semantic.request(llm=None):
        return loop.critic_report(said, marker=TOKEN)


# --- A: anything that might be a stated verdict G6 cannot read ---------------------------------------------------------

@pytest.mark.parametrize("obj", [
    '{"x":1,"x":2,}',
    '{"bad":oops,"x":1,"x":2}',
    '{"x":1,"x":2,"bad":]}',
    '{"\\u0078":1,"x":2}',
    '{"verdict":"defect","why":"a"]}',
    '{"verdict":"defect","nested":' + DEEP + "}",
    '{"nested":' + DEEP + ',"\\u0076erdict":"defect"}',
    '{"verdict":"defect",}',
    '[{"verdict":"defect"',
    'the "verdict" is in prose',
    '{note "verdict" here}',
    '{"why": "x"',
], ids=["repeat_trailing_comma", "repeat_invalid_value", "repeat_stray_closer", "repeat_escaped", "verdict_mismatched",
        "verdict_deep", "verdict_escaped_deep", "verdict_trailing_comma", "verdict_unclosed_in_array",
        "verdict_in_prose", "verdict_in_prose_braces", "never_closed"])
def test_a_verdict_g6_cannot_read_makes_the_reply_unreadable(obj):
    got = _report(f"READ: {TOKEN}\n{obj}\nVERDICT: none\n")
    assert got["verdict"] == "unreadable", got


def test_disagreeing_stated_verdicts_are_unreadable():
    said = f"READ: {TOKEN}\n{json.dumps(PROTOCOL)}\nVERDICT: none\n"
    assert _report(said)["verdict"] == "unreadable"


def test_a_verdict_inside_a_code_block_never_counts():
    said = (f"READ: {TOKEN}\nVERDICT: defect\nWHY: actual regression\n"
            '```python\nexpected = {"verdict": "none", "x": 1, "x": 2\n```\n'
            '    {"verdict": "none"\n')
    assert _report(said)["verdict"] == "defect"


# --- controls -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prose", ["the set {1, 2}", "it returns {'a': 1}", '{"a": ' + DEEP + "}", "a dict {}",
                                   'a list [1, 2) and {x]', '{"why": "the \\"verdict\\" key"}'])
def test_control_prose_and_objects_naming_no_verdict_stay_prose(prose):
    assert _report(f"READ: {TOKEN}\nVERDICT: defect\nWHY: {prose}\n")["verdict"] == "defect"


@pytest.mark.parametrize("layout", ["alone", "mid_line", "json_fence_in_quote"])
def test_control_a_stated_protocol_object_is_final(layout):
    obj = json.dumps(PROTOCOL)
    said = {"alone": f"{obj}\n", "mid_line": f"Result: {obj} (end)\n",
            "json_fence_in_quote": f"> ```json\n> {obj}\n> ```\n"}[layout]
    got = _report(said)
    assert (got["verdict"], got["why"]) == ("defect", "actual regression"), got
