# SPDX-License-Identifier: Apache-2.0
"""Review-4 regressions: closed exceptions, rendered navigation and ranked fallback."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from g6.core import selector
from g6.skills import corpus


def _census():
    spec = importlib.util.spec_from_file_location("_followup_census", Path(__file__).with_name("test_split_repair.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("change", ["add", "remove", "sentence", "reason", "replacement"])
def test_route_gate_rejects_every_unreviewed_exception_edit(monkeypatch, change):
    gate = _census()
    read = Path.read_text
    rows = read(gate.EXCEPTIONS, encoding="utf-8").splitlines()
    if change == "add":
        rows.append("G6's self-improvement loop on a folder.\tdropped-because: Redundant.\t-")
    elif change == "remove":
        rows.pop()
    else:
        column = {"sentence": 0, "reason": 1, "replacement": 2}[change]
        row = rows[-1].split("\t")
        row[column] = {"sentence": "G6's self-improvement loop on a folder.",
                       "reason": "dropped-because: A changed rationale.",
                       "replacement": "Changed replacement."}[change]
        rows[-1] = "\t".join(row)
    changed = "\n".join(rows) + "\n"

    def read_changed(path, *args, **kwargs):
        return changed if path == gate.EXCEPTIONS else read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_changed)
    with pytest.raises(AssertionError, match="lead review"):
        gate.test_each_route_reaches_its_original_safety_and_authority_sentences("question")


@pytest.mark.parametrize("word", ["never", "not", "do not", "person", "decides", "ask", "stop", "consent", "grant"])
def test_even_repinned_rewordings_must_keep_each_authority_term(monkeypatch, word):
    gate = _census()
    original = f"Keep {word} in this instruction."
    replacement = "Keep in this instruction."
    changed = ("sentence\treason\treplacement\n" + original
               + "\treworded-to: Test authority preservation.\t" + replacement + "\n")
    rows = list(csv.DictReader(changed.splitlines(), delimiter="\t"))
    # Isolate the independent authority check from the closed-table pin.
    monkeypatch.setattr(gate, "EXCEPTION_ROWS_SHA256",
                        hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest(), raising=False)
    read = Path.read_text

    def read_changed(path, *args, **kwargs):
        if path == gate.EXCEPTIONS:
            return changed
        return original if path == gate.BASELINE else read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_changed)
    monkeypatch.setattr(gate, "_held_sentences", lambda original, route_id: {original})
    monkeypatch.setattr(gate, "_reachable", lambda root, route: [replacement])
    with pytest.raises(AssertionError):
        gate.test_each_route_reaches_its_original_safety_and_authority_sentences("question")


@pytest.mark.parametrize("example", [
    "```text\n[example](deliver.md)\n```",
    "~~~markdown\n[example](deliver.md)\n~~~",
    "    ```text\n[example](deliver.md)\n    ```",
    "`[example](deliver.md)`",
    "``literal `[example](deliver.md)` literal``",
], ids=["backtick-fence", "tilde-fence", "indented-fence", "inline", "long-inline"])
def test_code_link_cannot_supply_route_navigation(monkeypatch, example):
    gate = _census()
    root = (gate.GUIDE / "SKILL.md").read_text(encoding="utf-8")
    read = Path.read_text

    def read_changed(path, *args, **kwargs):
        if path.resolve() == (gate.GUIDE / "references/question.md").resolve():
            return example
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_changed)
    texts = gate._reachable(root, gate.routing.load().routes["question"])
    if example == "    ```text\n[example](deliver.md)\n    ```":
        # Four-space backticks are indented code, but the unindented middle
        # line is a rendered CommonMark link and must supply navigation.
        assert (gate.GUIDE / "references/deliver.md").read_text(encoding="utf-8") in texts
    else:
        assert texts == [root, example], "literal code opened an unlinked reference"


@pytest.mark.parametrize("task,phase,expected,route", [
    ("add type hints to the utils module", "implement", "tool:g6_confirm_behaviour", "route:review"),
    ("summarise the error log and identify a failure mode", "diagnose", "tool:g6_premortem", "route:review"),
    ("derive answers from facts and rules in a datalog session", "implement", "op:datalog.query", "route:rules"),
])
def test_fallback_keeps_the_best_call_with_two_slots(task, phase, expected, route):
    reply = selector.select(task, phase, limit=2)
    assert [c["id"] for c in reply["candidates"]] == [expected, "route:discover"]
    assert reply["next"]["id"] == expected
    assert reply["candidates"][-1]["ranked_by"][-1] == "fallback"
    assert any(f"replaced {route} with the discovery fallback" in m["why"] for m in reply["mechanisms"])


def test_one_slot_keeps_its_ranked_route_and_explains_no_forced_fallback():
    data = corpus.load()
    routes = [e for e in data["entries"] if e["id"] in {"route:review", "route:discover"}]
    reply = selector.select("review my Python patch against its requirements and acceptance tests", "verify", limit=1,
                            corpus={**data, "entries": routes})
    assert [c["id"] for c in reply["candidates"]] == ["route:review"]
    assert any("limit=1" in m["why"] and "no discovery fallback is forced" in m["why"]
               for m in reply["mechanisms"])
