# SPDX-License-Identifier: Apache-2.0
"""Every caller of the door's copy answers an answer the copy does not read with its own refusal (issuance review of
the door's answer budget, finding 4; the certificate issuance window before 0.3.0).

``g6.core.door.held_copy`` (and ``corrected_result``, which calls it) raises ``AnswerNotRead`` past its budgets, "for
every caller". ``door_reply`` turned that into a blocked reply; a pipeline of blocks (``a >> b``) and a bundle's result
did not, so ``PipelineBlock.infer`` and ``BundleResult.to_dict`` raised where each promises a labelled result. The
class, not those two: the inventory below lists, from the code, every module that calls the copy, and each one is
shown here handing back its own ``blocked-escalated`` refusal with ``G6_E_RESOURCE_LIMIT`` and nothing of the answer.

Development fixtures only: no model runs.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import g6
from g6.bundles import Bundle, BundleResult, Field
from g6.bundles.server import run_bundle
from g6.core import door
from g6.core.ai_block import AIBlock
from g6.core.block_contract import BlockContract
from g6.core.compose import Stage, compose
from g6.core.resources import RESOURCE_LIMIT
from g6.core.result import Result
from g6.invariants import check_envelope

SRC = Path(g6.__file__).resolve().parent
#: Every module of G6 that names the door's copy, and the test here that shows what it does with an unread answer.
CALLERS = {
    "core/door.py": "tests/core/test_door_answer_budget.py (door_reply)",
    "core/ai_block.py": "test_a_pipeline_refuses_an_unread_answer_as_a_blocked_result",
    "core/compose.py": "test_a_composition_refuses_an_unread_answer_as_its_blocked_reply",
    "bundles/__init__.py": "test_a_bundle_result_refuses_unread_rows_in_the_shape_its_page_reads",
    "mcp/mission.py": "test_a_mission_reply_is_refused_by_its_guard",
}


def shared(count: int = 4, each: int = 50_000) -> list:
    """One list of ``each`` values held ``count`` times: a copy would reach ``(count - 1) * each`` values again."""
    rows = list(range(each))
    return [rows] * count


def test_the_inventory_is_every_module_that_calls_the_doors_copy():
    named = re.compile(r"\b(held_copy|corrected_result)\b")
    found = {path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py")
             if named.search(path.read_text(encoding="utf-8"))}
    assert found == set(CALLERS), "a new caller of the door's copy answers AnswerNotRead with its own refusal, shown here"


# --- a pipeline of blocks ---------------------------------------------------------------------------------------------


class First(AIBlock):
    def infer(self, data):
        return Result.ok({"x": 1, "evidence": [{"kind": "note", "summary": "first ran"}]})


class Shared(AIBlock):
    def infer(self, data):
        return Result.ok({"completion_state": "qualified-draft", "rows": shared()})


class Long(AIBlock):
    def infer(self, data):
        return Result.ok({"rows": list(range(200))})


class Third(AIBlock):
    ran = 0

    def infer(self, data):
        Third.ran += 1
        return Result.ok({"done": True})


def test_a_pipeline_refuses_an_unread_answer_as_a_blocked_result():
    result = (First("first") >> Shared("shared")).infer({})
    assert result.is_fail() and result.completion_state == "blocked-escalated"
    assert result.error.startswith(f"[{RESOURCE_LIMIT}] the pipeline first>>shared: ")
    assert f"more than {door.ANSWER_VISITS} values" in result.error
    assert result.warning_card["code"] == RESOURCE_LIMIT
    assert result.evidence == [{"stage": "first", "kind": "note", "summary": "first ran"}]


def test_a_pipeline_past_the_total_budget_says_so(monkeypatch):
    monkeypatch.setattr(door, "ANSWER_TOTAL", 100)
    result = (First("first") >> Long("long")).infer({})
    assert result.is_fail() and result.completion_state == "blocked-escalated"
    assert result.warning_card["code"] == RESOURCE_LIMIT and "values in all" in result.error


def test_a_stage_after_a_refused_pipeline_never_runs():
    Third.ran = 0
    result = ((First("first") >> Shared("shared")) >> Third("third")).infer({})
    assert result.is_fail() and result.completion_state == "blocked-escalated" and Third.ran == 0
    assert result.failed_stage == "first" and result.warning_card["code"] == RESOURCE_LIMIT


def test_a_pipeline_inside_the_budget_answers_as_before():
    class Little(AIBlock):
        def infer(self, data):
            rows = list(range(10))
            return Result.ok({"completion_state": "verified", "rows": [rows, rows]})

    result = (First("first") >> Little("little")).infer({})
    assert result.is_ok() and result.completion_state == "qualified-draft"
    assert result.value["rows"] == [list(range(10))] * 2 and result.value["completion_state"] == "qualified-draft"


# --- a checked composition --------------------------------------------------------------------------------------------


class SharedRows(AIBlock):
    __g6_block_contract__ = BlockContract(name="shared_rows", inputs_required_per_op={"run": ("number",)},
                                          outputs_per_op={"run": ("rows",)}, verification_method="unit_tests_only")

    def __init__(self) -> None:
        super().__init__("shared_rows")

    def infer(self, data: dict):
        return Result.ok({"completion_state": "qualified-draft", "rows": shared()})


def test_a_composition_refuses_an_unread_answer_as_its_blocked_reply():
    got = compose(Stage(SharedRows(), "run"), caller_inputs=("number",)).infer({"number": 1}).value
    assert got["ok"] is False and got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == RESOURCE_LIMIT and got["result"] is None
    assert got["error"].startswith(f"[{RESOURCE_LIMIT}]")


# --- a bundle's result ------------------------------------------------------------------------------------------------


def _rows(count: int) -> list[dict]:
    row = {"row": 1, "label": "verified", "source": "s", "note": "n"}
    return [row] * count


def test_a_bundle_result_refuses_unread_rows_in_the_shape_its_page_reads():
    read = BundleResult("qualified-draft", "three rows", rows=_rows(3), notes=["a note"]).to_dict(request_id="r1")
    assert read["rows"] == [{"row": 1, "label": "qualified-draft", "source": "s", "note": "n"}] * 3
    unread = BundleResult("qualified-draft", "many rows", rows=_rows(30_000), notes=["a note"]).to_dict(request_id="r1")
    assert set(unread) == set(read), "the page reads the same fields"
    assert unread["completion_state"] == "blocked-escalated" and unread["warning_card"]["code"] == RESOURCE_LIMIT
    assert unread["rows"] == [] and unread["notes"] == ["a note"] and unread["request_id"] == "r1"
    assert unread["summary"].startswith("this bundle: ") and "G6's door did not read" in unread["summary"]
    assert check_envelope(unread) == []


@pytest.mark.parametrize("state", ["verified", "qualified-draft", "blocked-escalated"])
def test_the_bundle_runner_always_returns_a_labelled_result(state):
    def many(inputs, llm):
        return BundleResult(state, "many rows", rows=_rows(30_000))

    out = run_bundle(Bundle("m", "M", "m", (Field("a", "A"),), many), {"a": "1"})
    assert out["completion_state"] == "blocked-escalated" and out["warning_card"]["code"] == RESOURCE_LIMIT
    assert out["rows"] == [] and "claimed_completion_state" not in out and check_envelope(out) == []


# --- a mission's reply ------------------------------------------------------------------------------------------------


def test_a_mission_reply_never_shares_and_is_refused_by_its_guard(monkeypatch):
    """A mission's reply copies the JSON it froze its answer as, so nothing in it is one object twice; past the total
    budget, the guard every reply is made under answers with the refusal."""
    from g6.mcp import mission

    held = mission.reply({"mission_id": "m1", "rows": shared(4, 30_000)})
    assert held["completion_state"] == "qualified-draft" and held["rows"] == [list(range(30_000))] * 4
    monkeypatch.setattr(door, "ANSWER_TOTAL", 100)
    refused = mission._guarded(lambda: mission.reply({"mission_id": "m1", "rows": list(range(200))}))
    assert refused["ok"] is False and refused["completion_state"] == "blocked-escalated"
    assert refused["warning_card"]["code"] == RESOURCE_LIMIT and "rows" not in refused


def test_a_mission_reply_is_refused_by_its_guard():
    """From the code: every reply a mission tool makes is made inside ``_guarded``, as a lambda handed to it or a
    function handed to it by name."""
    source = (SRC / "mcp" / "mission.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    guarded = {arg.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "_guarded" for arg in node.args if isinstance(arg, ast.Name)}
    replies = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "reply"]
    assert len(replies) >= 6
    for call in replies:
        scope = parent[call]
        while not isinstance(scope, (ast.Lambda, ast.FunctionDef)):
            scope = parent[scope]
        if isinstance(scope, ast.Lambda):
            outer = parent[scope]
            assert isinstance(outer, ast.Call) and getattr(outer.func, "id", "") == "_guarded", call.lineno
        else:
            assert scope.name in guarded, f"line {call.lineno}: reply() in {scope.name}, which no _guarded() is handed"
