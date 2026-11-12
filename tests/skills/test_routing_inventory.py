# SPDX-License-Identifier: Apache-2.0
"""The inventory test, from the code (0.3 design, 14.1): every MCP tool G6 registers and every typed op a component
lists resolves to a route of the root skill or to a reasoned exemption, and the guidance names nothing that is not
there. A change that adds a tool or an op without its manifest entry fails here.

Also 14.3: no Experta engine ships, and the route that holds its rule-reasoning role names ops that exist."""
from __future__ import annotations

import re
import tomllib

import pytest

from g6.core import discovery, skills
from g6.core.registry import get_registry
from g6.mcp import registrations
from g6.skills import routing

PACKAGE = registrations.package_root()
REPO = PACKAGE.parent.parent


def test_every_shipped_tool_and_op_is_routed_or_exempt() -> None:
    problems = routing.check(routing.load(), **registrations.inputs())
    assert problems == [], "\n".join(f"{p.code} {p.key}: {p.said}" for p in problems)


def test_the_inventory_is_not_empty() -> None:
    """A recorder or a registry that returned nothing would pass the check above with an empty manifest."""
    given = registrations.inputs()
    assert len(given["registrations"]["g6"]) >= 30 and len(given["registrations"]["abstraction"]) >= 17
    assert sum(len(listed) for listed in given["ops"].values()) >= 400
    assert routing.ROOT in given["texts"] and "help:g6_help" in given["texts"]
    assert any(surface.startswith("repo:plugins/") for surface in given["texts"])
    manifest = routing.load()
    assert set(manifest.entries) == routing.members(given["registrations"], given["ops"])
    assert {entry.route for entry in manifest.entries.values() if entry.agent_facing} == set(manifest.routes)


def test_the_manifest_is_strict() -> None:
    text = routing.manifest_path().read_text(encoding="utf-8")
    assert routing.dumps(routing.loads(text)) == text, "routing.json is not in its written form (routing.dumps)"
    for broken in (text.replace('"format": "g6.routing/1"', '"format": "g6.routing/1", "default_route": "discover"'),
                   text.replace('"agent_facing": true, "route": "discover"',
                                '"agent_facing": true, "exemption": "a reason of twenty characters", '
                                '"route": "discover"', 1),
                   text.replace('"agent_facing": true, "route": "discover", ', '"agent_facing": true, ', 1),
                   text.replace('"agent_facing": true, "route": "discover"',
                                '"agent_facing": false, "exemption": "short"', 1)):
        assert broken != text
        with pytest.raises(routing.ManifestError):
            routing.loads(broken)
    first = next(line for line in text.splitlines() if line.startswith('    "mcp:'))
    with pytest.raises(routing.ManifestError, match="duplicate"):
        routing.loads(text.replace(first, first + "\n" + first, 1))


def test_no_entry_is_matched_by_a_pattern() -> None:
    """Every key is one literal member: the format has no wildcard, prefix or default to satisfy an entry by."""
    manifest = routing.load()
    assert all(re.fullmatch(r"(mcp:[a-z0-9_-]+/\w+|op:[a-z0-9_]+/[a-z0-9_]+)", key) for key in manifest.entries)
    assert not any(character in key for key in manifest.entries for character in "*?[]{}()|^$\\ ")


def test_the_root_cap_is_the_skills_cap() -> None:
    assert routing.MAX_ROOT_WORDS == skills.MAX_WORDS == 1800


def test_a_component_whose_ops_cannot_be_listed_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """``discovery.ops`` answering ``[]`` for a component that declares a block is a failure, not zero members."""
    with_block = next(name for name in get_registry().names() if get_registry().get_class(name) is not None)
    real = discovery.ops
    monkeypatch.setattr(discovery, "ops", lambda name: [] if name == with_block else real(name))
    with pytest.raises(registrations.RegistrationError, match=with_block):
        registrations.component_ops()


def test_a_component_with_no_block_has_no_members() -> None:
    listed = registrations.component_ops()
    without = [name for name in get_registry().names() if get_registry().get_class(name) is None]
    assert without and all(listed[name] == () for name in without)


def test_no_experta_engine_ships() -> None:
    assert not [name for name in get_registry().names() if "experta" in name.lower()]
    assert not [path for path in PACKAGE.rglob("*") if "experta" in path.name.lower()]
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wanted = [*project.get("dependencies", []), *(d for extra in project["optional-dependencies"].values()
                                                  for d in extra)]
    assert wanted and not [d for d in wanted if "experta" in d.lower()]


def test_the_rules_route_names_the_engines_that_hold_the_role_and_ops_that_exist() -> None:
    manifest = routing.load()
    route = manifest.routes["rules"]
    rules = (PACKAGE / "skills" / "using-g6" / "references" / "rules.md")
    assert rules.relative_to(PACKAGE).as_posix() in route.detail
    text = rules.read_text(encoding="utf-8")
    pairs = re.findall(r"`([a-z_]+)\.([a-z_]+)`", text)
    for component in ("datalog", "prolog", "graph_reasoning"):
        assert f"`{component}`" in text
        assert [op for name, op in pairs if name == component], f"rules.md names no op of {component}"
        assert all(manifest.entries[f"op:{component}/{op}"].route == "rules" for op in discovery.ops(component))
    for component, op in pairs:
        assert op in discovery.ops(component), (component, op)
    flat = " ".join(text.split())
    assert "Experta is retired" in flat and "no engine port" in flat
    assert "keeps facts over a fixed" in flat
    # 14.3: current sessions do not establish a historical migration, and the retirement does not rest on one
    assert not re.search(r"migrat(ed|ion) from Experta|replaces Experta's|ported from Experta", flat)
