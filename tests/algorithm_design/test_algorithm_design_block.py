# SPDX-License-Identifier: Apache-2.0
"""The algorithm_design block over MCP (P1, PR #2): catalogue patterns, ontology metadata, bounded search and fusion
answer through ``g6_invoke`` as proposals that grant nothing, each refusal with its own code; the bundled data cites
sections and printed pages, never PDF pages or the book's own section titles."""
from __future__ import annotations

import json
import time

import pytest

from g6.algorithm_design.knowledge import bundled_ontology, bundled_problem_index
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

SEARCH = {"representations": ["sequence"], "target": "membership"}
FILES = {"shop/__init__.py": "", "shop/api.py": "from shop import state\ndef read():\n    return state.value\n",
         "shop/state.py": "value = 1\ndef update():\n    global value\n    value = 2\n"}
REQ = [{"id": "r1", "text": "Preserve authorization", "authority": "person_confirmed", "source": "person",
        "kind": "preserve"}]
OBL = [{"id": "o1", "requirement_id": "r1", "expectation": "No cross-account revocation", "oracle_source": "person",
        "normative": True}]
BASE = {"files": FILES, "package": "shop", "requirements": REQ, "obligations": OBL, "environment_digest": "env-v1"}


def test_primitives_lists_the_nine_catalogue_patterns():
    reply = invoke_reply("algorithm_design", "primitives", "{}")
    r = reply["result"]
    assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"
    assert len(r["primitives"]) == 9 and {"linear_scan", "binary_search", "dijkstra"} <= {p["id"] for p in r["primitives"]}
    assert r["warning_card"]["code"] == "G6_E_ALGO_CATALOGUE"


def test_ontology_is_metadata_and_never_assumes_a_precondition():
    got = invoke_reply("algorithm_design", "ontology", json.dumps({"problem_id": "interval_scheduling"}))["result"]
    assert {m["implementation_status"] for m in got["matches"]} == {"metadata_only"}
    assert any(m["status"] == "needs_preconditions" for m in got["matches"])


def test_search_proposes_candidates_only():
    r = invoke_reply("algorithm_design", "search", json.dumps(SEARCH))["result"]
    assert r["candidates"][0]["steps"] == ["linear_scan"] and {c["status"] for c in r["candidates"]} == {"candidate_only"}
    assert r["completion_state"] == "qualified-draft" and r["warning_card"]["code"] == "G6_E_ALGO_PROPOSAL"
    none = invoke_reply("algorithm_design", "search",
                        json.dumps({"representations": ["weighted_graph"], "target": "shortest_paths"}))
    assert none["warning_card"]["code"] == "G6_E_ALGO_NO_CANDIDATE"
    assert any("nonnegative_weights" in x for x in none["result"]["rejected"])


def test_fuse_binds_a_searched_candidate_to_the_frozen_intent_and_grants_nothing():
    found = invoke_reply("algorithm_design", "search", json.dumps(SEARCH))["result"]["candidates"][0]["id"]
    reply = invoke_reply("algorithm_design", "fuse",
                         json.dumps({**SEARCH, **BASE, "candidate_id": found, "changed": ["shop.state"]}))
    r = reply["result"]
    assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"
    assert r["normative_obligations"] == ["o1"] and "o1" in r["check_plan"]
    assert r["approval"] is False and r["ready_for_unrestricted_action"] is False
    assert r["evidence"][0]["declared_by"] == "caller"
    unknown = invoke_reply("algorithm_design", "fuse", json.dumps({**SEARCH, **BASE, "candidate_id": "nope"}))
    assert unknown["warning_card"]["code"] == "G6_E_ALGO_UNKNOWN_CANDIDATE"


@pytest.mark.parametrize("op", ["ontology", "search", "fuse"])
def test_an_op_with_no_parameters_is_refused_with_its_own_code(op):
    reply = invoke_reply("algorithm_design", op, "{}")
    assert reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []
    assert reply["warning_card"]["code"] == "G6_E_ALGO_INPUT_INVALID"


def _keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _keys(v)


def test_the_bundled_data_cites_sections_and_printed_pages_only():
    ontology, index = bundled_ontology(), bundled_problem_index()
    assert (len(ontology["problems"]), len(ontology["algorithms"]), len(index["problems"])) == (16, 22, 75)
    assert "pdf_page" not in set(_keys(ontology)) | set(_keys(index))
    for row in index["problems"]:                       # cited by section, described in G6's own words (ruling 5)
        assert "title" not in row and row["description"] and row["source"]["section"] == row["section"]
        assert set(row["source"]) == {"book_id", "section", "printed_page"}


# --- Task 2 (PR #3): the kernels through one invoke path, cost estimates, typed primitives from the catalogue ---------

