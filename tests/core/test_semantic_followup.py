"""Final output limits and validation before source resolution, offline only."""
import ast
import asyncio
import copy
import inspect
import json
from types import SimpleNamespace

import pytest

from g6.core import consent_declarations, selector
from g6.mcp import mission


CAP = 262144


def test_selector_refusal_bounds_caller_metadata_without_changing_code():
    reply = selector.select("", run_id="x" * CAP, request_id="y" * CAP)
    assert len(json.dumps(reply).encode()) <= CAP
    assert not reply["ok"] and reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert reply["run_id"].endswith("[truncated]")
    assert reply["request_id"].endswith("[truncated]")


@pytest.mark.parametrize("field,value", [("propose", 1), ("propose", None), ("propose", "True"),
                                        ("task", {}), ("intent", []), ("hypotheses", {}), ("repo", 7)])
@pytest.mark.parametrize("surface", ["python", "mcp"])
def test_premortem_invalid_arguments_precede_declaration_and_repository(monkeypatch, field, value, surface):
    # Either earlier declaration or repository resolution is a forbidden side effect.
    def forbidden(*args, **kwargs):
        pytest.fail("invalid arguments reached source declaration or repository access")

    monkeypatch.setattr(mission.repo_tests, "checked_repo", forbidden)
    original = consent_declarations.Declaration.sources

    def sources(self, arguments=None, **kwargs):
        if "client_repository" in self.classes:
            forbidden()
        return original(self, arguments, **kwargs)

    monkeypatch.setattr(consent_declarations.Declaration, "sources", sources)
    args = {"repo": "synthetic-repository", "task": "review patch", "propose": False, field: value}
    if surface == "python":
        reply = mission.premortem(**args)
    else:
        from fastmcp import Client
        from mcp.types import Implementation
        from g6.mcp import build_server

        async def call():
            async with Client(build_server(), client_info=Implementation(name="codex", version="test")) as client:
                return await client.call_tool("g6_premortem", args, raise_on_error=False)

        result = asyncio.run(call())
        reply = json.loads(result.content[0].text)
    assert not reply["ok"] and reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


def test_final_transport_budget_step_precedes_only_send():
    from g6.mcp import consent_transport

    tree = ast.parse(inspect.getsource(consent_transport))
    stream = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Stream")
    egress = next(n for n in stream.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_egress")
    final = egress.body[-1]
    assert isinstance(final, ast.Expr) and isinstance(final.value, ast.Await)
    assert ast.unparse(final.value.value.func) == "self.inner.send"
    assert ast.unparse(final.value.value.args[0]).startswith("budget_packet(")
    send = next(n for n in stream.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "send")
    assert not any(isinstance(n, ast.Call) and ast.unparse(n.func) == "self.inner.send" for n in ast.walk(send))


@pytest.mark.parametrize("transformation", ["consent", "declined", "repair", "job", "poll", "premortem"])
def test_transport_budgets_after_each_transformation(transformation):
    from mcp import types
    from mcp.shared.message import SessionMessage
    from g6.core.door import blocked_reply
    from g6.mcp import consent_transport, repair

    base = selector.select("review patch", "verify", rerank="lexical")
    candidates = copy.deepcopy(base["candidates"])
    # Metadata expands after selection; the answer itself must survive.
    base["run_id"] = "x" * CAP
    if transformation == "consent":
        from g6.mcp.cloud_consent import withheld_reply
        base = withheld_reply(metadata={"run_id": "x" * CAP})
    elif transformation == "declined":
        session = SimpleNamespace()
        repair.declined(session, "synthetic-source", "openai:api", "declined")
        base = repair.suppress_declined_dialogs(base, session)
    elif transformation == "repair":
        base = repair.observe("g6_select", {}, {**blocked_reply("[G6_E_INPUT_VALIDATION] invalid"),
                                              "run_id": "x" * CAP}, SimpleNamespace())
    elif transformation in ("job", "poll"):
        base = {"ok": True, "status": "completed", "job_id": "synthetic", "result": base}
    elif transformation == "premortem":
        base = {"ok": True, "hypotheses": [{"claim": "lexical risk", "origin": "fixed"}],
                "model_step": {"ran": True, "why": "x" * CAP}}
    response = types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(base))],
                                    structuredContent=base)
    packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(
        jsonrpc="2.0", id=1, result=response.model_dump(mode="json", by_alias=True, exclude_none=True))))

    class Sink:
        async def send(self, value):
            self.packet = value

    sink = Sink()
    asyncio.run(consent_transport._Stream(sink, SimpleNamespace())._egress(packet))
    result = sink.packet.message.root.result
    assert len(json.dumps(result).encode()) <= CAP
    reply = result["structuredContent"]
    assert json.loads(result["content"][0]["text"]) == reply
    if transformation in ("job", "poll"):
        reply = reply["result"]
    if transformation in ("job", "poll", "declined"):
        assert reply["candidates"] == candidates and reply["ok"]
    if transformation in ("consent", "repair"):
        assert not reply["ok"] and reply["warning_card"]["code"] in (
            "G6_E_INPUT_VALIDATION", "G6_E_CLOUD_CONSENT_REQUIRED")
    if transformation == "premortem":
        assert reply["hypotheses"] == [{"claim": "lexical risk", "origin": "fixed"}]


