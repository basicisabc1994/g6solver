import os
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path():
    root = Path(__file__).resolve().parents[2] / ".bt_rev_cmfu1" / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


@pytest.fixture(autouse=True)
def review_environment(monkeypatch, tmp_path):
    original = os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        return original(path, 0o777 if os.name == "nt" and mode == 0o700 else mode,
                        dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", mkdir)
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))
    monkeypatch.delenv("G6_REPO_PYTHON", raising=False)
    monkeypatch.delenv("G6_REPO_ENV", raising=False)
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")
