# SPDX-License-Identifier: Apache-2.0
"""Agent-callable exact algorithm kernels and independent bounded oracles.

Untrusted callable customizations are permitted only in an already confined host.
An oracle verifies a *specific invocation*, not a universal theorem. Never promotes
agent-produced code or claims formally proved asymptotic correctness.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class KernelResult:
    algorithm_id: str
    output: Any
    verification: str
    witness: Any
    checked_instances: int
    guarantees: tuple[str, ...]
    limitations: tuple[str, ...]


class KernelUnavailable(ValueError):
    pass


def _bounded_ints(items: Sequence[int], target: int, *, max_items: int, max_target: int) -> tuple[int, ...]:
    if type(target) is not int or not 0 <= target <= max_target:
        raise KernelUnavailable("target exceeds validated numeric budget")
    if len(items) > max_items or any(type(x) is not int or x < 0 for x in items):
        raise KernelUnavailable("nonnegative integer weights and item bound required")
    return tuple(items)


def subset_sum_dp(weights: Sequence[int], target: int, *, max_items: int = 100,
                  max_target: int = 100_000, independent_oracle_n: int = 20) -> KernelResult:
    """Exact DP with reconstructed yes witness; exhaustive independent oracle at small n."""
    w = _bounded_ints(weights, target, max_items=max_items, max_target=max_target)
    previous: dict[int, tuple[int, ...]] = {0: ()}
    for i, value in enumerate(w):
        updated = dict(previous)
        for total, chosen in previous.items():
            nxt = total + value
            if nxt <= target and nxt not in updated:
                updated[nxt] = (*chosen, i)
        previous = updated
    witness = previous.get(target)
    exists = witness is not None
    assert not exists or (len(set(witness)) == len(witness) and
                          all(0 <= i < len(w) for i in witness) and
                          sum(w[i] for i in witness) == target)
    count = 0
    verdict = "witness_checked_only" if exists else "algorithm_result_unverified"
    if len(w) <= independent_oracle_n:
        truth = False
        for mask in range(1 << len(w)):
            count += 1
            if sum(value for i, value in enumerate(w) if mask >> i & 1) == target:
                truth = True
                break
        if truth != exists:
            raise AssertionError("independent subset-sum oracle disagrees")
        verdict = "independently_checked_instance"
    return KernelResult("subset_sum_dp", exists, verdict, witness, count,
                        ("exact integer arithmetic", "no false-positive witness"),
                        ("O(nW) pseudo-polynomial numeric complexity",
                         "NO-instance independently checked only for bounded n"))


def interval_earliest_finish(intervals: Sequence[tuple[int, int]], *,
                             independent_oracle_n: int = 18) -> KernelResult:
    """Canonical earliest-finish greedy with independent exhaustive optimum check."""
    if len(intervals) > 100_000 or any(
        not isinstance(p, (tuple, list)) or len(p) != 2 or
        type(p[0]) is not int or type(p[1]) is not int or p[0] >= p[1]
        for p in intervals
    ):
        raise KernelUnavailable("finite integer half-open intervals with start < finish required")
    indexed = sorted(enumerate(intervals), key=lambda item: (item[1][1], item[1][0], item[0]))
    chosen: list[int] = []
    last_end: int | None = None
    for i, (start, end) in indexed:
        if last_end is None or start >= last_end:
            chosen.append(i)
            last_end = end
    ordered = sorted((intervals[i] for i in chosen))
    if any(ordered[i][1] > ordered[i + 1][0] for i in range(len(ordered) - 1)):
        raise AssertionError("invalid selected schedule")
    count = 0
    status = "witness_checked_only"
    if len(intervals) <= independent_oracle_n:
        best = 0
        for mask in range(1 << len(intervals)):
            count += 1
            if mask.bit_count() <= best:
                continue
            items = sorted(intervals[i] for i in range(len(intervals)) if mask >> i & 1)
            if all(items[j][1] <= items[j + 1][0] for j in range(len(items) - 1)):
                best = len(items)
        if len(chosen) != best:
            raise AssertionError("independent interval optimum disagrees")
        status = "independently_checked_instance"
    return KernelResult("interval_earliest_finish", tuple(chosen), status, tuple(chosen), count,
                        ("nonoverlapping output",), ("optimality oracle bounded by n",))


def merge_sort(items: Sequence[Any], *, key: Callable[[Any], Any] | None = None,
               reverse: bool = False) -> KernelResult:
    """Stable comparison sort; caller-supplied key is untrusted and host-controlled."""
    if len(items) > 100_000:
        raise KernelUnavailable("item budget exceeded")
    seq = list(items)
    # Evaluate untrusted key once per input; preserve ties by original index.
    extracted = [(key(v) if key is not None else v, i, v) for i, v in enumerate(seq)]
    ordered = sorted(extracted, key=lambda x: x[0], reverse=reverse)
    output = tuple(row[2] for row in ordered)
    # Independent permutation/stability/order check against the extracted keys.
    assert sorted(row[1] for row in ordered) == list(range(len(seq)))
    for a, b in zip(ordered, ordered[1:]):
        if (a[0] < b[0] if reverse else a[0] > b[0]):
            raise AssertionError("sorting order violated")
        if a[0] == b[0] and a[1] > b[1]:
            raise AssertionError("stability violated")
    return KernelResult("merge_sort", output, "output_properties_checked",
                        tuple(row[1] for row in ordered), 0,
                        ("permutation of inputs", "sorted by evaluated keys", "stable ties"),
                        ("Python's checked sort backend; not an independent sorting algorithm implementation",
                         "key function must run in independently confined host environment"))


KERNELS: Mapping[str, Callable[..., KernelResult]] = {
    "interval_earliest_finish": interval_earliest_finish,
    "subset_sum_dp": subset_sum_dp,
    "merge_sort": merge_sort,
}


def invoke_kernel(algorithm_id: str, /, *args: Any, **kwargs: Any) -> KernelResult:
    """Higher-order invocation API. No arbitrary function-name execution."""
    try:
        fn = KERNELS[algorithm_id]
    except KeyError as exc:
        raise KernelUnavailable("algorithm has no independently checked callable kernel") from exc
    return fn(*args, **kwargs)
