# SPDX-License-Identifier: Apache-2.0
"""Model-bound strings are redacted views with a map back to the canonical text; instruction-like text is reported
and changes nothing."""
from __future__ import annotations

import json
from pathlib import Path

from g6._context_engine import _engine as engine
from g6._context_engine._registry import ProjectStore
from g6._context_engine._view import label_view, view_of
from tests.knowledge.corpus import INSTRUCTION_SUBJECT, SECRET, SECRET_SUBJECT, SUBJECTS
from tests.knowledge.corpus.rig import NOW, make


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _strings(child)


def test_view_masks_planted_secret_and_keeps_span_map(tmp_path, monkeypatch):
    text = f'before the key\napi_key = "{SECRET}"\nmail ada@example.org and after the key\n'
    view = view_of(text, canonical_start=100)
    assert SECRET not in view.text and "ada@example.org" not in view.text and view.masked >= 2
    assert view.spans and all(view.text[a:b] == text[c - 100:d - 100] for a, b, c, d in view.spans)
    assert all(b > a and d - c == b - a for a, b, c, d in view.spans)
    assert [s[0] for s in view.spans] == sorted(s[0] for s in view.spans)
    plain = view_of("nothing to mask here", canonical_start=7)
    assert plain.masked == 0 and plain.spans == ((0, 20, 7, 27),) and plain.text == "nothing to mask here"
    assert view_of("").spans == ()
    # through the engine: the span map points into the document the passage was cut from
    rig = make(tmp_path, monkeypatch)
    rig.add()
    item = rig.items(SECRET_SUBJECT)[0]
    document = (rig.folder / "ops" / "deploy-notes.md").read_bytes().decode("utf-8")
    assert SECRET in document[item["start"]:item["end"]] and SECRET not in item["view"]["text"]
    assert item["view"]["masked"] >= 1
    for a, b, c, d in item["view"]["spans"]:
        assert item["view"]["text"][a:b] == document[c:d]


def test_every_model_bound_string_is_a_view(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    committed = rig.add()
    replies = [committed, engine.sources(rig.local("list"))]
    for query in (SECRET_SUBJECT, INSTRUCTION_SUBJECT, SUBJECTS[0], "deployment api_key upload", "ledger module"):
        response = rig.search(query, top_k=20)
        replies.append(response.model_dump())
        for item in response.payload["items"][:2]:
            replies.append(engine.fetch(rig.local("fetch"), item["passage_id"], clock=lambda: NOW)[0])
    forbidden = [SECRET, str(Path.home()), str(rig.folder), str(rig.project), rig.folder.as_posix(),
                 str(rig.home), rig.project.as_posix()]
    seen = 0
    for value in _strings(json.loads(json.dumps(replies, default=str))):
        seen += 1
        for planted in forbidden:
            assert planted not in value, (planted, value[:120])
    assert seen > 100
    assert label_view(str(Path.home() / "notes" / "a.md")).startswith("~")


def test_instruction_text_is_reported_not_obeyed(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    store = ProjectStore(rig.binding)
    before = (store.registrations(), store.tombstones(), store.generations(),
              sorted(p.relative_to(rig.folder).as_posix() for p in rig.folder.rglob("*")))
    items = rig.items(INSTRUCTION_SUBJECT)
    assert items and items[0]["path"] == "ops/vendor-readme.md"
    assert items[0]["instruction_signals"] and items[0]["trust"] == "untrusted"
    found, _ = engine.fetch(rig.local("fetch"), items[0]["passage_id"], raw=True, clock=lambda: NOW)
    assert found["passage"]["trust"] == "untrusted" and found["passage"]["instruction_signals"]
    assert all(i["trust"] == "untrusted" for i in rig.items(SUBJECTS[2]))
    after = (store.registrations(), store.tombstones(), store.generations(),
             sorted(p.relative_to(rig.folder).as_posix() for p in rig.folder.rglob("*")))
    assert after == before
