import os
from pathlib import Path
from uuid import uuid4

import pytest

os.environ['FASTMCP_CHECK_FOR_UPDATES'] = 'off'
os.environ['FASTMCP_ENV_FILE'] = os.devnull


@pytest.fixture
def tmp_path():
    path = Path(__file__).resolve().parents[2] / '.review' / 'tmp' / uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def isolate_review(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    from g6.core import keys
    monkeypatch.setattr(keys, 'get', lambda *a, **kw: None)
