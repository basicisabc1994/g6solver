# SPDX-License-Identifier: Apache-2.0
"""The offline recorder lists what G6's MCP servers really register (0.3 design, 14.1): tools added by a decorator, in
a loop, by ``add_tool`` or under another name; from every module that constructs a server; with nothing started; and
never a shorter list when a server cannot be built."""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

from g6.mcp import registrations


def test_tools_registered_in_a_loop_are_recorded() -> None:
    names = registrations.recorded()["g6"]
    assert {"metaharness_inspect", "g6_improve_status", "g6_help", "g6_review_patch"} <= set(names)
    assert list(names) == sorted(names) and len(set(names)) == len(names)


def test_a_tool_added_by_any_means_fastmcp_allows_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp import FastMCP
    from fastmcp.tools import Tool

    def build() -> FastMCP:
        server = FastMCP("synthetic")

        @server.tool()
        def decorated() -> str:
            return ""

        @server.tool(name="renamed_by_the_decorator")
        def original_name() -> str:
            return ""

        def looped_one() -> str:
            return ""

        def looped_two() -> str:
            return ""

        for tool in (looped_one, looped_two):
            server.tool()(tool)

        def added() -> str:
            return ""

        server.add_tool(Tool.from_function(added, name="added_by_add_tool"))
        return server

    monkeypatch.setattr(registrations, "_load", lambda target: build())
    assert registrations.recorded({"synthetic": "anything:build"}) == {
        "synthetic": ("added_by_add_tool", "decorated", "looped_one", "looped_two", "renamed_by_the_decorator")}


def test_every_fastmcp_source_is_a_known_server() -> None:
    known = {target.split(":", 1)[0] for target in registrations.SERVERS.values()}
    assert registrations.server_sources(registrations.package_root()) == known


def test_a_new_module_that_constructs_a_server_is_found(tmp_path: Path) -> None:
    package = tmp_path / "g6"
    (package / "extra").mkdir(parents=True)
    (package / "extra" / "__init__.py").write_text("from fastmcp import FastMCP\nserver = FastMCP('second')\n", "utf-8")
    (package / "low.py").write_text("from mcp.server.lowlevel import Server\n", "utf-8")
    (package / "words.py").write_text('"""Says FastMCP( in a docstring only."""\n', "utf-8")
    assert registrations.server_sources(package) == {"g6.extra", "g6.low"}


def test_recording_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    import fastmcp
    from mcp.server.fastmcp import FastMCP as SdkFastMCP

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the recorder started something")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(fastmcp.FastMCP, "run", refuse)
    monkeypatch.setattr(SdkFastMCP, "run", refuse)
    found = registrations.recorded()
    assert set(found) == set(registrations.SERVERS) and all(found.values())


def test_a_server_that_cannot_be_imported_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    servers = {**registrations.SERVERS, "broken": "g6_no_such_module_for_this_test:mcp"}
    monkeypatch.setitem(registrations.EXTRAS, "broken", "some-extra")
    with pytest.raises(registrations.RegistrationError) as caught:
        registrations.recorded(servers)
    assert "'broken'" in str(caught.value) and "g6[some-extra]" in str(caught.value)

    def gone(target: str) -> object:
        raise SystemExit("g6-mcp needs the MCP extra")

    monkeypatch.setattr(registrations, "_load", gone)
    with pytest.raises(registrations.RegistrationError, match="g6\\[mcp\\]"):
        registrations.recorded({"g6": registrations.SERVERS["g6"]})


def test_a_listing_that_needs_an_event_loop_is_an_error_not_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    class Waits:
        async def get_tools(self) -> dict:
            await asyncio.sleep(0)
            return {}

    monkeypatch.setattr(registrations, "_load", lambda target: Waits())
    with pytest.raises(registrations.RegistrationError, match="event loop"):
        registrations.recorded({"waits": "anything:server"})


def test_every_server_has_its_extra_named() -> None:
    assert set(registrations.EXTRAS) == set(registrations.SERVERS)


def test_no_tool_argument_can_answer_a_dialog() -> None:
    """One inventory for the class "an argument answers for the person": the behavioural tests of each dialog stay."""
    taken = {(f"mcp:{server}/{tool}", argument)
             for server, tools in registrations.arguments().items()
             for tool, names in tools.items() for argument in names if argument in registrations.DIALOG_WORDS}
    exempt = {(key, argument) for key, names in registrations.DIALOG_EXEMPT.items() for argument in names}
    assert taken == exempt, (sorted(taken - exempt), sorted(exempt - taken))
    assert all(len(why) >= 20 for names in registrations.DIALOG_EXEMPT.values() for why in names.values())
