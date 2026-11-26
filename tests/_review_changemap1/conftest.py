import os
import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def sandbox_directory_acl(monkeypatch):
    # Python's Windows 0700 ACL excludes the managed sandbox identity. Keep
    # inherited ACLs for temporary directories, without changing G6 logic.
    original = os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        if os.name == "nt" and mode == 0o700:
            mode = 0o777
        return original(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", mkdir)


@pytest.fixture
def tmp_path():
    # Avoid pytest's mode-0700 directory ACL under the managed Windows sandbox.
    root = Path(__file__).resolve().parents[2] / ".bt_rev_changemap1"
    path = root / ("case-" + uuid.uuid4().hex)
    path.mkdir(parents=True)
    return path
