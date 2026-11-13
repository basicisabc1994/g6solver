# SPDX-License-Identifier: Apache-2.0
"""Host-controlled algorithm invocation: the one path to all 22 of G6's fixed kernels.

A caller (a person, an agent, a model over MCP) chooses a kernel by its identifier and gives it plain data, never
Python code: a callable, an object or anything nested deeper than ``MAX_DEPTH`` is refused before any kernel runs. Each
kernel is fixed code that checks its own answer (an instance certificate, an independent bounded oracle or an output
property) and says how in ``verification``; that check covers this one answer, not every input. Nothing here writes a
file, starts a process or reaches the network.
"""
from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from g6.algorithm_design import checked_kernels as ck
from g6.algorithm_design import kernels

#: How deep lists and mappings may nest in a kernel's parameters.
MAX_DEPTH = 8


#: The longest one kernel call may run, in seconds; past it the call is stopped and refused (``KernelUnavailable``).
MAX_SECONDS = 10.0


def _legacy(fn: Callable[..., kernels.KernelResult], complexity: str,
            allowed: frozenset[str]) -> Callable[..., ck.CheckedResult]:
    """One of the first three kernels (``kernels``), answered as a ``CheckedResult`` like the others. Only its input
    parameters (``allowed``) reach it: its own bounds (``max_items``, ``max_target``, ``independent_oracle_n``) are
    G6's, never a caller's."""
    def run(**parameters: Any) -> ck.CheckedResult:
        extra = sorted(set(parameters) - allowed)
        if extra:
            raise ck.ContractError(f"parameters do not match the kernel contract: {', '.join(extra)[:200]} "
                                   f"(it takes {', '.join(sorted(allowed))})")
        got = fn(**parameters)
        return ck.CheckedResult(got.algorithm_id, got.output, got.witness, got.verification, complexity,
                                tuple(got.limitations))
    return run


class _Deadline:
    """A trace function that stops the traced kernel once ``seconds`` have passed: every kernel is pure Python, so the
    check runs inside its own loops. The previous trace function (a debugger's, a coverage tool's) comes back after."""

    def __init__(self, seconds: float) -> None:
        self.seconds, self.until, self.ticks = seconds, time.monotonic() + seconds, 0

    def __call__(self, frame: Any, event: str, arg: Any) -> Any:
        return self.local

    def local(self, frame: Any, event: str, arg: Any) -> Any:
        self.ticks += 1
        if not self.ticks & 0x3FF and time.monotonic() > self.until:
            raise kernels.KernelUnavailable(f"the kernel ran past G6's bound of {self.seconds:g} seconds; give it a "
                                            "smaller input")
        return self.local


def _bounded(fn: Callable[..., ck.CheckedResult], parameters: Mapping[str, Any]) -> ck.CheckedResult:
    previous = sys.gettrace()
    sys.settrace(_Deadline(MAX_SECONDS))
    try:
        return fn(**dict(parameters))
    finally:
        sys.settrace(previous)


_FUNCTIONS: dict[str, Callable[..., ck.CheckedResult]] = {
    "bfs": ck.bfs,
    "dijkstra": ck.dijkstra,
    "bellman_ford": ck.bellman_ford,
    "kahn": ck.kahn,
    "weighted_interval_dp": ck.weighted_interval_dp,
    "vertex_cover_2approx": ck.vertex_cover_2approx,
    "vertex_cover_fpt": ck.vertex_cover_fpt,
    "gale_shapley": ck.gale_shapley,
    "edmonds_karp": ck.edmonds_karp,
    "kruskal": ck.kruskal,
    "tree_independent_set": ck.tree_independent_set,
    "treewidth_independent_set": ck.treewidth_independent_set,
    "set_cover_greedy": ck.set_cover_greedy,
    "subset_sum_mitm": ck.subset_sum_mitm,
    "knapsack_dp": ck.knapsack_dp,
    "sequence_align_dp": ck.sequence_align_dp,
    "huffman": ck.huffman,
    "held_karp_tsp": ck.held_karp_tsp,
    "randomized_quicksort": ck.randomized_quicksort,
    "interval_earliest_finish": _legacy(kernels.interval_earliest_finish, "O(n log n)", frozenset({"intervals"})),
    "subset_sum_dp": _legacy(kernels.subset_sum_dp, "O(nW) pseudo-polynomial", frozenset({"weights", "target"})),
    "merge_sort": _legacy(kernels.merge_sort, "O(n log n) comparisons", frozenset({"items", "reverse"})),
}


@dataclass(frozen=True)
class KernelDescriptor:
    algorithm_id: str
    entrypoint: str
    verification_kind: str = "instance_certificate_or_independent_check"
    customization: str = "declarative_parameters_only"
    trusted_code: bool = True


def list_kernels() -> tuple[KernelDescriptor, ...]:
    """Every kernel, by identifier, each through :func:`invoke`."""
    return tuple(KernelDescriptor(name, "g6.algorithm_design.invocation.invoke") for name in sorted(_FUNCTIONS))


def _plain(value: Any, depth: int = 0) -> bool:
    if depth > MAX_DEPTH:
        return False
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_plain(item, depth + 1) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _plain(item, depth + 1) for key, item in value.items())
    return False


def invoke(algorithm_id: str, parameters: Mapping[str, Any]) -> ck.CheckedResult:
    """Run the fixed kernel ``algorithm_id`` on ``parameters``, named by its signature.

    ``kernels.KernelUnavailable`` (a ``ContractError``) for an identifier that names no kernel, or input past a kernel's
    bound; ``ContractError`` for parameters that are not plain data or do not match the kernel's contract. A result
    always says how its answer was checked; that is not a proof about every input.
    """
    if algorithm_id not in _FUNCTIONS:
        raise kernels.KernelUnavailable(f"{str(algorithm_id)[:100]!r} is not one of G6's fixed kernels")
    if not isinstance(parameters, Mapping) or not _plain(parameters):
        raise ck.ContractError("parameters must be declarative data")
    try:
        result = _bounded(_FUNCTIONS[algorithm_id], parameters)
    except TypeError as exc:
        raise ck.ContractError("parameters do not match the kernel contract") from exc
    if not isinstance(result, ck.CheckedResult) or not result.verification:
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


__all__ = ["MAX_DEPTH", "KernelDescriptor", "algorithm_purity_contract", "invoke", "list_kernels"]
