import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_review_environment(monkeypatch):
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")
    for name in ("ABSTRACTION_MCP_DIR", "ABSTRACTION_MCP_STATE"):
        monkeypatch.delenv(name, raising=False)
    folder = Path(__file__).parent / ("home-" + uuid.uuid4().hex)
    folder.mkdir()
    monkeypatch.setenv("G6_HOME", str(folder))


def test_standalone_abstraction_can_follow_its_guidance_pointer():
    from g6.abstraction.server import mcp

    async def run():
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        description = tools["load_state"].description
        pointer = re.search(r'Details: (\w+)\("([^"]+)"\)\.', description)
        if pointer is None:
            assert "never authorise pruning until revalidated" in " ".join(description.split())
            return
        # Follow the only disclosed route to the containment and pruning warnings.
        await mcp.call_tool(pointer[1], {"component": pointer[2]})

    asyncio.run(run())


def test_review_family_does_not_require_the_optional_abstraction_extra():
    # A fresh interpreter models the supported g6[mcp] installation: neither
    # numpy nor hypothesis is installed. All MCP tools and handlers are real.
    source = '''
import asyncio, json, sys
sys.modules["numpy"] = None
sys.modules["hypothesis"] = None
from fastmcp import Client
from mcp.types import Implementation
from g6.mcp import build_server
async def run():
    async with Client(build_server(), client_info=Implementation(name="codex", version="review")) as client:
        tool = await client.call_tool("g6_describe", {"component": "g6_review_patch"})
        assert tool.data.get("available") is True, tool.data
        result = await client.call_tool("g6_describe", {"component": "review"})
        print(json.dumps(result.data))
asyncio.run(run())
'''
    completed = subprocess.run(
        [sys.executable, "-B", "-c", source],
        capture_output=True, text=True, encoding="utf-8", env=os.environ.copy(),
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    reply = json.loads(completed.stdout)
    assert reply.get("available") is True, reply
    assert any(row["call"].get("tool") == "g6_review_patch" for row in reply["members"])
