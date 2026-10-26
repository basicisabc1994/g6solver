# SPDX-License-Identifier: Apache-2.0
"""``prove_invariant`` says what the checker found, ``k_inductive`` with its ``k``, never that G6 proved anything.

Before, the verdict was ``proved`` and the card said Z3 "proved" the invariants. ``completion_state`` was and stays
``qualified-draft``. The old verdict is no longer one an engine gives, so a result built with it is blocked."""
from __future__ import annotations

import importlib.util

import pytest

from g6.model_checking import finish, prove_invariant
from g6.model_checking.envelope import K_INDUCTION, NOT_CHECKED

COUNTER = {"variables": {"x": "Int"}, "init": "(= x 0)", "transition": "(= next_x (+ x 1))",
           "invariants": {"natural": "(>= x 0)"}}


@pytest.mark.skipif(importlib.util.find_spec("z3") is None, reason="z3-solver is not installed (pip install 'g6[formal]')")
def test_an_inductive_invariant_is_reported_k_inductive_with_its_k() -> None:
    result = prove_invariant(COUNTER)
    assert result.verdict == "k_inductive" and result.k == 1 and result.completion_state == "qualified-draft"
    card = result.warning_card
    assert card["code"] == K_INDUCTION and "1-inductive" in card["message"]
    assert "proved" not in card["message"] and "proof" not in card["suggested_action"]
    assert result.evidence[0]["verdict"] == "k_inductive"


def test_the_old_proved_verdict_is_not_one_an_engine_gives() -> None:
    old = finish("prove_invariant", engine="z3", verdict="proved", k=1,
                 recheck={"passed": True, "checks": [{}], "failures": []})
    assert old.code == NOT_CHECKED and old.completion_state == "blocked-escalated"
    new = finish("prove_invariant", engine="z3", verdict="k_inductive", k=1,
                 recheck={"passed": True, "checks": [{}], "failures": []})
    assert new.code == "" and new.completion_state == "qualified-draft"  # survival
