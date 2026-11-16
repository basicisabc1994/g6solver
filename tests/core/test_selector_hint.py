# SPDX-License-Identifier: Apache-2.0
"""``selector.hint``: the few words the prompt hook adds when a task starts (plan revision 8, A). Lexical, no model:
the top calls ``select(task=prompt, phase="plan")`` ranks, by name only, then the lead of G6's instructions. It never
repeats the prompt."""
from __future__ import annotations

import re

import pytest

from g6.core import selector
from g6.mcp.instructions import LEAD

PROMPTS = ["review my patch before I say it is done", "fix the date parser bug in utils.py",
           "add a caching layer to the API client", "why does this test keep failing",
           "check that the summary claims are supported by the sources"]


@pytest.mark.parametrize("prompt", PROMPTS)
def test_a_hint_is_short_names_g6_calls_and_ends_with_the_instructions_lead(prompt):
    said = selector.hint(prompt)
    assert 0 < len(said.split()) <= 60, said
    assert said.endswith(LEAD)
    named = re.findall(r"\b(?:g6|metaharness)_[a-z_]+\b", said.removesuffix(LEAD))
    top = [c["call"]["tool"] for c in selector.select(prompt, "plan")["candidates"] if "tool" in c["call"]][:2]
    assert named == top, (named, top)
    assert selector.hint(prompt) == said           # deterministic


def test_a_hint_never_repeats_the_prompt():
    marker = "zqxsecretcustomer"
    said = selector.hint(f"rename {marker} in the billing module and tell nobody about {marker}")
    assert said and marker not in said.lower() and "billing" not in said.lower() and "nobody" not in said.lower()
    fixed = set(re.findall(r"[a-z0-9_.]+", (LEAD + " " + selector.HINT_PREFIX).lower()))
    for prompt in PROMPTS:
        words = set(re.findall(r"[a-z0-9_.]+", selector.hint(prompt).lower()))
        tools = {w for w in words if w.startswith(("g6_", "metaharness_")) or "." in w}
        assert words - tools <= fixed, (prompt, words - tools - fixed)


def test_an_empty_prompt_has_no_hint():
    assert selector.hint("") == "" and selector.hint("   ") == ""


def test_the_hint_leaves_selects_reply_as_it_was():
    before = selector.select(PROMPTS[0], "plan", request_id="r", run_id="")
    selector.hint(PROMPTS[0])
    assert selector.select(PROMPTS[0], "plan", request_id="r", run_id="") == before
