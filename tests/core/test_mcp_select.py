# SPDX-License-Identifier: Apache-2.0
"""``g6_select`` over MCP: admitted on a fresh client that has granted nothing, since it reads no repository and
answers from the client's own arguments and G6's built-in text only. No test here grants a source (the conftest's
``declared_mcp_grants`` is not used)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from mcp.types import Implementation  # noqa: E402

from g6.mcp import build_server  # noqa: E402


def _call(arguments: dict, client: str = "codex") -> dict:
    async def run() -> dict:
        async with Client(build_server(), client_info=Implementation(name=client, version="test")) as session:
            result = await session.call_tool("g6_select", arguments)
            return result.structured_content or json.loads(result.content[0].text)
    return asyncio.run(run())


@pytest.mark.parametrize("client", ["codex", "claude-code"])
def test_a_fresh_client_with_no_grant_gets_the_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         client: str) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "fresh-home"))
    reply = _call({"task": "review my patch before I say it is done", "phase": "verify"}, client)
    # a withheld reply is blocked-escalated, not ok, and carries cloud_consent_code (g6.mcp.cloud_consent.withheld_reply)
    assert "cloud_consent_code" not in reply and reply["ok"] is True, json.dumps(reply)[:400]
    assert reply["completion_state"] == "qualified-draft"
    assert {c["id"] for c in reply["candidates"][:2]} & {"tool:g6_review_patch", "tool:g6_review_until_clean"}


def test_a_refusal_passed_back_reroutes_over_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_HOME", str(tmp_path / "fresh-home"))
    reply = _call({"task": "plan the change", "phase": "plan", "last_outcome": "G6_E_CLOUD_CONSENT_REQUIRED",
                   "considering": ["g6_premortem"], "limit": 3})
    assert reply["candidates"][0]["call"]["tool"] == "g6_cloud_grant"
    assert len(reply["candidates"]) <= 3


def test_its_reply_is_the_selectors_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.core import selector

    monkeypatch.setenv("G6_HOME", str(tmp_path / "fresh-home"))
    arguments = {"task": "check that the summary's claims are supported by the sources", "phase": "verify"}
    over_mcp = _call(arguments)
    direct = selector.select(**arguments)
    for reply in (over_mcp, direct):
        reply.pop("request_id"), reply.pop("run_id")
    assert over_mcp == direct and over_mcp["candidates"]


def test_it_takes_no_repository_argument() -> None:
    async def schema() -> dict:
        return (await build_server().get_tools())["g6_select"].parameters
    properties = asyncio.run(schema())["properties"]
    assert set(properties) == {"task", "phase", "last_outcome", "considering", "limit", "rerank"}
    assert "repo" not in properties
