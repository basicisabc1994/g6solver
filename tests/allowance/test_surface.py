# SPDX-License-Identifier: Apache-2.0
"""The real MCP tool and plugin hook, with offline allowance sources."""
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures('declared_mcp_grants')   # replies go to an identified, granted client

NOW = datetime(2026, 11, 8, tzinfo=UTC)


def test_mcp_tool_returns_both_providers_and_honest_advisory_limits(monkeypatch, tmp_path):
    from fastmcp import Client
    from g6.mcp import build_server

    monkeypatch.setenv("G6_HOME", str(tmp_path))
    async def call():
        async with Client(build_server()) as client:
            return await client.call_tool("g6_allowances", {"useful_parallel_work": True})
    reply = asyncio.run(call())
    data = reply.data
    assert set(data["providers"]) == {"codex", "claude"}
    assert data["advisory_only"] and "not established" in data["claude_support"]
    assert all(not r["suggest_increase"] for r in data["providers"].values())
    assert all(r["windows"] == [] for r in data["providers"].values())


def test_configured_report_refresh_is_asynchronous_and_hook_never_refreshes(monkeypatch, tmp_path):
    from g6.allowance import report
    from g6._allowance import store

    root = tmp_path / "allowances"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({"profiles": {"codex": "local-test"}}), encoding="utf-8")
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    called = []
    monkeypatch.setattr(store, "start_refresh", lambda *a, **kw: called.append((a, kw)))
    report(now=NOW, refresh=False)
    assert called == []
    result = report(now=NOW, refresh=True)
    assert len(called) == 1 and not result["providers"]["codex"]["suggest_increase"]


def test_context_note_only_on_advice_change_across_process_state(tmp_path):
    from g6._allowance.hook import context_note

    report = {"providers": {"codex": {"recommendation": "conserve", "suggest_increase": False},
                            "claude": {"recommendation": "unknown", "suggest_increase": False}}}
    note = context_note(tmp_path, report)
    assert len(note.splitlines()) == 1 and "g6_allowances" in note
    assert context_note(tmp_path, report) == ""
    report["providers"]["codex"]["recommendation"] = "usual workload"
    assert context_note(tmp_path, report)


def test_skill_codex_guidance_and_plugin_route_to_shipped_tool():
    root = Path(__file__).resolve().parents[2]
    skill = (root / "src/g6/skills/using-g6/SKILL.md").read_text(encoding="utf-8")
    assert "g6_allowances" in skill and "before optional fan-out" in skill
    assert len(skill.split("---", 2)[2].split()) <= 1000
    assert "g6_allowances" in (root / "AGENTS.md").read_text(encoding="utf-8")
    hooks = json.loads((root / "plugins/g6/hooks/hooks.json").read_text(encoding="utf-8"))
    assert "g6._allowance.hook" in json.dumps(hooks["hooks"]["UserPromptSubmit"])
