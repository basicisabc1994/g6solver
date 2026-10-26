# SPDX-License-Identifier: Apache-2.0
"""Reasoning: budgets, honest comparisons and voting for multi-call reasoning.

Reasoning: budgets, honest comparisons and voting for multi-call reasoning (audit 023 §11).

The archive's multi-call stepper is not ported: its gain over a raw call was never measured, and the
one recorded observation is negative. What is here is what any such experiment needs:

* ``Budget`` / ``MeteredCaller``: hard ceilings and a model caller that counts *every* call;
* ``compare``: a paired, compute-matched baseline gate (≥30 cases, exact sign test, equal calls);
* ``weighted_vote``: verified answers dominate; reports whether a real majority exists;
* ``DirectStep`` and ``self_consistency``: the single-call and n-call baselines a candidate must beat;
* ``first_json_object``: LaTeX-safe JSON extraction from model output.
"""
from g6.reasoning.budget import Budget, BudgetExhausted, MeteredCaller
# The function ``compare`` shadows the submodule of the same name on this package, so ``sign_test_p`` is exported here.
from g6.reasoning.compare import Arm, Case, Verdict, compare, sign_test_p
from g6.reasoning.steps import DirectStep, first_json_object, self_consistency
from g6.reasoning.structure import structure_map
from g6.reasoning.vote import Candidate, VoteResult, weighted_vote

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("confounded_gain", "false_analogy", "impression_not_measurement")
__g6_introduces__: tuple[str, ...] = ()

__g6_extras__ = {"graph": ("networkx",)}

__all__ = ["structure_map", "Arm", "Budget", "BudgetExhausted", "Candidate", "Case", "DirectStep", "MeteredCaller", "Verdict",
           "VoteResult", "compare", "first_json_object", "self_consistency", "sign_test_p", "weighted_vote"]
