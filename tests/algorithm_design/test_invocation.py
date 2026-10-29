# SPDX-License-Identifier: Apache-2.0
"""Agent API must allow only fixed identifiers and typed declarative parameters."""
import pytest
from g6.algorithm_design.invocation import invoke, list_kernels
from g6.algorithm_design.checked_kernels import ContractError

def test_registry_is_whitelisted():
    names = {x.algorithm_id for x in list_kernels()}
    assert len(names) == 22
    assert "dijkstra" in names and "subset_sum_dp" in names
    r = invoke("edmonds_karp", {"n":3,"edges":[(0,1,4),(1,2,3)]})
    assert r.output == 3
    assert r.verification == "primal_dual_optimality_certificate_checked"

def test_registry_rejects_untrusted_code_and_wrong_contract():
    with pytest.raises(ContractError):
        invoke("__import__", {"name":"os"})
    with pytest.raises(ContractError):
        invoke("merge_sort", {"items":[1,2,3], "key":lambda x: x})
    with pytest.raises(ContractError):
        invoke("dijkstra", {"n":2,"edges":[(0,1,-1)]})
    with pytest.raises(ContractError):
        invoke("dijkstra", {"unknown_parameter":1})
