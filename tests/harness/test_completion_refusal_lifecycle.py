# SPDX-License-Identifier: Apache-2.0
"""Refusals keep their identity, count and retained bytes across writer scopes."""
import contextvars

import pytest

from g6.core import cloud_consent as cloud
from g6.core.model_calls import note_model_call
from g6.core.storage import PathRefused
from g6.harness import completion_loop as loop
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


@pytest.mark.parametrize("kind", ["workspace", "path", "repository"])
def test_body_refusal_after_send_is_terminal_with_its_own_code(disk_run, monkeypatch, kind):
    repo, base, session = disk_run
    sent = []
    code = "G6_E_INPUT_VALIDATION" if kind == "repository" else OUTSIDE_ROOT

    def scout(*args):
        note_model_call()
        sent.append(1)
        if kind == "workspace":
            raise WorkspaceRefused(code, "refused output")
        if kind == "path":
            raise PathRefused("refused output", outside=True)
        raise loop.repo_tests.RepoRefused(code, "refused output")

    monkeypatch.setattr(loop, "_scout", scout)
    first = loop.advance(repo, "task", base, session)
    second = loop.advance(repo, "task", base, session)
    assert (first.action, first.code) == (loop.STOP, code)
    assert second.action == loop.STOP and second.note == loop.ALREADY_ENDED
    assert sent == [1] and loop.attempted(loop._load(session)) == 1


@pytest.mark.parametrize("operation", ["save", "reserve"])
def test_unreadable_state_refuses_expired_writer_without_publishing(tmp_path, operation):
    session = tmp_path / "run"
    state = {"attempted": 1, "reviews": [{"patch": "keep"}], "row_due": "owed"}
    with loop._writing(session), loop._run_count(session, state, fresh=False):
        loop._save(session, state, sources=())
        copied = contextvars.copy_context()
    (session / "state.json").write_bytes(b'{"attempted":9}')
    before = {p.name: p.read_bytes() for p in session.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match=cloud.REQUIRED):
        if operation == "reserve":
            copied.run(note_model_call)
        else:
            copied.run(loop._save, session, state, sources=())
    assert {p.name: p.read_bytes() for p in session.iterdir() if p.is_file()} == before


def test_reply_waits_for_writer_and_never_claims_no_step(tmp_path, monkeypatch):
    from contextlib import contextmanager

    session = tmp_path / "run"
    loop._save(session, {"attempted": 2, "done": True, "code": cloud.REQUIRED}, sources=())
    waits = []

    @contextmanager
    def held(self, *, wait=0):
        waits.append(wait)
        raise WorkspaceRefused(loop.BUSY, "reservation still flushing")
        yield

    monkeypatch.setattr(Coordination, "mutex", held)
    retained = loop.reply_state(session)
    assert waits and waits[0] > 0
    assert loop.attempted(retained) == 2
    assert "not sent" not in loop.read_report(session, retained).lower()
