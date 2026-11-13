"""Review-only fakes: no provider programs or credential reads."""
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core import host_models


@pytest.fixture
def review_dir():
    root = Path.cwd() / ".review" / "tmp" / uuid4().hex
    root.mkdir(parents=True)
    return root


@pytest.fixture
def fake_host(monkeypatch, review_dir):
    tmp_path = review_dir
    @contextmanager
    def folders():
        root = tmp_path / uuid4().hex
        work, files = root / "work", root / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    for cls in (host_models.ClaudeCodeCaller, host_models.CodexCaller):
        monkeypatch.setattr(cls, "locate", lambda self: "fake-host")

    def forbidden(*args, **kwargs):
        pytest.fail("the review must supply a fake launcher")

    monkeypatch.setattr(host_models, "_run", forbidden)
