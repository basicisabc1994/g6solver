# SPDX-License-Identifier: Apache-2.0
"""Removal as a checked, restartable step, as an inventory: every file under G6's home that holds a source's text or
file names, found by looking (not from a list), against a removal that fails (the file is open elsewhere) and a
deletion interrupted at each of its steps. A complete deletion never reports done while such a file remains, and
running it again finishes it."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import g6._context_engine
from g6._context_engine import _engine as engine
from g6._context_engine._registry import ProjectStore, Refused
from tests.knowledge.corpus import SUBJECTS
from tests.knowledge.corpus.rig import NOW, make

DOCS = SUBJECTS[0]
MARKS = (f"The {DOCS} convention".encode(), f"{DOCS}.md".encode())


def _everything(tmp_path, monkeypatch):
    """A source with every kind of record G6 keeps of it: two generations, audit copies, a snapshot, a saved
    context, a preview that was not accepted and the one that was."""
    rig = make(tmp_path, monkeypatch)
    engine.preview(rig.binding, str(rig.folder))
    source = rig.add(retention="audit")["source"]
    assert rig.search(DOCS).snapshot
    assert engine.context(rig.local("context"), f"{DOCS} convention", clock=lambda: NOW)[0]["pack"]
    (rig.folder / "docs" / f"{SUBJECTS[2]}.md").write_text("# rewritten\n", encoding="utf-8")
    assert engine.refresh(rig.local("refresh"), clock=lambda: NOW)["state"] == "published"
    return rig, source, ProjectStore(rig.binding)


def holders(rig) -> list[Path]:
    """Every file under G6's home that holds the source's text or one of its file names."""
    return sorted(p for p in (rig.home / "knowledge").rglob("*")
                  if p.is_file() and any(mark in p.read_bytes() for mark in MARKS))


def _delete(rig, source) -> dict:
    try:
        return engine.delete(rig.local("delete"), source, complete=True, attribution="Person")
    except Refused as exc:
        return {"refused": exc.code}


def test_control_every_kind_of_record_is_found_and_an_undisturbed_complete_deletion_leaves_none(tmp_path, monkeypatch):
    rig, source, store = _everything(tmp_path, monkeypatch)
    kinds = {p.parent.name if p.parent.name in ("retained", "packs", "previews") else p.name.split(".")[0]
             for p in holders(rig)}
    assert kinds >= {"retained", "packs", "previews", "lexical", "registry"}, kinds
    assert _delete(rig, source)["audit_copies"] == "removed" and holders(rig) == []
    assert store.pending_removals() == []


@pytest.mark.skipif(sys.platform != "win32", reason="not reached: an open file can be unlinked here")
def test_a_holder_that_cannot_be_removed_keeps_the_deletion_from_reporting_done_and_a_second_run_finishes(tmp_path, monkeypatch):
    def kind(path: Path) -> str:
        return path.parent.name if path.parent.name in ("retained", "packs", "previews") else path.name

    first, _, _ = _everything(tmp_path / "survey", monkeypatch)
    kinds = sorted({kind(p) for p in holders(first) if not p.name.startswith("registry.db")})
    assert {"retained", "packs", "previews"} <= set(kinds) and sum(k.startswith("lexical.") for k in kinds) == 2, kinds
    for n, wanted in enumerate(kinds):   # a fresh project for each kind of file, found by looking
        rig, source, store = _everything(tmp_path / f"case{n}", monkeypatch)
        holder = next(p for p in holders(rig) if kind(p) == wanted)
        with open(holder, "rb"):                      # another program has it open
            receipt = _delete(rig, source)
        assert receipt == {"refused": engine.DELETION_PENDING}, (wanted, receipt)
        assert store.pending_removals(), "what could not be removed is on the durable list"
        assert rig.items(DOCS) == [], "the source is denied although its deletion is not finished"
        assert _delete(rig, source)["audit_copies"] == "removed", wanted
        assert holders(rig) == [] and store.pending_removals() == [], wanted


def _steps() -> list[str]:
    """The registry and engine steps a complete deletion takes, read from the code."""
    path = Path(g6._context_engine.__file__).parent / "_engine.py"
    function = next(n for n in ast.parse(path.read_text(encoding="utf-8")).body
                    if isinstance(n, ast.FunctionDef) and n.name == "delete")
    return sorted({c.func.attr for c in ast.walk(function) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                   and isinstance(c.func.value, ast.Name) and c.func.value.id == "store"} | {
                  c.func.id for c in ast.walk(function) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                  and c.func.id.startswith("_") and c.func.id not in ("_revoke_sharing",)})


class Died(BaseException):
    """The process dying at a step."""


@pytest.mark.parametrize("step", _steps())
def test_a_complete_deletion_interrupted_at_any_step_is_finished_by_running_it_again(tmp_path, monkeypatch, step):
    assert {"delete", "stage", "erase", "pend", "remove_file", "retry_removals", "_carry", "_publish_staged", "_purge"} <= set(_steps())
    rig, source, store = _everything(tmp_path, monkeypatch)
    owner = ProjectStore if hasattr(ProjectStore, step) else engine
    real = getattr(owner, step)
    state = {"died": False}

    def dying(*args, **kwargs):
        if not state["died"]:
            state["died"] = True
            raise Died()
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, step, dying)
    try:
        engine.delete(rig.local("delete"), source, complete=True, attribution="Person")
    except Died:
        pass
    if not state["died"]:
        pytest.skip(f"not reached: an undisturbed complete deletion does not call {step}")
    if step != "delete":   # dying inside the first step leaves everything as it was; after it, the source is denied
        assert rig.items(DOCS) == [], "interrupted or not, the source is denied once the first step is done"
    done = _delete(rig, source)
    if "refused" in done:
        done = _delete(rig, source)
    assert done.get("audit_copies") == "removed", done
    assert holders(rig) == [] and store.pending_removals() == []
    more = rig.project / "more"
    more.mkdir()
    (more / "extra.md").write_text("# The pangolin decision\n\nThe pangolin note.\n", encoding="utf-8")
    assert rig.add(str(more))["state"] == "active" and rig.items("pangolin")


def test_a_refused_commit_and_a_failed_refresh_leave_nothing_they_wrote(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add(str(rig.folder / "docs"), retention="audit")
    store = ProjectStore(rig.binding)
    before = sorted(p.name for p in store.folder.rglob("*") if p.is_file())
    real = engine._lexical

    class Broken:
        def __init__(self, inner):
            self.inner = inner

        def add(self, passages):
            raise OSError("the disk is full")

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(engine, "_lexical", lambda s, g: Broken(real(s, g)))
    with pytest.raises(OSError):
        rig.add(str(rig.folder / "notes"), retention="audit")
    path = rig.folder / "docs" / f"{DOCS}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
    assert engine.refresh(rig.local("refresh"), clock=lambda: NOW)["state"] == "unavailable"
    monkeypatch.setattr(engine, "_lexical", real)
    after = sorted(p.name for p in store.folder.rglob("*") if p.is_file())
    assert after == before, sorted(set(after) ^ set(before))
    assert [g.state for g in store.generations()] == ["published", "abandoned", "abandoned"]