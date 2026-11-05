# SPDX-License-Identifier: Apache-2.0
"""Study review 4's nine findings, each a test that failed on 59271b9 for the reason the review gave.

A run's ending decided in one place from every session process's exit (2, 5); stores checked by identity below them too (1, 4);
one rule each for a cached review's bug, a run's repeat and a saved row's name (3, 6, 9); the writer's reread compares the ending
(7); layers keeps token boundaries (8). Fakes only: no Docker, no model, no network."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(STUDY))

import layers  # noqa: E402
import loop  # noqa: E402
import replay3  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402


def k3_helpers():
    spec = importlib.util.spec_from_file_location("st4_k3_helpers", ROOT / "tests" / "validation" / "test_k3_rule_step.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    return helpers


@contextlib.contextmanager
def junction(target: Path, alias: Path):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(alias))
    else:
        alias.symlink_to(target, target_is_directory=True)
    try:
        yield alias
    finally:
        os.rmdir(alias) if os.name == "nt" else alias.unlink()


def finished(archive, task="bug", arm="A", **start):
    run = archive.start("run", task, arm=arm, **start)
    run.write("final.patch", b"candidate patch\n")
    run.finish({"agent_started": True, **({"executor": start["executor"]} if "executor" in start else {})}, "finished")
    return run


# --- 2, 5: a run's ending from every session process's exit ------------------------------------------------------------------
@pytest.mark.parametrize("arm", ["A", "S", "T"])
def test_finding_2_no_arm_finishes_when_its_first_session_exits_but_0(tmp_path, monkeypatch, arm):
    helpers = k3_helpers()
    # a result equal to the success but another object: the existing fake's process then exits 1
    record, _, _, ctx, _ = helpers._run(monkeypatch, tmp_path, arm, first=dict(helpers.SUCCESS))
    assert record["agent_started"] is True and (ctx.archive.root / record["run"] / "final.patch").is_file()
    assert record["status"] == "error"


def test_finding_2_a_clean_exit_still_finishes(tmp_path, monkeypatch):
    helpers = k3_helpers()
    record, *_ = helpers._run(monkeypatch, tmp_path, "A")
    assert record["status"] == "finished"


@pytest.mark.parametrize("stop,code,ended", [("", 0, ""), ("", 1, "error"), ("", 125, "error"), ("", None, "error"),
                                             ("", True, "error"), ("timeout", 0, "timeout"), ("cap_cost", 137, "cap_cost")])
def test_the_one_decision_of_a_sessions_ending(stop, code, ended):
    assert we.session_ending(stop, code) == ended


def test_finding_5_arm_r_cannot_finish_after_its_repair_exec_failed(tmp_path, monkeypatch):
    helpers = k3_helpers()
    real_popen, real_session, real_review_once, sessions = helpers._Docker.popen, we.run_session, we.review_once, []

    def popen(docker, *args, **kwargs):
        sessions.append(1)
        if len(sessions) == 1:
            return real_popen(docker, *args, **kwargs)
        return SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(), poll=lambda: 125, wait=lambda **kw: 125)

    def review_once(*args, **kwargs):
        monkeypatch.setattr(we, "run_session", real_session)
        return real_review_once(*args, **kwargs)

    monkeypatch.setattr(helpers._Docker, "popen", popen)
    monkeypatch.setattr(we, "review_once", review_once)
    monkeypatch.setattr(we, "run_critic", lambda *a, **kw: {"verdict": "defect", "rule": "r", "why": "w",
                                                            "test": "def test_x(): assert False", "seconds": 0})
    monkeypatch.setattr(we, "run_set", lambda *a, **kw: we.ProbeRun(1, {"t::test_x": "failed"}, {}, ""))
    record, *_ = helpers._run(monkeypatch, tmp_path, "R")
    assert len(sessions) == 2 and record["status"] == "error" and record["repairs"]["ended"] == "error"


def _functions(tree):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_every_session_process_waited_on_reaches_the_one_decision():
    """Study review 4, findings 2 and 5: an exit read only where a caller asked for it (``codes``). Built from the code: every
    function that starts a session process (``popen``) and waits on it gives that exit to ``session_ending``; no call names a
    ``codes`` list; and no session's ending is discarded (a ``run_session`` call as a statement)."""
    tree = ast.parse((STUDY / "run.py").read_text(encoding="utf-8"))
    waited = []
    for fn in _functions(tree):
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        names = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in calls}
        waits = [n for n in calls if getattr(n.func, "attr", "") == "wait"]
        if any(not (isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "_stop") for n in waits):
            waited.append((fn.name, "session_ending" in names))     # a process waited on (a watchdog's own event aside)
    assert waited and all(ok for _, ok in waited), waited
    assert {name for name, _ in waited} == {"run_session", "host_session", "_run_task"}
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.keyword) and n.arg == "codes"]
    bare = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "id", "") == "run_session"]
    assert bare == []


