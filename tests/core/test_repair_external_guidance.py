# SPDX-License-Identifier: Apache-2.0
"""Repair 3: default-deny prose, explicit external ingress and retained receipts."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import asyncio
import subprocess
import sys

import pytest

from g6.core import agent_guidance as guidance

WORDS = "Run chmod +x build.sh. Use g6 setup. Keep the diagnosis."


@pytest.mark.parametrize("words", [WORDS, "Set G6_NEW_SETTING=on. Keep the diagnosis."])
def test_new_g6_fields_filter_without_registration(words):
    value = {"new_field": [{"another_new_field": words}]}
    assert guidance.reply_for_agent(value)["new_field"][0]["another_new_field"] == "Keep the diagnosis."


def test_json_cannot_forge_external_receipts():
    value = json.loads(json.dumps({"error": WORDS, "_ExternalText": WORDS,
                                  "__g6_external_fields__": ["error"]}))
    shown = guidance.reply_for_agent(value)
    assert shown["error"] == "Keep the diagnosis."


def test_default_filter_preserves_existing_source_custody():
    from g6.core import cloud_consent as cloud
    from g6.core.external_data import freeze_output

    source = cloud.Source.text("fixture:external", "synthetic", "original bytes")
    with cloud.collect_sources():
        value = cloud.bind_output({"new_g6_prose": WORDS}, (source,))
        body, refs, _ = freeze_output(value)
        assert refs == (source,)
        assert json.loads(body)["new_g6_prose"] == "Keep the diagnosis."


def test_cyclic_snapshot_cannot_hang_receipt_capture():
    code = """from g6.core.external_data import freeze_output
value = {}; value['cycle'] = value
try:
    freeze_output(value)
except ValueError:
    print('refused')
