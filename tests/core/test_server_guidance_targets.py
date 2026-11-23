# SPDX-License-Identifier: Apache-2.0
"""A tool's short guidance may point only to a tool on that same server."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from g6.mcp import registrations


def test_every_shipped_server_guidance_pointer_has_a_local_target():
    # The source inventory makes adding another FastMCP instance extend this
    # check, rather than silently restricting it to today's two servers.
    modules = {target.split(":", 1)[0] for target in registrations.SERVERS.values()}
    assert registrations.server_sources(registrations.package_root()) == modules
    for server_id, server in registrations._built():
        tools = dict(registrations._listed(server))
        assert tools, server_id
        for name, tool in tools.items():
            for target in re.findall(r'''Details:\s*(\w+)\(\s*["'][^"']+["']\s*\)''', tool.description or ""):
                assert target in tools, (server_id, name, target)


def _standalone_guidance(component):
    from g6.abstraction import server

    async def run():
        result = await server.mcp.call_tool("g6_describe", {"component": component})
        # The SDK may pair its content blocks with structured output. Read the
        # blocks, rather than treating the first list itself as a TextContent.
        content = result[0] if isinstance(result, tuple) else result
        return json.loads(content[0].text)

    return asyncio.run(run())


def test_standalone_abstraction_discloses_all_original_warnings():
    from g6.abstraction import server
    from g6.core.tool_guidance import short_description

    tools = dict(registrations._listed(server.mcp))
    for name, tool in tools.items():
        original = inspect.cleandoc(getattr(server, name).__doc__)
        assert tool.description == short_description(name, original)
        pointer = re.search(r'Details: (\w+)\("([^"\n]+)"\)\.', tool.description)
        assert pointer is not None and pointer[1] == "g6_describe" and pointer[2] == name
        reply = _standalone_guidance(pointer[2])
        assert reply["available"] is True and reply["name"] == name
        assert reply["call"] == {"server": "abstraction", "tool": name}
        assert reply["guidance"] == original
    restored = _standalone_guidance("load_state")["guidance"]
    assert "never authorise pruning until revalidated" in " ".join(restored.split())
    assert "ABSTRACTION_MCP_DIR" in restored and "G6_MCP_SENSITIVE" in restored


@pytest.mark.parametrize("component", ["g6_review_patch", "review", "abstraction", "absent_tool"])
def test_standalone_describe_refuses_names_outside_its_own_tools(component):
    reply = _standalone_guidance(component)
    assert reply["available"] is False
    assert reply["code"] == "tool_not_registered"
    assert reply["completion_state"] == "blocked-escalated"
    assert "guidance" not in reply and "call" not in reply


def test_standalone_compaction_requires_a_local_target(monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from g6.abstraction import server

    isolated = FastMCP("without-local-target")
    monkeypatch.setattr(server, "mcp", isolated)

    def read_file(path: str) -> str:
        """Read only inside the declared folder."""
        return path

    with pytest.raises(ValueError, match="g6_describe"):
        server._tool()(read_file)
    assert registrations._listed(isolated) == []


def test_local_describe_registration_keeps_main_discovery_unambiguous():
    from g6.mcp.families import describe, family_for

    assert family_for({"id": "mcp:abstraction/g6_describe", "kind": "mcp"}) == "logic"
    reply = describe("g6_describe")
    assert reply["available"] is True
    assert reply["family"] == "discover"
    assert reply["call"]["tool"] == "g6_describe" and "server" not in reply["call"]
    assert "Give a family name" in reply["guidance"]


def test_compacting_a_server_without_describe_refuses_before_displacing_guidance():
    from fastmcp import FastMCP
    from g6.mcp.families import compact_server

    server = FastMCP("without-describe")

    @server.tool()
    def read_file(path: str) -> str:
        """Read only inside the person's declared folder."""
        return path

    before = {name: tool.description for name, tool in registrations._listed(server)}
    with pytest.raises(ValueError, match="g6_describe"):
        compact_server(server, "test-without-describe")
    assert {name: tool.description for name, tool in registrations._listed(server)} == before


@pytest.mark.parametrize("standalone_first", [True, False])
def test_compacting_shared_tools_copies_only_the_main_description(standalone_first):
    from fastmcp import FastMCP
    from fastmcp.tools import Tool
    from g6.core.tool_guidance import short_description
    from g6.mcp.families import compact_server, full_guidance

    def read_file(path: str) -> str:
        """Read inside the declared folder.

        Keep every warning line.
          Keep the indented guard too.
        """
        return path

    shared = Tool.from_function(read_file)
    original = shared.description
    standalone = FastMCP("standalone")
    main = FastMCP("main")

    @main.tool()
    def g6_describe(component: str) -> str:
        """Read full guidance for a registered tool."""
        return full_guidance("shared-copy-test", component)

    if standalone_first:
        standalone.add_tool(shared)
    main.add_tool(shared)
    compact_server(main, "shared-copy-test")
    if not standalone_first:
        standalone.add_tool(shared)

    main_tool = dict(registrations._listed(main))["read_file"]
    standalone_tool = dict(registrations._listed(standalone))["read_file"]
    assert standalone_tool is shared
    assert main_tool is not shared
    assert standalone_tool.description == original == inspect.cleandoc(read_file.__doc__)
    assert main_tool.description == short_description("read_file", original)
    assert full_guidance("shared-copy-test", "read_file") == original
    assert main_tool.parameters == standalone_tool.parameters
    assert main_tool.fn is standalone_tool.fn


@pytest.mark.parametrize("first", ["g6", "abstraction"])
def test_shipped_server_construction_preserves_each_description(first):
    # Fresh interpreters cover both orders without retaining imported singleton
    # servers or guidance caches from another test.
    repo = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src")
    env["G6_DISABLE_LLM"] = "1"
    for key in ("G6_CLAUDE", "G6_CODEX"):
        env[key] = r"C:\nonexistent\g6-no-host.exe"
    for key in ("ABSTRACTION_MCP_STATE", "ABSTRACTION_MCP_DIR"):
        env.pop(key, None)
    done = subprocess.run([sys.executable, "-B", "-c", '''
import inspect, sys
from g6.mcp import registrations
from g6.mcp.families import full_guidance
from g6.core.tool_guidance import short_description
def descriptions(server):
    return {name: tool.description for name, tool in registrations._listed(server)}
first = sys.argv[1]
other = 'abstraction' if first == 'g6' else 'g6'
servers = {first: registrations._load(registrations.SERVERS[first])}
before = descriptions(servers[first])
servers[other] = registrations._load(registrations.SERVERS[other])
assert descriptions(servers[first]) == before
from g6.abstraction import server as abstraction
standalone = descriptions(servers['abstraction'])
assert standalone == {name: short_description(name, inspect.cleandoc(getattr(abstraction, name).__doc__))
                      for name in standalone}
assert 'g6_describe' in standalone
assert all(full_guidance('abstraction', name) == inspect.cleandoc(getattr(abstraction, name).__doc__)
           for name in standalone)
main = descriptions(servers['g6'])
assert 'g6_describe' in main
assert main == {name: short_description(name, full_guidance('g6', name)) for name in main}
registrations._load(registrations.SERVERS['g6'])
assert descriptions(servers['abstraction']) == standalone
assert descriptions(servers['g6']) == main
''', first], cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert done.returncode == 0, done.stdout + done.stderr
