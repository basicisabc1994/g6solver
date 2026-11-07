# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""``g6_confirm_behaviour`` over MCP, with fake clients (G6 0.3 design, 3.2 and 7.3; slice 3, items 2 to 4).

The transport is tested with a client that supports elicitation and one that does not: accept, decline, cancel and
skip, and a rule confirmation. The answer comes from the dialog's result only: no tool argument carries one (an
inventory read from the code lists every place an answer can enter), and ``g6_review_patch`` reads what was confirmed
from the task's store, never from its arguments. No real dialog, model or network is used.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import subprocess
from pathlib import Path

import pytest

from g6.invariants import check_envelope

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from fastmcp.client.elicitation import ElicitResult  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from g6.harness import question_store as qs  # noqa: E402
from g6.harness.confirmed_expectation import ANSWER_FIELDS, ExpectationStandard  # noqa: E402
from g6.harness.verification_templates import TemplateInstance, instance_digest  # noqa: E402
from g6.mcp import behaviour, build_server  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TASK = "Make total(prices) add the prices. A negative price is refused."
CANDIDATE = {"requirement_id": "R1", "function": "shop.prices.total", "args": [[2, -3]],
             "outcomes": {"A": {"returns": -1}, "B": {"raises": "ValueError"}}, "cited": "A negative price is refused.",
             "evidence": ["shop/prices.py: total sums the list with no check of the sign"]}
RULE = "total raises ValueError for every list that holds a negative price"


def data(reply) -> dict:
    return reply.data if isinstance(reply.data, dict) else reply.structured_content


def answering(**content):
    async def handler(message, response_type, params, context):
        return dict(content)
    return handler


async def declined(message, response_type, params, context):
    return ElicitResult(action="decline")


async def cancelled(message, response_type, params, context):
    return ElicitResult(action="cancel")


class Rig:
    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
        monkeypatch.delenv(qs.BUDGET_ENV, raising=False)
        self.home = tmp_path / "home"
        self.repo = tmp_path / "project"
        (self.repo / "shop").mkdir(parents=True)
        (self.repo / "shop" / "__init__.py").write_text("", encoding="utf-8")
        (self.repo / "shop" / "prices.py").write_text("def total(prices):\n    return sum(prices)\n", encoding="utf-8")
        for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Fixture", "-c",
                                                    "user.email=fixture@example.test", "commit", "-q", "-m", "base")):
            subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)
        self.server = build_server()
        self.asked: list[dict] = []

    def call(self, handler=None, **arguments) -> dict:
        async def seen(message, response_type, params, context):
            self.asked.append({"message": message, "schema": params.requestedSchema})
            return await handler(message, response_type, params, context)

        async def flow():
            client = Client(self.server, elicitation_handler=seen) if handler else Client(self.server)
            async with client:
                return data(await client.call_tool("g6_confirm_behaviour", {
                    "repo": str(self.repo), "task": TASK, **arguments}))

        return asyncio.run(flow())

    def store(self) -> qs.TaskQuestions:
        return qs.TaskQuestions(qs.task_identity(self.repo, TASK))


