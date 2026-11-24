# SPDX-License-Identifier: Apache-2.0
"""Unreadable publications grant no state and cannot trap prompt rearming."""
from contextlib import contextmanager

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.workspace._envelope import BUSY, WorkspaceRefused
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def test_rearming_passes_several_torn_runs_without_changing_their_bytes(disk_run, monkeypatch):
    from tests.harness.test_completion_amendment_reply import hook_run

    repo, base, _ = disk_run
    session, event = hook_run(repo.parent, monkeypatch, loop.Step(loop.STOP))
    asked = hook._task(session)
    loop._finish(session, {"attempted": 0}, "finished", ending="clean")
    torn = []
    for number in (1, 2):
        run = hook.current(event["session_id"], {**asked, "run": number})
        run.mkdir(parents=True)
        (run / "state.json").write_bytes(b'{"attempted":0}')
        torn.append(run)
    monkeypatch.setattr(hook, "_hint", lambda task: "")
    hook.on_prompt({**event, "prompt": "next task"})
    assert hook._task(session)["run"] == 3
    assert all((run / "state.json").read_bytes() == b'{"attempted":0}' for run in torn)
    assert all(not (run / "state.sha256").exists() for run in torn)


def test_unreadable_state_is_not_reported_as_a_sharing_refusal(tmp_path):
    session = tmp_path / "run"
    session.mkdir()
    (session / "state.json").write_bytes(b'{}')
    state = loop._load(session)
    assert state["code"] not in (cloud.REQUIRED, cloud.STATE_UNAVAILABLE, loop.CONSENT_STATE_AFTER_SEND)
    assert "g6_cloud_grant" not in state["stop"]


@pytest.mark.parametrize('orphan', ['state.sha256', '.write-version', 'state.json.new', 'state.sha256.new'])
def test_an_incomplete_first_publication_is_not_a_fresh_run(tmp_path, orphan):
    session = tmp_path / 'run'
    session.mkdir()
    target = session / orphan
    target.write_bytes(b'unfinished publication')
    state = loop._load(session)
    assert isinstance(state, loop._RetainedState) and not state.readable
    assert state['code'] == loop.NO_CLEAN_REVIEW and loop.attempted(state) is None
    assert target.read_bytes() == b'unfinished publication'


def test_reply_does_not_read_without_the_writer_lock(tmp_path, monkeypatch):
    from g6.workspace._coordination import Coordination

    @contextmanager
    def busy(*args, **kwargs):
        raise WorkspaceRefused(BUSY, "publication in progress")
        yield

    monkeypatch.setattr(Coordination, "mutex", busy)
    monkeypatch.setattr(loop, "_load", lambda session: pytest.fail("unlocked read"))
    with pytest.raises(WorkspaceRefused, match=BUSY):
        loop.reply_state(tmp_path / "run")


def test_receipt_replace_failure_leaves_an_unreadable_publication(tmp_path, monkeypatch):
    import os

    session = tmp_path / 'run'
    loop._save(session, {'attempted': 0}, sources=())
    replace = os.replace

    def held_receipt(source, target):
        if target == session / 'state.sha256':
            raise PermissionError('receipt held by a reader')
        return replace(source, target)

    monkeypatch.setattr(os, 'replace', held_receipt)
    with pytest.raises(PermissionError, match='receipt held'):
        loop._save(session, {'attempted': 1}, sources=())
    state = loop._load(session)
    assert not state.readable and state['code'] == loop.NO_CLEAN_REVIEW
    assert loop.attempted(state) is None


def test_settlement_keeps_the_owing_mark_of_an_unreadable_run(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    session = loop.store.home_path('completion_loop', 'torn')
    session.mkdir(parents=True)
    (session / 'state.json').write_bytes(b'{}')
    mark = loop.store.home_path(*loop.OWING, session.name)
    mark.parent.mkdir(parents=True)
    mark.touch()
    loop.settle()
    assert mark.is_file() and (session / 'state.json').read_bytes() == b'{}'
