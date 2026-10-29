# SPDX-License-Identifier: Apache-2.0
"""The algorithm_design block over MCP (P1, PR #2): catalogue patterns, ontology metadata, bounded search and fusion
answer through ``g6_invoke`` as proposals that grant nothing, each refusal with its own code; the bundled data cites
sections and printed pages, never PDF pages or the book's own section titles."""
from __future__ import annotations

import json

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