# --- 1, 4: stores by identity, below them too --------------------------------------------------------------------------------
def test_finding_1_a_side_store_never_overwrites_a_refused_row_of_an_indexed_run(tmp_path):
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A")         # a start that names no repeat: its row is refused, with no key
    run.finish({"agent_started": True}, "finished")
    (tmp_path / c.SAVED).mkdir()
    destination = tmp_path / c.SAVED / "cegis.json"
    destination.write_text(json.dumps({"run": run.rel, "passes": True}), encoding="utf-8")
    before = destination.read_bytes()
    assert c.shelf(archive, lambda task: None).rows == ()
    with pytest.raises(ValueError, match="holds the row"):
        c.beside(archive, "cegis.json")
    assert destination.read_bytes() == before
    destination.write_text(json.dumps({"run": "no/such/run"}), encoding="utf-8")     # no indexed run's: a store as before
    assert c.beside(archive, "cegis.json") == destination


def test_finding_4_a_junction_below_a_store_cannot_reach_the_rows(tmp_path):
    task = we.load_tasks()[0]
    archive = c.Archive(tmp_path)
    run = finished(archive, task.id, "K2", repeat=1)
    ins = replay3.rule()
    destination = c.keep(archive, {"run": run.rel, "passes": True, "failing": 0}, file=f"{task.id}-{ins.digest}.json")
    before = destination.read_bytes()
    store = c.beside(archive, ins.folder)
    store.mkdir()

    def ask(*args, **kwargs):
        return {"verdict": "none", "rule": "r", "why": "", "test": "", "error": ""}, None, None

    with junction(destination.parent, store / "bugs"):
        with pytest.raises(ValueError, match="leaves its store"):
            replay3.review_bug(SimpleNamespace(archive=archive), tmp_path, task, [{"run": run.rel, "task": task.id}],
                               "title", "issue", ask=ask)
        with pytest.raises(ValueError, match="leaves its store"):
            c.beside(archive, ins.folder, "bugs", "x.json")
    assert destination.read_bytes() == before
    assert c.beside(archive, ins.folder, "bugs", "x.json") == store / "bugs" / "x.json"
    with pytest.raises(ValueError):
        c.beside(archive, ins.folder, "..", "loop")


def test_no_caller_appends_a_path_past_the_store_check():
    """Study review 4, finding 4: a path below a store is checked by ``beside`` itself (its ``below`` parts), so no module of the
    study divides a ``beside(...)`` path further."""
    past = []
    for path in sorted(STUDY.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.left, ast.Call) and \
                    getattr(node.left.func, "attr", getattr(node.left.func, "id", "")) == "beside":
                past.append((path.stem, node.lineno))
    assert past == []


# --- 3, 6, 9: one rule each ----------------------------------------------------------------------------------------------------
def test_finding_3_a_cached_review_of_another_bug_is_refused_never_overwritten(tmp_path):
    task = we.load_tasks()[0]
    archive = c.Archive(tmp_path)
    candidate = finished(archive, task.id, "K2", repeat=1)
    (tmp_path / c.SAVED).mkdir()
    cached = replay3.bug_record(tmp_path, task.id)
    cached.parent.mkdir(parents=True)
    foreign = {"verdict": "none", "rule": "foreign rule", "why": "", "test": "", "error": ""}
    cached.write_text(json.dumps({"task": "another-bug", "found": foreign}), encoding="utf-8")
    before, asked = cached.read_bytes(), []

    def ask(*args, **kwargs):
        asked.append(1)
        return {**foreign, "rule": "requested bug's rule"}, None, None

    with pytest.raises(ValueError, match="on two bugs"):
        replay3.review_bug(SimpleNamespace(archive=archive), tmp_path, task, [{"run": candidate.rel, "task": task.id}],
                           "title", "issue", ask=ask)
    assert cached.read_bytes() == before and asked == []


