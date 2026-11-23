# SPDX-License-Identifier: Apache-2.0
"""What the abstraction server shows an agent for each tool is the same text on every Python: from 3.13 the compiler
strips a docstring's common indentation, so a tool described by its raw docstring showed other bytes on 3.13 than on
3.12, and the routing measurement's digest of the route (validation/routing/run.py, ``route_digests``) unbound."""
from __future__ import annotations

import asyncio
import inspect

import pytest

pytest.importorskip("mcp")
pytest.importorskip("numpy")

from g6.abstraction import server  # noqa: E402


def _shown() -> dict[str, str]:
    return {tool.name: tool.description or "" for tool in asyncio.run(server.mcp.list_tools())}


def test_every_tool_is_described_by_its_cleaned_docstring():
    shown = _shown()
    assert shown, "the server lists no tool"
    # fails while a description keeps the docstring's indentation or its trailing blank line, which 3.13 drops and
    # 3.12 keeps
    assert {name: text for name, text in shown.items() if text != inspect.cleandoc(text)} == {}


def test_a_multi_line_description_keeps_its_lines():
    # fails if cleaning joined or dropped lines rather than only the shared indentation
    text = _shown()["advise_pruning"]
    assert "\nGuards (each forces prune=False" in text and "\n  status guard" in text



def test_a_multi_line_disclosed_description_keeps_its_lines(disclosed_tool_guidance):
    text = disclosed_tool_guidance("advise_pruning", description=_shown()["advise_pruning"])
    assert "\nGuards (each forces prune=False" in text and "\n  status guard" in text
    assert text == inspect.cleandoc(server.advise_pruning.__doc__)
