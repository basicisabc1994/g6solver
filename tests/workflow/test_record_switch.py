# SPDX-License-Identifier: Apache-2.0
"""The record's switch at the one place a store under workflow/<project>/ is opened for writing (review revs17a,
finding 1; class: a guard that covers less than it guards). With the record off, nothing is added to any store, feedback
included, whoever the caller is; looking at, and removing, what is already held still works. The inventory below
holds every write in the package to that one check."""
from __future__ import annotations

import ast
import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest

import g6.harness.workflow
from g6.harness.workflow import _store, lifecycle, policy
from g6.harness.workflow import record as rec
from g6.harness.workflow.decisions import Decisions
from g6.harness.workflow.feedback import FeedbackStore
from tests.workflow.kit import PROJECT, T0, TASK, at, event, make_repo, sha, started

OFF = [None, "", "off", "0", "OFF", "On", " on"]
MARK = "WOMBAT-MARKER-5521"
_PACKAGE = Path(g6.harness.workflow.__file__).parent


def _off(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(policy.RECORD_ENV, raising=False)
    else:
        monkeypatch.setenv(policy.RECORD_ENV, value)
    assert policy.enabled()[0] is False


def _held(home) -> dict:
    return {str(p.relative_to(home)): p.read_bytes() for p in sorted(home.rglob("*"))
            if p.is_file() and not p.name.endswith(policy.LOCK)} if home.exists() else {}


@pytest.mark.parametrize("value", OFF)
def test_record_off_volunteered_feedback_is_not_kept_and_the_reply_says_so(home, tmp_path, monkeypatch, value):
    pytest.importorskip("fastmcp")
    from g6.mcp import workflow

    _off(monkeypatch, value)
    repo = make_repo(tmp_path / "repo")
    reply = asyncio.run(workflow.feedback(str(repo), "a task", None, text=f"it was slow {MARK}"))
    assert not (home / "workflow").exists()
    assert reply["result"]["status"] == "not_kept" and reply["result"]["asked"] is False
    assert MARK not in json.dumps(reply["result"])


@pytest.mark.parametrize("value", OFF)
def test_record_off_no_store_takes_an_entry_and_nothing_is_created(home, monkeypatch, value):
    _off(monkeypatch, value)
    record = rec.ProjectRecord(PROJECT)
    writes = {
        "start_run": lambda: started(record),
        "unsolicited": lambda: FeedbackStore(PROJECT).unsolicited(TASK, MARK, relayed_by="the agent", at=at()),
        "opt": lambda: FeedbackStore(PROJECT).opt("out", by="P", at=at(), via="form"),
        "reserve_invitation": lambda: FeedbackStore(PROJECT).reserve_invitation(TASK, now=T0),
        "decision": lambda: Decisions(PROJECT).record(sha("i"), "dismissed", task=TASK, by="P", at=at()),
        "project_decision": lambda: Decisions(PROJECT).record(sha("i"), "dismissed", task=TASK, scope="project",
                                                              by="P", at=at()),
    }
    for name, write in writes.items():
        with pytest.raises(_store.RecordOff):
            write()
        assert not (home / "workflow").exists(), name


@pytest.mark.parametrize("value", ["off", None])
def test_record_turned_off_adds_nothing_to_what_is_held(home, monkeypatch, value):
    record = rec.ProjectRecord(PROJECT)
    run = started(record)
    first = record.events(run=run)[0]
    FeedbackStore(PROJECT).unsolicited(TASK, "kept while on", relayed_by="the agent", at=at())
    before = _held(home)
    _off(monkeypatch, value)
    note = event(run, "annotation", "n", provenance="attributed_label", data={"target": first.event_id, "note": MARK})
    for write in (lambda: record.append(event(run, "loop_round", "1")), lambda: record.annotate(note),
                  lambda: started(record, "session-other"),
                  lambda: FeedbackStore(PROJECT).unsolicited(TASK, MARK, relayed_by="the agent", at=at(5))):
        with pytest.raises(_store.RecordOff):
            write()
    assert _held(home) == before
    # reading is not writing: what is held is still read with the record off
    assert [e.kind for e in record.events(run=run)] == ["run_started"]
    assert len(FeedbackStore(PROJECT).entries()) == 1


def test_record_off_corrections_that_add_are_refused_whole_and_deletions_still_apply(home, monkeypatch):
    record = rec.ProjectRecord(PROJECT)
    run = started(record)
    target = record.events(run=run)[0].event_id
    path = home / "workflow" / PROJECT / policy.CORRECTIONS
    path.write_text(json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [
        {"target": target, "action": "annotate", "note": MARK, "by": "P"}]}), encoding="utf-8")
    monkeypatch.setenv(policy.RECORD_ENV, "off")
    before = _held(home)
    result = lifecycle.apply_corrections(PROJECT, now=T0)
    assert result["status"] == "refused" and result["applied"] == 0
    assert any("off" in problem for problem in result["problems"]) and MARK not in json.dumps(result)
    assert _held(home) == before
    path.write_text(json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [
        {"action": "delete", "scope": "run", "id": run, "by": "P"}]}), encoding="utf-8")
    assert lifecycle.apply_corrections(PROJECT, now=T0)["applied"] == 1
    assert record.runs(TASK) == ()


