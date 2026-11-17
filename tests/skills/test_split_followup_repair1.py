# SPDX-License-Identifier: Apache-2.0
"""Whole-pool selection and fail-closed route census regressions."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from g6.core import selector
from g6.skills import corpus


def _gate():
    spec = importlib.util.spec_from_file_location("_repair1_census", Path(__file__).with_name("test_split_repair.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("limit,expected", [
    (1, ["tool:g6_review_patch"]),
    (2, ["tool:g6_review_patch", "route:discover"]),
    (3, ["tool:g6_review_patch", "route:review", "route:discover"]),
    (5, ["tool:g6_review_patch", "tool:g6_review_until_clean", "op:coding_agent_gate.verify_answer",
         "route:review", "route:discover"]),
])
def test_priority_sees_routes_below_the_call_window(monkeypatch, limit, expected):
    ranked = ["tool:g6_review_patch", "tool:g6_review_until_clean", "op:coding_agent_gate.verify_answer",
              "tool:g6_premortem", "tool:g6_find", "route:review"]
    data = corpus.load()
    entries = [e for e in data["entries"] if e["id"] in {*ranked, "route:discover"}]
    kinds = {e["id"]: e["kind"] for e in entries}
    monkeypatch.setattr(selector, "_lexical", lambda task, documents: {
        key: (100 - i) / (selector.LEXICAL_WEIGHT * (selector.ROUTE_WEIGHT if kinds[key] == "route" else 1))
        for i, key in enumerate(ranked)
    })
    reply = selector.select("ranking fixture", corpus={**data, "entries": entries}, limit=limit)
    assert [c["id"] for c in reply["candidates"]] == expected
    assert reply["next"]["id"] == expected[0]


@pytest.mark.parametrize("example,expected", [
    ("<!--\n[hidden](deliver.md)\n-->", []),
    ("    [hidden](deliver.md)", []),
    ("> ~~~markdown\n> [hidden](deliver.md)\n> ~~~", []),
    ("- > ~~~markdown\n  > [hidden](deliver.md)\n  > ~~~", []),
    ("[details][d]\n\n[d]: deliver.md", ["deliver.md"]),
    ("> [details][d]\n\n[d]: deliver.md", ["deliver.md"]),
    ("- [details][d]\n\n[d]: deliver.md", ["deliver.md"]),
    ("[deliver.md]\n\n[deliver.md]: deliver.md", ["deliver.md"]),
])
def test_navigation_uses_rendered_commonmark_links(example, expected):
    assert _gate()._navigation_links(example) == expected


@pytest.mark.parametrize("term", [
    "never", "not", "do not", "person", "decides", "ask", "stop", "consent", "grant",
    "cannot", "must", "only", "allow",
])
@pytest.mark.parametrize("change", ["drop", "reword"])
def test_authority_sentence_cannot_be_excepted_even_with_a_new_pin(monkeypatch, term, change):
    gate = _gate()
    original = f"Keep {term} in this instruction."
    # Counting authority words accepts this opposite qualification.
    replacement = original[:-1] + " unless inconvenient."
    row = {"sentence": original,
           "reason": "dropped-because: Test row." if change == "drop" else "reworded-to: Test row.",
           "replacement": "-" if change == "drop" else replacement}
    rows = [row]
    changed = "sentence\treason\treplacement\n" + "\t".join(row.values()) + "\n"
    assert list(csv.DictReader(changed.splitlines(), delimiter="\t")) == rows
    monkeypatch.setattr(gate, "EXCEPTION_ROWS_SHA256",
                        hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest())
    read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs:
                        changed if path == gate.EXCEPTIONS else read(path, *args, **kwargs))
    with pytest.raises(AssertionError):
        gate._reviewed_exceptions(original)


@pytest.mark.parametrize("rendered", [
    "Never change permissions yourself.",
    "Never **change** [permissions](ignored.md) yourself.",
    "Never\nchange `permissions` yourself.",
    "**Never change permissions yourself.**",
    "Never change **permissions** yourself.",
    "1. **Never change permissions yourself.** Keep this warning.",
])
def test_authority_sentence_preserves_more_than_markup_stripped_words(monkeypatch, rendered):
    gate = _gate()
    original = "Never change `permissions` yourself."
    read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs:
                        original if path == gate.BASELINE else read(path, *args, **kwargs))
    monkeypatch.setattr(gate, "_reviewed_exceptions", lambda text: {})
    monkeypatch.setattr(gate, "_held_sentences", lambda text, route_id: {"Never change permissions yourself."})
    monkeypatch.setattr(gate, "_reachable", lambda root, route: [rendered])
    gate.test_each_route_reaches_its_original_safety_and_authority_sentences("question")


@pytest.mark.parametrize("changed", [
    "Never change permissions.",
    "Never modify permissions yourself.",
    "Never change permissions yourself unless convenient.",
])
def test_authority_rendering_still_rejects_changed_words(monkeypatch, changed):
    gate = _gate()
    original = "Never change `permissions` yourself."
    read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs:
                        original if path == gate.BASELINE else read(path, *args, **kwargs))
    monkeypatch.setattr(gate, "_reviewed_exceptions", lambda text: {})
    monkeypatch.setattr(gate, "_held_sentences", lambda text, route_id: {"Never change permissions yourself."})
    monkeypatch.setattr(gate, "_reachable", lambda root, route: [changed])
    with pytest.raises(AssertionError):
        gate.test_each_route_reaches_its_original_safety_and_authority_sentences("question")


def test_route_reference_definition_can_be_elsewhere_in_its_document():
    gate = _gate()
    root = (gate.GUIDE / "SKILL.md").read_text(encoding="utf-8")
    root = root.replace("[read before asking](references/question.md)", "[read before asking][q]")
    root += "\n[q]: references/question.md\n"
    assert (gate.GUIDE / "references/question.md").read_text(encoding="utf-8") in gate._reachable(
        root, gate.routing.load().routes["question"])


def test_extracting_a_route_does_not_render_a_link_hidden_in_its_document():
    gate = _gate()
    section = "- **question.** [hidden](references/question.md)"
    document = "<!--\n" + section + "\n-->\n"
    assert gate._navigation_links(section, document=document) == []
