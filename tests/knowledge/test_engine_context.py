# SPDX-License-Identifier: Apache-2.0
"""Budgeted context assembly: revocation is checked again at return, a saved pack is denied once its source is
revoked or changed, withheld sources are absent from a pack, and a pack is checked again at every later use."""
from __future__ import annotations

import json

import pytest

from g6.knowledge import _engine as engine
from g6.knowledge._registry import ProjectStore, Refused
from g6.knowledge._scope import access
from tests.knowledge.corpus import SECRET, SECRET_SUBJECT, SUBJECTS
from tests.knowledge.corpus.rig import NOW, make

DOCS, NOTES = SUBJECTS[0], SUBJECTS[1]


def context(rig, task, acc=None, **kw):
    return engine.context(acc or rig.local("context"), task, clock=lambda: NOW, **kw)


def test_context_fits_the_budget_and_counts_what_it_left_out(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    wide, refs = context(rig, f"{DOCS} {NOTES} convention decision", budget_tokens=100_000)
    assert len(wide["items"]) == 20 and wide["omitted"] == 0 and wide["pack"].startswith("pack-") and len(refs) == 20
    assert wide["tokens"] == sum(engine_tokens(i) for i in wide["items"]) <= 100_000
    narrow, _ = context(rig, f"{DOCS} {NOTES} convention decision", budget_tokens=wide["tokens"] // 4)
    assert 0 < len(narrow["items"]) < 20 and narrow["omitted"] > 0 and narrow["tokens"] <= wide["tokens"] // 4
    assert [i["passage_id"] for i in narrow["items"]] == [i["passage_id"] for i in wide["items"]][:len(narrow["items"])]
    none, refs = context(rig, DOCS, budget_tokens=10)
    assert none["items"] == [] and none["pack"] == "" and "G6_E_KNOWLEDGE_FUSION_BUDGET" in none["refused"] and refs == ()
    secret, _ = context(rig, SECRET_SUBJECT)
    saved = (ProjectStore(rig.binding).folder / "packs" / f"{secret['pack']}.json").read_text(encoding="utf-8")
    assert SECRET not in saved and SECRET not in json.dumps(secret), "a pack holds views only"
    empty = make(tmp_path, monkeypatch, name="empty", build=False)
    assert context(empty, DOCS)[0]["backends"][0]["reason"].startswith("not_published")


def engine_tokens(item):
    from g6.knowledge._envelope import PassageItem
    from g6.knowledge._fuse import tokens_of

    return tokens_of(PassageItem(**item))


def test_context_rechecks_revocation_at_return(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]

    def revoke_here(name):
        if name == "search:checked":
            engine.revoke(rig.local("revoke"), source)

    monkeypatch.setattr(engine, "checkpoint", revoke_here)
    found, refs = context(rig, DOCS)
    assert found["items"] == [] and found["pack"] == "" and refs == ()
    assert {d["why"] for d in found["denied"]} == {"revoked"}
    assert not (ProjectStore(rig.binding).folder / "packs").exists()


def test_saved_pack_is_denied_after_revoke(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    made, _ = context(rig, DOCS)
    usable, refs = engine.pack(rig.local("context"), made["pack"], clock=lambda: NOW)
    assert usable["state"] == "usable" and usable["items"] == made["items"] and len(refs) == len(made["items"])
    engine.revoke(rig.local("revoke"), source)
    denied, refs = engine.pack(rig.local("context"), made["pack"], clock=lambda: NOW)
    assert denied == {"pack": made["pack"], "state": "denied", "why": ["revoked"], "items": [], "withheld": []} and refs == ()
    for bad in ("pack-" + "0" * 32, "../registry", "pack-..", ""):
        with pytest.raises(Refused):
            engine.pack(rig.local("context"), bad)
    other = make(tmp_path, monkeypatch, name="other", build=False)
    with pytest.raises(Refused):
        engine.pack(other.local("context"), made["pack"])


def test_withheld_sources_are_absent_from_the_pack(tmp_path, monkeypatch, accepted_cloud_dialog):
    rig = make(tmp_path, monkeypatch)
    shared = rig.add(str(rig.folder / "docs"))["source"]
    private = rig.add(str(rig.folder / "notes"))["source"]
    accepted_cloud_dialog(f"knowledge:{shared}", "anthropic:claude_code", text="synthetic corpus")
    cloud = access(rig.binding, "context", "anthropic:claude_code")
    found, refs = context(rig, f"{DOCS} {NOTES} convention", acc=cloud)
    assert found["items"] and {i["source"] for i in found["items"]} == {shared}
    assert found["withheld"] == [{"source": private, "provider": "anthropic:claude_code"}]
    assert {r.source_id for r in refs} == {f"knowledge:{shared}"}
    saved = (ProjectStore(rig.binding).folder / "packs" / f"{found['pack']}.json").read_text(encoding="utf-8")
    assert saved.find(private) < 0, saved[max(0, saved.find(private) - 200):saved.find(private) + 60]
    assert f"The {NOTES} convention" not in saved
    # the pack made for the person holds both sources; the cloud client may not use it
    local, _ = context(rig, f"{DOCS} {NOTES} convention")
    assert {i["source"] for i in local["items"]} == {shared, private}
    refused, refs = engine.pack(cloud, local["pack"], clock=lambda: NOW)
    assert refused["state"] == "denied" and refused["why"] == ["not_shared"] and refused["items"] == [] and refs == ()
    assert refused["withheld"] == [{"source": private, "provider": "anthropic:claude_code"}]


def test_subagent_pack_built_from_a_context_is_rechecked_at_use(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add(expires_days=1)
    made, _ = context(rig, f"{DOCS} {SUBJECTS[2]}")
    assert engine.pack(rig.local("context"), made["pack"], clock=lambda: NOW)[0]["state"] == "usable"
    path = rig.folder / "docs" / f"{DOCS}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nedited after the pack was made\n", encoding="utf-8")
    assert rig.items(DOCS) == []   # a search observed the change
    changed = engine.pack(rig.local("context"), made["pack"], clock=lambda: NOW)[0]
    assert changed["state"] == "denied" and changed["why"] == ["changed"] and changed["items"] == []
    engine.refresh(rig.local("refresh"), clock=lambda: NOW)
    assert engine.pack(rig.local("context"), made["pack"], clock=lambda: NOW)[0]["state"] == "denied", \
        "a refresh publishes new passages; the old pack stays denied and a new context is made"
    fresh, _ = context(rig, f"{DOCS} {SUBJECTS[2]}")
    from datetime import timedelta

    from g6.knowledge._types import instant

    late = instant(ProjectStore(rig.binding).registrations()[0].expires_at) + timedelta(seconds=1)
    expired = engine.pack(rig.local("context"), fresh["pack"], clock=lambda: late)[0]
    assert expired["state"] == "denied" and "expired" in expired["why"]