# SPDX-License-Identifier: Apache-2.0
"""The self-correction loop's stop rule as a small state model, for G6's model checker (PLAN.md, 2026-10-30).

The result is about this model only, never about a coding agent or a patch. It answers one design question: under which
rule can a run say it is done while a shape of input it never tried, or a counterexample, is still open?
"""
from __future__ import annotations

from typing import Any

from g6.model_checking import symbolic

DEPTH, SHAPES = 20, 4
VARIABLES = {"untried": "Int", "open": "Int", "stale": "Bool", "done": "Bool"}
INIT = f"(and (= untried {SHAPES}) (= open 0) (not stale) (not done))"
INVARIANTS = {"honest_stop": "(=> done (and (= untried 0) (= open 0) (not stale)))"}
#: Always true of the model; prove_invariant needs them to carry the induction.
BOUNDS = "(and (>= untried 0) (>= open 0))"


def _step(guard: str, *updates: str, keep: tuple[str, ...]) -> str:
    return f"(and {guard} (not done) {' '.join(updates)} {' '.join(f'(= next_{v} {v})' for v in keep)})"


TRY_PASSES = _step("(> untried 0)", "(= next_untried (- untried 1))", keep=("open", "stale", "done"))
TRY_FAILS = _step("(> untried 0)", "(= next_untried (- untried 1))", "(= next_open (+ open 1))", keep=("stale", "done"))
REPAIR = _step("(> open 0)", "(= next_open (- open 1))", "next_stale", keep=("untried", "done"))
#: A repair can change what an earlier shape does, so a rerun can reopen one.
RERUN_ALL = _step("stale", "(not next_stale)", "(>= next_open open)", "(<= next_open (+ open 1))", keep=("untried", "done"))
IDLE = "(and done (= next_untried untried) (= next_open open) (= next_stale stale) next_done)"


def _stop(guard: str) -> str:
    return _step(guard, "next_done", keep=("untried", "open", "stale"))


def _model(*steps: str) -> dict[str, Any]:
    return {"variables": VARIABLES, "init": INIT, "transition": f"(or {' '.join(steps)} {IDLE})", "invariants": INVARIANTS}


#: Arm A and arm E: the run stops once nothing it tried is failing.
OLD_RULE = _model(TRY_PASSES, TRY_FAILS, REPAIR, RERUN_ALL, _stop("(= open 0)"))
#: Every listed shape tried, nothing open, and every result taken after the last repair.
NEW_RULE = _model(TRY_PASSES, TRY_FAILS, REPAIR, RERUN_ALL, _stop("(and (= untried 0) (= open 0) (not stale))"))
#: The same stop, with the repair's rerun forgotten.
NEW_RULE_NO_RERUN = _model(TRY_PASSES, TRY_FAILS, REPAIR, _stop("(and (= untried 0) (= open 0))"))


def check(model: dict[str, Any]) -> dict[str, Any]:
    """``verdict``: violated, with the trace and what broke, or what induction shows about the model."""
    found = symbolic.check_invariant(model, depth=DEPTH)
    if found.verdict == "violated":
        return {"verdict": "violated", "violated": found.violated, "trace": [s["state"] for s in found.trace]}
    proved = symbolic.prove_invariant({**model, "invariants": {**INVARIANTS, "bounds": BOUNDS}}, depth=DEPTH)
    return {"verdict": "holds_by_induction" if proved.verdict == "k_inductive" else proved.verdict, "k": proved.k}


def finishes(model: dict[str, Any]) -> bool:
    """Whether the model can reach a stop at all: a rule nothing can satisfy would pass ``check`` too."""
    return symbolic.reachable(model, "done", depth=DEPTH).verdict == "reached"
