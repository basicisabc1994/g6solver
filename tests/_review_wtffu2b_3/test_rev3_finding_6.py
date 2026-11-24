# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 6: the mutex's own "it was removed while I waited" check now answers outside-root.

The commit says it maps "a PathRefused raised inside the protected operation" and keeps "actual OS lock contention on the
busy path". The ``except PathRefused`` it changed also catches the mutex's own two ``plain_file`` checks, made before the
body runs. A mutex file replaced during the wait is still ``G6_E_WORKSPACE_BUSY`` (raised by hand two lines below); one
removed during the wait, the same event, was ``BUSY`` and is now ``G6_E_WORKSPACE_OUTSIDE_ROOT``, on every surface that
shares ``Coordination`` (the frozen workspace ops among them). In the completion loop that is a ``stop`` where a repeat
would have run.
"""
import os

import pytest

from g6.core.storage import PathRefused
from g6.harness import completion_loop as loop
from g6.workspace import _coordination as coordination
from g6.workspace._envelope import BUSY, OUTSIDE_ROOT, WorkspaceRefused


def test_control_a_lock_another_holds_is_busy(tmp_path):
    lock = coordination.Coordination(tmp_path / "ws" / ".lock")
    with lock.mutex(wait=0):
        with pytest.raises(WorkspaceRefused) as refused:
            with coordination.Coordination(tmp_path / "ws" / ".lock").mutex(wait=0):
                pass
    assert refused.value.code == BUSY


def test_control_a_body_path_refusal_is_outside_root(tmp_path):
    with pytest.raises(WorkspaceRefused) as refused:
        with coordination.Coordination(tmp_path / "ws" / ".lock").mutex(wait=0):
            raise PathRefused("linked output", outside=True)
    assert refused.value.code == OUTSIDE_ROOT


def _removed_during_the_wait(monkeypatch, mutex):
    real = coordination._lock_once

    def lock_once(handle):
        os.unlink(mutex)             # what a cleanup holding the lock does before it lets go (delete sharing allows it)
        real(handle)

    monkeypatch.setattr(coordination, "_lock_once", lock_once)


def test_a_mutex_removed_during_the_wait_is_still_busy(tmp_path, monkeypatch):
    folder = tmp_path / "ws" / ".lock"
    _removed_during_the_wait(monkeypatch, folder / "mutex")
    ran = []
    with pytest.raises(WorkspaceRefused) as refused:
        with coordination.Coordination(folder).mutex(wait=0):
            ran.append(1)
    assert not ran
    assert refused.value.code == BUSY, refused.value


def test_a_step_whose_lock_was_removed_during_the_wait_waits(tmp_path, monkeypatch):
    session = tmp_path / "run"
    session.mkdir()
    _removed_during_the_wait(monkeypatch, session / ".step" / "mutex")
    monkeypatch.setattr(loop, "settle", lambda: None)
    step = loop.advance(tmp_path, "task", "HEAD", session)
    assert (step.action, step.code) == (loop.WAIT, loop.BUSY), step
