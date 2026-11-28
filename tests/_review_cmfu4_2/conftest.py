import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
os.environ['G6_HOME'] = str(ROOT / '.bt_rev_cmfu4_2' / 'g6-home')


@pytest.fixture(autouse=True)
def disk_permissions(monkeypatch):
    original = os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        return original(path, 0o777 if os.name == 'nt' and mode == 0o700 else mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, 'mkdir', mkdir)
    for name in ('G6_CLAUDE', 'G6_CODEX'):
        monkeypatch.setenv(name, r'C:\nonexistent\g6-no-host.exe')
    for name in ('G6_ALLOW_UNCONFINED', 'G6_REPO_PYTHON', 'G6_REPO_ENV'):
        monkeypatch.delenv(name, raising=False)
