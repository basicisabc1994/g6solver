# SPDX-License-Identifier: Apache-2.0
"""``g6_select`` over MCP: admitted on a fresh client that has granted nothing, since it reads no repository and
answers from the client's own arguments and G6's built-in text only. No test here grants a source (the conftest's
``declared_mcp_grants`` is not used)."""
from __future__ import annotations

import asyncio
import json
import re
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


@pytest.mark.parametrize("considering", [
    [{"hypothesis": "a lone \ud800 surrogate", "predicts": "x", "id": "\udfff"}],
    '[{"hypothesis": "a lone \\ud800 surrogate", "predicts": "x"}]',
    [{"requirement": "r", "id": "\ud800"}, {"tool": "g6_\udc00"}],
])
def test_agent_text_that_cannot_be_written_as_utf8_still_gets_the_envelope(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, considering: object) -> None:
    """A lone surrogate the client sent (a legal JSON escape) is replaced before the reply leaves, so the client gets
    G6's envelope, not a bare tool error."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "fresh-home"))

    async def run() -> tuple[bool, object]:
        async with Client(build_server(), client_info=Implementation(name="codex", version="test")) as session:
            result = await session.call_tool("g6_select", {"task": "review my patch", "considering": considering},
                                             raise_on_error=False)
            return result.is_error, result.structured_content or result.content[0].text
    is_error, reply = asyncio.run(run())
    assert not is_error, reply
    assert reply["completion_state"] == "qualified-draft" and reply["request_id"]
    assert "�" in json.dumps(reply["considering"], ensure_ascii=False)


def _texts(calls: list[tuple[str, dict]], client: str = "codex") -> list[str]:
    async def run() -> list[str]:
        async with Client(build_server(), client_info=Implementation(name=client, version="test")) as session:
            return [str(await session.call_tool(name, arguments)) for name, arguments in calls]
    return asyncio.run(run())


class _ReadyRung:
    """Stands in for the ColBERT rung with its extra and weights present (its ready() says "")."""

    def ready(self) -> str:
        return ""

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [float(len(t) % 7) for t in texts]


def test_a_colbert_rerank_says_nothing_of_this_computers_extras(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ported from wire-entry review 2, finding 3: g6_select (client_request and g6_operational only) answers
    rerank="colbert" lexically in fixed words, the same whether the ColBERT extra and weights are on this computer
    or not."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "fresh-home"))
    arguments = {"task": "check my patch before I say it is done", "phase": "verify", "rerank": "colbert"}
    absent, = _texts([("g6_select", arguments)])
    from g6.knowledge.ctx_colbert import rungs
    monkeypatch.setitem(rungs.RUNGS._rungs, "colbert", _ReadyRung())
    present, = _texts([("g6_select", arguments)])
    for reply in (absent, present):
        assert '"ok":true' in reply, reply[:300]
        assert "not ready on this machine" not in reply and "colbert ran" not in reply
    strip = re.compile(r"""["'](request_id|run_id|task_id)["']:\s*["'][^"']*["']""")    # ids differ per call
    assert strip.sub("", absent) == strip.sub("", present)


SECRET = "zqxdropinsecret"


def test_a_dropin_under_a_shipped_name_reaches_no_fresh_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                             registry_reset: object) -> None:
    """Ported from wire-entry review 2, finding 7: a package dropped into G6's namespace under a shipped family
    member's name (adapt_audio) is never imported or run by g6_select, and none of its text reaches a fresh reply:
    candidates and their validation come from the corpus built from shipped code."""
    import sys

    import g6

    folder = tmp_path / "ns"
    (folder / "adapt_audio").mkdir(parents=True)
    (folder / "adapt_audio" / "__init__.py").write_text(
        f'"""{SECRET} third-party text."""\nraise ImportError("{SECRET}: the person\'s private config text")\n',
        encoding="utf-8")
    monkeypatch.setattr(g6, "__path__", [*g6.__path__, str(folder)])
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    try:
        selected, = _texts([("g6_select", {"task": "normalise the loudness of an audio file with adapt_audio",
                                           "phase": "implement", "considering": ["adapt_audio.normalise"]})])
        assert SECRET not in selected and "g6.adapt_audio" not in sys.modules, selected[:400]
    finally:
        sys.modules.pop("g6.adapt_audio", None)


def test_it_takes_no_repository_argument() -> None:
    async def schema() -> dict:
        return (await build_server().get_tools())["g6_select"].parameters
    properties = asyncio.run(schema())["properties"]
    assert set(properties) == {"task", "phase", "last_outcome", "considering", "limit", "rerank"}
    assert "repo" not in properties
