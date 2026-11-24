# SPDX-License-Identifier: Apache-2.0
"""Settlement survives housekeeping; terminal refusals retain their debt."""
import json

import pytest

from g6.core.model_calls import note_model_call
from g6.harness import completion_loop as loop
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def test_cleanup_failure_is_recorded_without_replacing_settlement(tmp_path, monkeypatch):
    session = tmp_path / "run"

    def refused(*args):
        raise WorkspaceRefused(OUTSIDE_ROOT, "copy is linked")

    monkeypatch.setattr(loop.store, "remove_tree", refused)
    with loop._writing(session):
        step = loop._finish(session, {"attempted": 1, "visible": str(tmp_path / "copy")}, "kept report", ending="clean")
    state = loop._load(session)
    assert step.ending == state["ending"] == "clean" and step.code == state["code"] == ""
    assert OUTSIDE_ROOT in state["cleanup"]
    assert loop.read_report(session, state) == "kept report"


def test_cleanup_does_not_silence_a_filesystem_error(tmp_path, monkeypatch):
    import shutil

    session, visible = tmp_path / 'run', tmp_path / 'copy'
    visible.mkdir()

    def cannot_remove(path, **kwargs):
        if not kwargs.get('ignore_errors'):
            raise OSError('copy could not be removed')

    monkeypatch.setattr(shutil, 'rmtree', cannot_remove)
    with loop._writing(session):
        loop._finish(session, {'attempted': 0, 'visible': str(visible)}, 'kept report', ending='clean')
    state = loop._load(session)
    assert 'copy could not be removed' in state['cleanup']
    assert state['ending'] == 'clean'


@pytest.mark.parametrize("sends", [0, 1])
def test_repository_failure_is_retryable_only_before_any_reservation(disk_run, monkeypatch, sends):
    repo, base, session = disk_run
    loop._save(session, {"attempted": sends}, sources=())

    def refused(*args):
        raise loop.repo_tests.RepoRefused("G6_E_GIT_COMMAND_FAILED", "git timed out")

    monkeypatch.setattr(loop.repo_tests, "change", refused)
    step = loop.advance(repo, "task", base, session)
    state = loop._load(session)
    assert step.code == "G6_E_GIT_COMMAND_FAILED"
    assert bool(state.get("done")) == bool(sends)
    if sends:
        assert state["stop"].count(step.code) == 1


def test_terminal_body_refusal_has_its_own_failure_cause(disk_run, monkeypatch):
    repo, base, session = disk_run

    def refused(*args):
        note_model_call()
        raise WorkspaceRefused(OUTSIDE_ROOT, "output refused")

    monkeypatch.setattr(loop, "_scout", refused)
    step = loop.advance(repo, "task", base, session)
    state = loop._load(session)
    log = loop.store.home_path(*loop.FAILURES)
    rows = [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []
    row = next((row for row in rows if row["session"] == session.name), None)
    row = row or json.loads(state["row_due"])
    assert row["cause"] == "completion_body_refused" and step.code == OUTSIDE_ROOT
    assert state["stop"].count(OUTSIDE_ROOT) == 1
