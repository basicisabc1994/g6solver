"""Keep review fixtures in ordinary workspace folders on managed Windows."""
from pathlib import Path
from uuid import uuid4
import os
import tempfile
import pytest


@pytest.fixture
def tmp_path():
    path = Path(os.environ["TMP"]) / ("case_" + uuid4().hex)
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def review_home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_QUESTIONS_PER_TASK", raising=False)
    # mode=0o700 temporary directories are inaccessible to child processes in this
    # managed Windows session; ordinary workspace directories work.
    def ordinary_mkdtemp(suffix=None, prefix=None, dir=None):
        parent = Path(dir) if dir is not None else Path(os.environ["TMP"])
        path = parent / ((prefix or "tmp") + uuid4().hex + (suffix or ""))
        path.mkdir(parents=True)
        return str(path)
    monkeypatch.setattr(tempfile, "mkdtemp", ordinary_mkdtemp)
