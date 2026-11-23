# SPDX-License-Identifier: Apache-2.0
"""G6's one text for the moment of need (plan revision 8, A): ``g6.mcp.instructions.TEXT`` is the server's MCP
instructions, so a client connected to G6 is told when to call it before any skill is read; it is short, coding
first, and names only tools the server registers."""
from __future__ import annotations

import asyncio
import re

import pytest

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402

from g6.mcp import build_server  # noqa: E402


def test_the_text_is_short_and_names_only_registered_tools() -> None:
    from g6.mcp import registrations
    from g6.mcp.instructions import TEXT
    from g6.mcp.families import family_table

    assert len(TEXT.split()) <= 150, len(TEXT.split())
    assert TEXT.startswith(family_table())
    named = set(re.findall(r"\b(?:g6|metaharness)_[a-z_]+\b", TEXT))
    registered = set(registrations.recorded()["g6"])
    assert {"g6_review_patch", "metaharness_job", "g6_review_until_clean", "g6_premortem", "g6_confirm_behaviour",
            "g6_select", "g6_cloud_grant"} <= named
    assert named <= registered, named - registered
    assert "G6_E_CLOUD_CONSENT_REQUIRED" in TEXT and "g6 setup --allow" in TEXT
    assert "never grant for them" in TEXT and "Never call a result verified unless G6 says so" in TEXT


def test_a_connected_client_receives_the_text_as_the_servers_instructions() -> None:
    from g6.mcp.instructions import TEXT

    async def run() -> str | None:
        async with Client(build_server()) as session:
            return session.initialize_result.instructions
    assert asyncio.run(run()) == TEXT


def test_the_routing_check_reads_the_instructions_as_guidance() -> None:
    from g6.mcp import registrations
    from g6.mcp.instructions import TEXT

    assert registrations.shown().get("server:g6") == TEXT
