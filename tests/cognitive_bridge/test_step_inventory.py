# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 3 (guard_covers_less_than_it_guards): every tool step goes through G6's checked door. This lists,
from the code, every call of a tool or a step in ``g6.cognitive_bridge`` (``.call(``, ``.invoke(``) and allows only the
door: a step is ``invoke_checked``, and the only call of a tool is the one the door makes, through ``_ToolDoor``."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from g6.cognitive_bridge import CognitiveAgent, Hypothesis, Observation, Probe, StaticProposer, ToolSpec, engine
from g6.core import door

PACKAGE = Path(engine.__file__).resolve().parent


def _sites():
    """Every call in the package whose callee is ``.call``, ``.invoke`` or ``invoke_checked``: (module, enclosing
    class and function, callee, call node)."""
    found = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = (node.func.attr if isinstance(node.func, ast.Attribute)
                      else node.func.id if isinstance(node.func, ast.Name) else "")
            if callee not in ("call", "invoke", "invoke_checked"):
                continue
            func = cls = None
            up = parent.get(node)
            while up is not None and cls is None:
                if func is None and isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func = up
                if isinstance(up, ast.ClassDef):
                    cls = up
                up = parent.get(up)
            found.append((path.name, f"{cls.name if cls else ''}.{func.name if func else ''}", callee, node))
    return found


def test_the_inventory_finds_the_door_calls():
    callees = {(m, where, callee) for m, where, callee, _ in _sites()}
    assert ("engine.py", "CognitiveAgent._observe", "invoke_checked") in callees
    assert ("mixed_methods.py", "MixedMethodExecutor._invoke", "invoke_checked") in callees


def test_every_tool_or_step_call_is_the_doors():
    bad = []
    for module, where, callee, node in _sites():
        if callee == "invoke_checked":
            continue
        if callee == "call" and (module, where) == ("engine.py", "_ToolDoor.invoke"):
            continue  # the one call of a tool: the door makes it, through _ToolDoor
        bad.append(f"{module}:{node.lineno} {where} calls .{callee}(")
    assert bad == [], "a tool or step called outside G6's door: " + "; ".join(bad)


def test_the_tool_adapter_is_only_handed_to_the_door():
    bad = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        handed = {id(kw.value) for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and ((isinstance(node.func, ast.Attribute) and node.func.attr == "invoke_checked")
                       or (isinstance(node.func, ast.Name) and node.func.id == "invoke_checked"))
                  for kw in node.keywords if kw.arg == "registry"}
        bad += [f"{path.name}:{node.lineno}" for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "_ToolDoor" and id(node) not in handed]
    assert bad == [], "the tool adapter is built for something other than the door: " + ", ".join(bad)


def _agent(tool):
    probe = Probe("p", "measure", "read", {"A": True, "B": False}, .8)
    return CognitiveAgent(StaticProposer([probe]), {"read": ToolSpec("read", tool)}).run(
        "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)


def test_finding_3_the_agent_cannot_execute_a_tool_when_the_door_refuses(monkeypatch):
    from g6.cognitive_bridge import mixed_methods

    calls = []

    def refused(*args, **kwargs):
        return door.blocked_reply("[G6_E_SENSITIVE_COMPONENT_DENIED] person did not allow this op")

    monkeypatch.setattr(door, "invoke_checked", refused)
    monkeypatch.setattr(mixed_methods, "invoke_checked", refused)
    monkeypatch.delenv("G6_MCP_SENSITIVE", raising=False)

    def tool(question, arguments):
        calls.append((question, arguments))
        return Observation(True, "fake:measurement")

    probe = Probe("p", "measure", "sensitive", {"A": True, "B": False}, .8)
    result = CognitiveAgent(StaticProposer([probe]), {"sensitive": ToolSpec("sensitive", tool)}).run(
        "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    assert calls == [], f"tool ran without a checked-door pass: calls={calls}, state={result['completion_state']}"
    assert result["completion_state"] == "blocked-escalated" and result["code"] == "G6_E_COGNITIVE_STEP_FAILED"
    assert result["door"]["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED" and result["history"] == []


def test_a_step_is_credited_only_when_the_doors_reply_says_ok(monkeypatch):
    real = door.invoke_checked
    seen = []

    def held(*args, **kwargs):
        reply = real(*args, **kwargs)
        seen.append(reply)
        return {**reply, "ok": False}  # the door's own verdict says the answer may not be used

    monkeypatch.setattr(door, "invoke_checked", held)
    out = _agent(lambda q, a: Observation(True, "fake:measurement"))
    assert seen and out["completion_state"] == "blocked-escalated" and out["history"] == []
    assert out["posterior"] == {"A": .5, "B": .5}


@pytest.mark.parametrize("tool", [lambda q, a: 1 / 0, lambda q, a: {"positive": True}],
                         ids=["raises", "not-an-observation"])
def test_a_tool_that_fails_is_a_step_the_door_did_not_pass(tool):
    out = _agent(tool)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_COGNITIVE_STEP_FAILED"
    assert out["door"]["warning_card"]["code"] == "G6_E_COGNITIVE_STEP_FAILED"


def test_a_step_the_door_passes_is_credited_from_the_doors_copy():
    out = _agent(lambda q, a: Observation(True, "fake:measurement", "a note"))
    assert out["completion_state"] == "qualified-draft" and out["posterior"]["A"] == pytest.approx(.8)
    assert out["history"][0]["source_ref"] == "fake:measurement"
