# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 1 (untrusted_input_given_authority): the agent's execution history holds only tool steps whose
reply from G6's door said ``ok``. Nothing a proposer, or any other collaborator, writes enters it, and a credited result
counts only the steps in it."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from g6.cognitive_bridge import CognitiveAgent, Hypothesis, Observation, Probe, StaticProposer, ToolSpec, engine
from g6.core import door


def _hypotheses():
    return [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)]


def _probe(id="p"):
    return Probe(id, "measure", "read", {"A": True, "B": False}, .8)


def test_finding_1_proposer_cannot_supply_the_agents_execution_history(monkeypatch):
    # Review 4's test, as the reviewer wrote it.
    monkeypatch.setattr(door, "person_model", lambda: None)
    calls = []

    class Proposer:
        def propose(self, objective, hypotheses, history):
            history.append({"probe_id": "never-executed", "observation": True,
                            "source_ref": "invented:source", "posterior": {"A": 1., "B": 0.}})
            return []

    def observe(question, arguments):
        calls.append(question)
        return Observation(True, "actual:source")

    try:
        out = CognitiveAgent(Proposer(), {"read": ToolSpec("read", observe)}).run(
            "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    except (TypeError, AttributeError):
        assert calls == []  # A read-only history can refuse the attempted write.
        return
    assert calls == []
    assert out["history"] == [], "proposer-written history was credited without any tool step"


def test_a_proposer_that_writes_past_the_read_only_view_changes_nothing():
    # One the old pattern missed: the proposer rewrites the entries it was shown, by whatever write works, and
    # appends an invented step when it can. The history the agent credits is the one the door passed.
    tool_calls = []

    class Proposer:
        def __init__(self):
            self.calls = 0

        def propose(self, objective, hypotheses, history):
            self.calls += 1
            if self.calls == 1:
                return [_probe("p1")]
            for entry in history:
                try:
                    entry["observation"] = False
                except TypeError:
                    dict.__setitem__(entry, "observation", False)
            try:
                history.append({"probe_id": "never-executed", "observation": True, "source_ref": "invented"})
            except AttributeError:
                pass
            return []

    def observe(question, arguments):
        tool_calls.append(question)
        return Observation(True, "actual:source")

    out = CognitiveAgent(Proposer(), {"read": ToolSpec("read", observe)}).run(
        "measure", _hypotheses(), budget=1, max_steps=3)
    assert tool_calls == ["measure"]
    assert [h["probe_id"] for h in out["history"]] == ["p1"], f"history credited a written step: {out['history']}"
    assert out["history"][0]["observation"] is True, "the proposer rewrote a credited observation"
    assert out["posterior"]["A"] == pytest.approx(.8) and out["completion_state"] == "qualified-draft"


def test_a_proposer_that_keeps_the_history_it_was_shown_cannot_change_it_later():
    # The history shown at one step is held by the proposer; writing to it at the next step reaches nothing.
    shown = []

    class Proposer:
        def propose(self, objective, hypotheses, history):
            shown.append(history)
            for earlier in shown[:-1]:
                for entry in earlier:
                    dict.__setitem__(entry, "source_ref", "rewritten")
            return [_probe(f"p{len(shown)}")] if len(shown) < 3 else []

    out = CognitiveAgent(Proposer(), {"read": ToolSpec("read", lambda q, a: Observation(True, "actual:source"))}).run(
        "measure", _hypotheses(), budget=2, max_steps=3)
    assert [h["source_ref"] for h in out["history"]] == ["actual:source", "actual:source"], out["history"]


def test_each_history_entry_is_a_step_the_door_passed(monkeypatch):
    real = door.invoke_checked
    passed = []

    def spy(*args, **kwargs):
        reply = real(*args, **kwargs)
        if reply.get("ok") is True:
            passed.append(reply["request_id"])
        return reply

    monkeypatch.setattr(door, "invoke_checked", spy)
    proposer = StaticProposer([_probe("p1"), _probe("p2")])
    out = CognitiveAgent(proposer, {"read": ToolSpec("read", lambda q, a: Observation(True, "actual:source"))}).run(
        "measure", _hypotheses(), budget=2, max_steps=2)
    assert len(passed) == 2
    assert [h.get("request_id") for h in out["history"]] == passed, (
        f"a history entry does not name the door reply that passed it: {out['history']}")


def test_a_door_refusal_writes_no_history(monkeypatch):
    # The first step passes; the door refuses the second. Only the first is in the history, and the run is refused.
    real = door.invoke_checked
    replies = []

    def second_refused(*args, **kwargs):
        replies.append(None)
        if len(replies) == 2:
            return door.blocked_reply("[G6_E_SENSITIVE_COMPONENT_DENIED] person did not allow this op")
        return real(*args, **kwargs)

    monkeypatch.setattr(door, "invoke_checked", second_refused)
    calls = []

    def observe(question, arguments):
        calls.append(question)
        return Observation(True, "actual:source")

    out = CognitiveAgent(StaticProposer([_probe("p1"), _probe("p2")]), {"read": ToolSpec("read", observe)}).run(
        "measure", _hypotheses(), budget=2, max_steps=2)
    assert len(calls) == 1 and [h["probe_id"] for h in out["history"]] == ["p1"]
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_COGNITIVE_STEP_FAILED"


def test_a_reply_that_says_ok_without_an_observation_is_a_step_that_never_ran(monkeypatch):
    # The door's reply says ok, but no tool answer came back: nothing ran, so nothing is credited.
    monkeypatch.setattr(door, "invoke_checked", lambda *a, **k: {"ok": True, "result": {}, "request_id": "r",
                                                                  "warning_card": {}})
    out = CognitiveAgent(StaticProposer([_probe()]), {"read": ToolSpec("read", lambda q, a: Observation(True, "x"))}
                         ).run("measure", _hypotheses(), budget=1, max_steps=1)
    assert out["history"] == [] and out["completion_state"] == "blocked-escalated"


def test_a_probe_the_selector_named_but_that_never_ran_is_not_in_the_history():
    # The selector names a probe that is not a validated candidate: the run refuses before any tool runs.
    calls = []

    class Selector:
        def rank(self, beliefs, probes, budget):
            return [{"id": "never-proposed", "expected_information_bits": .5}]

    def observe(question, arguments):
        calls.append(question)
        return Observation(True, "actual:source")

    with pytest.raises(ValueError, match="validated candidates"):
        CognitiveAgent(StaticProposer([_probe()]), {"read": ToolSpec("read", observe)}, selector=Selector()).run(
            "measure", _hypotheses(), budget=1, max_steps=1)
    assert calls == []


def test_a_result_with_no_door_passed_step_is_not_credited():
    # Nothing ran (no probe proposed), whatever any collaborator wrote: the run is refused, not a draft.
    class Proposer:
        def propose(self, objective, hypotheses, history):
            try:
                history.append({"probe_id": "invented"})
            except AttributeError:
                pass
            return []

    out = CognitiveAgent(Proposer(), {}).run("measure", _hypotheses(), budget=1, max_steps=2)
    assert out["history"] == [] and out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_COGNITIVE_NO_PROBE"


def test_the_one_writer_of_the_history_refuses_a_reply_the_door_did_not_pass():
    credit = getattr(engine, "_credited", None)
    assert credit is not None, "the agent has no single writer of its history"
    for reply in ({"ok": False, "request_id": "r"}, {"ok": "true", "request_id": "r"}, {"request_id": "r"}):
        with pytest.raises(ValueError):
            credit(reply, {"probe_id": "p"})
    assert credit({"ok": True, "request_id": "r"}, {"probe_id": "p"})["request_id"] == "r"


def test_the_history_is_written_only_by_its_one_writer():
    # From the code: in CognitiveAgent.run, the history is only appended to with what _credited made, and is handed to
    # a collaborator only as a fresh copy.
    tree = ast.parse(Path(engine.__file__).read_text(encoding="utf-8"))
    run = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run")
    parent = {child: node for node in ast.walk(run) for child in ast.iter_child_nodes(node)}
    bad = []
    for node in ast.walk(run):
        if not (isinstance(node, ast.Name) and node.id == "history") or isinstance(node.ctx, ast.Store):
            continue
        up = parent[node]
        if isinstance(up, ast.Attribute):
            call = parent[up]
            if not (up.attr == "append" and isinstance(call, ast.Call) and len(call.args) == 1
                    and isinstance(call.args[0], ast.Call) and isinstance(call.args[0].func, ast.Name)
                    and call.args[0].func.id == "_credited"):
                bad.append(ast.unparse(call if isinstance(call, ast.Call) else up))
        elif isinstance(up, ast.Call) and isinstance(up.func, ast.Name) and up.func.id in ("fresh", "list", "len"):
            continue
        elif isinstance(up, (ast.UnaryOp, ast.BoolOp, ast.IfExp, ast.keyword)) or (
                isinstance(up, ast.Compare)):
            if isinstance(up, ast.keyword) and up.arg != "history":
                bad.append(ast.unparse(up))
            continue
        else:
            bad.append(ast.unparse(up))
    assert bad == [], "the history is used other than appended by _credited or handed out fresh: " + "; ".join(bad)
