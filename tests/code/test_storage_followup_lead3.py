# SPDX-License-Identifier: Apache-2.0
"""Repair 3's codes and metadata checks, as the lead ruled them. A path outside every declared folder is outside
(G6_E_CODE_OUTSIDE_ROOT), even when the declared folders are unavailable; only a path under an unavailable declared
folder is PATH_INVALID. A metadata check reports a hard-linked regular file's facts; only a content read refuses a file
kept under a second name, so a package walk still says which files it skipped as hard links."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from g6.code import _roots
from g6.code._types import CodeRefused


def _missing_roots(tmp_path: Path) -> _roots.Roots:
    return _roots.Roots((tmp_path.resolve() / "not-made-yet",), "test")


@pytest.mark.parametrize("call", ["existing", "destination", "read_text", "path_info"])
def test_a_path_outside_every_declared_folder_is_outside_when_they_are_unavailable(tmp_path: Path, call: str) -> None:
    (tmp_path / "other").mkdir()
    target = tmp_path.resolve() / "other" / "a.py"
    target.write_text("A = 1\n", encoding="utf-8")
    roots = _missing_roots(tmp_path)
    with pytest.raises(CodeRefused) as refused:
        if call == "existing":
            _roots.existing(str(target), roots, kind="file")
        elif call == "destination":
            _roots.destination(str(tmp_path.resolve() / "other" / "new"), roots)
        elif call == "read_text":
            _roots.read_text(target, 1000, roots=roots)
        else:
            _roots.path_info(target, roots=roots)
    assert refused.value.code == _roots.OUTSIDE_ROOT, refused.value


def test_a_path_under_an_unavailable_declared_folder_is_not_outside(tmp_path: Path) -> None:
    with pytest.raises(CodeRefused) as refused:
        _roots.existing(str(tmp_path.resolve() / "not-made-yet" / "a.py"), _missing_roots(tmp_path), kind="file")
    assert refused.value.code == _roots.PATH_INVALID, refused.value


def test_a_metadata_check_reports_a_hard_linked_file_and_a_read_refuses_it(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "root"
    root.mkdir()
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    try:
        os.link(root / "a.py", root / "b.py")
    except OSError:
        pytest.skip("hard links cannot be made here")
    roots = _roots.Roots((root,), "test")
    info = _roots.path_info(root / "b.py", roots=roots)
    assert info is not None and info.st_nlink == 2
    with pytest.raises(CodeRefused):
        _roots.read_text(root / "b.py", 1000, roots=roots)
