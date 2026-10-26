# SPDX-License-Identifier: Apache-2.0
"""g6.causal's graph criteria against hand-worked textbook graphs, and d-separation and minimal adjustment sets against
an independent brute force written here: every simple path enumerated and judged by the path rules, every subset of
candidates tried. The brute force shares no code with the package."""
from __future__ import annotations

import itertools
import random

import pytest

from g6.causal import criteria
from g6.causal.graph import CausalGraph, read_graph

CONFOUNDER = read_graph("digraph { Z -> T; Z -> Y; T -> Y }")
M_GRAPH = read_graph("digraph { A -> T; A -> M; B -> M; B -> Y; T -> Y }")
FRONTDOOR = read_graph("digraph { U [observed=no]; U -> T; U -> Y; T -> M -> Y }")
INSTRUMENT = read_graph("digraph { U [observed=no]; W -> T; U -> T; U -> Y; T -> Y }")
HIDDEN = read_graph("digraph { U [observed=no]; U -> T; U -> Y; T -> Y }")


# ---- the brute force: simple paths in the skeleton, each judged by the path rules -----------------------------------

def _descendants(edges: set[tuple[str, str]], node: str) -> set[str]:
    found, stack = set(), [node]
    while stack:
        current = stack.pop()
        for a, b in edges:
            if a == current and b not in found:
                found.add(b)
                stack.append(b)
    return found


def _paths(edges: set[tuple[str, str]], start: str, end: str) -> list[list[str]]:
    around: dict[str, set[str]] = {}
    for a, b in edges:
        around.setdefault(a, set()).add(b)
        around.setdefault(b, set()).add(a)
    out: list[list[str]] = []

    def walk(path: list[str]) -> None:
        if path[-1] == end:
            out.append(list(path))
            return
        for nxt in sorted(around.get(path[-1], ())):
            if nxt not in path:
                path.append(nxt)
                walk(path)
                path.pop()

    walk([start])
    return out


def _open(edges: set[tuple[str, str]], path: list[str], given: set[str]) -> bool:
    for before, node, after in zip(path, path[1:], path[2:]):
        collider = (before, node) in edges and (after, node) in edges
        if collider and not ({node} | _descendants(edges, node)) & given:
            return False
        if not collider and node in given:
            return False
    return True


def _brute_separated(graph: CausalGraph, xs: set[str], ys: set[str], given: set[str]) -> bool:
    edges = set(graph.edges)
    return not any(_open(edges, p, given) for x in xs for y in ys for p in _paths(edges, x, y))


def _brute_backdoor(graph: CausalGraph, t: str, y: str, zs: set[str]) -> bool:
    edges = set(graph.edges)
    if zs & _descendants(edges, t):
        return False
    return not any(_open(edges, p, zs) for p in _paths(edges, t, y) if (p[1], t) in edges)


def _brute_minimal_sets(graph: CausalGraph, t: str, y: str) -> list[list[str]]:
    edges = set(graph.edges)
    pool = [n for n in graph.observed if n not in (t, y) and n not in _descendants(edges, t)]
    valid = [set(c) for k in range(len(pool) + 1) for c in itertools.combinations(pool, k)
             if _brute_backdoor(graph, t, y, set(c))]
    minimal = [s for s in valid if not any(other < s for other in valid)]
    return sorted((sorted(s) for s in minimal), key=lambda s: (len(s), s))


def _random_dag(rng: random.Random, n: int, p: float, hidden: float) -> CausalGraph:
    names = [f"V{i}" for i in range(n)]
    edges = tuple((a, b) for i, a in enumerate(names) for b in names[i + 1:] if rng.random() < p)
    unobserved = frozenset(v for v in names if rng.random() < hidden)
    return CausalGraph(tuple(names), edges, unobserved)


# ---- d-separation ---------------------------------------------------------------------------------------------------

def test_d_separation_agrees_with_the_brute_force_on_random_graphs() -> None:
    rng = random.Random(20261020)
    checked = 0
    for _ in range(400):
        graph = _random_dag(rng, rng.randint(3, 8), rng.uniform(0.15, 0.6), 0.0)
        nodes = list(graph.nodes)
        rng.shuffle(nodes)
        k = rng.randint(0, len(nodes) - 2)
        xs, ys, given = {nodes[0]}, {nodes[1]}, set(nodes[2:2 + k])
        if rng.random() < 0.3 and len(nodes) > 3 + k:
            xs.add(nodes[2 + k])
        assert criteria.d_separated(graph, xs, ys, given) == _brute_separated(graph, xs, ys, given), (
            graph.edges, xs, ys, given)
        checked += 1
    assert checked == 400


