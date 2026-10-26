# SPDX-License-Identifier: Apache-2.0
"""Get started: install, connect any of the agents the owner named (and the common MCP clients), use the library or a
bundle. Each client's snippet is the shape its own documentation gives (checked 2026-10-19)."""
from __future__ import annotations

import html
import json
import re
import tomllib
from pathlib import Path

import pytest

from pages import clients

DOCS = Path(__file__).resolve().parents[2] / "site" / "docs"


@pytest.fixture
def page(client):
    return html.unescape(client.get("/get-started/").content.decode())


def test_every_named_agent_has_a_tab(page):
    for name in ("Claude Code", "Codex", "OpenCode", "Hermes", "Muse Code", "Cursor", "VS Code", "Gemini CLI",
                 "Windsurf", "Zed", "Claude Desktop"):
        assert f">{name}<" in page, name


def test_each_snippet_is_the_shape_its_client_reads():
    by = {c.key: c for c in clients.CLIENTS}
    assert tomllib.loads(by["codex"].snippet) == {"mcp_servers": {"g6": {"command": "g6-mcp"}}}
    assert json.loads(by["opencode"].snippet)["mcp"]["g6"] == {"type": "local", "command": ["g6-mcp"], "enabled": True}
    assert json.loads(by["vscode"].snippet) == {"servers": {"g6": {"type": "stdio", "command": "g6-mcp", "args": []}}}
    assert "context_servers" in json.loads(by["zed"].snippet)
    assert json.loads(by["muse"].snippet)["schema_version"] == 1
    assert "mcp_servers:" in by["hermes"].snippet and "command: g6-mcp" in by["hermes"].snippet
    for key in ("claude_code", "cursor", "gemini", "windsurf", "claude_desktop"):
        assert json.loads(by[key].snippet)["mcpServers"]["g6"]["command"] == "g6-mcp", key
    assert all(c.docs.startswith("https://") for c in clients.CLIENTS)


def test_the_page_shows_each_command_and_its_source(page):
    for c in clients.CLIENTS:
        if c.cli:
            assert c.cli in page, c.key
        assert c.docs in page, c.key
    assert "claude mcp add --transport stdio --scope user g6 -- g6-mcp" in page


def test_the_using_g6_skill_is_one_command_away(page):
    # handoff skills/README.md: the Get started page links the skill that teaches an agent to use G6
    assert "g6 skills install" in page and "using-g6" in page
    assert "g6 skills install --with-superpowers" in page


def test_the_bundle_guide_is_on_the_page(page):
    assert "Choose a bundle" in page and "blocked-escalated" in page


def test_jev_on_openrouter_has_its_own_model_tab(page):
    assert ">Jev (OpenRouter)<" in page
    assert "G6_LLM_PROVIDER=openrouter" in page and "G6_LLM_MODEL=typesafe/jev-router" in page


def test_the_page_says_keys_go_in_the_key_file(page):
    # owner ruling 2026-10-21: a person gives G6 a key only by writing it in their key file
    assert "~/.g6/.env" in page and "g6 doctor" in page and "$env:" not in page


def test_where_a_person_installs_they_read_that_unconfined_code_runs_with_their_rights(client):
    # CORE_SANDBOX-01 (owner ruling, 2026-10-23: warn, never restrict): the README's Install section, g6 doctor and the
    # sensitive-op help say it in confine.UNCONFINED_WARNING's words. Fails while the get-started page's Install step,
    # the developers page or a docs install page leaves the paragraph out
    from g6.core.confine import UNCONFINED_WARNING

    def flat(page: str) -> str:
        return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())

    started = client.get("/get-started/").content.decode()
    step = started.split('id="from-agent"', 1)[1].split("</li>", 1)[0]
    assert UNCONFINED_WARNING in flat(step) and "g6 doctor" in flat(step)
    assert UNCONFINED_WARNING in flat(client.get("/developers/").content.decode())
    for doc in ("install.md", "developers.md"):
        assert UNCONFINED_WARNING in " ".join((DOCS / doc).read_text(encoding="utf-8").split()), doc


def test_no_page_says_a_bundle_takes_or_stores_a_key(client):
    # owner ruling 2026-10-21: a key comes only from the person's key file; g6/bundles/server.py never takes one from
    # its page (a body with "api_key" is refused) and keeps only the provider and model. Fails while install.md says
    # "paste your key" or the Security page says a key given to a bundle is stored in the .g6 folder
    def flat(page: str) -> str:
        return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())

    pages = {path: flat(client.get(path).content.decode()) for path in ("/get-started/", "/security/")}
    docs = {doc: " ".join((DOCS / doc).read_text(encoding="utf-8").split()) for doc in ("install.md",)}
    for where, text in {**pages, **docs}.items():
        assert "paste your key" not in text and "If you give a bundle one" not in text, where
        assert "~/.g6/.env" in text and "never takes a key" in text, where


def test_each_model_snippet_builds_the_caller_it_names_from_the_key_file(key_file):
    from g6.core import llm

    for m in clients.MODELS:
        key_file(m.snippet + "\n")                 # where the page says the lines go
        env = dict(line.split("=", 1) for line in m.snippet.splitlines())
        caller = llm.provider_from_env({})
        inner = caller._inner
        assert inner.model == env.get("G6_LLM_MODEL", inner.model), m.key
        if env["G6_LLM_PROVIDER"] == "openrouter":
            assert inner.base_url == "https://openrouter.ai/api/v1"
