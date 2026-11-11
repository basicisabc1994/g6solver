from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path():
    path = Path(__file__).resolve().parents[2] / '.review' / 'tmp' / uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def isolated_review(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('FASTMCP_CHECK_FOR_UPDATES', 'off')
    from g6.core import keys
    # A review fake must never consult a credential or sign-in file.
    monkeypatch.setattr(keys, 'get', lambda *args, **kwargs: None)
