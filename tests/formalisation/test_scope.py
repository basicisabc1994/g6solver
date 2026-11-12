# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Check 5: what the statement covers is bound to its bytes, its rendering, its adapter and the standard's revision."""
from __future__ import annotations

import dataclasses

import pytest

from g6.harness.formalisation.adapters import from_expectation
from g6.harness.formalisation.render import render
from g6.harness.formalisation.scope import ScopeBinding, bind_scope, check_scope
from g6.harness.confirmed_expectation import ExpectationStandard
from tests.formalisation._cases import confirmed, relation

FIRST = confirmed([3], {"A": {"returns": 4}, "B": {"returns": 5}})
STANDARD = ExpectationStandard((FIRST,))
ONE = relation("(= result (+ x 1))", raises_when="(< x 0)", error="ValueError")
CASES = (from_expectation(FIRST),)


def bound():
    return bind_scope(ONE, render(ONE), STANDARD, CASES)


def test_binding_names_every_part_and_passes_unchanged():
    binding = bound()
    assert binding.statement_sha256 == ONE.sha256 and binding.definitions_sha256 == ONE.definitions_sha256
    assert binding.assumptions_sha256 == ONE.assumptions_sha256 and binding.adapter == ONE.adapter
    assert binding.rendering_sha256 == render(ONE).sha256
    assert (binding.standard_sha256, binding.standard_revision) == (STANDARD.sha256, 1)
    assert "shop.f" in binding.covers and "returns" in binding.covers
    found = check_scope(binding, ONE, render(ONE), STANDARD)
    assert (found.check, found.status, found.reasons) == ("scope", "passed", ())


@pytest.mark.parametrize("part, change", [
    ("statement", {"text": "(= result (+ x 2))"}),
    ("definitions", {"definitions": (("raises_when", "(< x 1)"),)}),
    ("assumptions", {"domain": ("(>= x 0)", "(<= x 11)")}),
    ("assumptions", {"assumptions": ("(> x 2)",)}),
    ("adapter", {"adapter": "smt.return.int_bool.v2"}),
])
def test_changed_byte_breaks_binding(part, change):
    binding = bound()
    changed = dataclasses.replace(ONE, **change)
    found = check_scope(binding, changed, render(ONE), STANDARD)
    assert found.status == "failed" and any(why.startswith(f"{part}:") for why in found.reasons)


def test_changed_rendering_breaks_binding():
    binding = bound()
    shown = render(ONE)
    for change in ({"words": shown.words + " "}, {"formal_text": "(= result (+ x 2))"}, {"renderer": "smt.words.v0"}):
        found = check_scope(binding, ONE, dataclasses.replace(shown, **change), STANDARD)
        assert found.status == "failed" and any(why.startswith("rendering:") for why in found.reasons)
    # a rendering whose digest was bound but is not what the renderer gives for this statement
    forged = dataclasses.replace(shown, words=shown.words.replace("plus 1", "plus 2"))
    binding = dataclasses.replace(binding, rendering_sha256=forged.sha256)
    found = check_scope(binding, ONE, forged, STANDARD)
    assert found.status == "failed" and any("not what the renderer gives" in why for why in found.reasons)


def test_superseded_standard_invalidates():
    binding = bound()
    contradicting = confirmed([3], {"A": {"returns": 4}, "B": {"returns": 5}}, chosen="B")
    newer = STANDARD.superseding(contradicting)
    assert newer.supersedes == STANDARD.sha256
    found = check_scope(binding, ONE, render(ONE), newer)
    assert found.status == "failed" and any(why.startswith("standard:") for why in found.reasons)
    # a later revision that only adds an agreeing answer is another standard too: the binding is made again
    added = STANDARD.with_expectation(confirmed([5], {"A": {"returns": 6}, "B": {"returns": 7}}))
    assert check_scope(binding, ONE, render(ONE), added).status == "failed"


def test_not_covered_always_listed():
    effect = confirmed([4], {"A": {"effect": {"argument": 0, "equals": 1}}, "B": {"returns": 5}})
    standard = STANDARD.with_expectation(effect)
    cases = tuple(from_expectation(one) for one in standard.expectations)
    binding = bind_scope(ONE, render(ONE), standard, cases)
    assert any(effect.expectation_id in line and "effect" in line for line in binding.not_covered)
    assert any("effects on arguments" in line for line in binding.not_covered)
    assert bound().not_covered and all(FIRST.expectation_id not in line for line in bound().not_covered)


def test_an_example_from_outside_the_standard_fails():
    stranger = from_expectation(confirmed([9], {"A": {"returns": 10}, "B": {"returns": 11}}))
    with pytest.raises(ValueError, match="not one of the standard"):
        bind_scope(ONE, render(ONE), STANDARD, (stranger,))


def test_binding_digest_covers_every_field():
    binding = bound()
    for field in dataclasses.fields(ScopeBinding):
        value = getattr(binding, field.name)
        other = value + 1 if isinstance(value, int) else (*value, "x") if isinstance(value, tuple) else value + "x"
        assert dataclasses.replace(binding, **{field.name: other}).sha256 != binding.sha256