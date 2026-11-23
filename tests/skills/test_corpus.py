# SPDX-License-Identifier: Apache-2.0
"""The selector's corpus (plan revision 8, phase B): one checked-in file over G6's own MCP tools, skill routes and
runnable built-in ops, regenerated from the code and compared, so it cannot drift."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from g6.mcp import registrations
from g6.skills import corpus, routing
from g6.core.skills import vendored


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
    assert len(ids) == len(data["entries"]) == len(tools) + len(routing.load().routes) + len(ops) + len(vendored()) + len(corpus._bundled_skills())
    # the separate abstraction server is registered by the person alone, not by g6-mcp: none of its tools is here
    assert not any(entry["call"].get("server") == "abstraction" for entry in data["entries"])
    assert set(corpus.SERVERS_NOT_SELECTED) == set(registrations.SERVERS) - {"g6"}


def test_every_entry_has_text_and_says_what_it_establishes_and_what_it_does_not() -> None:
    full = corpus.entries()
    assert len(full) == len(corpus.load()["entries"])
    for entry in full:
        assert entry["kind"] in ("tool", "route", "skill", "op"), entry["id"]
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


def test_an_op_template_is_validated_only_when_g6_invoke_runs_it_as_given(monkeypatch, tmp_path) -> None:
    """``validated`` is true only when the template, run as given through g6_invoke's own path (``invoke_reply``)
    when the corpus was built, was not refused. A template the door's typed input check refuses, one the op itself
    refuses, and one never run (it could act, needs a model, a program, isolation or an optional extra, or its
    component is not on the reviewed list) are ``validated: false``, with why in G6's words and the fields to fill."""
    from g6.core.registry import build_typed_input, get_registry
    from g6.mcp import invoke_reply

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    registry = get_registry()
    validated = 0
    for entry in corpus.entries():
        if entry["kind"] != "op":
            continue
        component, op = entry["id"][3:].split(".", 1)
        params = json.loads(entry["call"]["arguments"]["params_json"])
        _, error = build_typed_input(registry.get_class(component)(), {**params, "op": op},
                                     code=f"[{component.upper()}_ERROR]", op=op)
        assert isinstance(entry["fill"], list) and set(entry["fill"]) <= set(entry["fields"]), entry["id"]
        if error:
            assert not entry["validated"] and entry["issues"][0].startswith("[G6_E_INPUT_VALIDATION]"), entry
        if entry["validated"]:
            validated += 1
            assert component in corpus.RUN_AT_BUILD and not entry["sensitive"] and error is None, entry["id"]
            ran = invoke_reply(component, op, entry["call"]["arguments"]["params_json"], allow_sensitive=frozenset())
            assert ran["completion_state"] != "blocked-escalated", (entry["id"], ran["warning_card"])
            assert entry["issues"] == [], entry["id"]
        else:
            assert entry["issues"], entry["id"]
        for issue in entry["issues"]:
            assert not re.search(r"[A-Za-z]:[\\/]|\\\\|/(?:home|Users|tmp)/", issue), issue
    assert validated >= 5


def test_a_template_the_op_refuses_as_given_names_the_fields_to_fill() -> None:
    full = {entry["id"]: entry for entry in corpus.entries()}
    gate = full["op:action_gating.evaluate"]
    assert gate["validated"] is False and gate["fill"] == ["action", "scope"], gate
    assert gate["issues"][0].startswith("[G6_E_ACTION_GATING_ACTION_REQUIRED] action_gating.evaluate:")
    assert full["op:coding_agent_gate.preflight"]["validated"] is True


def _unfilled(value: object) -> bool:
    return (value in ([], {}, "") or value is False or (type(value) in (int, float) and value == 0)
            or (isinstance(value, str) and re.fullmatch(r"<[^<>]*>", value) is not None))


