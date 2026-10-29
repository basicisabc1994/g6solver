# SPDX-License-Identifier: Apache-2.0
"""Bounded independent reference checks for the expanded algorithm library."""
from itertools import combinations
import random
import pytest
from g6.algorithm_design.checked_kernels import (
    bfs, dijkstra, kahn, weighted_interval_dp, vertex_cover_2approx, gale_shapley,
    tree_independent_set, edmonds_karp, kruskal, subset_sum_mitm, knapsack_dp,
    bellman_ford, vertex_cover_fpt, set_cover_greedy, sequence_align_dp, huffman,
    held_karp_tsp, randomized_quicksort, treewidth_independent_set, ContractError,
)

def test_contract_examples():
    assert bfs(3, [(0,1)]).output == (0,1,None)
    assert dijkstra(3, [(0,1,3),(1,2,1),(0,2,9)]).output == (0,3,4)
    assert kahn(2, [(0,1),(1,0)]).output is None
    assert weighted_interval_dp([(0,2,4),(2,4,9),(0,4,10)]).output == 13
    assert gale_shapley([[0,1],[0,1]], [[0,1],[0,1]]).output == (0,1)
    assert edmonds_karp(3,[(0,1,3),(1,2,2)]).output == 2
    assert kruskal(3,[(0,1,1),(1,2,2),(0,2,10)]).output == 3
    assert vertex_cover_2approx(3,[(0,1),(1,2)]).verification == "approximation_certificate_checked"
    assert tree_independent_set(3,[(0,1),(1,2)]).output == 2
    assert subset_sum_mitm([2,3,8],5).output is True
    assert knapsack_dp([2,3],[3,5],4).output == 5
    assert bellman_ford(3,[(0,1,1),(1,2,-3)]).output == (0,1,-2)
    assert bellman_ford(2,[(0,1,-1),(1,0,0)]).output is None
    assert vertex_cover_fpt(3,[(0,1),(1,2)],1).output
    assert set_cover_greedy([1,2],[[1],[2]]).output == (0,1)
    assert sequence_align_dp("cat","cut").output == 1
    assert huffman([4,4]).output == (1,1)
    assert held_karp_tsp([[0,2,4],[2,0,3],[4,3,0]]).output == 9
    assert randomized_quicksort([9,1,1]).output == (1,1,9)
    assert treewidth_independent_set(3,[(0,1),(1,2)],[[0,1],[1,2]],[(0,1)]).output == 2

def test_invalid_inputs_refused():
    with pytest.raises(ContractError):
        dijkstra(2,[(0,1,-4)])
    with pytest.raises(ContractError):
        tree_independent_set(3,[(0,1),(1,2),(2,0)])
    with pytest.raises(ContractError):
        treewidth_independent_set(3,[(0,2)],[[0,1],[1,2]],[(0,1)])
    with pytest.raises(ContractError):
        set_cover_greedy([1,2],[[1]])

def test_randomized_reference_checks():
    rng = random.Random(884)
    for _ in range(60):
        n = rng.randrange(2,9)
        weights = [rng.randrange(10) for _ in range(n)]
        values = [rng.randrange(10) for _ in range(n)]
        cap = rng.randrange(20)
        opt = max((sum(values[i] for i in range(n) if mask >> i & 1)
                  for mask in range(1 << n)
                  if sum(weights[i] for i in range(n) if mask >> i & 1) <= cap), default=0)
        assert knapsack_dp(weights, values, cap).output == opt
        target = rng.randrange(20)
        assert subset_sum_mitm(weights, target).output == any(
            sum(weights[i] for i in range(n) if mask >> i & 1) == target
            for mask in range(1 << n))
        edge_list = [(u,v) for u in range(n) for v in range(u+1,n) if rng.random() < 0.25]
        cover = vertex_cover_2approx(n,edge_list)
        optimum = min(mask.bit_count() for mask in range(1<<n)
                      if all(mask>>u&1 or mask>>v&1 for u,v in edge_list))
        assert len(cover.output) <= 2*optimum
        k = rng.randrange(4)
        assert vertex_cover_fpt(n,edge_list,k).output == any(
            all(u in s or v in s for u,v in edge_list)
            for j in range(k+1) for s in combinations(range(n),j))
