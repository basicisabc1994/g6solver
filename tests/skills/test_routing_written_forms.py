# SPDX-License-Identifier: Apache-2.0
"""One reading of "a route names a tool" (0.3 design 14.1). ``routing.owned`` is the text a route owns and
``routing.forms`` the written forms that name a tool; ``NOT_NAMED`` and ``STALE`` both read them. The cases are
derived from the manifest and the recorded servers: every route, every surface it owns, every registered tool."""
from __future__ import annotations

from typing import Any

import pytest

from g6.mcp import registrations
from g6.skills import routing

GONE = "a_tool_no_server_has"


@pytest.fixture(scope="module")
def given() -> dict[str, Any]:
    found = registrations.inputs()
    assert routing.check(routing.load(), **found) == []
    assert all(GONE not in tool for tools in found["registrations"].values() for tool in tools)
    return found


def _with(given: dict[str, Any], surface: str, text: str) -> dict[str, Any]:
    """The inputs with ``surface`` (a guidance text or a detail file) holding ``text``."""
    where = "texts" if surface in given["texts"] else "details"
    return {**given, where: {**given[where], surface: text}}


def _prefixed(tool: str) -> bool:
    return routing._TOOL.fullmatch(tool) is not None


def test_every_registered_tool_is_named_by_each_of_its_written_forms_and_by_no_prose_word(given: dict[str, Any]) -> None:
    for server, tools in given["registrations"].items():
        for tool in tools:
            for written in (f"Call `{tool}` now.", f"Call `{tool}()` now.", f"Call `{tool}(x, y=1)` now.",
                            f"The client shows mcp__{server}__{tool}.", f"Use `mcp__{server}__{tool}`."):
                assert routing.forms(written).names(server, tool), (server, tool, written)
            prose = f"You may {tool} it, or {tool}ed it."
            assert routing.forms(prose).names(server, tool) == _prefixed(tool), (server, tool)
            assert not routing.forms(f"mcp__another__{tool}").names(server, tool)
            assert not routing.forms(f"`{tool}_more` and `{tool}x()`").names(server, tool)


def test_a_route_owns_its_section_and_each_of_its_detail_files_and_nothing_else(given: dict[str, Any]) -> None:
    manifest = routing.load()
    root = given["texts"][routing.ROOT]
    mine = routing.owned(manifest, root, {**given["details"], **given["texts"]})
    assert set(mine) == set(manifest.routes)
    for route_id, route in manifest.routes.items():
        assert set(mine[route_id]) == {routing.ROOT, *route.detail}, route_id
        assert mine[route_id][routing.ROOT] == routing.section(root, route.anchor) != ""


def _surfaces(given: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """(route, surface, whether one of the route's servers has a tool the prefix does not mark)."""
    manifest = routing.load()
    out = []
    for route_id, route in sorted(manifest.routes.items()):
        servers = {key[4:].split("/", 1)[0] for key, entry in manifest.entries.items()
                   if entry.route == route_id and key.startswith("mcp:")}
        plain = any(not _prefixed(tool) for server in servers for tool in given["registrations"][server])
        out += [(route_id, surface, plain) for surface in (routing.ROOT, *route.detail)]
    return out


def test_a_stale_name_is_found_in_every_surface_a_route_owns_in_every_written_form(given: dict[str, Any]) -> None:
    manifest, missed = routing.load(), []
    surfaces = _surfaces(given)
    assert len(surfaces) >= len(manifest.routes) + 4 and any(plain for _, _, plain in surfaces)
    root = given["texts"][routing.ROOT]
    for route_id, surface, plain in surfaces:
        written = [f"g6_{GONE}", f"`metaharness_{GONE}()`", f"mcp__g6__{GONE}", f"`mcp__nowhere__{GONE}`"]
        written += [f"`{GONE}`", f"`{GONE}()`", f"`{GONE}(domain, depth=2)`"] if plain else []
        for form in written:
            if surface == routing.ROOT:                 # inside the route's own section: one more line at its end
                section = routing.section(root, manifest.routes[route_id].anchor).rstrip()
                assert root.count(section) == 1, route_id
                text = root.replace(section, f"{section}\n  Then call {form}.")
            else:
                text = {**given["details"], **given["texts"]}[surface] + f"\n\nThen call {form}.\n"
            found = [p for p in routing.check(manifest, **_with(given, surface, text))
                     if p.code == "STALE" and GONE in p.said]
            if [p.key for p in found] != [surface]:
                missed.append((route_id, surface, form, [p.key for p in found]))
    assert not missed, missed


def test_a_routed_tool_is_named_only_in_a_written_form(given: dict[str, Any]) -> None:
    """For every agent-facing tool: with its route's own text naming every other tool of the route and holding this
    one only as a prose word, the tool is ``NOT_NAMED`` exactly when the prefix does not mark it; named in backticks
    it is not."""
    manifest, wrong = routing.load(), []
    for key, entry in sorted(manifest.entries.items()):
        if not (entry.agent_facing and key.startswith("mcp:")):
            continue
        server, tool = key[4:].split("/", 1)
        route = manifest.routes[entry.route]
        others = sorted(k[4:].split("/", 1)[1] for k, e in manifest.entries.items()
                        if e.route == entry.route and k.startswith("mcp:") and k != key)
        named = f"{route.anchor}\nIts tools: " + ", ".join(f"`{name}`" for name in others) + "."
        for said, expected in ((f" We {tool} things here.", [] if _prefixed(tool) else [key]), (f" Call `{tool}`.", [])):
            root = f"{routing.ABSENT_TOOL_RULE}\n\n{named}{said}\n"
            one = routing.Manifest(manifest.format, manifest.release, {entry.route: routing.Route(route.id, route.anchor, ())},
                                   {k: e for k, e in manifest.entries.items() if e.route == entry.route
                                    and k.startswith("mcp:")})
            found = routing.check(one, registrations={s: tuple(t for t in tools if f"mcp:{s}/{t}" in one.entries)
                                                      for s, tools in given["registrations"].items()},
                                  ops={}, texts={routing.ROOT: root}, root_words=10, version=given["version"], files=())
            if [p.key for p in found if p.code == "NOT_NAMED"] != expected:
                wrong.append((key, said, [(p.code, p.key) for p in found]))
    assert not wrong, wrong[:5]


def test_a_components_own_skill_may_name_its_code_and_other_components(given: dict[str, Any]) -> None:
    """Derived from the routes: a detail file outside the root skill's folder has the names of its component's code;
    without them the check holds every backticked name there against the route's tools."""
    own = sorted(surface for _route, surface, plain in _surfaces(given)
                 if plain and surface != routing.ROOT and not surface.startswith(routing.REFERENCES))
    assert own and set(own) <= set(given["identifiers"])
    strict = routing.check(routing.load(), **{**given, "identifiers": {}})
    assert strict and {p.code for p in strict} == {"STALE"} and {p.key for p in strict} == set(own)
    assert all(GONE not in given["identifiers"][surface] for surface in own)