def test_fill_names_every_field_the_template_leaves_absent_empty_or_a_placeholder() -> None:
    """Validated or not, ``fill`` lists each field the template holds as an empty value or a placeholder, and each
    field the op requires that the template leaves out (for an op that requires none, those its help line names):
    a run as given that answered only on placeholders or defaults is not a usable answer."""
    full = {entry["id"]: entry for entry in corpus.entries()}
    for entry in full.values():
        if entry["kind"] != "op":
            continue
        params = json.loads(entry["call"]["arguments"]["params_json"])
        unfilled = {key for key, value in params.items() if _unfilled(value)}
        assert unfilled <= set(entry["fill"]), (entry["id"], sorted(unfilled - set(entry["fill"])))
    improvement = full["op:decision_making.expected_improvement"]
    assert improvement["validated"] and {"mean", "std", "best"} <= set(improvement["fill"]), improvement
    assert {"task", "answer"} <= set(full["op:coding_agent_gate.verify_answer"]["fill"])
    assert full["op:coding_agent_gate.preflight"]["fill"] == ["constraints", "evidence", "proposed_action", "risk_tier",
                                                               "task"]


def test_only_ops_that_compute_over_their_input_alone_are_run_when_the_corpus_is_built() -> None:
    data = corpus.load()
    for name in corpus.RUN_AT_BUILD:
        needs = data["components"][name]["needs"]
        assert needs["model"] == "none" and not needs["confined"] and not needs["outside"] and not needs["extras"], name
    for entry in corpus.entries():
        if entry["kind"] == "op" and (entry["component"] not in corpus.RUN_AT_BUILD or entry["sensitive"]):
            assert entry["validated"] is False and "not run when the corpus was built" in entry["issues"][-1], entry


def test_a_component_whose_reply_reads_the_clock_is_not_run_when_the_corpus_is_built(monkeypatch) -> None:
    """duty_of_care.directory says how many days old its numbers are (it reads the clock), so it is not on the
    reviewed list of components that compute over their typed input alone, and none of its ops is validated."""
    import datetime as real
    import types

    from g6.jobs._care import directory
    from g6.mcp import invoke_reply

    def on(day: real.date) -> str:
        class _Clock(real.datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ANN001, ANN206
                return real.datetime(day.year, day.month, day.day, tzinfo=tz)

        monkeypatch.setattr(directory, "dt", types.SimpleNamespace(
            datetime=_Clock, date=real.date, timezone=real.timezone, timedelta=real.timedelta))
        return invoke_reply("duty_of_care", "directory", "{}", allow_sensitive=frozenset())["warning_card"]["message"]

    assert on(real.date(2026, 11, 1)) != on(real.date(2027, 9, 1))
    assert "duty_of_care" not in corpus.RUN_AT_BUILD
    assert not any(e["validated"] for e in corpus.entries() if e.get("component") == "duty_of_care")


def test_the_build_runs_in_a_scratch_home_and_leaves_the_persons_home_alone(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    assert corpus.dumps(corpus.build()) == corpus.PATH.read_text(encoding="utf-8")
    assert list(home.iterdir()) == []


def test_the_door_fields_a_refusal_names_are_filled_into_the_template() -> None:
    from g6.core.registry import build_typed_input, get_registry

    query = next(e for e in corpus.entries() if e["id"] == "op:database.query")
    params = json.loads(query["call"]["arguments"]["params_json"])
    _, error = build_typed_input(get_registry().get_class("database")(), {**params, "op": "query"},
                                 code="[DATABASE_ERROR]", op="query")
    assert "sql" in params and error is None
    assert query["validated"] is False and "not run when the corpus was built" in query["issues"][0]


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


@pytest.mark.parametrize("key,fields", [
    ("op:coding_agent_gate.preflight", {"task", "proposed_action", "evidence", "risk_tier", "constraints"}),
    ("op:epistemic_integrity.adjudicate", {"message", "action_criticality", "evidence"}),
])
def test_fill_names_every_field_the_ops_signature_declares_not_only_the_required_ones(key: str, fields: set) -> None:
    """Review 4, finding 8: an op that requires one field still has optional fields that change its answer (the act
    a gate decides on, the evidence); fill lists them, and next says why."""
    from g6.core import selector

    entry = next(e for e in corpus.entries() if e["id"] == key)
    assert fields <= set(entry["fill"]), entry["fill"]
    candidate = {**entry, "kind": "op", "issues": list(entry["issues"])}
    said = selector._fill_say(candidate)
    assert all(f in said for f in fields) and "change" in said, said
