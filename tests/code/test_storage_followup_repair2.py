# SPDX-License-Identifier: Apache-2.0
"""Repair-2 regressions: exact handle paths and read/write parent checks."""
from __future__ import annotations

import builtins
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.code import _roots
from g6.code._types import CodeRefused

HERE = Path(__file__).resolve().parent


def test_handle_path_components_keep_case():
    assert not _roots.under(HERE / "PROJ" / "mod.py", (HERE / "proj",))
    assert _roots.under(HERE / "proj" / "mod.py", (HERE / "proj",))


@pytest.mark.parametrize("kind", ["reparse", "special"])
def test_preopen_walk_refuses_unsafe_parent_without_opening(kind, monkeypatch):
    roots = _roots.for_folder(HERE.parent, "test source")
    target = Path(__file__).resolve()
    real_plain, real_lstat, real_open = _roots.plain_file, os.lstat, builtins.open
    checked = False

    def plain(path):
        nonlocal checked
        result = real_plain(path)
        checked = True
        return result

    def lstat(path, *args, **kwargs):
        if checked and Path(path) == HERE:
            return SimpleNamespace(st_mode=stat.S_IFDIR if kind == "reparse" else stat.S_IFIFO,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT if kind == "reparse" else 0)
        return real_lstat(path, *args, **kwargs)

    def opening(path, *args, **kwargs):
        if checked and Path(path) == target:
            pytest.fail("unsafe parent reached the file open")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(_roots, "plain_file", plain)
    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(builtins, "open", opening)
    with pytest.raises(CodeRefused) as refused:
        _roots.read_text(target, 100_000, roots=roots)
    assert refused.value.code == _roots.OUTSIDE_ROOT


@pytest.mark.parametrize("writer", ["render", "build"])
def test_writer_refuses_replaced_parent_before_second_open(writer, monkeypatch):
    """Simulate changed directory identity, without junctions or writes outside a declared folder."""
    from g6.code import _build
    from g6.code.component_creator import render

    real_lstat, real_open = os.lstat, builtins.open
    calls = 0
    opened = []

    def mkdir(path, *args, **kwargs):
        nonlocal calls
        if path == HERE:
            calls += 1

    def lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if Path(path) == HERE and calls >= 2:
            return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino + 1,
                                   st_file_attributes=getattr(info, "st_file_attributes", 0))
        return info

    class Sink:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, data):
            pass

    def opening(path, mode="r", *args, **kwargs):
        if mode == "xb":
            opened.append(Path(path).name)
            return Sink()
        return real_open(path, mode, *args, **kwargs)

    roots = _roots.for_folder(HERE.parent, "test source")
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(builtins, "open", opening)
    monkeypatch.setattr(Path, "open", lambda path, *a, **k: opening(path, *a, **k))
    monkeypatch.setattr(Path, "read_bytes", lambda _: b"data")
    monkeypatch.setattr(_roots, "destination", lambda *_: HERE)
    with pytest.raises(CodeRefused) as refused:
        if writer == "render":
            render.write(HERE, {"r2-a.txt": "data", "r2-b.txt": "data"})
        else:
            _build.write_files(SimpleNamespace(files={"r2-a.txt": b"data", "r2-b.txt": b"data"}),
                               str(HERE), roots, dry_run=False, component="test")
    assert refused.value.code == _roots.OUTSIDE_ROOT
    assert opened == ["r2-a.txt"]
