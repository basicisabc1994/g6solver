# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""What of the earlier repository's autoformaliser is not here (design 15.4): asserted at the exposed boundary."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

import g6.harness.formalisation as package
from g6.harness.formalisation import FRAGMENTS, UNSUPPORTED, Unsupported, supported
from tests.formalisation._cases import relation

SOURCE = Path(package.__file__).parent


def test_no_curry_howard_route():
    """Archive defect fixture 4: applying assumptions P and Q without compatible types, and a bodyless P => Q, enter
    no supported evidence path: there is no such language, fragment, op or import to give them to."""
    assert not any("curry" in name.lower() or "howard" in name.lower() for name in dir(package))
    assert not any("curry" in fragment or entry["language"] == "curry_howard" for fragment, entry in FRAGMENTS.items())
    assert not list(SOURCE.glob("*curry*")) and not list(SOURCE.glob("*howard*"))
    for text in ("(P Q)", "(=> P Q)"):                       # the two invalid derivations, as statements
        for language in ("curry_howard", "lean4", "smt"):
            odd = dataclasses.replace(relation("true"), language=language, text=text,
                                      fragment="curry_howard.v1" if language == "curry_howard" else
                                      "smt.relation.int_bool.v1")
            with pytest.raises(Unsupported, match=UNSUPPORTED):
                supported(odd)


def test_no_z3_python_target():
    assert not any("python" in fragment for fragment in FRAGMENTS)
    from g6.harness.formalisation import prompts
    from g6.harness.formalisation.draft import draft_pair

    assert {language for language, _ in prompts.PROMPTS} == {"smt", "lean4"}
    with pytest.raises(Unsupported, match=UNSUPPORTED):
        draft_pair(None, language="z3_python", fragment="smt.relation.int_bool.v1", callers={})


@pytest.mark.parametrize("word", ["grounding_label", "strategy_insight", "trust_label", "deepseek", "litellm",
                                  "load_backend", "math_reasoning"])
def test_nothing_else_of_the_archive_is_here(word):
    for path in SOURCE.glob("*.py"):
        assert word not in path.read_text(encoding="utf-8").lower(), path.name


def test_the_four_prompts_are_the_port_and_carry_no_acceptance_word():
    from g6.harness.formalisation import prompts

    assert len(prompts.PROMPTS) == 4
    for text in prompts.PROMPTS.values():
        assert "{statement}" in text and "{context_block}" in text
        assert "verified" not in text.lower() and "proved" not in text.lower()