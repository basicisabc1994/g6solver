# SPDX-License-Identifier: Apache-2.0
"""Conservative, evidence-aware complexity obligations for large codebases.

These are planning constraints, never proof that an architectural property holds.
A partial static graph cannot certify absence of cross-module dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from g6.complexity.ir import CIR


PRINCIPLES = (
    "formalize externally observable behavior and preserve frozen requirements before optimization",
    "separate essential domain complexity from accidental implementation complexity",
    "prefer information hiding, narrow module interfaces, and low coupling",
    "make dependencies, side effects, mutable state and ownership explicit",
    "minimize cognitive load with small cohesive units and locally checkable invariants",
    "avoid unnecessary shared state and premature abstractions",
    "track asymptotic time, memory, bit complexity and I/O costs under real workload dimensions",
    "prove assumptions and preserve semantics when decomposing, fusing or parallelizing algorithms",
    "bound the affected dependency cone and verify behavior beyond the edited function",
    "keep evaluation independent: frozen tests, trusted oracles, adversarial cases and honest uncertainty",
)


@dataclass(frozen=True)
class ArchitectureReview:
    principles: tuple[str, ...]
    obligations: tuple[str, ...]
    observed: tuple[str, ...]
    unknowns: tuple[str, ...]
    risk: str


def review_codebase(cir: CIR, *, impact: tuple[str, ...] = (),
                    large_file_threshold: int = 30, large_dependency_threshold: int = 50) -> ArchitectureReview:
    """Host-side bounded preflight; thresholds only prioritize attention, not claims."""
    if large_file_threshold < 1 or large_dependency_threshold < 1:
        raise ValueError("invalid complexity review thresholds")
    vector = cir.vector(impact)
    observed = (
        f"represented_components={len(cir.components)}",
        f"represented_dependency_edges={len(cir.dependencies)}",
        f"represented_mutable_state_owners={vector['mutable_state_owners_touched']}",
        f"represented_effect_edges={vector['may_read_write_effect_edges']}",
        f"reverse_impact_scope={len(impact)}",
    )
    unknowns = tuple(sorted(set(cir.unknowns) | {
        "no_complete_call_graph_or_sound_alias_analysis",
        "dynamic_dispatch_or_reflection_may_escape_static_scope",
        "unmeasured_performance_and_unverified_cost_model",
    }))
    large = len(cir.components) >= large_file_threshold or len(cir.dependencies) >= large_dependency_threshold
    obligations = (
        "freeze and compare observable behavior for the whole reverse-dependency impact scope",
        "review public API changes, state ownership and cross-component effects",
        "evaluate failure modes: cycles, concurrency, shared state and external I/O",
        "justify algorithmic and architectural complexity with measured resource budgets",
        "require independent verification of algorithm preconditions and produced outputs",
    )
    if large:
        obligations += (
            "partition change by bounded subsystems with explicit interface contracts",
            "plan staged integration and broaden regression testing across boundaries",
        )
    return ArchitectureReview(PRINCIPLES, obligations, observed, unknowns,
                              "requires_bounded_review" if large else "review_required")
