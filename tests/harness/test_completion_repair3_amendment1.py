# SPDX-License-Identifier: Apache-2.0
"""Reply accounting remains honest across bounded writer waits and torn state."""
import contextvars
from contextlib import contextmanager

import pytest

from g6.core import cloud_consent as cloud
from g6.core.model_calls import note_model_call
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import BUSY, WorkspaceRefused
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _busy_writer(monkeypatch):
    waits = []

    @contextmanager
    def busy(self, *, wait=0):
        waits.append(wait)
        raise WorkspaceRefused(BUSY, "reservation still flushing")
        yield

    monkeypatch.setattr(Coordination, "mutex", busy)
    return waits


@pytest.mark.parametrize("retained_count", [None, 0, 3])
def test_no_python_reply_always_says_zero_spent(disk_run, monkeypatch, retained_count):
    repo, base, session = disk_run
    if retained_count is not None:
        loop._save(session, {loop.ATTEMPTED: retained_count}, sources=())
    monkeypatch.setattr(loop, "advance", lambda *a, **k: loop.Step(loop.STOP, code=loop.NO_PYTHON))
    reply = review_loop.step(str(repo), "task", "disk-review2", base)
    assert reply["code"] == loop.NO_PYTHON and "nothing was spent" in reply["error"]
    assert "requests_sent" not in reply
    if retained_count is not None:
        assert loop.attempted(loop._load(session)) == retained_count


def test_unsent_hook_starts_with_its_words_and_keeps_the_code(tmp_path, monkeypatch):
    from tests.harness.test_completion_amendment_reply import hook_run

    step = loop.Step(loop.STOP, code=cloud.REQUIRED, ending="untested")
    session, event = hook_run(tmp_path, monkeypatch, step)
    loop._save(session, {loop.ATTEMPTED: 0}, sources=())
    reply = hook.on_stop(event)
    assert reply["systemMessage"].startswith("The review was not sent.")
    assert reply["systemMessage"].count(cloud.REQUIRED) == 1


def test_busy_reply_reads_a_receipted_snapshot_after_waiting(tmp_path, monkeypatch):
    session = tmp_path / "run"
    loop._save(session, {loop.ATTEMPTED: 2, "done": True, "code": cloud.REQUIRED}, sources=())
    waits = _busy_writer(monkeypatch)
    state = loop.reply_state(session)
    assert waits and waits[0] > 0
    assert state.readable and loop.attempted(state) == 2


@pytest.mark.parametrize("torn", [False, True])
def test_mcp_busy_reply_stops_with_checked_count_or_unknown(disk_run, monkeypatch, torn):
    repo, base, session = disk_run
    loop._save(session, {loop.ATTEMPTED: 2, "done": True, "code": cloud.REQUIRED,
                         "ending": "untested"}, sources=())
    if torn:
        (session / "state.json").write_bytes(b'{"attempted":0,"done":true}')
    before = {p.name: p.read_bytes() for p in session.iterdir() if p.is_file()}
    real_reply_state = loop.reply_state
    waits = []

    def contended(run):
        # Contend only at reply time, after MCP has read its task binding.
        with monkeypatch.context() as patch:
            observed = _busy_writer(patch)
            state = real_reply_state(run)
            waits.extend(observed)
            return state

    monkeypatch.setattr(loop, "reply_state", contended)
    monkeypatch.setattr(loop, "advance", lambda *a, **k:
                        loop.Step(loop.STOP, "old not sent words", cloud.REQUIRED, ending="untested"))
    reply = review_loop.step(str(repo), "task", "disk-review2", base)
    assert waits and waits[0] > 0
    assert reply["action"] == loop.STOP and "did nothing" not in reply["error"]
    if torn:
        assert reply["code"] == loop.NO_CLEAN_REVIEW
        assert reply.get("requests_sent") is None
        assert not reply.get("ending") and not reply.get("note")
        assert "cannot be read" in reply["error"] and "unknown" in reply["error"]
        assert "g6_cloud_grant" not in reply["report"]
    else:
        assert type(reply["requests_sent"]) is int and reply["requests_sent"] == 2
        assert "up to 2 request(s)" in reply["report"]
    assert {p.name: p.read_bytes() for p in session.iterdir() if p.is_file()
            and p.name != "task.json" and p.name != "task.sha256"} == before


def test_unreadable_hook_does_not_invent_a_retained_ending(tmp_path, monkeypatch):
    from tests.harness.test_completion_amendment_reply import hook_run

    session, event = hook_run(tmp_path, monkeypatch, loop.Step(loop.STOP, code=cloud.REQUIRED))
    (session / "state.json").write_bytes(b'{"attempted":0}')
    reply = hook.on_stop(event)
    assert loop.NO_CLEAN_REVIEW in reply["systemMessage"]
    assert "cannot be read" in reply["systemMessage"] and "unknown" in reply["systemMessage"]
    assert "already ended" not in reply["systemMessage"] and "not sent" not in reply["systemMessage"]


def test_load_expired_save_and_reservation_leave_torn_bytes_exactly_unchanged(tmp_path):
    session = tmp_path / "run"
    state = {loop.ATTEMPTED: 1, "reviews": [{"patch": "keep"}], "row_due": "owed"}
    with loop._writing(session), loop._run_count(session, state, fresh=False):
        loop._save(session, state, sources=())
        expired = contextvars.copy_context()
    (session / "state.json").write_bytes(b'{"attempted":9,"reviews":[{"patch":"torn"}]}')
    before = {p.name: p.read_bytes() for p in session.iterdir() if p.is_file()}
    retained = loop._load(session)
    assert not retained.readable and retained["code"] == loop.NO_CLEAN_REVIEW
    assert {p.name: p.read_bytes() for p in session.iterdir() if p.is_file()} == before
    with pytest.raises(ValueError, match=loop.NO_CLEAN_REVIEW):
        expired.run(loop._save, session, state, sources=())
    assert {p.name: p.read_bytes() for p in session.iterdir() if p.is_file()} == before
    with pytest.raises(ValueError, match=loop.NO_CLEAN_REVIEW):
        expired.run(note_model_call)
    assert {p.name: p.read_bytes() for p in session.iterdir() if p.is_file()} == before
