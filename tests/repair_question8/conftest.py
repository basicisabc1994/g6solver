import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path():
    path = Path(os.environ['TMP']) / ('review_' + uuid4().hex)
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('G6_QUESTIONS_PER_TASK', raising=False)

    def mkdtemp(suffix=None, prefix=None, dir=None):
        path = Path(dir or os.environ['TMP']) / ((prefix or 'tmp') + uuid4().hex + (suffix or ''))
        path.mkdir(parents=True)
        return str(path)

    monkeypatch.setattr(tempfile, 'mkdtemp', mkdtemp)
