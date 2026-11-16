# SPDX-License-Identifier: Apache-2.0
"""The library route (plan revision 8, the repair loop, part D): where the right op cannot run over MCP for this
person (a sensitive op not allowed, a component with no block, several ops composed), ``g6_select`` and the repair
step offer the Python form, checked against the corpus, ranked after every call that runs now over MCP, and never
``next``. ``allow`` in ``g6.invoke`` is the person's own decision in their own code: G6 never grants it."""
from __future__ import annotations

import ast
import json

import pytest

from g6.core import selector
from g6.skills import corpus

DENIED = "[G6_E_SENSITIVE_COMPONENT_DENIED] lean.check_proof: this op runs a program on your machine"


def _library(reply: dict) -> list[dict]:
    return [c for c in reply["candidates"] if c["kind"] == "library"]


def test_the_corpus_lists_each_blockless_components_python_form() -> None:
    library = corpus.load()["library"]
    assert {"safety_engine", "navigator", "cegis", "workforce"} <= set(library)
    assert not set(library) & set(corpus.load()["components"])       # only components g6_invoke cannot run
    found = library["safety_engine"]
    assert found["module"] == "g6.safety_engine"
    assert any(f["name"] == "detect_test_weakening" and f["signature"].startswith("detect_test_weakening(")
               for f in found["functions"])


def test_a_task_for_a_blockless_component_gets_its_import_after_every_runnable_call() -> None:
    reply = selector.select("detect test weakening between the old and the new test suite snapshots", "verify")
    library = _library(reply)
    assert library and library[0]["component"] == "safety_engine", [c["id"] for c in reply["candidates"]]
    code = library[0]["call"]["python"]
    ast.parse(code)
    assert code.startswith("from g6.safety_engine import detect_test_weakening")
    assert library[0]["validated"] is True and library[0]["runs_over_mcp"] is False
    kinds = [c["kind"] for c in reply["candidates"]]
    assert kinds.index("library") > max(i for i, k in enumerate(kinds) if k != "library")
    assert reply["next"]["id"] != library[0]["id"]


def test_a_sensitive_refusal_offers_the_persons_own_python_form_and_never_grants() -> None:
    reply = selector.select("check my lean proof", "verify", DENIED)
    remedy = reply["remedy"]
    assert remedy["tell_person"] is True and reply["next"]["tell_person"] is True and reply["next"]["call"] is None
    form = 'g6.invoke("lean", "check_proof", '
    assert form in remedy["action"] and 'allow=("lean.check_proof",)' in remedy["action"]
    assert "the person's own decision" in remedy["action"]
    [library] = [c for c in _library(reply) if c["id"] == "library:invoke:lean.check_proof"]
    ast.parse(library["call"]["python"])
    assert form in library["call"]["python"] and library["validated"] is True


def test_the_repair_step_of_a_sensitive_refusal_offers_the_python_form() -> None:
    from g6.mcp.repair import block

    found = block("g6_invoke", {"ok": False, "completion_state": "blocked-escalated", "error": DENIED})
    assert found["by"] == "person" and 'allow=("lean.check_proof",)' in found["step"]


def test_ops_composed_in_considering_get_one_python_form_with_no_allowance_named_for_a_safe_op() -> None:
    considering = json.dumps([{"component": "grounding", "op": "check_claims", "args": {"claims": [], "sources": {}}},
                              {"component": "epistemic_integrity", "op": "adjudicate", "args": {"message": "m"}}])
    reply = selector.select("check the claims, then adjudicate the message", "verify", considering=considering)
    [composed] = [c for c in _library(reply) if c["id"] == "library:compose"]
    code = composed["call"]["python"]
    ast.parse(code)
    assert code.count("g6.invoke(") == 2 and "allow=" not in code


def test_a_task_no_blockless_component_matches_gets_no_library_form() -> None:
    assert not _library(selector.select("review my patch before I say it is done", "verify"))


@pytest.mark.parametrize("name", ["safety_engine", "navigator"])
def test_every_sketch_names_only_what_the_corpus_lists(name: str) -> None:
    found = corpus.load()["library"][name]
    sketch = selector.library_sketch(name, found["functions"][0]["name"])
    tree = ast.parse(sketch)
    imported = [a.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for a in node.names]
    assert imported == [found["functions"][0]["name"]]
    assert selector.library_sketch(name, "no_such_function") == ""
