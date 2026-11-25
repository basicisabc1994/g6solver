from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path(request):
    # Windows denies access to directories pytest creates with mode=0o700.
    # Use a real, precreated basetemp and ordinary new directories; no chmod/ACL change.
    root = Path(request.config.option.basetemp)
    assert root.is_dir()
    child = root / uuid4().hex
    child.mkdir()
    return child


@pytest.fixture(autouse=True)
def exact_no_host_paths(monkeypatch):
    monkeypatch.setenv('G6_CLAUDE', r'C:\nonexistent\g6-no-host.exe')
    monkeypatch.setenv('G6_CODEX', r'C:\nonexistent\g6-no-host.exe')
