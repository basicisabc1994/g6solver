# SPDX-License-Identifier: Apache-2.0
"""Deterministic, bounded algorithm kernels with independently checked *instance* certificates.

This module has no LLM, execution, filesystem, or network access.  Each algorithm
is a fixed callable and checks its own output against a separately written
certificate checker.  No formal verification of the Python implementation is
claimed.  Arbitrary LLM-authored code belongs in an isolated candidate runner,
never in this registry.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from typing import Any, Mapping, Sequence


class ContractError(ValueError):
    """Inputs violate a published algorithm contract or verification budget."""


@dataclass(frozen=True)
class CertifiedResult:
    algorithm_id: str
    output: Any
    witness: Any
    verification: str
    complexity: str
    limitations: tuple[str, ...] = ()


def _integer(value: Any, *, lower: int = 0, upper: int = 10**9) -> bool:
    return type(value) is int and lower <= value <= upper


def _size(n: int, *, limit: int) -> None:
    if not _integer(n, upper=limit):
        raise ContractError("instance exceeds bounded verifier limits")


def _edges(n: int, edges: Sequence[tuple[int, int, int]], *, limit: int,
           weighted: bool = True, negative: bool = False, directed: bool = True):
    _size(n, limit=2000)
    if len(edges) > limit or (n == 0 and len(edges) > 0):
        raise ContractError("too many edges or edges without vertices")
    out = []
    for edge in edges:
        if not isinstance(edge, (tuple, list)) or len(edge) != (3 if weighted else 2):
            raise ContractError("wrong edge representation")
        u, v = edge[:2]
        w = edge[2] if weighted else 1
        if not (_integer(u, upper=max(0, n-1)) and _integer(v, upper=max(0, n-1))
                and _integer(w, lower=-10**6 if negative else 0, upper=10**6)):
            raise ContractError("invalid vertex or integer edge weight")
        if not directed and u == v:
            raise ContractError("undirected self-loop unsupported")
        out.append((u, v, w))
    return tuple(out)


def _verify_potentials(n: int, edges: tuple[tuple[int, int, int], ...], source: int,
                       distances: tuple[int | None, ...], parents: tuple[int | None, ...]) -> None:
    """Global shortest-path certificate: valid root paths + every edge inequality.

    A path from the source gives an upper bound on optimum and triangle
    inequalities give a lower bound, establishing equality. Closure establishes
    that no alleged unreachable vertex is actually reachable.
    """
    if len(distances) != n or len(parents) != n or distances[source] != 0 or parents[source] is not None:
        raise AssertionError("invalid root labels")
    es = {(u, v, w) for u, v, w in edges}
    reachable = {source}
    for _ in range(n):
        for v, p in enumerate(parents):
            if p is not None and p in reachable:
                if distances[p] is None or distances[v] is None or not any(
                    u == p and vv == v and distances[p] + w == distances[v] for u, vv, w in es
                ):
                    raise AssertionError("invalid predecessor path")
                reachable.add(v)
    if any((distances[v] is None) != (v not in reachable) for v in range(n)):
        raise AssertionError("unrealized distance or inaccessible vertex")
    for u, v, w in edges:
        if distances[u] is not None:
            if distances[v] is None or distances[v] > distances[u] + w:
                raise AssertionError("violated distance inequality")


def bfs(n: int, edges: Sequence[tuple[int, int]], source: int = 0) -> CertifiedResult:
    """Directed unweighted single-source shortest paths, O(V+E)."""
    _size(n, limit=1200)
    if n == 0 or not _integer(source, upper=n-1):
        raise ContractError("source not in graph")
    es = _edges(n, edges, limit=12000, weighted=False)
    adj = [[] for _ in range(n)]
    for u, v, _ in es:
        adj[u].append(v)
    distances: list[int | None] = [None] * n
    parents: list[int | None] = [None] * n
    distances[source] = 0
    q = deque([source])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if distances[v] is None:
                distances[v] = distances[u] + 1
                parents[v] = u
                q.append(v)
    result = tuple(distances)
    _verify_potentials(n, es, source, result, tuple(parents))
    return CertifiedResult("bfs", result, tuple(parents), "certificate_checked_instance", "O(V+E)")


def dijkstra(n: int, edges: Sequence[tuple[int, int, int]], source: int = 0) -> CertifiedResult:
    """Directed nonnegative integer shortest paths; heap algorithm."""
    from heapq import heappush, heappop
    _size(n, limit=1200)
    if n == 0 or not _integer(source, upper=n-1):
        raise ContractError("source not in graph")
    es = _edges(n, edges, limit=12000)
    adj = [[] for _ in range(n)]
    for u, v, w in es:
        adj[u].append((v, w))
    distances: list[int | None] = [None] * n
    parents: list[int | None] = [None] * n
    distances[source] = 0
    heap = [(0, source)]
    while heap:
        du, u = heappop(heap)
        if du != distances[u]:
            continue
        for v, w in adj[u]:
            if distances[v] is None or du + w < distances[v]:
                distances[v], parents[v] = du + w, u
                heappush(heap, (du + w, v))
    _verify_potentials(n, es, source, tuple(distances), tuple(parents))
    return CertifiedResult("dijkstra", tuple(distances), tuple(parents),
                           "certificate_checked_instance", "O((V+E) log V) arithmetic operations",
                           ("integer bit-complexity is additional",))


def kahn(n: int, edges: Sequence[tuple[int, int]]) -> CertifiedResult:
    """Topological ordering or independently checked directed-cycle witness."""
    _size(n, limit=600)
    es = _edges(n, edges, limit=12000, weighted=False)
    adj = [[] for _ in range(n)]
    indeg = [0] * n
    for u, v, _ in es:
        indeg[v] += 1
        adj[u].append(v)
    q = deque(i for i in range(n) if indeg[i] == 0)
    order = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    if len(order) == n:
        rank = {v: i for i, v in enumerate(order)}
        if len(rank) != n or any(rank[u] >= rank[v] for u, v, _ in es):
            raise AssertionError("invalid topological ordering")
        return CertifiedResult("kahn", tuple(order), tuple(order), "certificate_checked_instance", "O(V+E)")
    # Explicit cycle extraction from the remaining induced subgraph (not merely a boolean).
    active = {i for i in range(n) if indeg[i] > 0}
    color = [0] * n
    parent: dict[int, int] = {}
    cycle = None
    def visit(u: int):
        nonlocal cycle
        color[u] = 1
        for v in adj[u]:
            if v not in active or cycle is not None:
                continue
            if color[v] == 0:
                parent[v] = u
                visit(v)
            elif color[v] == 1:
                path = [u]
                while path[-1] != v:
                    path.append(parent[path[-1]])
                cycle = tuple(reversed(path)) + (v,)
                break
        color[u] = 2
    for u in active:
        if color[u] == 0 and cycle is None:
            visit(u)
    if cycle is None or any((a, b, 1) not in es for a, b in zip(cycle, cycle[1:])):
        raise AssertionError("cycle witness missing or invalid")
    return CertifiedResult("kahn", None, cycle, "certificate_checked_instance", "O(V+E)",
                           ("output None indicates a directed cycle",))


def weighted_interval_dp(intervals: Sequence[tuple[int, int, int]]) -> CertifiedResult:
    """Maximum-weight compatible subset, nonnegative integer rewards."""
    _size(len(intervals), limit=500)
    items = []
    for i, p in enumerate(intervals):
        if not isinstance(p, (tuple, list)) or len(p) != 3 or not all(type(x) is int for x in p):
            raise ContractError("(start, finish, nonnegative reward) required")
        a, b, weight = p
        if a >= b or not _integer(weight, upper=10**9):
            raise ContractError("invalid weighted interval")
        items.append((b, a, weight, i))
    items.sort()
    ends = [v[0] for v in items]
    preds = [bisect_right(ends, x[1], 0, j) for j, x in enumerate(items)]
    dp = [0] * (len(items) + 1)
    for j, x in enumerate(items, 1):
        dp[j] = max(dp[j-1], x[2] + dp[preds[j-1]])
    chosen = []
    j = len(items)
    while j:
        x = items[j-1]
        if x[2] + dp[preds[j-1]] > dp[j-1]:
            chosen.append(x[3])
            j = preds[j-1]
        else:
            j -= 1
    chosen.reverse()
    val = sum(intervals[i][2] for i in chosen)
    selected = sorted((intervals[i][0], intervals[i][1]) for i in chosen)
    if val != dp[-1] or any(selected[k][1] > selected[k+1][0] for k in range(len(selected)-1)):
        raise AssertionError("invalid weighted interval solution")
    # Independent recurrence over sorted ends, using a separate memoized branch.
    @lru_cache(None)
    def check(j: int) -> int:
        if j == 0:
            return 0
        a, b, w, idx = items[j-1]
        return max(check(j-1), w + check(sum(1 for end in ends[:j-1] if end <= b)))
    if val != check(len(items)):
        raise AssertionError("independent optimum check disagrees")
    return CertifiedResult("weighted_interval_dp", val, tuple(chosen),
                           "independently_checked_instance", "O(n log n) arithmetic operations")


def vertex_cover_2approx(n: int, edges: Sequence[tuple[int, int]]) -> CertifiedResult:
    """A maximal matching supplies both the cover and the <=2OPT certificate."""
    _size(n, limit=2000)
    es = _edges(n, edges, limit=12000, weighted=False)
    used = set()
    matching = []
    for u, v, _ in es:
        if u == v:
            raise ContractError("self loops unsupported")
        if u not in used and v not in used:
            used.update((u, v))
            matching.append((u, v))
    if any(u not in used and v not in used for u, v, _ in es):
        raise AssertionError("cover failed")
    if len({u for edge in matching for u in edge}) != 2 * len(matching):
        raise AssertionError("not a matching")
    # Every cover needs >=|matching| vertices; constructed cover = 2|matching|.
    return CertifiedResult("vertex_cover_2approx", tuple(sorted(used)), tuple(matching),
                           "approximation_certificate_checked", "O(V+E)",
                           ("2-approximation, not exact optimum",))


def gale_shapley(proposers: Sequence[Sequence[int]], receivers: Sequence[Sequence[int]]) -> CertifiedResult:
    """Strict complete preference lists, proposer-optimal stable matching."""
    n = len(proposers)
    _size(n, limit=200)
    if len(receivers) != n:
        raise ContractError("both sides need same size")
    expected = set(range(n))
    if any(len(p) != n or set(p) != expected or any(type(x) is not int for x in p)
           for p in [*proposers, *receivers]):
        raise ContractError("each preference list must be a permutation of 0..n-1")
    rank = [{person: i for i, person in enumerate(pref)} for pref in receivers]
    next_choice = [0] * n
    matched: list[int | None] = [None] * n
    free = deque(range(n))
    while free:
        p = free.popleft()
        if next_choice[p] >= n:
            raise AssertionError("unexpected exhausted proposer")
        r = proposers[p][next_choice[p]]
        next_choice[p] += 1
        old = matched[r]
        if old is None:
            matched[r] = p
        elif rank[r][p] < rank[r][old]:
            matched[r] = p
            free.append(old)
        else:
            free.append(p)
    partners = [None] * n
    for r, p in enumerate(matched):
        partners[p] = r
    if None in partners or len(set(partners)) != n:
        raise AssertionError("imperfect matching")
    for p in range(n):
        r0 = partners[p]
        for r in proposers[p][:proposers[p].index(r0)]:
            if rank[r][p] < rank[r][matched[r]]:
                raise AssertionError("blocking pair")
    return CertifiedResult("gale_shapley", tuple(partners), tuple(partners),
                           "stable_matching_certificate_checked", "O(n^2)",
                           ("proposer-optimality relies on standard Gale-Shapley theorem",))


def tree_independent_set(n: int, edges: Sequence[tuple[int, int]],
                         weights: Sequence[int] | None = None) -> CertifiedResult:
    """Maximum-weight independent set in an undirected forest."""
    _size(n, limit=500)
    es = _edges(n, edges, limit=1500, weighted=False, directed=False)
    if weights is not None and (len(weights) != n or any(not _integer(w, upper=10**9) for w in weights)):
        raise ContractError("weights must be nonnegative integers of length n")
    ws = list(weights) if weights is not None else [1]*n
    adj = [[] for _ in range(n)]
    for u, v, _ in es:
        adj[u].append(v)
        adj[v].append(u)
    seen = set()
    roots = []
    parent = [-1]*n
    order = []
    for root in range(n):
        if root in seen:
            continue
        roots.append(root)
        seen.add(root)
        queue = [root]
        for u in queue:
            order.append(u)
            for v in adj[u]:
                if v == parent[u]:
                    continue
                if v in seen:
                    raise ContractError("input is not a forest")
                seen.add(v)
                parent[v] = u
                queue.append(v)
    inside = [0]*n
    outside = [0]*n
    for u in reversed(order):
        inside[u] = ws[u]
        for v in adj[u]:
            if parent[v] == u:
                inside[u] += outside[v]
                outside[u] += max(inside[v], outside[v])
    chosen = set()
    stack = [(r, False) for r in roots]
    while stack:
        u, excluded = stack.pop()
        selected = not excluded and inside[u] > outside[u]
        if selected:
            chosen.add(u)
        stack.extend((v, selected) for v in adj[u] if parent[v] == u)
    if any(u in chosen and v in chosen for u, v, _ in es):
        raise AssertionError("dependent solution")
    value = sum(ws[u] for u in chosen)
    if value != sum(max(inside[r], outside[r]) for r in roots):
        raise AssertionError("solution does not match DP bound")
    # A second recurrence on the actual parent forest validates the optimum.
    @lru_cache(None)
    def check(u: int, blocked: bool) -> int:
        children = [v for v in adj[u] if parent[v] == u]
        no = sum(check(v, False) for v in children)
        return no if blocked else max(no, ws[u] + sum(check(v, True) for v in children))
    if value != sum(check(r, False) for r in roots):
        raise AssertionError("independent tree recurrence disagrees")
    return CertifiedResult("tree_independent_set", value, tuple(sorted(chosen)),
                           "independently_checked_instance", "O(V+E)",
                           ("requires a forest, not an arbitrary graph",))


def edmonds_karp(n: int, edges: Sequence[tuple[int, int, int]],
                 source: int = 0, sink: int | None = None) -> CertifiedResult:
    """Integer max-flow with a max-flow/min-cut optimality certificate."""
    _size(n, limit=120)
    if sink is None:
        sink = n - 1
    if n < 2 or not _integer(source, upper=n-1) or not _integer(sink, upper=n-1) or source == sink:
        raise ContractError("distinct source and sink are required")
    es = _edges(n, edges, limit=1500)
    if any(u == v for u, v, _ in es):
        raise ContractError("self-loops not supported in flow contract")
    # Residual multigraph: forward/reverse arcs with paired indices.
    arcs: list[list[int]] = []  # [destination, residual_capacity, reverse_arc_index]
    adj = [[] for _ in range(n)]
    original_indices = []
    for u, v, cap in es:
        a = len(arcs)
        b = a + 1
        arcs.append([v, cap, b])
        arcs.append([u, 0, a])
        adj[u].append(a)
        adj[v].append(b)
        original_indices.append(a)
    while True:
        path = {source: -1}
        q = deque([source])
        while q and sink not in path:
            u = q.popleft()
            for a in adj[u]:
                v, remaining, _ = arcs[a]
                if remaining and v not in path:
                    path[v] = a
                    q.append(v)
        if sink not in path:
            break
        amount = None
        v = sink
        while v != source:
            a = path[v]
            amount = arcs[a][1] if amount is None else min(amount, arcs[a][1])
            v = arcs[arcs[a][2]][0]
        if amount is None or amount <= 0:
            raise AssertionError("augmenting path invalid")
        v = sink
        while v != source:
            a = path[v]
            arcs[a][1] -= amount
            arcs[arcs[a][2]][1] += amount
            v = arcs[arcs[a][2]][0]
    flows = tuple(cap - arcs[a][1] for (_, _, cap), a in zip(es, original_indices))
    # Independent check of primal feasibility and dual cut equality.
    balance = [0]*n
    for (u, v, cap), f in zip(es, flows):
        if f < 0 or f > cap:
            raise AssertionError("flow bounds violation")
        balance[u] -= f
        balance[v] += f
    if any(balance[v] != 0 for v in range(n) if v not in (source, sink)):
        raise AssertionError("flow conservation failure")
    flow_value = balance[sink]
    if balance[source] != -flow_value or flow_value < 0:
        raise AssertionError("flow value imbalance")
    # Rebuild a residual graph solely from original graph and published flow.
    remaining_adj = [[] for _ in range(n)]
    for (u, v, cap), f in zip(es, flows):
        if f < cap:
            remaining_adj[u].append(v)
        if f:
            remaining_adj[v].append(u)
    cut = {source}
    q = deque([source])
    while q:
        for v in remaining_adj[q.popleft()]:
            if v not in cut:
                cut.add(v)
                q.append(v)
    capacity = sum(cap for u, v, cap in es if u in cut and v not in cut)
    if sink in cut or capacity != flow_value:
        raise AssertionError("max-flow/min-cut certificate failed")
    return CertifiedResult("edmonds_karp", flow_value,
                           {"flow_by_edge": flows, "source_cut": tuple(sorted(cut)), "cut_capacity": capacity},
                           "primal_dual_optimality_certificate_checked", "O(V E^2) arithmetic operations")


def kruskal(n: int, edges: Sequence[tuple[int, int, int]]) -> CertifiedResult:
    """Minimum spanning forest; checks optimality using the cycle property."""
    _size(n, limit=140)
    es = _edges(n, edges, limit=1500, directed=False)
    parent = list(range(n))
    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    chosen = []
    for i in sorted(range(len(es)), key=lambda j: (es[j][2], j)):
        u, v, _ = es[i]
        ru, rv = root(u), root(v)
        if ru != rv:
            parent[ru] = rv
            chosen.append(i)
    chosen_set = set(chosen)
    forest = [[] for _ in range(n)]
    for i in chosen:
        u, v, w = es[i]
        forest[u].append((v, w))
        forest[v].append((u, w))
    # Independently check chosen edges have no cycles and span original components.
    p2 = list(range(n))
    def root2(x: int) -> int:
        while p2[x] != x:
            x = p2[x]
        return x
    for i in chosen:
        u, v, _ = es[i]
        ru, rv = root2(u), root2(v)
        if ru == rv:
            raise AssertionError("chosen forest has cycle")
        p2[ru] = rv
    if any(root2(u) != root2(v) for u, v, _ in es):
        raise AssertionError("forest fails to span graph components")
    # For each excluded edge, no heavier tree edge may lie on its tree path.
    for i, (u, v, w) in enumerate(es):
        if i in chosen_set:
            continue
        paths = [(u, -1, 0)]
        found = False
        while paths:
            node, prev, maximum = paths.pop()
            if node == v:
                if maximum > w:
                    raise AssertionError("cycle property violated")
                found = True
                break
            paths.extend((to, node, max(maximum, weight)) for to, weight in forest[node] if to != prev)
        if not found:
            raise AssertionError("missing path in forest")
    return CertifiedResult("kruskal", sum(es[i][2] for i in chosen), tuple(chosen),
                           "cycle_property_optimality_certificate_checked", "O(E log E) algorithm; O(EV) verifier",
                           ("supports nonnegative integer weights and disconnected forests",))


def subset_sum_mitm(weights: Sequence[int], target: int) -> CertifiedResult:
    """Exact meet-in-middle with a witness, small bounded exhaustive NO oracle."""
    _size(len(weights), limit=32)
    if not _integer(target, upper=10**12) or any(not _integer(x, upper=10**12) for x in weights):
        raise ContractError("nonnegative integer weights/target required")
    n = len(weights)
    midpoint = n // 2
    left, right = tuple(weights[:midpoint]), tuple(weights[midpoint:])
    sums = {}
    for mask in range(1 << len(left)):
        total = sum(w for i, w in enumerate(left) if mask >> i & 1)
        sums.setdefault(total, mask)
    found = None
    for mask in range(1 << len(right)):
        v = sum(w for i, w in enumerate(right) if mask >> i & 1)
        if target - v in sums:
            lmask = sums[target-v]
            found = tuple(i for i in range(midpoint) if lmask >> i & 1) + tuple(
                midpoint+i for i in range(len(right)) if mask >> i & 1)
            break
    if found is not None and sum(weights[i] for i in found) != target:
        raise AssertionError("invalid witness")
    # NO-checker independently generates sums using recurrence (not bit masks).
    if found is None:
        reachable = {0}
        for weight in weights:
            reachable |= {s + weight for s in reachable if s + weight <= target}
        if target in reachable:
            raise AssertionError("false NO result")
    return CertifiedResult("subset_sum_mitm", found is not None, found,
                           "independently_checked_instance" if found is None else "witness_checked_instance",
                           "O(n 2^(n/2)) time; O(2^(n/2)) memory",
                           ("exponential; n capped at 32",))


def knapsack_dp(weights: Sequence[int], values: Sequence[int], capacity: int) -> CertifiedResult:
    """Exact integer 0/1 knapsack with independent recurrence value check."""
    n = len(weights)
    _size(n, limit=100)
    if len(values) != n or not _integer(capacity, upper=10000) or any(
        not _integer(w, upper=10000) for w in weights
    ) or any(not _integer(v, upper=10**9) for v in values):
        raise ContractError("nonnegative integer weights/values and bounded capacity required")
    if n * (capacity + 1) > 500_000:
        raise ContractError("DP cell budget exceeded")
    table = [[0] * (capacity+1) for _ in range(n+1)]
    for i, (weight, value) in enumerate(zip(weights, values), 1):
        for c in range(capacity+1):
            table[i][c] = max(table[i-1][c], value + table[i-1][c-weight]) if weight <= c else table[i-1][c]
    chosen = []
    c = capacity
    for i in range(n, 0, -1):
        w, v = weights[i-1], values[i-1]
        if w <= c and table[i][c] == v + table[i-1][c-w] and table[i][c] > table[i-1][c]:
            chosen.append(i-1)
            c -= w
    chosen.reverse()
    score = sum(values[i] for i in chosen)
    if sum(weights[i] for i in chosen) > capacity or score != table[n][capacity]:
        raise AssertionError("invalid knapsack witness")
    @lru_cache(None)
    def verify(i: int, space: int) -> int:
        if i == n:
            return 0
        without = verify(i+1, space)
        return max(without, values[i]+verify(i+1, space-weights[i])) if weights[i] <= space else without
    if score != verify(0, capacity):
        raise AssertionError("independent knapsack optimum disagrees")
    return CertifiedResult("knapsack_dp", score, tuple(chosen),
                           "independently_checked_instance", "O(nW) pseudo-polynomial",
                           ("not polynomial in binary length of W",))


def bellman_ford(n: int, edges: Sequence[tuple[int, int, int]], source: int = 0) -> CertifiedResult:
    """Shortest paths with negative weights, or a reachable negative-cycle witness."""
    _size(n, limit=300)
    if n == 0 or not _integer(source, upper=n - 1):
        raise ContractError("invalid source")
    es = _edges(n, edges, limit=3000, negative=True)
    dist: list[int | None] = [None] * n
    pred: list[int | None] = [None] * n
    pred_edge: list[int | None] = [None] * n
    dist[source] = 0
    last = None
    for _ in range(n):
        last = None
        for i, (u, v, weight) in enumerate(es):
            if dist[u] is not None and (dist[v] is None or dist[u] + weight < dist[v]):
                dist[v] = dist[u] + weight
                pred[v] = u
                pred_edge[v] = i
                last = v
        if last is None:
            break
    if last is None:
        _verify_potentials(n, es, source, tuple(dist), tuple(pred))
        return CertifiedResult("bellman_ford", tuple(dist), tuple(pred),
                               "shortest_path_certificate_checked", "O(VE)")
    # A relaxation on the nth round guarantees a reachable negative cycle.
    x = last
    for _ in range(n):
        x = pred[x]
    backward = [x]
    y = pred[x]
    while y != x:
        backward.append(y)
        y = pred[y]
        if len(backward) > n:
            raise AssertionError("cycle extraction failed")
    backward.reverse()
    cycle = backward + [backward[0]]
    edge_ids = [pred_edge[cycle[i+1]] for i in range(len(cycle)-1)]
    if any(es[j][0] != cycle[i] or es[j][1] != cycle[i+1]
           for i, j in enumerate(edge_ids)) or sum(es[j][2] for j in edge_ids) >= 0:
        raise AssertionError("not a negative cycle")
    reach = {source}
    for _ in range(n):
        reach |= {v for u, v, _ in es if u in reach}
    if cycle[0] not in reach:
        raise AssertionError("negative cycle not reachable")
    return CertifiedResult("bellman_ford", None,
                           {"cycle": tuple(cycle), "edge_ids": tuple(edge_ids)},
                           "negative_cycle_certificate_checked", "O(VE)")


def vertex_cover_fpt(n: int, edges: Sequence[tuple[int, int]], k: int) -> CertifiedResult:
    """Exact bounded-k vertex-cover decision via edge branching, with a NO search-tree certificate."""
    _size(n, limit=30)
    if not _integer(k, upper=12):
        raise ContractError("bounded k required (<=12)")
    es = _edges(n, edges, limit=500, weighted=False)
    if any(u == v for u, v, _ in es):
        raise ContractError("self-loops not supported")
    pairs = tuple((u, v) for u, v, _ in es)
    @lru_cache(None)
    def solve(remaining: tuple[tuple[int, int], ...], budget: int):
        if not remaining:
            return ()
        if budget == 0:
            return None
        u, v = remaining[0]
        for chosen in (u, v):
            leftover = tuple(edge for edge in remaining if chosen not in edge)
            child = solve(leftover, budget - 1)
            if child is not None:
                return (chosen, *child)
        return None
    witness = solve(pairs, k)
    if witness is not None:
        assert len(set(witness)) <= k
        assert all(u in witness or v in witness for u, v in pairs)
        status = "cover_witness_checked"
    else:
        # Exact NO certification via independent exhaustive enumeration for bounded n.
        if n <= 23:
            for sz in range(min(n, k) + 1):
                for ids in combinations(range(n), sz):
                    selected = set(ids)
                    if all(u in selected or v in selected for u, v in pairs):
                        raise AssertionError("false NO answer")
            status = "independently_checked_instance"
        else:
            status = "branching_search_exhausted_not_independently_verified"
    return CertifiedResult("vertex_cover_fpt", witness is not None, witness, status,
                           "O(2^k poly(V+E))", ("fixed-parameter, not uniform polynomial in k",))


def set_cover_greedy(universe: Sequence[int], sets: Sequence[Sequence[int]]) -> CertifiedResult:
    """Greedy unweighted set cover and an inspectable greedy-step approximation witness."""
    _size(len(universe), limit=2000)
    _size(len(sets), limit=1000)
    if any(type(x) is not int for x in universe) or len(set(universe)) != len(universe):
        raise ContractError("universe must contain unique integer labels")
    allowed = set(universe)
    subsets = []
    for row in sets:
        if any(type(x) is not int for x in row) or not set(row) <= allowed:
            raise ContractError("set outside universe")
        subsets.append(frozenset(row))
    pending = set(universe)
    chosen = []
    gains = []
    while pending:
        idx = max(range(len(subsets)), key=lambda i: (len(subsets[i] & pending), -i), default=-1)
        gain = len(subsets[idx] & pending) if idx >= 0 else 0
        if gain == 0:
            raise ContractError("cover infeasible: union of sets does not include universe")
        chosen.append(idx)
        gains.append(gain)
        pending -= subsets[idx]
    # Independent greedy trace verification (a theorem supplies H_m factor).
    missing = set(universe)
    for idx, gain in zip(chosen, gains):
        if len(missing & subsets[idx]) != gain or gain != max((len(missing & row) for row in subsets), default=0):
            raise AssertionError("invalid greedy trace")
        missing -= subsets[idx]
    if missing or len(chosen) != len(set(chosen)):
        raise AssertionError("invalid cover witness")
    return CertifiedResult("set_cover_greedy", tuple(chosen), tuple(gains),
                           "greedy_trace_and_coverage_checked", "polynomial in total input size",
                           ("H_|U| approximation relies on standard greedy set-cover theorem",))


def sequence_align_dp(left: str, right: str) -> CertifiedResult:
    """Exact unit-cost Levenshtein distance with independently checked edit script."""
    if not isinstance(left, str) or not isinstance(right, str) or len(left) * len(right) > 100_000:
        raise ContractError("bounded text sequence lengths required")
    n, m = len(left), len(right)
    dp = [[0]*(m+1) for _ in range(n+1)]
    for i in range(n+1): dp[i][0] = i
    for j in range(m+1): dp[0][j] = j
    for i in range(1,n+1):
        for j in range(1,m+1):
            dp[i][j] = min(dp[i-1][j]+1, dp[i][j-1]+1,
                           dp[i-1][j-1] + (left[i-1] != right[j-1]))
    steps = []
    i, j = n, m
    while i or j:
        if i and j and dp[i][j] == dp[i-1][j-1]+(left[i-1]!=right[j-1]):
            steps.append(("match" if left[i-1]==right[j-1] else "replace", left[i-1],right[j-1]))
            i -= 1; j -= 1
        elif i and dp[i][j] == dp[i-1][j]+1:
            steps.append(("delete", left[i-1], ""))
            i-=1
        else:
            steps.append(("insert", "",right[j-1]))
            j-=1
    steps.reverse()
    position = 0
    output = []
    edits = 0
    for kind, a, b in steps:
        if kind in ("match", "replace", "delete"):
            if position >= n or left[position] != a:
                raise AssertionError("script consumes unexpected character")
            position += 1
        if kind != "delete":
            output.append(b)
        edits += kind != "match"
    if position != n or "".join(output) != right or edits != dp[n][m]:
        raise AssertionError("edit script invalid")
    # Independent row-wise distance verifier with separate memory layout.
    previous = list(range(m+1))
    for i,a in enumerate(left,1):
        row = [i]
        for j,b in enumerate(right,1):
            row.append(min(previous[j]+1,row[-1]+1,previous[j-1]+(a!=b)))
        previous = row
    if dp[n][m] != previous[m]:
        raise AssertionError("independent distance check disagrees")
    return CertifiedResult("sequence_align_dp", edits, tuple(steps),
                           "independently_checked_instance", "O(nm) time and space")


def huffman(weights: Sequence[int]) -> CertifiedResult:
    """Optimal binary prefix-code lengths; zero weights permitted; one symbol uses length 1."""
    from heapq import heapify, heappop, heappush
    n = len(weights)
    _size(n, limit=1500)
    if n == 0 or any(not _integer(w, upper=10**9) for w in weights):
        raise ContractError("nonempty nonnegative integer frequency list required")
    if n == 1:
        return CertifiedResult("huffman", (1,), {0:"0"}, "prefix_code_checked_instance",
                               "O(n log n)", ("single symbol code length 1 convention",))
    heap = [(w,i,i) for i,w in enumerate(weights)]
    heapify(heap)
    nodes = {}
    next_id = n
    while len(heap)>1:
        a=heappop(heap)
        b=heappop(heap)
        nodes[next_id] = (a[2],b[2])
        heappush(heap,(a[0]+b[0],next_id,next_id))
        next_id+=1
    root = heap[0][2]
    codes = {}
    stack=[(root,"")]
    while stack:
        u,prefix=stack.pop()
        if u<n:
            codes[u] = prefix
        else:
            a,b=nodes[u]
            stack.append((a,prefix+"0"))
            stack.append((b,prefix+"1"))
    if len(codes)!=n or any(a!=b and codes[a].startswith(codes[b]) for a in codes for b in codes):
        raise AssertionError("invalid prefix-free code")
    lengths = tuple(len(codes[i]) for i in range(n))
    # Independent Huffman weight-merging lower-bound check, separated from tree reconstruction.
    sums=sorted(weights)
    total=0
    while len(sums)>1:
        a=sums.pop(0); b=sums.pop(0)
        total+=a+b
        sums.append(a+b)
        sums.sort()
    if sum(w*lengths[i] for i,w in enumerate(weights))!=total:
        raise AssertionError("Huffman merge optimum differs")
    return CertifiedResult("huffman", lengths, codes, "prefix_code_and_merge_cost_checked",
                           "O(n log n) algorithm", ("optimality uses Huffman merging theorem",))


def held_karp_tsp(distances: Sequence[Sequence[int]]) -> CertifiedResult:
    """Held-Karp exact Hamiltonian tour from/to vertex 0, bounded n<=14."""
    n=len(distances)
    _size(n,limit=14)
    if n<2 or any(len(row)!=n or any(not _integer(x,upper=10**9) for x in row) for row in distances):
        raise ContractError("square nonnegative integer matrix of size 2..14 required")
    if any(distances[i][i]!=0 for i in range(n)):
        raise ContractError("diagonal must be zero")
    # memoized suffix costs; state (current, set of visited nonzero vertices)
    @lru_cache(None)
    def cost(u:int, mask:int)->int:
        if mask == (1<<(n-1))-1:
            return distances[u][0]
        return min(distances[u][v]+cost(v,mask|(1<<(v-1)))
                   for v in range(1,n) if not mask & (1<<(v-1)))
    answer=cost(0,0)
    path=[0]; mask=0; u=0
    while mask != (1<<(n-1))-1:
        v=next(v for v in range(1,n) if not mask & (1<<(v-1))
               and distances[u][v]+cost(v,mask|(1<<(v-1)))==cost(u,mask))
        path.append(v)
        mask|=1<<(v-1);u=v
    path.append(0)
    if set(path[1:-1])!=set(range(1,n)) or sum(distances[a][b] for a,b in zip(path,path[1:]))!=answer:
        raise AssertionError("invalid tour witness")
    if n<=8:
        from itertools import permutations
        optimum=min(sum(distances[a][b] for a,b in zip((0,*p),( *p,0)))
                    for p in permutations(range(1,n)))
        if optimum != answer:
            raise AssertionError("exhaustive TSP oracle disagrees")
        evidence="independently_checked_instance"
    else:
        evidence="dp_recurrence_and_tour_checked"
    return CertifiedResult("held_karp_tsp", answer, tuple(path), evidence,
                           "O(n^2 2^n) time; O(n 2^n) space",
                           ("exponential; exhaustive oracle only for n<=8",))


def randomized_quicksort(items: Sequence[int], *, seed: int = 0) -> CertifiedResult:
    """Random pivot quicksort, deterministic seeded replay, independent order/permutation check."""
    import random
    _size(len(items), limit=1500)
    if not _integer(seed,upper=2**32-1) or any(type(x)is not int for x in items):
        raise ContractError("integer keys and deterministic 32-bit seed required")
    rng=random.Random(seed)
    def recurse(arr):
        if len(arr)<2: return arr
        pivot=arr[rng.randrange(len(arr))]
        small=[x for x in arr if x<pivot]
        equal=[x for x in arr if x==pivot]
        big=[x for x in arr if x>pivot]
        return recurse(small)+equal+recurse(big)
    output=tuple(recurse(list(items)))
    if output != tuple(sorted(items)):
        raise AssertionError("quicksort oracle disagrees")
    return CertifiedResult("randomized_quicksort", output, {"seed":seed},
                           "independently_checked_instance", "expected O(n log n); worst O(n^2)",
                           ("not cryptographic randomness",))


def treewidth_independent_set(n: int, edges: Sequence[tuple[int,int]],
                              bags: Sequence[Sequence[int]], bag_tree: Sequence[tuple[int,int]],
                              weights: Sequence[int] | None = None) -> CertifiedResult:
    """Exact weighted independent set on a supplied, verified tree decomposition.

    The trusted verifier checks edge coverage, running intersection, the bag-tree
    topology and an independent exhaustive optimum for n<=17. The cost of
    obtaining the tree decomposition is excluded.
    """
    _size(n,limit=120)
    if n==0 or not 1<=len(bags)<=200 or len(bag_tree)!=len(bags)-1:
        raise ContractError("nonempty decomposition tree required")
    if weights is None:
        weights=[1]*n
    if len(weights)!=n or any(not _integer(w,upper=10**9) for w in weights):
        raise ContractError("invalid weights")
    es=_edges(n,edges,limit=2000,weighted=False,directed=False)
    if any(u==v for u,v,_ in es):
        raise ContractError("self-loops unsupported")
    bs=[]
    for row in bags:
        if len(row)>10 or len(row)!=len(set(row)) or any(not _integer(x,upper=n-1) for x in row):
            raise ContractError("invalid bag or width budget")
        bs.append(tuple(row))
    adj=[[] for _ in bs]
    for pair in bag_tree:
        if not isinstance(pair,(tuple,list)) or len(pair)!=2 or any(
            not _integer(x,upper=len(bs)-1) for x in pair) or pair[0]==pair[1]:
            raise ContractError("invalid decomposition-tree edge")
        u,v=pair
        adj[u].append(v);adj[v].append(u)
    par=[-1]*len(bs);order=[0]; visited={0}
    for u in order:
        for v in adj[u]:
            if v==par[u]: continue
            if v in visited: raise ContractError("bag graph contains cycle")
            visited.add(v);par[v]=u;order.append(v)
    if len(visited)!=len(bs): raise ContractError("bag graph disconnected")
    for v in range(n):
        present={i for i,row in enumerate(bs) if v in row}
        if not present: raise ContractError("vertex missing from decomposition")
        todo=[next(iter(present))]; found=set(todo)
        for u in todo:
            for w in adj[u]:
                if w in present and w not in found:
                    found.add(w);todo.append(w)
        if found!=present: raise ContractError("running intersection property violated")
    for u,v,_ in es:
        if not any(u in row and v in row for row in bs):
            raise ContractError("edge absent from every bag")
    dp:dict[int,dict[int,int]]={}
    chosen_child:dict[tuple[int,int,int],int]={}
    for t in reversed(order):
        bag=bs[t]
        pbag=set(bs[par[t]]) if par[t]>=0 else set()
        fresh={v for v in bag if v not in pbag}
        local_edges=[(bag.index(u),bag.index(v)) for u,v,_ in es if u in bag and v in bag]
        d={}
        for mask in range(1<<len(bag)):
            if any(mask>>u&1 and mask>>v&1 for u,v in local_edges): continue
            score=sum(weights[v] for i,v in enumerate(bag) if v in fresh and mask>>i&1)
            feasible=True
            for child in adj[t]:
                if par[child]!=t: continue
                overlap=set(bag)&set(bs[child])
                expected={v for i,v in enumerate(bag) if v in overlap and mask>>i&1}
                options=[(value, childmask) for childmask,value in dp[child].items()
                         if {v for i,v in enumerate(bs[child]) if v in overlap and childmask>>i&1}==expected]
                if not options:
                    feasible=False; break
                value, childmask=max(options, key=lambda item:(item[0],-item[1]))
                score+=value
                chosen_child[t,mask,child]=childmask
            if feasible: d[mask]=score
        if not d: raise AssertionError("no feasible bag assignments")
        dp[t]=d
    rootmask=max(dp[0],key=lambda m:(dp[0][m],-m))
    optimum=dp[0][rootmask]
    selected=set();pending=[(0,rootmask)]
    while pending:
        t,mask=pending.pop();bag=bs[t]
        pbag=set(bs[par[t]]) if par[t]>=0 else set()
        selected.update(v for i,v in enumerate(bag) if v not in pbag and mask>>i&1)
        pending.extend((v,chosen_child[t,mask,v]) for v in adj[t] if par[v]==t)
    if any(u in selected and v in selected for u,v,_ in es) or sum(weights[v] for v in selected)!=optimum:
        raise AssertionError("invalid reconstructed independent set")
    if n<=17:
        brute=max(sum(weights[i] for i in range(n) if mask>>i&1) for mask in range(1<<n)
                  if all(not(mask>>u&1 and mask>>v&1) for u,v,_ in es))
        if brute!=optimum: raise AssertionError("independent optimum oracle disagrees")
        status="independently_checked_instance"
    else:
        status="decomposition_and_dp_recurrence_checked"
    return CertifiedResult("treewidth_independent_set",optimum,tuple(sorted(selected)),status,
                           "O(bags * 2^width * poly(width, degree)) for given decomposition",
                           ("decomposition discovery cost not included",))