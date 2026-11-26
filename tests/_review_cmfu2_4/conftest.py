import os
import tempfile

import pytest

@pytest.fixture(autouse=True)
def local_environment(monkeypatch, tmp_path_factory):
    folder = tmp_path_factory.mktemp("cmfu2-4-environment")
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    monkeypatch.setenv("G6_HOME", str(folder / "g6-home"))
    for name in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(name, str(folder))
    original = os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        return original(path, 0o777 if os.name == 'nt' and mode == 0o700 else mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, 'mkdir', mkdir)
    for name in ('G6_CLAUDE', 'G6_CODEX'):
        monkeypatch.setenv(name, r'C:\nonexistent\g6-no-host.exe')
    for name in ('G6_REPO_PYTHON', 'G6_REPO_ENV', 'G6_ALLOW_UNCONFINED'):
        monkeypatch.delenv(name, raising=False)