def test_record_off_the_person_can_still_look_delete_and_expire(home, monkeypatch):
    record = rec.ProjectRecord(PROJECT)
    run = started(record)
    monkeypatch.setenv(policy.RECORD_ENV, "off")
    assert lifecycle.export(PROJECT, now=T0).is_file()
    assert lifecycle.delete(PROJECT, scope="run", ident=run, by="P", now=T0)["status"] == "deleted"
    monkeypatch.setenv(policy.RECORD_ENV, "on")
    started(record, "session-late")
    monkeypatch.setenv(policy.RECORD_ENV, "off")
    assert lifecycle.expire(PROJECT, now=T0 + timedelta(days=policy.RETENTION_DAYS["events"] + 1))["removed"] >= 1
    assert record.runs(TASK) == ()
    assert [r["reason"] for r in lifecycle.receipts(PROJECT)] == ["deletion_requested", "expired"]


# -- the inventory ----------------------------------------------------------------------------------------------------
#: Calls that create or change a file or folder.
_WRITE_CALLS = {"_locked", "write_whole", "_replace", "mkdir", "replace", "rmtree", "unlink", "write_text", "write_bytes",
                "ChainLedger", "fdopen", "keep"}
#: Every function in the package that makes one of those calls, and why it may.
#: "admitted": it calls ``_store.admit`` (the record's switch) before anything is written, or its only write is
#: ``write_whole``, which does. A file the person's own request writes (``policy.WRITTEN_WHEN_OFF``) is admitted by name.
#: "removal": it removes, or rewrites without, what is held; it adds no entry (deletion and expiry work with the record
#: off). "primitive": the bare write under ``write_whole`` and ``Chain.keep``, called by classified writers only.
#: "read": a ledger opened to read, or a lock taken beside a file that is already there.
WRITERS = {
    ("_store", "write_whole"): "admitted",
    ("_store", "_Open.append"): "admitted",          # only a chain Chain.open admitted for writing appends
    ("_store", "Chain.open"): "admitted",
    ("record", "ProjectRecord.start_run"): "admitted",
    ("lifecycle", "_write_receipts"): "admitted",   # receipts: written with the record off too, by name
    ("lifecycle", "export"): "admitted",            # the inspection file: likewise
    ("_store", "Chain.keep"): "removal",
    ("lifecycle", "_remove"): "removal",
    ("lifecycle", "_prune"): "removal",
    ("lifecycle", "_strip"): "removal",
    ("_store", "_replace"): "primitive",
    ("_store", "Chain._ledger"): "read",
    ("lifecycle", "apply_corrections"): "read",     # the lock beside the person's own corrections.json
}


def _name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        if func.attr == "replace" and not (isinstance(func.value, ast.Name) and func.value.id == "os"):
            return ""                                # a string's or a time's replace writes nothing
        return func.attr
    return getattr(func, "id", "")


def _writers() -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = {}
    for path in sorted(_PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node, owner: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    visit(child, child.name + ".")
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    calls = {_name(c) for c in ast.walk(child) if isinstance(c, ast.Call)}
                    hit = calls & _WRITE_CALLS
                    if hit:
                        found[(path.stem, owner + child.name)] = hit
        visit(tree, "")
    return found


def test_every_writer_in_the_package_is_classified_and_the_admitted_ones_pass_the_switch():
    found = _writers()
    assert set(found) == set(WRITERS), sorted(set(found) ^ set(WRITERS))
    assert set(WRITERS.values()) <= {"admitted", "removal", "primitive", "read"}
    for (module, name), kind in WRITERS.items():
        source = ast.get_source_segment((_PACKAGE / f"{module}.py").read_text(encoding="utf-8"), _function(module, name))
        if kind == "admitted":
            assert "admit(" in source or found[(module, name)] == {"write_whole"}, name
        elif kind == "primitive":
            assert module == "_store", name
        elif kind == "read":
            assert found[(module, name)] <= {"_locked", "ChainLedger"}, name
        if kind != "admitted":
            assert "admit(" not in source, name
    # a ledger is built in the store module only, so every entry is appended through Chain.open or Chain.keep
    assert {module for (module, name), calls in found.items() if "ChainLedger" in calls} == {"_store"}
    assert set(policy.WRITTEN_WHEN_OFF) == {policy.INSPECTION, policy.RECEIPTS}


def _function(module: str, name: str):
    tree = ast.parse((_PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    owner, _, leaf = name.rpartition(".")
    scope = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner) if owner else tree
    return next(n for n in scope.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == leaf)


@pytest.mark.parametrize("name", [policy.ANNOTATIONS, policy.DECISIONS, policy.FEEDBACK, "x" + policy.RUN_SUFFIX,
                                  "feedback" + policy.HEAD, "runs"])
def test_admit_refuses_every_record_file_with_the_record_off(home, monkeypatch, name):
    monkeypatch.setenv(policy.RECORD_ENV, "off")
    with pytest.raises(_store.RecordOff) as off:
        _store.admit(home / name)
    assert off.value.code == "G6_E_WORKFLOW_RECORD_OFF" and policy.RECORD_ENV in str(off.value)
    for kept in policy.WRITTEN_WHEN_OFF:
        _store.admit(home / kept)
