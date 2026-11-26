from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path(request):
    root = Path(request.config.option.basetemp)
    assert root.is_dir()
    child = root / uuid4().hex
    child.mkdir()
    return child


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('G6_HOME', str(home))
    for name in ('G6_CLAUDE', 'G6_CODEX'):
        monkeypatch.setenv(name, r'C:\nonexistent\g6-no-host.exe')
    for name in ('G6_ALLOW_UNCONFINED', 'G6_MCP_SENSITIVE', 'G6_LLM_PROVIDER', 'G6_LLM_MODEL'):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def key_file(monkeypatch, tmp_path):
    home = tmp_path / 'key-home'
    home.mkdir()
    monkeypatch.setenv('G6_HOME', str(home))

    def write(text):
        (home / '.env').write_text(text, encoding='utf-8')

    write.path = home / '.env'
    return write
