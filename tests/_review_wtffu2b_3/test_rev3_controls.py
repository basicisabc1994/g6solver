# SPDX-License-Identifier: Apache-2.0
"""Review 3: checks walked without a finding, kept as passing controls.

The gate on the names the author's inventory test does not list (the shared row log, its spare, the owing marks, the
generated folders), a step that dies on a BaseException, and a path refusal settled before a send.
"""
import os

import pytest

from g6.core.model_calls import counting, note_model_call
from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused
from tests._review_wtffu2b_3.rev3_world import TASK, hard_link, quiet_scouts
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _junction(target, link):
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"no symbolic links here: {exc}")


def _unlink(link):
    if link.is_symlink():
        link.unlink()
    else:
        os.rmdir(link)


@pytest.mark.parametrize("name", ["failures.jsonl", "failures.jsonl.new", "owing/run", ".failures/mutex"])
def test_shared_names_refuse_a_second_name(tmp_path, monkeypatch, name):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    target = store.home_path("completion_loop") / name
    victim = hard_link(tmp_path, target)
    try:
        for write in (lambda: store.append_text(target, "row\n"), lambda: store.write_bytes(target, b"row"),
                      lambda: store.touch(target), lambda: store.unlink(target, missing_ok=True),
                      lambda: store.replace(target.with_name("fresh"), target)):
            with pytest.raises(WorkspaceRefused) as refused:
                write()
            assert refused.value.code == OUTSIDE_ROOT
        assert victim.read_bytes() == b"keep" and target.exists()
    finally:
        target.unlink()


@pytest.mark.parametrize("name", ["owing", ".failures", "rounds", "sets", "sets/critic", ".step", ".prompt"])
def test_generated_folders_refuse_a_junction(tmp_path, monkeypatch, name):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    run = hook.folder("rev3-controls")
    base = run.parent if name in ("owing", ".failures") else run
    outside, link = tmp_path / "outside", base / name
    outside.mkdir()
    _junction(outside, link)
    try:
        for write in (lambda: store.mkdir(link / "inner", parents=True, exist_ok=True),
                      lambda: store.write_bytes(link / "file", b"x"), lambda: store.touch(link / "mark"),
                      lambda: store.check_tree(link)):
            with pytest.raises(WorkspaceRefused) as refused:
                write()
            assert refused.value.code == OUTSIDE_ROOT
        assert list(outside.iterdir()) == []
    finally:
        _unlink(link)


def test_a_step_that_dies_leaves_the_run_free_and_its_count_kept(disk_run, monkeypatch):
    repo, base, session = disk_run

    class Died(BaseException):
        pass

    def scout(change, task, text, where, host, deadline):
        note_model_call()
        raise Died("the hook was killed after its first send")

    monkeypatch.setattr(loop, "_scout", scout)
    with pytest.raises(Died):
        loop.advance(repo, TASK, base, session)
    assert loop._WRITER.get() is None and loop._WRITER_WAIT.get() == 0 and loop._RUN_COUNT.get() is None
    assert loop.attempted(loop._load(session)) == 1
    quiet_scouts(monkeypatch)
    with counting() as sent:
        step = loop.advance(repo, TASK, base, session)
    assert step.action == loop.STOP and step.code == "" and sent() == 0
    assert loop.attempted(loop._load(session)) == 1


def test_a_refused_reservation_sends_nothing_and_counts_nothing(disk_run, monkeypatch, tmp_path):
    repo, base, session = disk_run
    sent = []

    def scout(change, task, text, where, host, deadline):
        note_model_call()          # the reservation: refused here, before any dispatch
        sent.append(1)
        return {"left_out": "", "where": str(where), "at_base": None, "refused": []}

    monkeypatch.setattr(loop, "_scout", scout)
    session.mkdir(parents=True)
    with loop._writing(session):
        loop._save(session, {"attempted": 2, "sets": {}}, sources=())
    hard_link(tmp_path, session / "state.sha256.new")
    try:
        for _ in range(3):
            step = loop.advance(repo, TASK, base, session)
            assert (step.action, step.code) == (loop.STOP, OUTSIDE_ROOT)
    finally:
        (session / "state.sha256.new").unlink()
    assert sent == [] and loop.attempted(loop._load(session)) == 2
