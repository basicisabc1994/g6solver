# SPDX-License-Identifier: Apache-2.0
"""Host-controlled algorithm invocation.

LLMs choose from catalog identifiers and declarative arguments, not Python code
to execute. An algorithm implementation is fixed and its own instance checker is
required to complete. Caller-supplied code is never admitted to this registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from g6.algorithm_design import verified_kernels as vk


_FUNCTIONS = {
    "bfs": vk.bfs,
    "dijkstra": vk.dijkstra,
    "bellman_ford": vk.bellman_ford,
    "kahn": vk.kahn,
    "weighted_interval_dp": vk.weighted_interval_dp,
    "vertex_cover_2approx": vk.vertex_cover_2approx,
    "vertex_cover_fpt": vk.vertex_cover_fpt,
    "gale_shapley": vk.gale_shapley,
    "edmonds_karp": vk.edmonds_karp,
    "kruskal": vk.kruskal,
    "tree_independent_set": vk.tree_independent_set,
    "treewidth_independent_set": vk.treewidth_independent_set,
    "set_cover_greedy": vk.set_cover_greedy,
    "subset_sum_mitm": vk.subset_sum_mitm,
    "knapsack_dp": vk.knapsack_dp,
    "sequence_align_dp": vk.sequence_align_dp,
    "huffman": vk.huffman,
    "held_karp_tsp": vk.held_karp_tsp,
    "randomized_quicksort": vk.randomized_quicksort,
}
# The three prior kernels remain supported by the original API. It is not
# safe to give arbitrary custom key Python callables to an unconfined agent.
_LEGACY = {"interval_earliest_finish", "subset_sum_dp", "merge_sort"}

@dataclass(frozen=True)
class KernelDescriptor:
    algorithm_id: str
    entrypoint: str
    verification_kind: str = "instance_certificate_or_independent_check"
    customization: str = "declarative_parameters_only"
    trusted_code: bool = True


def list_kernels() -> tuple[KernelDescriptor, ...]:
    return tuple(KernelDescriptor(name, "g6.algorithm_design.invocation.invoke")
                 for name in sorted(set(_FUNCTIONS) | _LEGACY))


def invoke(algorithm_id: str, parameters: Mapping[str, Any]) -> vk.CertifiedResult:
    """Execute an allowlisted deterministic algorithm with declarative parameters.

    This is a trusted-library entrypoint, not a sandbox for generated code.
    Results expose the precise evidence type; a pass is not a formal universal
    proof. No filesystem writes or subprocesses are authorized.
    """
    if algorithm_id not in _FUNCTIONS:
        raise vk.ContractError("kernel unavailable in certified host registry")
    if not isinstance(parameters, Mapping) or any(not isinstance(k,str) for k in parameters):
        raise vk.ContractError("parameters must be a declarative mapping")
    try:
        result = _FUNCTIONS[algorithm_id](**dict(parameters))
    except TypeError as exc:
        raise vk.ContractError("parameters do not match the kernel contract") from exc
    if not isinstance(result, vk.CertifiedResult) or not result.verification:
        raise AssertionError("kernel produced no verification receipt")
    return result


def algorithm_purity_contract() -> tuple[str, ...]:
    return (
        "no arbitrary model-authored callables",
        "no kernel source mutation",
        "bounded input sizes and typed precondition checks",
        "instance verification is distinct from a proof about all inputs",
        "every result records algorithm, witness, complexity and evidence status",
    )
