# SPDX-License-Identifier: Apache-2.0
"""Bundled catalog is discoverable, structured, and never grants authority."""
from g6.algorithm_design.knowledge import bundled_ontology, bundled_problem_index, search_bundled_ontology


def test_bundled_data_counts():
    ontology = bundled_ontology()
    assert len(ontology["problems"]) == 16
    assert len(ontology["algorithms"]) == 22
    assert len(bundled_problem_index()["problems"]) == 75


def test_provenance_and_metadata_only():
    for row in bundled_ontology()["algorithms"]:
        assert row.get("implementation_status") == "metadata_only"
        assert row.get("id")
    assert bundled_problem_index()["scope"].startswith("catalog section titles only")


def test_preconditions_not_invented():
    ontology = bundled_ontology()
    sample = next((a for a in ontology["algorithms"] if a.get("recognition_rules")), None)
    assert sample is not None
    problem = sample["problem_ids"][0]
    matches = search_bundled_ontology(problem_id=problem, established_features=())
    selected = next(m for m in matches if m.algorithm_id == sample["id"])
    assert selected.status == "needs_preconditions"
    eligible = search_bundled_ontology(problem_id=problem,
        established_features=tuple(sample["recognition_rules"]))
    assert any(m.algorithm_id == sample["id"] and m.status == "eligible_as_proposal" for m in eligible)


def test_fresh_read_avoids_shared_mutation():
    first = bundled_ontology()
    first["problems"].clear()
    assert len(bundled_ontology()["problems"]) == 16
