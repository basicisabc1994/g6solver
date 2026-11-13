# SPDX-License-Identifier: Apache-2.0
"""Hard-coded, conservative resource and device planning for algorithm-design agents.

Estimates count abstract operations, not seconds. None of these estimates proves an
asymptotic theorem, verifies a cost model, or establishes that a proposed kernel runs
on a device. Device capabilities are supplied by the host, never by the coding agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from g6.core.ledger import digest


@dataclass(frozen=True)
class DeviceBudget:
    ram_bytes: int
    scratch_bytes: int
    max_operations: int
    cpu_cores: int = 1
    accelerator: str = "none"
    checked_accelerator_kernels: tuple[str, ...] = ()
    allow_pseudopolynomial: bool = True
    allow_exponential: bool = False
    allow_external_memory: bool = True

    def __post_init__(self) -> None:
        if self.ram_bytes <= 0 or self.scratch_bytes < 0 or self.max_operations <= 0 or self.cpu_cores < 1:
            raise ValueError("positive RAM/operation/CPU limits and nonnegative scratch are required")
        if self.accelerator not in ("none", "cuda", "metal", "rocm", "tpu"):
            raise ValueError("unknown accelerator; host must declare an explicit device")

    @property
    def fingerprint(self) -> str:
        return digest(self.__dict__)


@dataclass(frozen=True)
class ResourceEstimate:
    algorithm_id: str
    operations: int | None
    ram_bytes: int | None
    scratch_bytes: int | None
    complexity_kind: str
    device: str
    strategy: str
    eligible: bool
    reasons: tuple[str, ...]
    assumptions: tuple[str, ...] = ()

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.eligible else "blocked-escalated"


# All figures intentionally conservative approximations, bounded below.
# Numeric formulas use Python big integers; we reject absurd dimensions before evaluation.
_ALIASES = {
    "subset_sum_dp": "knapsack_dp",
    "merge_sort": "sort_comparison",
    "bfs": "bfs",
    "kahn": "topological_sort",
    "weighted_interval_dp": "weighted_interval_dp",
}
_PSEUDO = {"knapsack_dp"}
_EXPONENTIAL = {"subset_sum_mitm", "held_karp_tsp"}
_SIMPLE = {"linear_scan", "sort_comparison", "binary_search", "bfs", "dijkstra",
           "topological_sort", "dag_shortest_paths", "interval_scheduling",
           "knapsack_dp", "subset_sum_mitm", "weighted_interval_dp", "bellman_ford"}


def _read_int(dimensions: Mapping[str, int], name: str) -> int | None:
    value = dimensions.get(name)
    if type(value) is not int or value < 0 or value > 10**12:
        return None
    return value


def estimate(algorithm_id: str, dimensions: Mapping[str, int], device: DeviceBudget,
             *, exact: bool = True) -> ResourceEstimate:
    """Choose CPU/streaming/external-memory execution *plans*; never allocate hardware.

    Parametric/Pseudo-polynomial costs cannot be reported as uniform polynomial
    in binary input length. Unknown parameters or kernels fail closed.
    """
    name = _ALIASES.get(algorithm_id, algorithm_id)
    if name not in _SIMPLE:
        return ResourceEstimate(algorithm_id, None, None, None, "unknown", "cpu",
                                "unmodeled", False, ("missing_hard_coded_cost_model",))
    n, v, e, w = (_read_int(dimensions, x) for x in ("n", "V", "E", "W"))
    needed = ("V", "E") if name in ("bfs", "dijkstra", "topological_sort", "dag_shortest_paths",
                                  "bellman_ford") else (("n", "W") if name == "knapsack_dp" else ("n",))
    if any(_read_int(dimensions, k) is None for k in needed):
        return ResourceEstimate(algorithm_id, None, None, None, "unknown", "cpu",
                                "missing_parameters", False,
                                tuple("missing_or_invalid:" + k for k in needed
                                      if _read_int(dimensions, k) is None))
    n, v, e, w = n or 0, v or 0, e or 0, w or 0
    logn = max(1, n.bit_length())
    logv = max(1, v.bit_length())
    kind, strategy = "polynomial", "in_memory"
    scratch = 0
    if name == "linear_scan":
        ops, ram = max(1, n), 4096
        strategy = "streaming"
    elif name == "sort_comparison":
        ops, ram = max(1, n * logn), max(4096, 64 * n)
    elif name == "binary_search":
        ops, ram = logn, 4096
    elif name in ("bfs", "topological_sort", "dag_shortest_paths"):
        ops, ram = max(1, v + e), max(4096, 64 * v + 48 * e)
    elif name == "dijkstra":
        ops, ram = max(1, (v + e) * logv), max(4096, 96 * v + 48 * e)
    elif name == "bellman_ford":
        ops, ram = max(1, v * e), max(4096, 96 * v + 48 * e)
    elif name == "interval_scheduling":
        ops, ram = max(1, n * logn), max(4096, 64 * n)
    elif name == "weighted_interval_dp":
        ops, ram = max(1, n * logn), max(4096, 96 * n)
    elif name == "knapsack_dp":
        kind = "pseudo_polynomial_in_numeric_W"
        ops, ram = max(1, n * (w + 1)), max(4096, 8 * (n + 1) * (w + 1))
    elif name == "subset_sum_mitm":
        kind = "exponential_in_n"
        if n > 256:
            return ResourceEstimate(algorithm_id, None, None, None, kind, "cpu",
                                    "unbounded", False, ("exponent_exceeds_estimation_cap",))
        ops, ram = max(1, n * (1 << ((n + 1) // 2))), 80 * (1 << ((n + 1) // 2))
    else:
        raise AssertionError("hard-coded cost table incomplete")
    reasons: list[str] = []
    if kind.startswith("pseudo") and not device.allow_pseudopolynomial:
        reasons.append("pseudo_polynomial_disallowed")
    if kind.startswith("exponential") and not device.allow_exponential:
        reasons.append("exponential_disallowed")
    if ops > device.max_operations:
        reasons.append("operation_budget_exceeded")
    if ram > device.ram_bytes:
        if name == "sort_comparison" and device.allow_external_memory and device.scratch_bytes >= 4 * ram:
            strategy, scratch, ram = "external_merge_sort_proposal", 4 * ram, min(ram, device.ram_bytes)
            # External memory changes I/O complexity; a separate implementation must be validated.
            reasons.append("external_memory_kernel_not_installed")
        else:
            reasons.append("ram_budget_exceeded")
    accelerator = "cpu"
    if (device.accelerator != "none" and algorithm_id in device.checked_accelerator_kernels
            and not reasons):
        accelerator = device.accelerator
        strategy = "host_checked_accelerator_kernel"
    # Never infer GPU availability or performance from an algorithm's name.
    if exact and name == "knapsack_dp":
        # Cost of storing witnesses is included. Bit-length cannot be dropped from a claim.
        reasons.append("requires_independent_correctness_and_bit_cost_verification")
    # Verification-required is a disclosure, not a cost-budget rejection.
    blocking = [r for r in reasons if r != "requires_independent_correctness_and_bit_cost_verification"]
    return ResourceEstimate(algorithm_id, ops, ram, scratch, kind, accelerator,
                            strategy, not blocking, tuple(reasons))


def rank_options(algorithms: tuple[str, ...], dimensions: Mapping[str, int],
                 device: DeviceBudget) -> tuple[ResourceEstimate, ...]:
    """Deterministic preference among cost-feasible algorithms, retaining all refusals."""
    rows = tuple(estimate(a, dimensions, device) for a in algorithms)
    return tuple(sorted(rows, key=lambda r: (not r.eligible,
                                            r.operations if r.operations is not None else 10**100,
                                            r.algorithm_id)))
