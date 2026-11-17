"""Final reply budgeting and strict MCP mode input regressions; offline only."""
import ast
import asyncio
import json

import pytest

from g6.core import consent_callers, selector


def test_reply_budget_is_the_single_final_selection_step():
    tree = ast.parse(selector.__loader__.get_source(selector.__name__))
    select = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "select")
    final = select.body[-1]
    assert isinstance(final, ast.Return) and isinstance(final.value, ast.Call)
    assert isinstance(final.value.func, ast.Name) and final.value.func.id == "_budget_reply"
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "_budget_reply"]
    assert len(calls) == 1, "all reply construction must precede one final budget step"


@pytest.mark.parametrize("mode", [1, 0, True, False, None, "yes", [], {}])
def test_mcp_invalid_rerank_has_input_envelope_without_provider_access(monkeypatch, mode):
    from fastmcp import Client
    from mcp.types import Implementation
    from g6.mcp import build_server

    accessed = []

    def forbidden():
        accessed.append(True)
        pytest.fail("invalid rerank must not resolve a model")

    monkeypatch.setattr(consent_callers, "provider_from_env", forbidden)

    async def call():
        async with Client(build_server(), client_info=Implementation(name="codex", version="test")) as client:
            return await client.call_tool("g6_select", {"task": "PRIVATE_TASK_MARKER", "rerank": mode,
                                                        "last_outcome": "PRIVATE_OUTCOME_MARKER"},
                                          raise_on_error=False)

    result = asyncio.run(call())
    assert result.content[0].text.startswith("{"), "invalid mode needs G6's JSON validation envelope"
    body = json.loads(result.content[0].text)
    assert body["ok"] is False and body["completion_state"] == "blocked-escalated"
    assert body["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert "PRIVATE_TASK_MARKER" not in result.content[0].text
    assert "PRIVATE_OUTCOME_MARKER" not in result.content[0].text
    assert not accessed


@pytest.mark.parametrize("mode", [1, 0, None, "yes", "Auto", "", [], {}])
def test_mcp_invalid_propose_has_input_envelope_without_provider_or_repository_access(monkeypatch, mode):
    from fastmcp import Client
    from mcp.types import Implementation
    from g6.mcp import build_server, mission

    accessed = []

    def forbidden(*args):
        accessed.append(True)
        pytest.fail("invalid propose must not resolve a model or read a repository")

    monkeypatch.setattr(consent_callers, "provider_from_env", forbidden)
    monkeypatch.setattr(mission.repo_tests, "checked_repo", forbidden)

    async def call():
        async with Client(build_server(), client_info=Implementation(name="codex", version="test")) as client:
            return await client.call_tool("g6_premortem", {"repo": "PRIVATE_REPO_MARKER",
                "task": "PRIVATE_TASK_MARKER", "propose": mode}, raise_on_error=False)

    result = asyncio.run(call())
    assert result.content[0].text.startswith("{"), "invalid mode needs G6's JSON validation envelope"
    body = json.loads(result.content[0].text)
    assert body["ok"] is False and body["completion_state"] == "blocked-escalated"
    assert body["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert "PRIVATE_REPO_MARKER" not in result.content[0].text
    assert "PRIVATE_TASK_MARKER" not in result.content[0].text
    assert not accessed
