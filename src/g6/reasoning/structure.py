# SPDX-License-Identifier: Apache-2.0
"""Small labelled directed graph comparisons, with optional VF2 and no name similarity."""
from itertools import combinations


def _networkx():
    import networkx
    return networkx


def _validate(spec):
    if not isinstance(spec, dict) or set(spec) != {"nodes", "edges"}:
        raise ValueError("a graph needs only nodes and edges")
    nodes, edges = spec["nodes"], spec["edges"]
    if not isinstance(nodes, dict) or not 1 <= len(nodes) <= 7:
        raise ValueError("a graph needs 1 to 7 labelled nodes")
    if any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in nodes.items()):
        raise ValueError("node ids and labels must be nonempty strings")
    if not isinstance(edges, (list, tuple)) or len(edges) > 49:
        raise ValueError("at most 49 edges")
    seen = set()
    for edge in edges:
        if not isinstance(edge, (list, tuple)) or len(edge) != 3:
            raise ValueError("edges are source, target, label triples")
        a, b, label = edge
        if not all(isinstance(x, str) for x in edge) or a not in nodes or b not in nodes or not label:
            raise ValueError("edge endpoints must exist and labels must be nonempty")
        if (a, b) in seen:
            raise ValueError("parallel edges are not supported")
        seen.add((a, b))
    return nodes, edges


def structure_map(source: dict, target: dict) -> dict:
    """Return the largest induced labelled mapping for graphs of at most seven nodes.

    Node ids are names only; node and edge labels must match exactly. Exhaustive
    subset search is deliberately bounded. A partial mapping is not equivalence.
    """
    def refused(code, message):
        return {"mapping": {}, "unmapped_relations": [], "verdict": "unchecked",
                "completion_state": "blocked-escalated", "warning_card": {"code": code, "message": message},
                "evidence": {"method": "VF2_labelled_directed"}, "request_id": "", "run_id": ""}
    try:
        sn, se = _validate(source)
        tn, te = _validate(target)
    except (ValueError, TypeError):
        return refused("G6_E_GRAPH_INPUT", "invalid labelled graph; use 1 to 7 nodes and unique directed edges")
    try:
        nx = _networkx()
    except ImportError:
        return refused("G6_E_GRAPH_NOT_INSTALLED", "install g6[graph] to compare structures")
    except Exception:
        return refused("G6_E_GRAPH_CHECK_FAILED", "the graph dependency could not load")
    try:
        def build(nodes, edges):
            graph = nx.DiGraph()
            graph.add_nodes_from((key, {"label": label}) for key, label in sorted(nodes.items()))
            graph.add_edges_from((a, b, {"label": label}) for a, b, label in edges)
            return graph
        sg, tg = build(sn, se), build(tn, te)
        mapping = {}
        for size in range(min(len(sn), len(tn)), 0, -1):
            for subset in combinations(sorted(sn), size):
                matcher = nx.algorithms.isomorphism.DiGraphMatcher(
                    tg, sg.subgraph(subset), node_match=lambda a, b: a["label"] == b["label"],
                    edge_match=lambda a, b: a["label"] == b["label"])
                match = next(matcher.subgraph_isomorphisms_iter(), None)
                if match is not None:
                    mapping = {v: k for k, v in match.items()}
                    break
            if mapping:
                break
        missing = [list(edge) for edge in se if edge[0] not in mapping or edge[1] not in mapping or
                   not tg.has_edge(mapping[edge[0]], mapping[edge[1]]) or
                   tg.edges[mapping[edge[0]], mapping[edge[1]]]["label"] != edge[2]]
    except Exception:
        return refused("G6_E_GRAPH_CHECK_FAILED", "the isomorphism dependency failed; no analogy was accepted")
    verdict = "isomorphic" if len(mapping) == len(sn) == len(tn) and not missing else (
        f"partial(mapped={len(mapping)}/{len(sn)})" if mapping else "no_consistent_mapping")
    return {"mapping": mapping, "unmapped_relations": missing, "verdict": verdict,
            "completion_state": "qualified-draft", "warning_card": None, "request_id": "", "run_id": "",
            "evidence": {"method": "VF2_labelled_directed", "not_established": "truth of the supplied domain models"}}