#: Each kernel's contract example from tests/algorithm_design/test_checked_kernels.py, with its arguments named by the
#: kernel's signature, plus the three kernels that came first (kernels.py).
KERNEL_EXAMPLES = [
    ("bfs", {"n": 3, "edges": [[0, 1]]}),
    ("dijkstra", {"n": 3, "edges": [[0, 1, 3], [1, 2, 1], [0, 2, 9]]}),
    ("kahn", {"n": 2, "edges": [[0, 1], [1, 0]]}),
    ("weighted_interval_dp", {"intervals": [[0, 2, 4], [2, 4, 9], [0, 4, 10]]}),
    ("gale_shapley", {"proposers": [[0, 1], [0, 1]], "receivers": [[0, 1], [0, 1]]}),
    ("edmonds_karp", {"n": 3, "edges": [[0, 1, 3], [1, 2, 2]]}),
    ("kruskal", {"n": 3, "edges": [[0, 1, 1], [1, 2, 2], [0, 2, 10]]}),
    ("vertex_cover_2approx", {"n": 3, "edges": [[0, 1], [1, 2]]}),
    ("tree_independent_set", {"n": 3, "edges": [[0, 1], [1, 2]]}),
    ("subset_sum_mitm", {"weights": [2, 3, 8], "target": 5}),
    ("knapsack_dp", {"weights": [2, 3], "values": [3, 5], "capacity": 4}),
    ("bellman_ford", {"n": 3, "edges": [[0, 1, 1], [1, 2, -3]]}),
    ("vertex_cover_fpt", {"n": 3, "edges": [[0, 1], [1, 2]], "k": 1}),
    ("set_cover_greedy", {"universe": [1, 2], "sets": [[1], [2]]}),
    ("sequence_align_dp", {"left": "cat", "right": "cut"}),
    ("huffman", {"weights": [4, 4]}),
    ("held_karp_tsp", {"distances": [[0, 2, 4], [2, 0, 3], [4, 3, 0]]}),
    ("randomized_quicksort", {"items": [9, 1, 1]}),
    ("treewidth_independent_set", {"n": 3, "edges": [[0, 1], [1, 2]], "bags": [[0, 1], [1, 2]], "bag_tree": [[0, 1]]}),
    ("subset_sum_dp", {"weights": [3, 7, 10], "target": 13}),
    ("interval_earliest_finish", {"intervals": [[0, 5], [0, 2], [2, 4], [4, 7]]}),
    ("merge_sort", {"items": [3, 1, 2]}),
]


def test_list_kernels_names_all_22_through_one_entrypoint():
    r = invoke_reply("algorithm_design", "list_kernels", "{}")["result"]
    assert len(r["kernels"]) == 22 and {k["entrypoint"] for k in r["kernels"]} == {"g6.algorithm_design.invocation.invoke"}
    assert {k["algorithm_id"] for k in r["kernels"]} == {a for a, _ in KERNEL_EXAMPLES}


@pytest.mark.parametrize("algorithm_id,parameters", KERNEL_EXAMPLES)
def test_every_kernel_runs_over_mcp_and_says_how_its_answer_was_checked(algorithm_id, parameters):
    r = invoke_reply("algorithm_design", "run_kernel", json.dumps({"algorithm_id": algorithm_id, "parameters": parameters}))
    assert r["completion_state"] == "qualified-draft" and r["warning_card"]["code"] == "G6_E_ALGO_KERNEL_CHECKED"
    assert r["result"]["verification"] and r["result"]["algorithm_id"] == algorithm_id and check_envelope(r) == []


@pytest.mark.parametrize("algorithm_id,parameters", [                  # Review Focus 5: one past each stated bound
    ("held_karp_tsp", {"distances": [[0] * 15 for _ in range(15)]}),
    ("subset_sum_mitm", {"weights": [1] * 33, "target": 3}),
    ("vertex_cover_fpt", {"n": 31, "edges": [], "k": 1}),
    ("no_such_kernel", {})])
def test_a_kernel_past_its_bound_is_refused_quickly(algorithm_id, parameters):
    started = time.monotonic()
    r = invoke_reply("algorithm_design", "run_kernel", json.dumps({"algorithm_id": algorithm_id, "parameters": parameters}))
    assert r["warning_card"]["code"] == "G6_E_ALGO_KERNEL_REFUSED" and time.monotonic() - started < 2
    assert r["completion_state"] == "blocked-escalated" and check_envelope(r) == []


def test_more_than_16_parameter_keys_is_refused_at_the_door():
    params = {"algorithm_id": "bfs", "parameters": {f"k{i}": 0 for i in range(17)}}
    assert invoke_reply("algorithm_design", "run_kernel", json.dumps(params))["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


def test_estimate_refuses_what_the_device_disallows_and_what_it_cannot_model():
    device = {"ram_bytes": 8_000_000, "scratch_bytes": 0, "max_operations": 1_000_000, "allow_pseudopolynomial": False}
    r = invoke_reply("algorithm_design", "estimate", json.dumps({"algorithms": ["knapsack_dp", "unknown_primitive"],
                                                                "dimensions": {"n": 100, "W": 1000}, "device": device}))["result"]
    by = {e["algorithm_id"]: e for e in r["estimates"]}
    assert not by["knapsack_dp"]["eligible"] and "pseudo_polynomial_disallowed" in by["knapsack_dp"]["reasons"]
    assert by["unknown_primitive"]["reasons"] == ["missing_hard_coded_cost_model"] and r["warning_card"]["code"] == "G6_E_ALGO_ESTIMATE"


def test_search_with_a_problem_id_adds_only_reviewed_typed_primitives():
    p = {"representations": ["intervals"], "target": "maximum_compatible_intervals",
         "established_properties": ["unit_reward", "intervals"], "problem_id": "interval_scheduling"}
    r = invoke_reply("algorithm_design", "search", json.dumps(p))["result"]
    assert any(c["steps"] == ["interval_earliest_finish"] for c in r["candidates"])
