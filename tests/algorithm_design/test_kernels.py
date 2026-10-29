# SPDX-License-Identifier: Apache-2.0
"""Executable kernel results and independent bounded reference checks."""
import pytest

from g6.algorithm_design.kernels import invoke_kernel, KernelUnavailable


def test_subset_sum_yes_no_and_zero():
    yes = invoke_kernel("subset_sum_dp", [3, 7, 10], 13)
    assert yes.output and yes.verification == "independently_checked_instance"
    assert yes.witness == (0, 2)
    assert invoke_kernel("subset_sum_dp", [4, 6], 7).output is False
    assert invoke_kernel("subset_sum_dp", [4, 6], 0).output is True


def test_interval_greedy_and_boundaries():
    result = invoke_kernel("interval_earliest_finish", [(0, 5), (0, 2), (2, 4), (4, 7)])
    assert len(result.output) == 3
    assert result.verification == "independently_checked_instance"
    with pytest.raises(KernelUnavailable):
        invoke_kernel("interval_earliest_finish", [(2, 1)])


def test_stable_sort():
    values = [("b", 2), ("a", 4), ("b", 7)]
    result = invoke_kernel("merge_sort", values, key=lambda item: item[0])
    assert result.output == (("a", 4), ("b", 2), ("b", 7))
    assert result.verification == "output_properties_checked"


def test_fail_closed_unimplemented_and_budgets():
    with pytest.raises(KernelUnavailable):
        invoke_kernel("dijkstra", {})
    with pytest.raises(KernelUnavailable):
        invoke_kernel("subset_sum_dp", [2] * 101, 100)