def test_finding_6_a_run_that_names_no_repeat_blocks_no_repeat(tmp_path):
    archive = c.Archive(tmp_path)
    run = finished(archive, "bug", "K2", model=we.R3_MODELS[1])
    assert c.run_facts(archive, run.rel).repeat is None
    assert we.r3_done(archive.rows(), archive.root) == {}
    ctx = SimpleNamespace(archive=archive, tasks=[], model=we.R3_MODELS[1], arm="K2", repeat=1, executor="host",
                          g6={"commit": we.R3_LOOP_G6}, rerun_why="")
    why, _, _ = we.r3_refusal(ctx, SimpleNamespace(id="bug"))
    assert "already made" not in why
    made = finished(archive, "bug", "K2", model=we.R3_MODELS[1], repeat=1)
    assert we.r3_made(archive.rows(), archive.root, (we.R3_MODELS[1], "bug", "K2", 1)) == made.rel


@pytest.mark.parametrize("name,ok", [("moved.json", True), ("moved.JSON", os.name == "nt"), ("moved.json.part", False),
                                     ("a/moved.json", False), ("", False), ("moved", False)])
def test_finding_9_the_rows_reader_and_writer_accept_one_set_of_names(name, ok):
    assert c.row_file(name) is ok


@pytest.mark.skipif(os.name != "nt", reason="the Windows file system compares names without case")
def test_finding_9_a_row_the_reader_finds_as_moved_JSON_is_written_again_there(tmp_path, monkeypatch):
    archive = c.Archive(tmp_path)
    run = finished(archive, "bug", "A", repeat=1, executor="host")
    (tmp_path / c.SAVED).mkdir()
    destination = tmp_path / c.SAVED / "moved.JSON"
    destination.write_text(json.dumps({"run": run.rel, "passes": True, "failing": 0, c.MADE_UNDER: "stale"}), encoding="utf-8")
    monkeypatch.setattr(we, "amendment", lambda task: None)
    held = c.shelf(archive, we.amendment).get(("A", "bug", 1))
    assert held is not None and held.file == "moved.JSON" and not held.holds
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: json.dumps({"checks": [{"patch": "final", "passes": True, "failing": []}]}))
    assert loop.one(tmp_path, "g6", "A", "bug", 1)["passes"] is True
    assert json.loads(destination.read_text("utf-8"))[c.MADE_UNDER] == c.run_facts(archive, run.rel).ending_sha256


# --- 7: the writer's reread compares the ending --------------------------------------------------------------------------------
def test_finding_7_an_ending_changed_before_the_write_voids_the_verdict(tmp_path, monkeypatch):
    archive = c.Archive(tmp_path)
    run = finished(archive, "bug", "A", repeat=1, executor="host")
    monkeypatch.setattr(we, "amendment", lambda task: None)
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: json.dumps(
        {"run": run.rel} if command == "run" else {"checks": [{"patch": "final", "passes": True, "failing": []}]}))
    real_keep = c.keep

    def keep(root, row, **kwargs):
        run.failed("failure after the caller's reread", agent_started=True)
        return real_keep(root, row, **kwargs)

    monkeypatch.setattr(c, "keep", keep)
    with pytest.raises(SystemExit, match="ending changed"):
        loop.one(tmp_path, "g6", "A", "bug", 1)
    assert not (tmp_path / c.SAVED / "A-bug-1.json").exists()


def test_finding_7_the_writer_refuses_a_row_made_under_another_ending(tmp_path):
    archive = c.Archive(tmp_path)
    run = finished(archive, "bug", "A", repeat=1, executor="host")
    with pytest.raises(c.ArchiveError, match="ending changed"):
        c.keep(archive, {"run": run.rel, "passes": True, "failing": 0, c.MADE_UNDER: "0" * 64})
    assert c.keep(archive, {"run": run.rel, "passes": True, "failing": 0,
                            c.MADE_UNDER: c.run_facts(archive, run.rel).ending_sha256}).is_file()


# --- 8: token boundaries -------------------------------------------------------------------------------------------------------
def test_finding_8_two_token_sequences_are_two_changes():
    def patch(expression):
        return ("diff --git a/api.py b/api.py\n--- a/api.py\n+++ b/api.py\n"
                "@@ -1,2 +1,2 @@\n def f(x, notx):\n-    return x\n" f"+    return {expression}\n")

    rows = [(hashlib.sha256(t.encode()).hexdigest(), layers.change(t), False) for t in (patch("not x"), patch("notx"))]
    assert set(layers.kinds_of(rows).values()) == {"other wrong"}
    same = [(hashlib.sha256(t.encode()).hexdigest(), layers.change(t), False)
            for t in (patch("not x"), patch("not  x  # same tokens"))]
    assert set(layers.kinds_of(same).values()) == {"commonest wrong"}
