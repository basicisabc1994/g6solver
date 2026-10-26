# SPDX-License-Identifier: Apache-2.0
"""g6.causal's graph reader: a causal graph as JSON or a strict subset of DOT, read by G6's own parser into data, and
refused with its own code when it does not parse, has a cycle or a self-loop, names a node it never declares, or is
over a size limit."""
from __future__ import annotations

import pytest

from g6.causal.envelope import GRAPH_CYCLE, GRAPH_INVALID, INPUT_TOO_LARGE, SELF_LOOP, UNKNOWN_NODE, Refusal
from g6.causal.graph import MAX_EDGES, MAX_NODES, MAX_TEXT, read_graph


def _refused(spec, code: str) -> str:
    with pytest.raises(Refusal) as caught:
        read_graph(spec)
    assert caught.value.code == code, caught.value.reason
    return caught.value.reason


def test_json_graph_is_read_in_declaration_order() -> None:
    g = read_graph({"nodes": ["Z", "T", "Y", "U"], "edges": [["Z", "T"], ["Z", "Y"], ["T", "Y"], ["U", "Y"]],
                    "unobserved": ["U"]})
    assert g.nodes == ("Z", "T", "Y", "U") and g.unobserved == frozenset({"U"})
    assert g.observed == ("Z", "T", "Y") and g.edges == (("Z", "T"), ("Z", "Y"), ("T", "Y"), ("U", "Y"))
    assert g.parents("Y") == frozenset({"Z", "T", "U"}) and g.children("Z") == frozenset({"T", "Y"})
    assert g.as_dict() == {"nodes": ["Z", "T", "Y", "U"], "edges": [["Z", "T"], ["Z", "Y"], ["T", "Y"], ["U", "Y"]],
                           "unobserved": ["U"]}


def test_dot_graph_with_chains_comments_quotes_and_an_unobserved_node() -> None:
    text = """// a confounded effect
    digraph confounded {
        /* the unobserved cause */
        U [observed=no];
        "Z" -> T -> Y;   // comment to the end of the line
# a line that starts with a hash is a comment too
        Z -> Y
        U -> Y; U -> T
    }"""
    g = read_graph(text)
    assert g.nodes == ("U", "Z", "T", "Y") and g.unobserved == frozenset({"U"})
    assert set(g.edges) == {("Z", "T"), ("T", "Y"), ("Z", "Y"), ("U", "Y"), ("U", "T")}


def test_dot_observed_values_and_repeats() -> None:
    g = read_graph('digraph { A [observed="yes"]; B [observed=false]; C [observed="no"]; A -> B; A -> B; C -> A; }')
    assert g.unobserved == frozenset({"B", "C"}) and g.edges == (("A", "B"), ("C", "A"))


@pytest.mark.parametrize(("text", "says"), [
    ("graph { A -- B }", "undirected"),
    ("digraph { A -- B }", "line 1, column 13"),
    ("digraph { A -> B [label=x]; }", "observed"),
    ("digraph { A [color=red]; }", "observed"),
    ("digraph { A [observed=maybe]; }", "maybe"),
    ("digraph { subgraph s { A -> B } }", "subgraph"),
    ("digraph { node [shape=box]; A -> B }", "node"),
    ("digraph { 1x -> B }", "line 1, column 11"),
    ('digraph { "a b" -> B }', "a b"),
    ("digraph { A -> B ", "end of the text"),
    ("digraph { A -> B } extra", "after the closing brace"),
    ("digraph { A -> }", "line 1, column 16"),
    ("digraph { /* never closed A -> B }", "comment"),
    ('digraph { "A -> B }', "quote"),
    ("strict digraph { A -> B }", "strict"),
    ("", "empty"),
    ("digraph { }", "no nodes"),
])
def test_dot_that_is_not_the_subset_is_refused_with_where(text: str, says: str) -> None:
    assert says in _refused(text, GRAPH_INVALID)


@pytest.mark.parametrize(("spec", "says"), [
    ({"nodes": ["A"], "edges": [], "extra": 1}, "extra"),
    ({"edges": [["A", "B"]]}, "nodes"),
    ({"nodes": [], "edges": []}, "no nodes"),
    ({"nodes": ["A", "B"], "edges": [["A"]]}, "two names"),
    ({"nodes": ["A", "B"], "edges": [["A", 2]]}, "2"),
    ({"nodes": ["A", "b c"], "edges": []}, "b c"),
    ({"nodes": "A", "edges": []}, "list"),
    ('{"nodes": ["A"], "edges": []}', "digraph"),
])
def test_json_that_does_not_fit_is_refused(spec, says: str) -> None:
    assert says in _refused(spec, GRAPH_INVALID)


def test_self_loops_cycles_and_unknown_nodes_are_refused_by_name() -> None:
    assert "A -> A" in _refused("digraph { A -> A }", SELF_LOOP)
    assert "A -> B -> C -> A" in _refused({"nodes": ["A", "B", "C", "D"],
                                           "edges": [["D", "A"], ["A", "B"], ["B", "C"], ["C", "A"]]}, GRAPH_CYCLE)
    assert "A -> B -> A" in _refused("digraph { A -> B; B -> A }", GRAPH_CYCLE)
    assert "'Q'" in _refused({"nodes": ["A", "B"], "edges": [["A", "Q"]]}, UNKNOWN_NODE)
    assert "'U'" in _refused({"nodes": ["A", "B"], "edges": [["A", "B"]], "unobserved": ["U"]}, UNKNOWN_NODE)


def test_size_limits_are_refused_with_their_own_code() -> None:
    nodes = [f"N{i}" for i in range(MAX_NODES + 1)]
    assert str(MAX_NODES) in _refused({"nodes": nodes, "edges": []}, INPUT_TOO_LARGE)
    chain = " ".join(f"N{i} -> N{i + 1};" for i in range(MAX_NODES))
    assert str(MAX_NODES) in _refused("digraph { " + chain + " }", INPUT_TOO_LARGE)
    few = [f"N{i}" for i in range(40)]
    edges = [[a, b] for i, a in enumerate(few) for b in few[i + 1:]][:MAX_EDGES + 1]
    assert str(MAX_EDGES) in _refused({"nodes": few, "edges": edges}, INPUT_TOO_LARGE)
    assert str(MAX_TEXT) in _refused("digraph { A -> B }" + " " * MAX_TEXT, INPUT_TOO_LARGE)


def test_ancestors_descendants_and_surgery() -> None:
    g = read_graph("digraph { A -> B -> C; A -> D; E -> C }")
    assert g.ancestors({"C"}) == {"A", "B", "E"} and g.descendants({"A"}) == {"B", "C", "D"}
    cut = g.without_edges_out_of({"B"})
    assert ("B", "C") not in cut.edges and ("A", "B") in cut.edges and cut.nodes == g.nodes
    assert ("A", "B") not in g.without_edges_into({"B"}).edges
