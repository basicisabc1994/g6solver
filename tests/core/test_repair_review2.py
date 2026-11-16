# SPDX-License-Identifier: Apache-2.0
"""Repair 2: agent guidance preserves person decisions across every reply route."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from g6.core import refusals, selector
from g6.harness import completion_hook
from g6.mcp import cloud_consent, repair
from g6.skills import corpus


class Session:
    pass


def test_agent_surface_census():
    # Removing the safe route restriction or routing authority instructions must fail here.
    data = corpus.load()
    replies = [selector.select(task, "verify", considering=[
        {"component": "action_logic", "op": "assess_material_action", "args": {}},
        {"component": "epistemic_integrity", "op": "adjudicate", "args": {}}])
        for task in ("allow execution and confirm workforce mission", "check my lean proof",
                     "detect test weakening", "review claims")]
    rows = [r for codes in refusals.component_declarations().values() for r in codes.values()]
    rows += [refusals.layer(code) for code in refusals.LAYERS]
    blocks = [repair.block("g6_invoke", {"ok": False, "error": f"[{row.code}] refused"}) for row in rows]
    denial = cloud_denial()
    from g6.core import discovery, skills as component_skills
    from g6.core.agent_guidance import skill_for_agent
    from g6.core.registry import get_registry
    from g6.mcp import describe_reply, invoke_reply

    descriptions = [describe_reply(name) for name in get_registry().names()]
    op_help = [invoke_reply(name, "help", "{}", allow_sensitive=frozenset()) for name in get_registry().names()]
    root = Path(__file__).resolve().parents[2]
    skills = [p.read_text(encoding="utf-8") for p in root.glob("**/using-g6/SKILL.md") if ".build" not in p.parts]
    skills += [skill_for_agent(component_skills.skill_text(name)) for name in get_registry().names()]
    lines = [completion_hook.repair_line("mcp__g6__g6_select", b) for b in blocks if b]
    from g6.mcp import build_server
    import asyncio

    tools = asyncio.run(build_server().get_tools())
    surfaces = {"corpus": data, "selection and next actions": replies, "repairs": blocks,
                "denial and warning": denial, "hook context": lines, "agent skills": skills,
                "descriptions": descriptions, "help": [discovery.HELP, op_help],
                "tool descriptions": [t.description for t in tools.values()]}
    from g6.mcp import mission, improve

    surfaces["mission and improvement denials"] = [mission.refused(), improve.refused()]
    forbidden = (r"allow\s*=|G6_MCP_SENSITIVE|G6_ALLOW_UNCONFINED|allow_execution|\ballow_sensitive\b|"
                 r"\b(?:confirm|approve|grant|promote)\s*\(|\bapprover\b|autonomous_low_risk|"
                 r"\bg6\s+setup\b|--allow|allow parameter")
    entries = {e["id"]: e for e in data["entries"] if e["kind"] == "op"}
    assert any(c["kind"] == "library" for r in replies for c in r["candidates"])
    for reply in replies:
        for candidate in reply["candidates"]:
            if candidate["kind"] != "library":
                continue
            assert candidate["call"] is not None
            tree = ast.parse(candidate["call"]["python"])
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
            assert calls
            for call in calls:
                assert ast.unparse(call.func) == "g6.invoke" and len(call.args) == 3 and not call.keywords
                component, op, params = map(ast.literal_eval, call.args)
                entry = entries[f"op:{component}.{op}"]
                assert not entry["sensitive"] and entry["validated"] and not entry["issues"]
                assert params == json.loads(entry["call"]["arguments"]["params_json"])
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, child in value.items():
                yield str(key)
                yield from strings(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                yield from strings(child)

    bad = {name: sorted({m.group() for text in strings(value) for m in re.finditer(forbidden, text, re.I)})
           for name, value in surfaces.items()}
    bad = {name: hits for name, hits in bad.items() if hits}
    assert not bad, bad


def cloud_denial():
    from g6.mcp import invoke_reply
    return invoke_reply("lean", "check_proof", "{}", allow_sensitive=frozenset())


def test_blockless_and_refused_templates_have_no_library_call():
    assert selector.library_sketch("workforce", "allow_execution") == ""
    assert not [c for c in selector.select("workforce allow execution confirm mission")["candidates"]
                if c["kind"] == "library"]
    data = corpus.load()
    bad = next(e for e in data["entries"] if e["kind"] == "op" and not e["sensitive"] and not e["validated"])
    assert selector.invoke_sketch([bad["id"][3:]], data) == ""


def test_undeclared_refusals_use_the_session_observer(monkeypatch):
    logged = []
    monkeypatch.setattr(repair, "_log", logged.append)
    session = Session()
    args = {"task": "x", "extra": "secret"}
    replies = [cloud_consent.input_refusal("g6_select", ["task"], arguments=args, session=session) for _ in range(2)]
    assert [(r["repair"]["by"], r["repair"]["retries_left"]) for r in replies] == [("agent", 2), ("person", 0)]
    assert len([r for r in logged if r["event"] == "refused"]) == 2
    assert "secret" not in json.dumps([replies, logged])


def test_grant_input_refusal_never_offers_its_dialog():
    found = cloud_consent.input_refusal("g6_cloud_grant", ["source", "provider"])["repair"]
    assert found["by"] == "person" and found["call"] is None
    assert completion_hook.repair_line("mcp__g6__g6_cloud_grant", {
        "by": "agent", "code": "G6_E_INPUT_VALIDATION"}) == ""


@pytest.mark.parametrize("status", ["declined", "cancelled"])
def test_declined_pair_stays_closed_across_withheld_routes(monkeypatch, status):
    import asyncio
    from types import SimpleNamespace
    from g6.core import cloud_consent as policy
    from g6.mcp.consent import Consent

    session = Session()
    ctx = SimpleNamespace(session=session)
    shown = []

    async def dialog(*args, **kwargs):
        shown.append(1)
        return Consent(status)

    monkeypatch.setattr(cloud_consent, "request_consent", dialog)
    monkeypatch.setattr(policy, "preview", lambda *a: (False, 0))
    asyncio.run(cloud_consent.request_grant(ctx, "component_catalogue", "openai:codex"))
    asyncio.run(cloud_consent.request_grant(ctx, "component_catalogue", "openai:codex"))
    assert len(shown) == 1
    source = policy.Source.text("component_catalogue", "synthetic", "text")
    blocked = cloud_consent.withheld_reply(provider="openai:codex", sources=(source,), session=session)
    assert blocked["repair"]["by"] == "person" and blocked["repair"]["call"] is None
    assert status in blocked["repair"]["step"] and "g6_cloud_grant" not in json.dumps(blocked)
    for other_source, other_provider in (("jobs", "openai:codex"), ("component_catalogue", "anthropic:claude_code")):
        other = policy.Source.text(other_source, "synthetic", "text")
        assert cloud_consent.withheld_reply(provider=other_provider, sources=(other,), session=session)["repair"]["call"]
    assert cloud_consent.withheld_reply(provider="openai:codex", sources=(source,), session=Session())["repair"]["call"]
    capture = SimpleNamespace(client=("openai:codex", session))
    selected = selector.select("describe grounding", last_outcome="[G6_E_CLOUD_CONSENT_REQUIRED] withheld")
    result = cloud_consent._repaired("g6_select", {}, selected, capture)
    assert result["next"]["call"] is None and result["next"]["tell_person"]
    assert status in result["next"]["say"]
    selected = selector.select("describe grounding", last_outcome="[G6_E_CLOUD_CONSENT_REQUIRED] withheld",
                               considering={"tool": "g6_cloud_grant", "arguments": {
                                   "source": ["invalid"], "provider": "openai:codex"}})
    result = cloud_consent._repaired("g6_select", {}, selected, capture)
    assert result["next"]["call"] is None and result["next"]["tell_person"]


def test_stats_keeps_good_rows_around_invalid_utf8():
    from g6.core.repair_log import repair_stats

    class Log:
        def read_bytes(self):
            return b'{"event":"refused","code":"G6_E_INPUT_VALIDATION"}\n\xff\n' * 2

        def read_text(self, **kwargs):
            return self.read_bytes().decode("utf-8")

    assert repair_stats(Log())["G6_E_INPUT_VALIDATION"]["refused"] == 2


