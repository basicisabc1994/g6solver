# SPDX-License-Identifier: Apache-2.0
"""No-follow metadata checks and accurate unavailable-folder refusal codes."""
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.code import _roots
from g6.code._types import CodeRefused
from g6.code.component_creator import conformance

HERE = Path(__file__).resolve().parent


@pytest.mark.parametrize("error", [PermissionError, ValueError, MemoryError])
def test_package_init_metadata_error_is_a_relative_refusal(error, monkeypatch):
    roots = _roots.for_folder(HERE, "test")
    original = os.lstat

    def lstat(path, *args, **kwargs):
        if Path(path) == HERE / "__init__.py":
            raise error("hidden-system-detail")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)
    # The legacy following check is an error as well; it must never be used.
    def following(*args, **kwargs):
        raise error("hidden-system-detail")

    monkeypatch.setattr(Path, "is_file", following)
    with pytest.raises(CodeRefused) as caught:
        conformance.check_folder(str(HERE), roots)
    assert caught.value.code == _roots.PATH_INVALID
    assert "hidden-system-detail" not in caught.value.reason
    assert str(HERE) not in caught.value.reason


def test_unavailable_declared_folder_is_path_invalid():
    roots = _roots.Roots((HERE / "repair3-absent",), "test")
    with pytest.raises(CodeRefused) as caught:
        roots.refuse_if_empty("read")
    assert caught.value.code == _roots.PATH_INVALID


@pytest.mark.parametrize("entry", ["existing", "destination", "read_text", "path_info"])
def test_missing_declared_folder_beside_a_usable_root_is_path_invalid(entry):
    missing = HERE.parent / "repair3-missing-folder"
    roots = _roots.Roots((HERE, missing), "test")
    target = missing / "a.py"
    with pytest.raises(CodeRefused) as caught:
        if entry == "read_text":
            _roots.read_text(target, 100, roots=roots)
        elif entry == "path_info":
            _roots.path_info(target, roots=roots)
        else:
            getattr(_roots, entry)(str(target), roots)
    assert caught.value.code == _roots.PATH_INVALID


@pytest.mark.parametrize("mode,attrs", [(stat.S_IFDIR, stat.FILE_ATTRIBUTE_REPARSE_POINT), (stat.S_IFIFO, 0)])
def test_metadata_parent_is_refused_before_querying_a_child(mode, attrs, monkeypatch):
    roots = _roots.for_folder(HERE.parent, "test")
    original = os.lstat

    def lstat(path, *args, **kwargs):
        if Path(path) == HERE:
            return SimpleNamespace(st_mode=mode, st_file_attributes=attrs)
        if Path(path) == HERE / "metadata":
            pytest.fail("unsafe parent was traversed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(CodeRefused) as caught:
        _roots.path_info(HERE / "metadata", roots=roots)
    assert caught.value.code == _roots.PATH_INVALID


def test_metadata_root_case_variant_must_name_the_captured_folder(monkeypatch):
    """Simulate a different folder at a case variant, without enabling per-folder flags."""
    roots = _roots.for_folder(HERE, "test")
    variant = Path(str(HERE).upper())
    original = os.lstat

    def lstat(path, *args, **kwargs):
        if Path(path).parts == variant.parts:
            info = original(HERE)
            return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino + 1,
                                   st_file_attributes=0)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(CodeRefused) as caught:
        _roots.path_info(variant / Path(__file__).name, roots=roots)
    assert caught.value.code == _roots.OUTSIDE_ROOT
