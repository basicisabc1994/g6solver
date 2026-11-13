# SPDX-License-Identifier: Apache-2.0
"""Conservative bridge from textbook metadata into G6's typed design search.

Metadata may inform proposals, never establish a verified algorithm or code.
Only manually reviewed signatures can become composable primitives. Feature
assertions originate in the host's DesignProblem, not in an agent response.
"""
from __future__ import annotations

from dataclasses import dataclass

from g6.algorithm_design.ir import AlgorithmPrimitive, DesignProblem
from g6.algorithm_design.knowledge import bundled_ontology


@dataclass(frozen=True)
class CatalogInsight:
    algorithm_id: str
    problem_id: str
    status: str
    missing_features: tuple[str, ...]
    assumptions: tuple[str, ...]
    complexity_claim: str
    complexity_qualification: str
    cost_model: str
    proof_obligations: tuple[str, ...]
    source_refs: tuple[str, ...]
    implementation_status: str


@dataclass(frozen=True)
class CatalogPlan:
    insights: tuple[CatalogInsight, ...]
    typed_primitives: tuple[AlgorithmPrimitive, ...]
    agent_operations: tuple[str, ...]
    workflow_id: str


# Manually reviewed representation / output signatures. A signature is NOT a proof.
# No semantic equivalence is inferred from a matching keyword or source citation.
_REVIEWED: dict[str, tuple[str, str]] = {
    "interval_earliest_finish": ("intervals", "maximum_compatible_intervals"),
    "weighted_interval_dp": ("weighted_intervals", "maximum_weight_compatible_intervals"),
    "bfs": ("unweighted_graph", "shortest_paths"),
    "dijkstra": ("weighted_graph", "shortest_paths"),
    "bellman_ford": ("weighted_graph", "shortest_paths_or_negative_cycle"),
    "kahn": ("directed_graph", "topological_order_or_cycle"),
    "subset_sum_dp": ("nonnegative_integer_subset_sum", "subset_sum_decision"),
    "subset_sum_mitm": ("nonnegative_integer_subset_sum", "subset_sum_decision"),
    "knapsack_dp": ("nonnegative_integer_knapsack", "optimal_knapsack_value"),
    "merge_sort": ("sequence", "sorted_sequence"),
    "held_karp_tsp": ("complete_weighted_graph", "optimal_tsp_tour"),
}

# Existing G6 primitives have their own (potentially stronger) preconditions.
# Preserve those instead of silently replacing them from source metadata.
_EXISTING = {"bfs", "dijkstra", "knapsack_dp"}


def catalog_plan(problem: DesignProblem, *, problem_id: str | None) -> CatalogPlan:
    """Return inspectable options and independently gated typed extensions.

    Absent an explicit host-selected problem family, leave existing search alone.
    Missing recognition facts, representation mismatches, and unreviewed entries
    are advisory only. No LLM can extend the _REVIEWED execution whitelist.
    """
    if problem_id is None:
        return CatalogPlan((), (), (), "")
    ontology = bundled_ontology()
    family_ids = {p["id"] for p in ontology["problems"]}
    if problem_id not in family_ids:
        raise ValueError("unknown_catalog_problem_id")
    known = set(problem.established_properties)
    insights: list[CatalogInsight] = []
    typed: list[AlgorithmPrimitive] = []
    for row in ontology["algorithms"]:
        if problem_id not in row["problem_ids"]:
            continue
        name = str(row["id"])
        necessary = tuple(str(f) for f in row.get("recognition_rules", ()))
        missing = tuple(sorted(set(necessary) - known))
        signature = _REVIEWED.get(name)
        status = "unreviewed_type_bridge"
        if missing:
            status = "needs_preconditions"
        elif signature is not None and (signature[0] not in problem.representations
                                         or signature[1] != problem.target):
            status = "representation_or_objective_mismatch"
        elif signature is not None:
            status = "eligible_as_proposal"
        cx = row.get("complexity", {})
        source_refs = tuple(
            f"{ref.get('source_id', 'unknown')}:{ref.get('section', '?')}"
            for ref in row.get("source_refs", ()) if isinstance(ref, dict)
        )
        proof = tuple(str(x) for x in row.get("proof_obligations", ()))
        insights.append(CatalogInsight(
            name, problem_id, status, missing,
            tuple(str(x) for x in row.get("assumptions", ())),
            str(cx.get("time", "unknown")),
            str(cx.get("qualification", "not_established")),
            str(cx.get("model", "unknown")),
            proof, source_refs, str(row.get("implementation_status", "unknown")),
        ))
        if status == "eligible_as_proposal" and name not in _EXISTING:
            # Symbolic preconditions remain enforced by search_algorithms.
            consumes, produces = signature
            typed.append(AlgorithmPrimitive(
                name, consumes, produces, necessary,
                str(row.get("guarantee", "unspecified")) + " under stated assumptions",
                str(cx.get("time", "unknown")),
                tuple(sorted(set(proof) | {
                    "prove semantic compatibility of source/target representations",
                    "verify preconditions against actual inputs",
                    "validate claim in stated cost model and bit encoding",
                })),
                "+".join(str(x) for x in row.get("design_pattern_ids", ())),
                ",".join(source_refs) or "bundled_metadata",
            ))
    operators = tuple(str(o["id"]) for o in ontology.get("agent_operators", ())
                      if isinstance(o, dict) and isinstance(o.get("id"), str))
    workflows = ontology.get("workflows", ())
    workflow = str(workflows[0].get("id", "")) if workflows and isinstance(workflows[0], dict) else ""
    return CatalogPlan(tuple(insights), tuple(typed), operators, workflow)
