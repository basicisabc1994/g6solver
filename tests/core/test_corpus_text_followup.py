# SPDX-License-Identifier: Apache-2.0
"""Repair 2 removed text that teaches an agent to lift a person's block. G6's own declarations that carry no such text
stay in the corpus: an op's help line, a tool's first description sentence, a component's summary line. The selector
ranks on this text, so dropping all of it made one in five ops a bare name. A sensitive refusal says its sentence once."""
from __future__ import annotations

import re

from g6.core import agent_guidance
from g6.core.registry import get_registry
from g6.mcp import corpus_build
from g6.skills import corpus


def _entries() -> dict[str, dict]:
    return {e["id"]: e for e in corpus.entries()}


def test_every_clean_help_line_is_its_ops_text() -> None:
    registry, missing = get_registry(), []
    for key, entry in _entries().items():
        if entry["kind"] != "op":
            continue
        name, op = key[3:].split(".", 1)
        line = next((ln for ln in corpus_build._help_lines(registry, name) if re.match(rf"\s*{re.escape(op)}\b", ln)), "")
        if line and not agent_guidance._AUTHORITY.search(line) and not entry["text"].startswith(line):
            missing.append(key)
    assert not missing, (len(missing), missing[:10])


def test_no_op_text_teaches_the_agent_to_lift_a_block() -> None:
    bad = [k for k, e in _entries().items() if agent_guidance._AUTHORITY.search(e.get("text", ""))]
    assert not bad, bad[:10]


def test_a_tool_keeps_its_first_description_sentence() -> None:
    text = _entries()["tool:g6_review_patch"]["text"]
    assert text != corpus_build.TOOL_DATA["g6_review_patch"]["when"], text


def test_a_sensitive_refusal_says_its_sentence_once(tmp_path, monkeypatch, declared_mcp_grants) -> None:
    import asyncio
    import json

    import pytest

    pytest.importorskip("fastmcp")
    from fastmcp import Client
    from mcp.types import Implementation

    from g6.mcp import build_server

    monkeypatch.setenv("G6_HOME", str(tmp_path / "fresh-home"))

    async def run() -> dict:
        async with Client(build_server(), client_info=Implementation(name="codex", version="test")) as session:
            result = await session.call_tool("g6_invoke", {"component": "lean", "op": "check_proof",
                                                           "params_json": "{}"}, raise_on_error=False)
            return result.structured_content or json.loads(result.content[0].text)

    step = asyncio.run(run())["repair"]["step"]
    sentences = [s.strip() for s in re.split(r"(?<=\.)\s+", step) if s.strip()]
    assert len(sentences) == len(set(sentences)), step
