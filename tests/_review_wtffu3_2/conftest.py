from pathlib import Path
from uuid import uuid4

import pytest

collect_ignore = ['disk', 'scratch']


@pytest.fixture
def tmp_path(request):
    # As in review 1: a real, precreated basetemp and ordinary new directories
    # (pytest's own tmp_path is made with mode=0o700). No chmod or ACL change.
    root = Path(request.config.option.basetemp)
    assert root.is_dir()
    child = root / uuid4().hex
    child.mkdir()
    return child


@pytest.fixture(autouse=True)
def exact_no_host_paths(monkeypatch, tmp_path):
    for name in ('G6_CLAUDE', 'G6_CODEX'):
        monkeypatch.setenv(name, r'C:\nonexistent\g6-no-host.exe')
    monkeypatch.delenv('G6_ALLOW_UNCONFINED', raising=False)
    # Every test here gets a home of its own on the real disk, beside its folder: imported
    # tests list their tmp_path, so nothing of this conftest may sit inside it.
    home = tmp_path.parent / ('home-' + tmp_path.name)
    home.mkdir()
    monkeypatch.setenv('G6_HOME', str(home))
