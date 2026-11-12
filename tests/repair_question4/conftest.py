"""Keep review test state inside this checkout; use no live services."""
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path():
    path = Path(os.environ["TMP"]) / ("review4_" + uuid4().hex)
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def review_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_QUESTIONS_PER_TASK", raising=False)

    def local_mkdtemp(suffix=None, prefix=None, dir=None):
        path = Path(dir or os.environ["TMP"]) / ((prefix or "tmp") + uuid4().hex + (suffix or ""))
        path.mkdir(parents=True)
        return str(path)

    monkeypatch.setattr(tempfile, "mkdtemp", local_mkdtemp)