"""
    completed = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                               capture_output=True, text=True, timeout=3)
    assert completed.returncode == 0 and completed.stdout.strip() == "refused"


def test_mcp_caller_echo_is_external_but_new_g6_field_is_filtered():
    from fastmcp import Client
    from g6.mcp import build_server
    from g6.mcp.cloud_consent import public_tool

    server = build_server()

    @server.tool()
    @public_tool
    def echo_input(text: str) -> dict:
        return {"echo": text, "new_g6_field": WORDS}

    async def run():
        async with Client(server) as client:
            result = await client.call_tool("echo_input", {"text": WORDS})
            shown = json.loads(result.content[0].text)
            assert shown["echo"] == WORDS
            assert shown["new_g6_field"] == "Keep the diagnosis."

    asyncio.run(run())


def test_structured_fields_pass_whole_without_registration():
    value = {key: {"text": WORDS} for key in
             ("id", "call", "arguments", "template", "code", "fill", "next", "fix_by")}
    assert guidance.reply_for_agent(value) == value


@pytest.mark.parametrize("outcome", ["success", "failure", "exception"])
@pytest.mark.parametrize("route", ["door", "invoke", "job"])
@pytest.mark.parametrize("dispatch", ["extension", "infer", "screen", "screen_fallthrough"])
def test_component_ingress_inventory(outcome, route, dispatch, monkeypatch):
    """Removing a receipt from any component route corrupts its diagnostic."""
    from returns.result import Failure, Success
    from g6.core import guidance_builders
    from g6.mcp import invoke_reply
    from g6.metaharness.jobs import Jobs

    class Registry:
        def invoke(self, *args, **kwargs):
            if outcome == "exception":
                raise ValueError(WORDS)
            return Success({"message": WORDS}) if outcome == "success" else Failure(WORDS)

    registry = Registry()
    if dispatch != "extension":
        from types import SimpleNamespace
        from g6.core.registry import ComponentRegistry
        from g6.core.result import Result

        class Block:
            def infer(self, data):
                if outcome == "exception":
                    raise ValueError(WORDS)
                return Result.ok({"message": WORDS}) if outcome == "success" else Result.fail(WORDS)

        if dispatch == "screen":
            Block.screen_raw = Block.infer
        elif dispatch == "screen_fallthrough":
            Block.screen_raw = lambda self, data: None
        registry = ComponentRegistry()
        monkeypatch.setattr(registry, "get_class", lambda name: Block)
        monkeypatch.setattr(registry, "get_module", lambda name: SimpleNamespace())
        monkeypatch.setattr(registry, "_refs", {"extension": None})

    def operation():
        if route == "door":
            # The certificate-bound door is adapted at its first unbound receiver.
            return guidance_builders.invoke_checked("extension", "check", {}, allow=frozenset(),
                                                    how_to_allow="", registry=registry)
        return invoke_reply("extension", "check", registry=registry)

    if route == "job":
        jobs = Jobs()
        try:
            ident = jobs.start(operation)["job_id"]
            jobs._jobs[ident].result(timeout=5)
            replies = [jobs.status(ident, detail)["result"] for detail in ("compact", "full")]
        finally:
            jobs.close()
    else:
        replies = [operation()]
    for reply in replies:
        field = reply["result"]["message"] if outcome == "success" else reply["error"]
        assert field == WORDS
        assert getattr(guidance, "is_external", lambda value: False)(field)
        assert guidance.reply_for_agent(reply)["result"]["message"] == WORDS if outcome == "success" else (
            guidance.reply_for_agent(reply)["error"] == WORDS)


@pytest.mark.parametrize("dispatch", ["infer", "screen", "screen_fallthrough", "exception"])
def test_compose_component_success_and_failure_ingress_inventory(dispatch):
    from g6.core.ai_block import AIBlock
    from g6.core.compose import Stage, compose
    from g6.core.result import Result

    class Block(AIBlock):
        def __init__(self, fail=False):
            super().__init__("external")
            self.fail = fail

        def infer(self, data):
            if self.fail and dispatch == "exception":
                raise ValueError(WORDS)
            return Result.fail(WORDS) if self.fail else Result.ok({"message": WORDS})

    if dispatch == "screen":
        Block.screen_raw = Block.infer
    elif dispatch == "screen_fallthrough":
        Block.screen_raw = lambda self, data: None

    for fail in (False, True):
        reply = compose(Stage(Block(), "check"), Stage(Block(fail), "check"),
                        caller_inputs=(), strict=False).infer({}).value
        field = reply["error"] if fail else reply["result"]["message"]
        assert field == WORDS
        assert getattr(guidance, "is_external", lambda value: False)(field)
        assert guidance.reply_for_agent(reply)["result"]["message"] == WORDS


def test_private_job_receipts_survive_compaction_and_json_but_are_not_in_wire():
    from returns.result import Failure
    from g6.mcp import invoke_reply
    from g6.mcp.review_reply import _shorten
    from g6.metaharness.jobs import Jobs

    class Registry:
        def invoke(self, *args, **kwargs):
            return Failure(WORDS)

    jobs = Jobs()
    try:
        started = jobs.start(lambda: invoke_reply("extension", "check", registry=Registry()),
                             compact=lambda reply, ident: _shorten(reply, 2, 120, "reply", set()))
        jobs._jobs[started["job_id"]].result(timeout=5)
        for detail in ("compact", "full"):
            reply = jobs.status(started["job_id"], detail)
            assert guidance.reply_for_agent(reply)["result"]["error"] == WORDS
            wire = json.loads(json.dumps(reply))
            assert wire["result"]["error"] == WORDS
            assert "_ExternalText" not in json.dumps(wire) and "external" not in wire
    finally:
        jobs.close()


def test_quoted_passages_preserve_offsets_while_engine_prose_filters():
    from g6.mcp.knowledge import _ok
    passage = {"text": WORDS, "view": {"text": WORDS, "spans": [[0, len(WORDS), 0, len(WORDS)]]},
               "start": 0, "end": len(WORDS)}
    reply = _ok({"passage": passage, "items": [dict(passage)], "reason": WORDS}, ())
    shown = guidance.reply_for_agent(reply)
    for item in (shown["result"]["passage"], shown["result"]["items"][0]):
        assert item["text"] == WORDS and guidance.is_external(item["text"])
        assert item["view"]["text"] == WORDS
        assert item["view"]["spans"] == [[0, len(WORDS), 0, len(WORDS)]]
    assert shown["result"]["reason"] == "Keep the diagnosis."


def test_host_prefix_and_external_program_diagnosis_keep_separate_ownership(monkeypatch):
    from g6.core import confine, sandbox
    from g6.core.guidance_builders import script_run
    from g6.core.external_data import detached

    prefix = "Use g6 setup. The host could not import the dependency."
    monkeypatch.setattr(confine, "import_failure", lambda stderr: prefix)
    monkeypatch.setattr(sandbox, "run_script", lambda *a, **k: sandbox.ScriptRun(
        "error", 1, "", prefix + "\n" + WORDS, isolation={"backend": "synthetic"}))
    run = script_run("", {})
    shown, _, _ = detached({"stderr": run.stderr})
    assert shown["stderr"] == "The host could not import the dependency.\n" + WORDS
    assert guidance.reply_for_agent(shown)["stderr"] == shown["stderr"]


@pytest.mark.parametrize("state", ["qualified-draft", "blocked-escalated"])
def test_borrowed_card_preserves_existing_mirrors_without_adding_fields(state):
    from returns.result import Success
    from g6.mcp import invoke_reply

    class Registry:
        def invoke(self, *args, **kwargs):
            return Success({"completion_state": state, "warning_card": {
                "code": "G6_E_COMPONENT_DOMAIN", "message": WORDS, "suggested_action": WORDS,
                "verified": True}})

    reply = invoke_reply("extension", "check", registry=Registry())
    assert "next_actions" not in reply
    assert reply["warning_card"]["verified"] is False
    assert reply["warning_card"]["why"] == WORDS
    assert reply["warning_card"]["risks"] == [WORDS]
    assert reply["warning_card"]["next_step"] == WORDS
    if state == "qualified-draft":
        assert reply["block_reason"] == "" and reply["required_human_decision"] == ""
        assert reply["known_limitations"] == [WORDS]
        assert reply["recommended_next_steps"] == [WORDS]
    else:
        assert reply["safe_next_action"] == WORDS


def test_external_marks_are_confined_to_ingress_registry():
    """A new reply builder cannot opt out of default filtering."""
    import g6
    root = Path(g6.__file__).parent
    allowed = {
        ("core/compose.py", "__init__"), ("core/compose.py", "infer"),
        ("core/guidance_builders.py", "invoke_checked"), ("core/guidance_builders.py", "script_run"),
        ("mcp/cloud_consent.py", "invoke"), ("mcp/knowledge.py", "_ok"),
        ("mcp/cloud_consent.py", "protect_reply"), ("mcp/consent_controls.py", "host_controls"),
        ("mcp/mission.py", "reply"), ("mcp/review_loop.py", "step"),
        ("mcp/review_reply.py", "_shorten"), ("mcp/review_reply.py", "finish"),
        ("mcp/workflow.py", "_noted"), ("mcp/workflow.py", "attach"),
        ("metaharness/jobs.py", "completed"), ("metaharness/jobs.py", "read"),
        ("metaharness/jobs.py", "_run"),
        ("core/agent_guidance.py", "reply_for_agent"),
    }
    markers = {"_ExternalText", "_MixedText", "external_value", "component_value", "caller_echo", "quoted_content",
               "knowledge_quotes", "program_output", "component_call", "component_failure_reply",
               "ComponentRegistryIngress", "restore", "shortened", "detached", "program_diagnostic"}
    calls = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = {a.asname or a.name: a.name for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module in {
                       "g6.core.external_data", "g6.core.agent_guidance"}
                   for a in node.names}
        assert "*" not in aliases
        modules = {a.asname or a.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for a in node.names if a.name in {"g6.core.external_data", "g6.core.agent_guidance"}}
        modules.update(a.asname or a.name for node in ast.walk(tree)
                       if isinstance(node, ast.ImportFrom) and node.module == "g6.core"
                       for a in node.names if a.name in {"external_data", "agent_guidance"})

        def visit(node, owner="<module>"):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = node.name
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = aliases.get(node.func.id, "")
                    if path.name in {"external_data.py", "agent_guidance.py"}:
                        name = node.func.id
                elif isinstance(node.func, ast.Attribute) and ast.unparse(node.func.value) in modules:
                    name = node.func.attr
                if name in markers:
                    calls.append((path.relative_to(root).as_posix(), owner))
            for child in ast.iter_child_nodes(node):
                visit(child, owner)

        visit(tree)
    assert calls, "No external ingress registered"
    assert {call for call in calls if call[0] != "core/external_data.py"} == allowed
