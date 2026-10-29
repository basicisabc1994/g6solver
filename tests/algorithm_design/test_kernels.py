# SPDX-License-Identifier: Apache-2.0
"""Executable kernel results and independent bounded reference checks, through the one invoke path."""
import pytest

from g6.algorithm_design.invocation import invoke
from g6.algorithm_design.kernels import KernelUnavailable


def test_subset_sum_yes_no_and_zero():
    yes = invoke("subset_sum_dp", {"weights": [3, 7, 10], "target": 13})
    assert yes.output and yes.verification == "independently_checked_instance"
    assert yes.witness == (0, 2)
    assert invoke("subset_sum_dp", {"weights": [4, 6], "target": 7}).output is False
    assert invoke("subset_sum_dp", {"weights": [4, 6], "target": 0}).output is True


def test_interval_greedy_and_boundaries():
    result = invoke("interval_earliest_finish", {"intervals": [(0, 5), (0, 2), (2, 4), (4, 7)]})
    assert len(result.output) == 3
    assert result.verification == "independently_checked_instance"
    with pytest.raises(KernelUnavailable):
        invoke("interval_earliest_finish", {"intervals": [(2, 1)]})


def test_stable_sort():
    # A key function is code, which the one invoke path refuses; the items sort by their own order, ties stable.
    values = [("b", 2), ("a", 4), ("b", 7)]
    result = invoke("merge_sort", {"items": values})
    assert result.output == (("a", 4), ("b", 2), ("b", 7))
    assert result.verification == "output_properties_checked"


def test_fail_closed_unimplemented_and_budgets():
    with pytest.raises(KernelUnavailable):
        invoke("no_such_kernel", {})                 # an algorithm with no kernel (dijkstra now has one)
    with pytest.raises(KernelUnavailable):
        invoke("subset_sum_dp", {"weights": [2] * 101, "target": 100})
