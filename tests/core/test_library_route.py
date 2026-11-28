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
    # Repair 2 supersedes the old free-function inventory: only the door is a library route.
    assert corpus.load()["library"] == {}


def test_a_task_for_a_blockless_component_gets_its_import_after_every_runnable_call() -> None:
    reply = selector.select("detect test weakening between the old and the new test suite snapshots", "verify")
    assert not _library(reply)


def test_a_sensitive_refusal_offers_the_persons_own_python_form_and_never_grants() -> None:
    reply = selector.select("check my lean proof", "verify", DENIED)
    remedy = reply["remedy"]
    assert remedy["tell_person"] is True and reply["next"]["tell_person"] is True and reply["next"]["call"] is None
    assert "sensitive" in remedy["action"] and "person decides" in remedy["action"]
    assert "allow=" not in remedy["action"]
    assert not _library(reply)


def test_the_repair_step_of_a_sensitive_refusal_offers_the_python_form() -> None:
    from g6.mcp.repair import block

    found = block("g6_invoke", {"ok": False, "completion_state": "blocked-escalated", "error": DENIED})
    assert found["by"] == "person" and found["call"] is None
    assert "sensitive" in found["step"] and "allow=" not in found["step"]


def test_ops_composed_in_considering_get_one_python_form_with_no_allowance_named_for_a_safe_op() -> None:
    considering = json.dumps([{"component": "action_logic", "op": "assess_material_action", "args": {}},
                              {"component": "epistemic_integrity", "op": "adjudicate", "args": {"message": "m"}}])
    reply = selector.select("check the claims, then adjudicate the message", "verify", considering=considering)
    [composed] = [c for c in _library(reply) if c["id"] == "library:compose"]
    code = composed["call"]["python"]
    ast.parse(code)
    assert code.count("g6.invoke(") == 2 and "allow=" not in code
    assert composed["validated"] and not composed["needs"]["sensitive"]


def test_a_task_no_blockless_component_matches_gets_no_library_form() -> None:
    assert not _library(selector.select("review my patch before I say it is done", "verify"))


@pytest.mark.parametrize("name", ["safety_engine", "navigator"])
def test_every_sketch_names_only_what_the_corpus_lists(name: str) -> None:
    assert selector.library_sketch(name, "detect_test_weakening") == ""
    assert selector.library_sketch(name, "no_such_function") == ""