def test_the_brute_force_catches_a_planted_wrong_d_separation() -> None:
    """Survival control: an algorithm that ignores colliders disagrees with the brute force, so agreement above means
    something."""
    def collider_blind(graph: CausalGraph, xs: set[str], ys: set[str], given: set[str]) -> bool:
        edges = set(graph.edges)
        return not any(all(n not in given for n in p[1:-1]) for x in xs for y in ys for p in _paths(edges, x, y))

    assert collider_blind(M_GRAPH, {"A"}, {"B"}, set()) != _brute_separated(M_GRAPH, {"A"}, {"B"}, set())


@pytest.mark.parametrize(("graph", "xs", "ys", "given", "separated"), [
    (CONFOUNDER, {"T"}, {"Y"}, {"Z"}, False),                        # the direct edge
    (CONFOUNDER.without_edges_out_of({"T"}), {"T"}, {"Y"}, {"Z"}, True),
    (CONFOUNDER.without_edges_out_of({"T"}), {"T"}, {"Y"}, set(), False),
    (M_GRAPH, {"A"}, {"B"}, set(), True),                            # M is a collider
    (M_GRAPH, {"A"}, {"B"}, {"M"}, False),                           # adjusting for it opens the path
    (M_GRAPH, {"T"}, {"B"}, {"M", "A"}, True),                       # ...and adjusting for A closes it again
    (FRONTDOOR, {"T"}, {"Y"}, {"M"}, False),                         # through U
    (FRONTDOOR, {"T"}, {"Y"}, {"M", "U"}, True),
])
def test_textbook_d_separations(graph, xs, ys, given, separated: bool) -> None:
    assert criteria.d_separated(graph, xs, ys, given) is separated
    assert _brute_separated(graph, xs, ys, given) is separated


def test_an_open_path_is_shown_when_nodes_are_connected() -> None:
    path = criteria.open_path(M_GRAPH, {"A"}, {"B"}, {"M"})
    assert path == ["A", "M", "B"] and criteria.render_path(M_GRAPH, path) == "A -> M <- B"
    assert criteria.open_path(M_GRAPH, {"A"}, {"B"}, set()) is None


# ---- backdoor paths and adjustment sets ----------------------------------------------------------------------------

def test_the_confounder_triangle() -> None:
    paths = criteria.backdoor_paths(CONFOUNDER, "T", "Y")
    assert [p["rendered"] for p in paths] == ["T <- Z -> Y"] and paths[0]["open"] is True
    blocked = criteria.backdoor_paths(CONFOUNDER, "T", "Y", {"Z"})[0]
    assert blocked["open"] is False and blocked["blocked_by"] == ["Z"]
    assert criteria.adjustment_sets(CONFOUNDER, "T", "Y") == [["Z"]]
    assert criteria.is_backdoor_set(CONFOUNDER, "T", "Y", set()) is False
    assert criteria.instruments(CONFOUNDER, "T", "Y") == []


def test_the_m_graph_where_adjusting_for_the_collider_opens_a_path() -> None:
    path = criteria.backdoor_paths(M_GRAPH, "T", "Y")[0]
    assert path["rendered"] == "T <- A -> M <- B -> Y" and path["open"] is False and path["colliders"] == ["M"]
    opened = criteria.backdoor_paths(M_GRAPH, "T", "Y", {"M"})[0]
    assert opened["open"] is True
    assert criteria.adjustment_sets(M_GRAPH, "T", "Y") == [[]]             # nothing needs adjusting
    assert criteria.is_backdoor_set(M_GRAPH, "T", "Y", {"M"}) is False     # the collider alone is wrong
    assert criteria.is_backdoor_set(M_GRAPH, "T", "Y", {"M", "A"}) is True
    assert criteria.is_backdoor_set(M_GRAPH, "T", "Y", {"M", "B"}) is True


