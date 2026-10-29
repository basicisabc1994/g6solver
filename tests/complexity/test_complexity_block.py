# SPDX-License-Identifier: Apache-2.0
"""The complexity block over MCP (P1, PR #2): the change-impact map answers through ``g6_invoke`` as a qualified draft
bound to the tree it read, keeps a caller's authority as the caller's word, and refuses bad or oversized input with its
own code."""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

import g6.complexity
from g6.complexity import build_atlas
from g6.complexity.inputs import ObligationIn, RequirementIn, intent_from
from g6.core import discovery
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

FILES = {"shop/__init__.py": "", "shop/api.py": "from shop import state\ndef read():\n    return state.value\n",
         "shop/state.py": "value = 1\ndef update():\n    global value\n    value = 2\n"}
REQ = [{"id": "r1", "text": "Preserve authorization", "authority": "person_confirmed", "source": "person",
        "kind": "preserve"}]
OBL = [{"id": "o1", "requirement_id": "r1", "expectation": "No cross-account revocation", "oracle_source": "person",
        "normative": True}]
BASE = {"files": FILES, "package": "shop", "requirements": REQ, "obligations": OBL, "environment_digest": "env-v1"}


def test_atlas_answers_over_mcp_as_a_qualified_draft_bound_to_the_tree():
    reply = invoke_reply("complexity", "atlas", json.dumps(BASE))
    assert reply["ok"] and check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"
    r = reply["result"]
    assert (r["component"], r["op"], r["warning_card"]["code"]) == ("complexity", "atlas", "G6_E_COMPLEXITY_ATLAS")
    intent = intent_from([RequirementIn(**REQ[0])], [ObligationIn(**OBL[0])])
    assert r["fingerprint"] == build_atlas(FILES, package="shop", intent=intent, environment_digest="env-v1").fingerprint
    assert "shop.state" in r["components"] and r["complete"] is (not r["unknowns"])
    assert r["evidence"][0]["declared_by"] == "caller"          # Review Focus 4: the authority is the caller's word


def test_impact_names_the_reverse_cone_and_keeps_every_normative_obligation():
    r = invoke_reply("complexity", "impact", json.dumps({**BASE, "changed": ["shop.state"]}))["result"]
    assert "shop.api" in r["impacted"] and r["preservation_obligations"] == ["o1"] and r["parallel_safe"] is False


def test_simplify_proposes_only_for_state_the_caller_marked_avoidable():
    notes = {"shop.state.value": {"essentiality": "accidental_avoidable", "derivation": "read through api",
                                  "justification": "cache"}}
    r = invoke_reply("complexity", "simplify", json.dumps({**BASE, "state_annotations": notes}))["result"]
    assert [p["target"] for p in r["proposals"]] == ["shop.state.value"]
    assert {p["status"] for p in r["proposals"]} == {"proposal_only"} and "o1" in r["proposals"][0]["required_checks"]


@pytest.mark.parametrize("params", [
    {}, {**BASE, "files": {}}, {**BASE, "environment_digest": ""},
    {**BASE, "package": "not a package"},
    {**BASE, "files": {f"big/m{i}.py": "x" * 900_000 for i in range(5)}},            # Review Focus 5: 4.5 MB
    {**BASE, "requirements": [{**REQ[0], "authority": "model_hypothesis"}]}])         # a model cannot make it normative
@pytest.mark.parametrize("op", ["atlas", "impact", "simplify"])
def test_bad_input_is_refused_with_its_own_code(op, params):
    reply = invoke_reply("complexity", op, json.dumps(params))
    assert reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []
    assert reply["warning_card"]["code"] in ("G6_E_COMPLEXITY_INPUT_INVALID", "G6_E_INPUT_VALIDATION")


_RUNS = {"subprocess", "socket", "importlib"}
_CALLS = {"exec", "eval", "compile"}


def test_it_is_a_registered_beta_block_that_runs_no_code():
    info = discovery.describe("complexity")
    assert (info["block"], info["maturity"], sorted(info["ops"]), info["sensitive_ops"]) == \
        ("ComplexityBlock", "beta", ["atlas", "impact", "simplify"], [])
    for path in sorted(Path(g6.complexity.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & _RUNS, (path.name, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in _RUNS, (path.name, node.lineno)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in _CALLS, (path.name, node.lineno)


# --- final review: work stays near linear inside the 4,000,000-character bound, and no input escapes as a raw error ---

def test_many_functions_sharing_print_and_calling_each_other_answer_quickly():
    n = 6000
    body = "".join(f"def f{i}():\n    print()\n    f{(i + 1) % n}()\n" for i in range(n))
    files = {"big/__init__.py": "", "big/m.py": body}
    started = time.monotonic()
    r = invoke_reply("complexity", "impact", json.dumps({**BASE, "files": files, "package": "big",
                                                         "changed": ["big.m.f0"]}))
    assert r["warning_card"]["code"] == "G6_E_COMPLEXITY_IMPACT", r["warning_card"]
    assert len(r["result"]["impacted"]) == n and time.monotonic() - started < 20


@pytest.mark.parametrize("source", ["x = " + "not " * 7000 + "y\n", "x = " + "(" * 300 + "1" + ")" * 300 + "\n",
                                    "x = 1" + " + 1" * 200_000 + "\n"])
def test_a_source_too_deep_for_the_parser_is_refused_not_raised(source):
    files = {"deep/__init__.py": "", "deep/m.py": source}
    for op in ("atlas", "impact"):
        r = invoke_reply("complexity", op, json.dumps({**BASE, "files": files, "package": "deep",
                                                       "changed": ["deep.m"]}))
        assert r["completion_state"] in ("qualified-draft", "blocked-escalated"), r
        assert r["warning_card"]["code"].startswith("G6_E_COMPLEXITY_"), r["warning_card"]