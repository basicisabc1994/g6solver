# SPDX-License-Identifier: Apache-2.0
"""The selector's corpus (plan revision 8, phase B): one checked-in file over G6's own MCP tools, skill routes and
runnable built-in ops, regenerated from the code and compared, so it cannot drift."""
from __future__ import annotations

import json
import re
from pathlib import Path

from g6.mcp import registrations
from g6.skills import corpus, routing


def test_the_checked_in_corpus_is_what_the_code_builds() -> None:
    built = corpus.dumps(corpus.build())
    assert built == corpus.PATH.read_text(encoding="utf-8"), "run: python -P -m g6.skills.corpus --write"


def test_it_holds_every_tool_of_g6_mcp_every_route_and_every_runnable_built_in_op() -> None:
    data = corpus.load()
    ids = {entry["id"] for entry in data["entries"]}
    tools = set(registrations.recorded()["g6"])
    assert {f"tool:{tool}" for tool in tools} <= ids and "tool:g6_select" in ids
    assert {f"route:{route}" for route in routing.load().routes} <= ids
    ops = {f"op:{component}.{op}" for component, listed in registrations.component_ops().items() for op in listed}
    assert ops and ops <= ids
    assert len(ids) == len(data["entries"]) == len(tools) + len(routing.load().routes) + len(ops)
    # the separate abstraction server is registered by the person alone, not by g6-mcp: none of its tools is here
    assert not any(entry["call"].get("server") == "abstraction" for entry in data["entries"])
    assert set(corpus.SERVERS_NOT_SELECTED) == set(registrations.SERVERS) - {"g6"}


def test_every_entry_has_text_and_says_what_it_establishes_and_what_it_does_not() -> None:
    full = corpus.entries()
    assert len(full) == len(corpus.load()["entries"])
    for entry in full:
        assert entry["kind"] in ("tool", "route", "op"), entry["id"]
        assert entry["text"].strip() and entry["establishes"].strip() and entry["not_established"].strip(), entry["id"]
        assert set(entry["phases"]) <= set(corpus.PHASES), entry["id"]
        assert entry["cost"] in corpus.COSTS, entry["id"]
        assert set(entry["needs"]) == {"sensitive", "consent", "model", "confined", "outside", "extras"}, entry["id"]


def test_the_hand_written_tool_data_covers_exactly_the_registered_tools() -> None:
    assert set(corpus.TOOL_DATA) == set(registrations.recorded()["g6"])


def test_it_carries_no_local_path() -> None:
    text = corpus.PATH.read_text(encoding="utf-8")
    for private in (str(Path.home()), str(Path.home()).replace("\\", "/"), Path.home().name + "\\"):
        assert private not in text


def test_an_agent_facing_member_with_no_corpus_entry_is_a_problem() -> None:
    inputs = registrations.inputs()
    assert routing.check(routing.load(), **inputs) == []
    selectable = dict(inputs["selectable"])
    dropped = next(key for key in selectable if key.startswith("op:grounding/"))
    del selectable[dropped]
    problems = routing.check(routing.load(), **{**inputs, "selectable": selectable})
    assert [(p.code, p.key) for p in problems] == [("UNSELECTABLE", dropped)]
    exempt = {**selectable, dropped: "exempt: short"}
    problems = routing.check(routing.load(), **{**inputs, "selectable": exempt})
    assert [(p.code, p.key) for p in problems] == [("UNSELECTABLE", dropped)]


def test_an_op_template_is_validated_exactly_when_the_doors_typed_input_check_takes_it() -> None:
    """``validated`` is the door's own check (registry.build_typed_input, what g6_invoke runs), made when the corpus is
    built from shipped code; a refused template says why in G6's words, with no path."""
    from g6.core.registry import build_typed_input, get_registry

    registry = get_registry()
    for entry in corpus.entries():
        if entry["kind"] != "op":
            continue
        component, op = entry["id"][3:].split(".", 1)
        params = json.loads(entry["call"]["arguments"]["params_json"])
        _, error = build_typed_input(registry.get_class(component)(), {**params, "op": op},
                                     code=f"[{component.upper()}_ERROR]", op=op)
        assert entry["validated"] is (error is None), (entry["id"], error)
        if error:
            assert entry["issues"] and all(i.startswith("[G6_E_INPUT_VALIDATION]") for i in entry["issues"]), entry
        else:
            assert entry["issues"] == [], entry["id"]
        for issue in entry["issues"]:
            assert not re.search(r"[A-Za-z]:[\\/]|\\\\|/(?:home|Users|tmp)/", issue), issue


def test_the_door_fields_a_refusal_names_are_filled_into_the_template() -> None:
    query = next(e for e in corpus.entries() if e["id"] == "op:database.query")
    assert "sql" in json.loads(query["call"]["arguments"]["params_json"]) and query["validated"] is True


def test_entries_are_copies_so_a_caller_that_fills_a_template_changes_no_later_entry() -> None:
    before = json.dumps(corpus.entries(), sort_keys=True)
    try:
        for entry in corpus.entries():
            entry["call"].setdefault("arguments", {})["filled"] = "by the caller"
            entry["needs"]["consent"] = ["changed"]
            entry.get("phases", []).append("changed")
        assert json.dumps(corpus.entries(), sort_keys=True) == before
    finally:
        corpus._loaded.cache_clear()


def test_the_corpus_is_sorted_and_written_one_way() -> None:
    data = json.loads(corpus.PATH.read_text(encoding="utf-8"))
    ids = [entry["id"] for entry in data["entries"]]
    assert ids == sorted(ids)
