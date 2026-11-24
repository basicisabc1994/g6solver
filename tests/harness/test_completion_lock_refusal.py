# SPDX-License-Identifier: Apache-2.0
"""The step lock preserves a refusal raised by its protected body."""
import pytest

from g6.core.storage import PathRefused
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused


def test_step_lock_does_not_turn_a_body_path_refusal_into_busy(tmp_path):
    with pytest.raises(WorkspaceRefused) as refused:
        with Coordination(tmp_path / "lock").mutex():
            raise PathRefused("linked output", outside=True)
    assert refused.value.code == OUTSIDE_ROOT
