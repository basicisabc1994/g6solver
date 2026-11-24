# SPDX-License-Identifier: Apache-2.0
"""The mutex's own checks retain contention codes on every caller."""
import os

import pytest

from g6.workspace import _coordination as coordination
from g6.workspace._envelope import BUSY, WorkspaceRefused


def test_removed_mutex_is_busy_before_the_body_can_run(tmp_path, monkeypatch):
    folder = tmp_path / "lock"
    lock_once = coordination._lock_once

    def removed(handle):
        os.unlink(folder / "mutex")
        lock_once(handle)

    monkeypatch.setattr(coordination, "_lock_once", removed)
    with pytest.raises(WorkspaceRefused) as refused:
        with coordination.Coordination(folder).mutex(wait=0):
            pytest.fail("a removed mutex admitted the body")
    assert refused.value.code == BUSY
