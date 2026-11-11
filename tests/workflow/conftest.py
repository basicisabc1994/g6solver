# SPDX-License-Identifier: Apache-2.0
"""Each workflow test gets a G6_HOME of its own that does not exist yet, and a workflow record that is on."""
from __future__ import annotations

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "g6-home"          # not created: the store must make its own way
    monkeypatch.setenv("G6_HOME", str(path))
    monkeypatch.setenv("G6_WORKFLOW_RECORD", "on")
    return path