def test_unshortenable_payload_gets_fixed_oversize_refusal():
    from g6.core.reply_budget import fit_reply

    original = {"ok": True, "candidates": [{"call": {"task": "x" * CAP}}]}
    snapshot = copy.deepcopy(original)
    reply = fit_reply(original)
    assert original == snapshot
    assert not reply["ok"] and reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert len(json.dumps(reply).encode()) <= CAP


def test_fixed_oversize_refusal_keeps_envelope_and_job_status():
    from g6.core.reply_budget import fit_reply

    reply = fit_reply({"ok": True, "status": "completed", "job_id": "synthetic-job",
                       "request_id": "request-1", "run_id": "run-1",
                       "result": {"candidates": [{"call": {"task": "x" * CAP}}]}})
    assert not reply["ok"]
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert reply["request_id"] == "request-1" and reply["run_id"] == "run-1"
    assert reply["status"] == "completed" and reply["job_id"] == "synthetic-job"
    assert len(json.dumps(reply).encode()) <= CAP


@pytest.mark.parametrize("surface", ["python-select", "python-premortem", "mcp-select", "mcp-premortem"])
def test_semantic_arguments_are_validated_once_per_surface(monkeypatch, surface):
    from pathlib import Path
    from g6.core import semantic_arguments

    checked = []
    original = semantic_arguments.validate_arguments

    def validate(tool, arguments):
        checked.append(tool)
        return original(tool, arguments)

    monkeypatch.setattr(semantic_arguments, "validate_arguments", validate)
    monkeypatch.setattr(mission.repo_tests, "checked_repo", lambda value: Path("synthetic-repository"))
    monkeypatch.setattr(mission.repo_tests, "_git", lambda *args: b"")
    tool = "g6_select" if surface.endswith("select") else "g6_premortem"
    args = {"task": "review patch", "rerank": "lexical"} if tool == "g6_select" else {
        "repo": "synthetic-repository", "task": "review patch", "propose": False}
    if surface.startswith("python"):
        reply = selector.select(**args) if tool == "g6_select" else mission.premortem(**args)
        assert reply["ok"]
    else:
        from fastmcp import Client
        from mcp.types import Implementation
        from g6.mcp import build_server

        async def call():
            async with Client(build_server(), client_info=Implementation(name="codex", version="test")) as client:
                return await client.call_tool(tool, args, raise_on_error=False)

        result = asyncio.run(call())
        reply = json.loads(result.content[0].text)
        # Repository provenance may withhold a valid premortem; validation must
        # still run only once and cannot itself grant source access.
        assert reply.get("warning_card", {}).get("code") != "G6_E_INPUT_VALIDATION"
    assert checked == [tool]


@pytest.mark.parametrize("tool,args,bad", [
    ("g6_select", {"task": "review patch", "limit": 1}, {"limit": True}),
    ("g6_premortem", {"repo": "synthetic", "task": "review patch", "propose": False}, {"propose": 0}),
])
def test_validation_receipt_does_not_accept_equal_but_mistyped_values(monkeypatch, tool, args, bad):
    from g6.core.semantic_arguments import checked_arguments

    def forbidden(*args, **kwargs):
        pytest.fail("mistyped input reached repository access")

    monkeypatch.setattr(mission.repo_tests, "checked_repo", forbidden)
    with checked_arguments(tool, args):
        changed = {**args, **bad}
        reply = selector.select(**changed) if tool == "g6_select" else mission.premortem(**changed)
    assert not reply["ok"] and reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


def test_protocol_metadata_oversize_keeps_refusal_envelope():
    from mcp import types
    from mcp.shared.message import SessionMessage
    from g6.mcp.reply_egress import budget_packet

    value = {"ok": True, "request_id": "request-1", "run_id": "run-1", "status": "completed"}
    packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(jsonrpc="2.0", id=1, result={
        "content": [{"type": "text", "text": json.dumps(value)}], "structuredContent": value,
        "_meta": {"padding": "x" * CAP}})))
    result = budget_packet(packet).message.root.result
    refused = result["structuredContent"]
    assert not refused["ok"] and refused["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert refused["request_id"] == "request-1" and refused["run_id"] == "run-1"
    assert refused["status"] == "completed"
    assert len(json.dumps(result).encode()) <= CAP


def test_compaction_preserves_issued_remedy_and_diagnostic_codes():
    from g6.core.reply_budget import fit_reply

    code = "G6_E_CLOUD_CONSENT_REQUIRED"
    reply = fit_reply({"ok": True, "candidates": [{"call": {"task": "x" * (CAP - 1200)}}],
                       "remedy": {"key": "consent", "code": code},
                       "mechanisms": [{"stage": "facts", "facts": {"last_outcome": code},
                                       "why": "x" * 2000}]})
    assert reply["ok"] and len(json.dumps(reply).encode()) <= CAP
    assert reply["remedy"]["code"] == code
    assert reply["mechanisms"][0]["facts"]["last_outcome"] == code