def test_a_descendant_of_the_treatment_is_never_an_adjustment() -> None:
    graph = read_graph("digraph { Z -> T; Z -> Y; T -> M -> Y; T -> D }")
    assert criteria.is_backdoor_set(graph, "T", "Y", {"Z", "D"}) is False
    assert criteria.adjustment_sets(graph, "T", "Y") == [["Z"]]


def test_minimal_adjustment_sets_agree_with_the_brute_force_on_random_graphs() -> None:
    rng = random.Random(7)
    compared = 0
    while compared < 150:
        graph = _random_dag(rng, rng.randint(4, 8), rng.uniform(0.2, 0.55), 0.2)
        observed = list(graph.observed)
        if len(observed) < 2:
            continue
        t, y = sorted(rng.sample(observed, 2), key=graph.nodes.index)
        assert criteria.adjustment_sets(graph, t, y) == _brute_minimal_sets(graph, t, y), (graph.edges,
                                                                                            graph.unobserved, t, y)
        compared += 1


# ---- frontdoor, instruments and identification ---------------------------------------------------------------------

def test_frontdoor_identification() -> None:
    assert criteria.adjustment_sets(FRONTDOOR, "T", "Y") == []
    assert criteria.frontdoor_sets(FRONTDOOR, "T", "Y") == [["M"]]
    found = criteria.identify(FRONTDOOR, "T", "Y")
    assert found["identifiable"] is True and found["route"] == "frontdoor" and found["frontdoor"]["chosen"] == ["M"]
    assert "P(M | T=t)" in found["estimand"]
    assert criteria.is_frontdoor_set(FRONTDOOR, "T", "Y", {"M"}) is True


def test_a_frontdoor_set_must_intercept_every_directed_path() -> None:
    graph = read_graph("digraph { U [observed=no]; U -> T; U -> Y; T -> M -> Y; T -> Y }")
    assert criteria.frontdoor_sets(graph, "T", "Y") == []
    assert criteria.identify(graph, "T", "Y")["identifiable"] is False


def test_an_instrument() -> None:
    assert criteria.instruments(INSTRUMENT, "T", "Y") == ["W"]
    assert criteria.adjustment_sets(INSTRUMENT, "T", "Y") == []
    found = criteria.identify(INSTRUMENT, "T", "Y")
    assert found["identifiable"] is True and found["route"] == "iv" and found["iv"]["instruments"] == ["W"]
    assert "Cov(Y, W) / Cov(T, W)" in found["estimand"] and "same for every unit" in found["estimand"]


def test_an_instrument_need_not_be_a_parent_and_a_confounded_one_is_not_an_instrument() -> None:
    upstream = read_graph("digraph { U [observed=no]; Q [observed=no]; Z -> Q -> T; U -> T; U -> Y; T -> Y }")
    assert criteria.instruments(upstream, "T", "Y") == ["Z"]
    confounded = read_graph("digraph { U [observed=no]; A [observed=no]; A -> W; A -> Y; W -> T; U -> T; U -> Y; "
                            "T -> Y }")
    assert criteria.instruments(confounded, "T", "Y") == []


def test_a_graph_with_an_unobserved_confounder_is_not_identifiable_and_says_why() -> None:
    found = criteria.identify(HIDDEN, "T", "Y")
    assert found["identifiable"] is False and found["route"] is None and found["estimand"] is None
    assert "T <- U -> Y" in found["reason"] and "unobserved" in found["reason"]
    assert "do-calculus" in found["reason"]


def test_backdoor_identification_writes_out_the_estimand() -> None:
    found = criteria.identify(CONFOUNDER, "T", "Y")
    assert found["route"] == "backdoor" and found["backdoor"]["chosen"] == ["Z"]
    assert found["estimand"] == "E[Y | do(T=t)] = sum over Z of E[Y | T=t, Z] * P(Z)"
    empty = criteria.identify(read_graph("digraph { T -> Y }"), "T", "Y")
    assert empty["estimand"] == "E[Y | do(T=t)] = E[Y | T=t]" and empty["backdoor"]["chosen"] == []


def test_no_directed_path_means_no_effect() -> None:
    found = criteria.identify(read_graph("digraph { Z -> T; Z -> Y }"), "T", "Y")
    assert found["identifiable"] is True and found["route"] == "no_directed_path"
    assert found["estimand"] == "P(Y | do(T=t)) = P(Y)"
