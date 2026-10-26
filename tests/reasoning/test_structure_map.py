# SPDX-License-Identifier: Apache-2.0
import importlib
import pytest


def graph(prefix, relation="feeds"):
    return {"nodes": {prefix + "a": "source", prefix + "b": "sink"},
            "edges": [(prefix + "a", prefix + "b", relation)]}


def test_labelled_structure_maps_despite_different_names():
    # Removing structure.py's VF2 mapping inversion makes this fail (DEEP_UNDERSTANDING-01).
    pytest.importorskip("networkx")
    from g6.reasoning import structure_map

    result = structure_map(graph("copper"), graph("river"))
    assert result["mapping"] == {"coppera": "rivera", "copperb": "riverb"}
    assert result["verdict"] == "isomorphic"
    assert result["unmapped_relations"] == [] and result["completion_state"] == "qualified-draft"
    assert "confidence" not in result


def test_same_names_with_different_relations_are_not_isomorphic():
    # Removing structure.py's edge-label matcher makes this fail (DEEP_UNDERSTANDING-02, D1).
    pytest.importorskip("networkx")
    from g6.reasoning import structure_map

    result = structure_map(graph("same"), graph("same", "blocks"))
    assert result["verdict"] == "partial(mapped=1/2)" and result["unmapped_relations"]
    assert len(result["mapping"]) == 1
    assert structure_map(graph("a"), {"nodes": {"x": "unrelated"}, "edges": []})["verdict"] == "no_consistent_mapping"


def test_missing_graph_dependency_refuses(monkeypatch):
    # Removing structure.py's import-failure refusal makes this fail (DEEP_UNDERSTANDING-03).
    from g6.reasoning import structure_map
    module = importlib.import_module("g6.reasoning.structure")
    monkeypatch.setattr(module, "_networkx", lambda: (_ for _ in ()).throw(ImportError("missing")))
    result = structure_map(graph("a"), graph("b"))
    assert result["completion_state"] == "blocked-escalated" and result["mapping"] == {}
    assert result["warning_card"]["code"] == "G6_E_GRAPH_NOT_INSTALLED"


def test_failed_isomorphism_dependency_refuses(monkeypatch):
    # Removing structure.py's matcher-failure refusal makes this fail.
    nx = pytest.importorskip("networkx")
    from g6.reasoning import structure_map
    monkeypatch.setattr(nx.algorithms.isomorphism, "DiGraphMatcher",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("broken")))
    result = structure_map(graph("a"), graph("b"))
    assert result["completion_state"] == "blocked-escalated"
    assert result["warning_card"]["code"] == "G6_E_GRAPH_CHECK_FAILED"

def test_graph_loader_failure_blocks(monkeypatch):
    # Removing structure_map's dependency-load exception branch makes this fail.
    import g6.reasoning.structure as structure
    monkeypatch.setattr(structure, "_networkx", lambda: (_ for _ in ()).throw(RuntimeError("broken")))
    result = structure.structure_map({"nodes": {"a": "x"}, "edges": []}, {"nodes": {"b": "x"}, "edges": []})
    assert result["warning_card"]["code"] == "G6_E_GRAPH_CHECK_FAILED"
