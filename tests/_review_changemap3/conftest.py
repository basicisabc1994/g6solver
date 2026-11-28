import os
import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def review_environment(monkeypatch):
    original = os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        return original(path, 0o777 if os.name == "nt" and mode == 0o700 else mode,
                        dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", mkdir)
    for name in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(name, r"C:\nonexistent\g6-no-host.exe")


@pytest.fixture
def tmp_path(monkeypatch, tmp_path_factory):
    root = Path(__file__).resolve().parents[2] / ".bt_rev_changemap3" / uuid.uuid4().hex
    root.mkdir(parents=True)
    monkeypatch.setenv("G6_HOME", str(tmp_path_factory.mktemp("change-map-home") / "g6-home"))
    return root


@pytest.fixture(autouse=True)
def collection_runtime(monkeypatch, request):
    if os.name == "nt":
        monkeypatch.setenv("G6_REPO_PYTHON", request.getfixturevalue("confined_python"))
