# SPDX-License-Identifier: Apache-2.0
"""Progressive discovery through the registered MCP surface, against the shipped base."""
from __future__ import annotations

import ast
import asyncio
import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import discovery, semantic, selector
from g6.mcp import build_server, registrations
from g6.skills import corpus, routing

BASE = "315c931151f39021cdd34694562d2605483a86de"
REPO = Path(__file__).resolve().parents[2]


def _call(name, arguments):
    async def run():
        async with Client(build_server(), client_info=Implementation(name="codex", version="test")) as client:
            result = await client.call_tool(name, arguments)
            return result.data
    return asyncio.run(run())


def _sentences(text):
    return set(re.split(r"(?<=[.!?])\s+", " ".join(text.split()))) - {""}


def _base_functions():
    files = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", BASE, "src/g6/mcp", "src/g6/abstraction/server.py"],
        cwd=REPO, text=True).splitlines()
    functions = {}
    for file in files:
        if not file.endswith(".py"):
            continue
        source = subprocess.check_output(["git", "show", f"{BASE}:{file}"], cwd=REPO, text=True, encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(node):
                functions[node.name] = node
    return functions


def test_every_registered_tool_route_and_skill_has_exactly_one_family():
    from g6.mcp.families import FAMILIES, inventory

    rows = inventory()
    expected = {f"mcp:{server}/{tool}" for server, tools in registrations.recorded().items() for tool in tools}
    expected |= {f"route:{route}" for route in routing.load().routes}
    expected |= {entry["id"] for entry in [*corpus._skills(), *corpus._bundled_skills()]}
    actual = [row["id"] for row in rows if row["kind"] != "op"]
    assert set(actual) == expected
    assert len(actual) == len(set(actual))
    assert 6 <= len(FAMILIES) <= 12
    assert all(row["family"] in FAMILIES and row["call"] for row in rows)


def test_tool_description_budget():
    tools = {key: value for key, value in registrations.shown().items() if key.startswith("tool:")}
    counts = {key: len(value.split()) for key, value in tools.items()}
    total = sum(counts.values())
    print(f"MCP descriptions: {len(tools)} tools, {total} words, {sum(map(len, tools.values())) / 4:g} chars/4")
    assert counts and max(counts.values()) <= 60, counts
    assert total <= 2000  # 6851 words at BASE: at least 70% removed, across both servers.


def test_describe_keeps_every_sentence_from_the_base_tool_descriptions():
    baseline = {name: ast.get_docstring(node) for name, node in _base_functions().items()}
    for server, names in registrations.recorded().items():
        for name in names:
            reply = _call("g6_describe", {"component": name})
            assert reply["available"] is True, (server, name, reply)
            assert _sentences(baseline[name]) <= _sentences(reply["guidance"]), name
            assert reply["call"]["tool"] == name


def test_describe_keeps_every_line_from_the_base_tool_descriptions(disclosed_tool_guidance):
    baseline = {name: ast.get_docstring(node) for name, node in _base_functions().items()}
    shown = registrations.shown()
    for server, names in registrations.recorded().items():
        for name in names:
            guidance = disclosed_tool_guidance(name, description=shown[f"tool:{server}/{name}"])
            assert baseline[name] in guidance, (server, name)


def test_each_family_resolves_with_one_line_members_and_call_templates():
    from g6.mcp.families import FAMILIES, inventory

    rows = inventory()
    for name, family in FAMILIES.items():
        reply = _call("g6_describe", {"component": name})
        assert reply["available"] is True and reply["purpose"] == family.purpose
        assert {row["id"] for row in reply["members"]} == {row["id"] for row in rows if row["family"] == name}
        assert all("\n" not in row["summary"] and row["call"] for row in reply["members"])


@pytest.mark.parametrize("limit", [1, 3, 5, 100])
def test_select_and_find_keep_explicit_limits_and_fields_with_families(limit, tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    task = "review my patch before I say done"
    with semantic.request(llm=None):
        selected = _call("g6_select", {"task": task, "phase": "verify", "limit": limit})
        found = _call("g6_find", {"query": task, "limit": limit})
        direct = selector.select(task, phase="verify", limit=limit)
        direct_found = discovery.find(task, limit=limit)
    for rows, expected in ((selected["candidates"], direct["candidates"]), (found, direct_found)):
        assert len(rows) == len(expected) > 0
        for row, original in zip(rows, expected, strict=True):
            assert {key: row[key] for key in original} == original
            assert row["families"] and row["id"]
    assert {c["id"] for c in selected["candidates"][:2]} & {"tool:g6_review_patch", "tool:g6_review_until_clean"}
    assert selected["mechanisms"] == direct["mechanisms"]
    assert selected["unknown_until_observed"] == direct["unknown_until_observed"]
    assert selected["completion_state"] == "qualified-draft" and selected["warning_card"]


def test_family_table_leads_instructions_and_skill():
    from g6.mcp.families import FAMILIES, family_table
    from g6.mcp.instructions import TEXT

    root = registrations.package_root() / routing.ROOT
    skill = root.read_text(encoding="utf-8")
    assert TEXT.startswith(family_table()) and len(TEXT.split()) <= 150
    assert skill.index("| Family |") < skill.index("## Routes")
    for family in FAMILIES:
        assert f"references/family-{family}.md" in skill
        assert (root.parent / "references" / f"family-{family}.md").is_file()


def test_unknown_family_members_fail_closed():
    from g6.mcp.families import family_for

    with pytest.raises(ValueError, match="family"):
        family_for({"id": "route:unregistered", "kind": "route"})
    with pytest.raises(ValueError, match="family"):
        family_for({"id": "op:adapt_audio.absent_operation", "kind": "op"})
    reply = _call("g6_describe", {"component": "absent_family_or_tool"})
    assert reply["available"] is False


def test_an_op_without_its_shipped_component_record_has_no_known_family(monkeypatch):
    from g6.mcp import catalogue
    from g6.mcp.families import family_for

    entry = next(row for row in corpus.entries() if row["kind"] == "op")
    shipped = dict(catalogue.shipped())
    shipped.pop(entry["component"])
    monkeypatch.setattr(catalogue, "shipped", lambda: shipped)
    with pytest.raises(ValueError, match="family"):
        family_for(entry)


def test_cli_setup_text_and_selector_hint_stay_frozen():
    from g6.mcp import instructions

    source = subprocess.check_output(["git", "show", f"{BASE}:src/g6/mcp/instructions.py"],
                                     cwd=REPO, text=True, encoding="utf-8")
    assignments = {node.targets[0].id: node.value for node in ast.parse(source).body
                   if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    lead = ast.literal_eval(assignments["LEAD"])
    setup_text = lead + ast.literal_eval(assignments["TEXT"].right)
    assert instructions.LEAD == lead
    assert instructions.agents_block() == (
        f"{instructions.AGENTS_BEGIN}\n## G6\n\n{setup_text}\n{instructions.AGENTS_END}")


def test_families_do_not_shadow_existing_components():
    from g6.mcp.catalogue import shipped
    from g6.mcp.families import FAMILIES

    assert set(FAMILIES).isdisjoint(shipped())


def test_tool_arguments_match_the_base_function_signatures():
    baseline = _base_functions()
    for tools in registrations.arguments().values():
        for name, arguments in tools.items():
            node = baseline[name]
            expected = {arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                        if arg.arg != "ctx"}
            assert set(arguments) == expected, name


def test_component_family_ops_follow_the_registered_package_family():
    from g6.core.registry import FAMILIES as COMPONENT_FAMILIES
    from g6.mcp.catalogue import shipped
    from g6.mcp.families import FAMILIES, family_for

    assigned = [name for family in FAMILIES.values() for name in family.component_families]
    assert set(assigned) == COMPONENT_FAMILIES and len(assigned) == len(set(assigned))
    for entry in corpus.entries():
        if entry["kind"] != "op":
            continue
        module = shipped()[entry["component"]].split(".")
        if len(module) > 2 and module[1] in COMPONENT_FAMILIES:
            expected = next(name for name, f in FAMILIES.items() if module[1] in f.component_families)
            assert family_for(entry) == expected, entry["id"]


def test_internal_ranking_corpus_keeps_base_identities_and_matches_its_writer():
    baseline = json.loads(subprocess.check_output(["git", "show", f"{BASE}:src/g6/skills/corpus.json"],
                                                  cwd=REPO, text=True, encoding="utf-8"))
    assert {entry["id"] for entry in baseline["entries"]} <= {entry["id"] for entry in corpus.entries()}
    assert corpus.load() == corpus.build()


@pytest.mark.parametrize("broken", ["absent", "duplicate", "unclosed"])
def test_missing_or_ambiguous_full_route_detail_is_not_a_known_record(tmp_path, monkeypatch, broken):
    root = tmp_path / routing.ROOT
    root.parent.mkdir(parents=True)
    root.write_text("## Find\nA short discovery entry.\n", encoding="utf-8")
    path = root.parent / "references" / "family-discover.md"
    path.parent.mkdir()
    record = "<!-- route:discover -->\nFull guidance.\n<!-- /route:discover -->\n"
    text = {"absent": "Only a summary.", "duplicate": record * 2,
            "unclosed": "<!-- route:discover -->\nFull guidance."}[broken]
    path.write_text(text, encoding="utf-8")
    manifest = routing.load()
    route = replace(manifest.routes["discover"], detail=(path.relative_to(tmp_path).as_posix(),))
    monkeypatch.setattr(routing, "load", lambda: replace(manifest, routes={"discover": route}))
    monkeypatch.setattr(registrations, "package_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="detail"):
        corpus._routes([entry for entry in corpus.entries() if entry["kind"] == "tool"])