@pytest.fixture
def rig(tmp_path, monkeypatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


# --- the tool, and that no argument answers ------------------------------------------------------------------------


def test_the_tool_is_on_the_surface_and_takes_no_answer_of_any_kind(rig):
    tool = asyncio.run(rig.server.get_tools())["g6_confirm_behaviour"]
    assert set(tool.parameters["properties"]) == {"repo", "task", "candidates", "review_job", "template_instances"}

    async def flow(extra):
        async with Client(rig.server, elicitation_handler=answering(choice="A", name="Ada")) as client:
            await client.call_tool("g6_confirm_behaviour", {"repo": str(rig.repo), "task": TASK,
                                                            "candidates": [CANDIDATE], **extra})

    for extra in ({"answer": "A"}, {"option": "A"}, {"choice": "A"}, {"answered_by": "person:Ada"},
                  {"name": "Ada"}, {"confirmed": True}):
        with pytest.raises(ToolError):
            asyncio.run(flow(extra))
    assert not rig.store().exists()


def test_an_accepted_form_confirms_exactly_the_option_the_person_chose(rig):
    got = rig.call(answering(choice="B", text="refuse it", name="Ada"), candidates=[CANDIDATE])
    assert check_envelope(got) == [] and got["completion_state"] == "qualified-draft"
    assert got["status"] == "confirmed" and got["warning_card"]["code"] == "G6_E_QUESTION_CONFIRMED"
    assert got["answer"]["answered_by"] == "person:Ada" and got["answer"]["identity"] == behaviour.IDENTITY
    assert got["answer"]["option"] == "B" and got["answer"]["text"] == "refuse it" and got["person_seconds"] >= 0
    assert got["coverage"] == "unknown" and got["task_determined"] == "unknown"
    assert got["candidate"]["source"] == "caller" and got["plan"]["chosen"]["source"] == "caller"
    [shown] = rig.asked
    for part in ("Input: shop.prices.total([2, -3])", "Outcome A: returns -1", "Outcome B: raises ValueError",
                 "Cited from the task: A negative price is refused.", "Setup: "):
        assert part in shown["message"]
    [held] = rig.store().state().standard().expectations
    assert held.option == "B" and held.instance.template == "example.v1" and held.provenance == "person_confirmed"
    assert got["expectation"]["expectation_id"] == held.expectation_id
    assert got["expectation"]["check_sha256"] == held.check_sha256


@pytest.mark.parametrize("handler, why", [(declined, "declined"), (cancelled, "cancelled"),
                                          (answering(choice="skip", name="Ada"), "skipped"),
                                          (answering(choice="A"), "invalid"), (None, "unavailable")],
                         ids=["decline", "cancel", "skip", "no name", "no dialog"])
def test_no_answer_leaves_the_question_unconfirmed_reported_and_answerable_later(rig, handler, why):
    got = rig.call(handler, candidates=[CANDIDATE])
    assert check_envelope(got) == [] and got["status"] == "pending" and got["why"] == why
    assert got["question"]["function"] == "shop.prices.total" and got["candidate"]["cited"] == CANDIDATE["cited"]
    assert "Never answer it for the person" in got["next_action"] and "answer" not in got
    state = rig.store().state()
    assert len(state.asked) == 1 and state.answers == () and state.standard().expectations == ()
    # later, the same question is shown again, at no further cost, and the person answers it
    again = rig.call(answering(choice="A", name="Ada"))
    assert again["status"] == "confirmed" and again["question_sha256"] == got["question_sha256"]
    assert len(rig.store().state().asked) == 1


def test_a_rule_is_shown_in_full_and_confirmed_only_when_the_person_chooses_it(rig):
    got = rig.call(answering(choice="B", binds="rule", name="Ada"), candidates=[{**CANDIDATE, "rule": RULE}])
    [shown] = rig.asked
    assert f"Rule offered, in full: {RULE}" in shown["message"]
    assert shown["schema"]["properties"]["binds"]["enum"] == ["case", "rule"]
    assert got["status"] == "confirmed" and got["answer"]["answers"] == qs.TaskQuestions(
        qs.task_identity(rig.repo, TASK)).state().asked[0].rule.sha256
    [held] = rig.store().state().standard().expectations
    assert held.scope.startswith("the rule, as shown in full") and RULE in held.scope
    assert held.instance.bindings["cases"] == [{"args": [[2, -3]], "raises": "ValueError"}]   # one case enforced


def test_one_question_per_task_and_only_the_person_allows_more(rig):
    rig.call(answering(choice="A", name="Ada"), candidates=[CANDIDATE])
    other = {**CANDIDATE, "args": [[1, -1]]}
    spent = rig.call(answering(choice="A", name="Ada"), candidates=[other])
    assert spent["status"] == "budget_spent" and spent["candidate"]["args"] == [[1, -1]] and len(rig.asked) == 1
    assert "question" not in spent and len(rig.store().state().asked) == 1


def test_the_person_can_allow_one_more_in_the_form(rig):
    first = rig.call(answering(choice="A", name="Ada", further="one_more"), candidates=[CANDIDATE])
    assert first["budget"]["allowance"] == 2
    second = rig.call(answering(choice="B", name="Ada"), candidates=[{**CANDIDATE, "args": [[1, -1]]}])
    assert second["status"] == "confirmed" and len(rig.store().state().standard().expectations) == 2


def test_with_no_candidate_the_question_is_held_and_with_a_budget_of_none_nothing_is_asked(rig, monkeypatch):
    held = rig.call(answering(choice="A", name="Ada"))
    assert held["status"] == "held" and rig.asked == [] and not rig.store().exists()
    monkeypatch.setenv(qs.BUDGET_ENV, "0")
    none = rig.call(answering(choice="A", name="Ada"), candidates=[CANDIDATE])
    assert none["status"] == "budget_spent" and rig.asked == []


def test_a_store_g6_cannot_read_is_refused_never_read_as_empty(rig):
    rig.call(answering(choice="A", name="Ada"), candidates=[CANDIDATE])
    path = rig.store().path
    path.write_text(path.read_text(encoding="utf-8")[:-15], encoding="utf-8")
    got = rig.call(answering(choice="A", name="Ada"), candidates=[CANDIDATE])
    assert got["completion_state"] == "blocked-escalated" and got["warning_card"]["code"] == qs.UNREADABLE
    with pytest.raises(qs.StoreUnreadable):
        behaviour.enforced(str(rig.repo), TASK)


def test_a_candidate_from_a_review_s_differences_comes_from_g6_s_own_record(rig):
    one = TemplateInstance(id="D1", template="differential.v1", requirement_id="R1", intent="change",
                           bindings={"function": "shop.prices.total", "mode": "outputs", "calls": [{"args": [[2, -3]]}]})
    job = "f" * 32
    saved = {"assessment": {"completion": {"open_questions": [{"check": "D1"}], "activities": [{
        "activity": "template instance", "check": "D1", "template": "differential.v1", "result": "behaviour_changed",
        "instance_sha256": instance_digest(one), "differences": [{
            "input": json.dumps({"args": [[2, -3]], "kwargs": {}}, sort_keys=True), "base": '{"returned": -1}',
            "patch": '{"raised": "ValueError"}'}]}]}}}
    (rig.home / "reviews").mkdir(parents=True)
    (rig.home / "reviews" / f"{job}.json").write_text(json.dumps(saved), encoding="utf-8")
    instances = [{k: v for k, v in one.model_dump().items() if k != "provenance"}]
    got = rig.call(answering(choice="A", name="Ada"), review_job=job, template_instances=instances)
    assert got["status"] == "confirmed" and got["candidate"]["source"] == "differential"
    assert any(job in line for line in got["candidate"]["evidence"])
    refused = rig.call(answering(choice="A", name="Ada"), review_job="0" * 32, template_instances=instances)
    assert refused["completion_state"] == "blocked-escalated"   # a review G6 holds no record of gives nothing


# --- g6_review_patch reads what was confirmed from the store -----------------------------------------------------------


def test_the_review_tool_enforces_what_the_store_holds_and_takes_no_answer(rig, monkeypatch):
    from g6.mcp import review_reply

    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    seen: list[dict] = []
    monkeypatch.setattr(review_reply, "review", lambda repo, **kwargs: seen.append(kwargs) or {"ok": True})
    server = build_server()
    tool = asyncio.run(server.get_tools())["g6_review_patch"]
    assert not set(tool.parameters["properties"]) & {"confirmed", "answer", "expectations", *ANSWER_FIELDS}

    def review() -> dict:
        async def flow():
            async with Client(server) as client:
                job = data(await client.call_tool("g6_review_patch", {"repo": str(rig.repo), "task": TASK}))
                if "job_id" not in job:
                    return job
                for _ in range(500):
                    got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
                    if got.get("status") in ("completed", "failed"):
                        return got
                    await asyncio.sleep(0.01)
        return asyncio.run(flow())

    review()
    assert "confirmed" not in seen[-1]   # nothing asked about this task: the review is called as it was
    rig.call(answering(choice="B", name="Ada"), candidates=[CANDIDATE])
    review()
    standard = seen[-1]["confirmed"]
    assert type(standard) is ExpectationStandard and [e.option for e in standard.expectations] == ["B"]
    path = rig.store().path
    path.write_text("not a ledger\n", encoding="utf-8")
    refused = review()
    assert refused["completion_state"] == "blocked-escalated" and refused["warning_card"]["code"] == qs.UNREADABLE
    assert len(seen) == 2


# --- inventories, read from the code -----------------------------------------------------------------------------------


RECORDS = ("Answer", "ConfirmedExpectation")


def _calls(path: Path) -> list[tuple[str, str]]:
    """Each call in ``path`` that makes an answer record or a confirmed expectation (``Answer``,
    ``ConfirmedExpectation``, either's ``from_record``, slice 2's ``confirm``), reads an answer from a dialog
    (``bq.read``), or, in a module that uses the question store, hands one to a store (any ``.answer``): (enclosing
    function, what)."""
    text = path.read_text(encoding="utf-8")
    tree, uses_store = ast.parse(text), "question_store" in text
    found = []

    def visit(node, where):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            where = node.name
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
            if name in RECORDS or (name == "from_record" and owner in RECORDS) or (
                    name == "confirm" and isinstance(func, ast.Name)) or (
                    name == "answer" and isinstance(func, ast.Attribute) and uses_store) or (
                    name == "read" and owner == "bq"):
                found.append((where, f"{owner + '.' if owner else ''}{name}"))
        for child in ast.iter_child_nodes(node):
            visit(child, where)

    visit(tree, "<module>")
    return found


def test_every_place_an_answer_can_enter_none_of_them_a_tool_argument():
    """Read from the code: every call in the product that makes an answer record (``Answer``, ``Answer.from_record``),
    reads one from a dialog (``bq.read``) or hands one to a store (``store.answer``)."""
    found = {}
    for path in sorted((ROOT / "src" / "g6").rglob("*.py")):
        calls = _calls(path)
        if calls:
            found[path.relative_to(ROOT / "src").as_posix()] = sorted(set(calls))
    assert found == {
        "g6/harness/behaviour_question.py": [("read", "Answer")],              # the dialog's result, read
        "g6/harness/confirmed_expectation.py": [("__init__", "Answer.from_record"),
                                                ("_answer_of", "Answer.from_record"),
                                                ("_copied", "ConfirmedExpectation.from_record"), ("confirm", "Answer"),
                                                ("confirm", "ConfirmedExpectation"),
                                                ("from_record", "Answer.from_record")],
        # slice 2's own records: copies read back from their bytes, and confirm, which no product module calls
        "g6/harness/question_store.py": [("_answer", "Answer.from_record"),   # read back from the store's ledger
                                         ("_standard", "ConfirmedExpectation")],   # paired with the question asked
        "g6/mcp/behaviour.py": [("confirm", "bq.read"), ("confirm", "store.answer")]}
    # the one dialog reader is given only what the client's dialog returned
    tree = ast.parse((ROOT / "src" / "g6" / "mcp" / "behaviour.py").read_text(encoding="utf-8"))
    confirm = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "confirm")
    elicited = next(n for n in ast.walk(confirm) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await))
    assert ast.unparse(elicited.value.value.func) == "elicit"
    bound = [t.id for t in elicited.targets[0].elts]
    read = next(n for n in ast.walk(confirm) if isinstance(n, ast.Call) and ast.unparse(n.func) == "bq.read")
    assert [ast.unparse(arg) for arg in read.args[:2]] == bound[:2] == ["action", "content"]
    # and no MCP tool takes an answer's field, or a name or choice, as an argument
    tools = asyncio.run(build_server().get_tools())
    taken = {name: set(tool.parameters.get("properties", {})) for name, tool in tools.items()}
    words = {*ANSWER_FIELDS, "answer", "option", "choice", "name", "binds", "further"}
    assert {name: params & words for name, params in taken.items() if params & words} == {}


WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
         11: "eleven", 12: "twelve", 13: "thirteen"}
GROUPS = {"g6_mission_": ("g6_mission_",), "g6_improve_": ("g6_improve_",), "metaharness_": ("metaharness_",
                                                                                              "meta-harness")}


def _counted(text: str, tool: str, tools: set[str]) -> bool:
    if re.search(rf"(?<![\w]){re.escape(tool)}(?![\w])", text):
        return True
    for prefix, said in GROUPS.items():
        if tool.startswith(prefix):
            count = WORDS[sum(one.startswith(prefix) for one in tools)]
            return any(re.search(rf"\b{count}\b\W{{0,4}}{re.escape(name)}", text, re.IGNORECASE) for name in said)
    return False


def test_every_mcp_tool_is_counted_in_each_doc_that_counts_them():
    import g6.mcp

    tools = set(asyncio.run(build_server().get_tools()))
    assert "g6_confirm_behaviour" in tools
    docs = {"g6.mcp's docstring": g6.mcp.__doc__,
            **{rel: (ROOT / rel).read_text(encoding="utf-8")
               for rel in ("site/docs/developers.md", "web/content/developers.md")}}
    missing = {name: sorted(tool for tool in tools if not _counted(text, tool, tools)) for name, text in docs.items()}
    assert {name: found for name, found in missing.items() if found} == {}
