# SPDX-License-Identifier: Apache-2.0
"""Handle checks reject changed roots and uncontained opens before reading."""
from __future__ import annotations

import builtins
import os
import stat
from pathlib import Path

import pytest

from g6.code import _roots
from g6.code._types import CodeRefused

HERE = Path(__file__).resolve().parent


@pytest.mark.parametrize("error", [RecursionError, ValueError, TypeError, MemoryError])
def test_json_resource_errors_are_code_refusals(error, monkeypatch):
    def fail(_):
        raise error("private-system-details")

    monkeypatch.setattr(_roots, "_decode_json", fail)
    with pytest.raises(CodeRefused) as refused:
        _roots.parse("{}", "G6_CODE_CONFIG")
    assert refused.value.code == _roots.CONFIG_INVALID
    assert "private-system-details" not in refused.value.reason


def test_root_identity_change_refuses_read_existing_and_walk(monkeypatch):
    roots = _roots.for_folder(HERE, "test source folder")
    fstat = os.fstat

    def changed(fd):
        info = fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            values = list(info)
            values[1] += 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr(os, "fstat", changed)
    for call in (lambda: _roots.read_text(Path(__file__), 100_000, roots=roots),
                 lambda: _roots.existing(__file__, roots, kind="file"),
                 lambda: _roots.destination(str(HERE / "new-folder"), roots)):
        with pytest.raises(CodeRefused) as refused:
            call()
        assert refused.value.code == _roots.OUTSIDE_ROOT
    walked = _roots.walk(HERE, roots, max_files=100, max_file_bytes=100_000)
    assert not walked.files


def test_open_handle_outside_is_refused_before_content_is_read(monkeypatch):
    roots = _roots.for_folder(HERE, "test source folder")
    target = HERE / "test_storage_followup_roots.py"
    other = HERE.parent / "sources" / "test_ctx_git_storage_followup.py"
    original = builtins.open

    class Opened:
        def __enter__(self):
            self.handle = original(other, "rb")
            return self

        def __exit__(self, *args):
            self.handle.close()

        def fileno(self):
            return self.handle.fileno()

        def read(self, *_):
            pytest.fail("outside handle content was read")

    def swapped(path, *args, **kwargs):
        return Opened() if Path(path) == target else original(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", swapped)
    with pytest.raises(CodeRefused) as refused:
        _roots.read_text(target, 100_000, roots=roots)
    assert refused.value.code == _roots.OUTSIDE_ROOT


def test_control_real_handle_inside_is_read():
    roots = _roots.for_folder(HERE, "test source folder")
    text = _roots.read_text(HERE / "test_storage_followup_roots.py", 100_000, roots=roots)
    assert "def test_read_requires_declared_roots():" in text


@pytest.mark.parametrize("method,call", [
    ("is_file", lambda roots: _roots.existing(__file__, roots, kind="file")),
    ("is_dir", lambda roots: _roots.existing(str(HERE), roots, kind="folder")),
    ("exists", lambda roots: _roots.existing(__file__, roots)),
    ("iterdir", lambda roots: _roots.destination(str(HERE), roots)),
])
def test_metadata_denials_are_relative_g6_refusals(method, call, monkeypatch):
    roots = _roots.for_folder(HERE.parent, "test source folder")

    def fail(*_):
        raise PermissionError("private-system-details")

    monkeypatch.setattr(Path, method, fail)
    with pytest.raises(CodeRefused) as refused:
        call(roots)
    assert refused.value.code == _roots.PATH_INVALID
    assert str(HERE.parent).lower() not in refused.value.reason.lower()
    assert "private-system-details" not in refused.value.reason


