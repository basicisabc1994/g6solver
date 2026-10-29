# SPDX-License-Identifier: Apache-2.0
"""Small, inspectable catalog of classical algorithm patterns, not auto-certified implementations.

Technique summaries reflect standard algorithm-design motifs in Kleinberg/Tardos and Skiena.
G6 never treats a catalog entry as evidence that generated code is correct.
"""
from __future__ import annotations

from g6.algorithm_design.ir import AlgorithmPrimitive


def primitives() -> tuple[AlgorithmPrimitive, ...]:
    P = AlgorithmPrimitive
    return (
        P("linear_scan", "sequence", "membership", (), "item found iff present",
          "O(n) comparisons", ("loop invariant", "exhaustive miss condition"), "brute_force"),
        P("sort_comparison", "sequence", "sorted_sequence", (),
          "sorted permutation of input", "O(n log n) comparisons",
          ("permutation preserved", "output sorted", "comparison total order"), "divide_and_conquer"),
        P("binary_search", "sorted_sequence", "membership", ("total_order",),
          "item found iff present", "O(log n) comparisons",
          ("sortedness precondition", "interval invariant", "boundary handling"), "decrease_and_conquer"),
        P("bfs", "unweighted_graph", "shortest_paths", (),
          "shortest edge-count paths from source", "O(V+E)",
          ("queue layer invariant", "unreachable handling", "graph representation"), "graph_traversal"),
        P("dijkstra", "weighted_graph", "shortest_paths", ("nonnegative_weights",),
          "shortest paths from source", "O((V+E) log V) with a binary heap",
          ("no negative edges", "relaxation invariant", "unreachable vertices"), "greedy"),
        P("topological_sort", "dag", "topological_order", (),
          "valid order of every directed edge", "O(V+E)",
          ("acyclicity checked", "every vertex emitted"), "graph_traversal"),
        P("dag_shortest_paths", "dag_weighted_graph", "shortest_paths", ("acyclic",),
          "single-source shortest paths in a DAG", "O(V+E)",
          ("valid topological order", "relax every outgoing edge"), "dynamic_programming"),
        P("interval_scheduling", "intervals", "maximum_compatible_intervals", ("finish_times_orderable",),
          "maximum number of nonoverlapping intervals", "O(n log n) including sorting",
          ("compatible endpoints specified", "exchange argument", "tie handling"), "greedy"),
        P("knapsack_dp", "nonnegative_integer_knapsack", "optimal_knapsack_value",
          ("nonnegative_integer_capacity",),
          "optimal value for integer-capacity 0/1 knapsack", "O(nW) pseudo-polynomial",
          ("recurrence", "base cases", "reconstruction", "capacity encoding bit-length"), "dynamic_programming"),
    )
