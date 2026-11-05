# SPDX-License-Identifier: Apache-2.0
"""Read-only Complexity Intelligence Representation (CIR), v0.1.

This is an advisory structural read model, not an evaluator, executor, or approval service.
"""
from g6.complexity.ir import (
    CIR, ComplexityFinding, Dependency, Effect, FrozenIntent, Hypothesis, Obligation,
    Requirement, Snapshot, StateVariable,
)
from g6.complexity.atlas import build_atlas
from g6.complexity.impact import impact_cone
from g6.complexity.simplify import propose_simplifications

__all__ = [
    "CIR", "ComplexityFinding", "Dependency", "Effect", "FrozenIntent", "Hypothesis",
    "Obligation", "Requirement", "Snapshot", "StateVariable", "build_atlas",
    "impact_cone", "propose_simplifications",
]
