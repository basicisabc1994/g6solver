# SPDX-License-Identifier: Apache-2.0
"""Dot-dot classification follows the host's path semantics without cancelling links."""
import os

import pytest

from g6.code import _roots
from g6.code._types import CodeRefused


@pytest.mark.parametrize("sibling", [False, True])
def test_normalised_inside_path_is_walked(tmp_path, sibling):
    root = tmp_path / "root"
    root.mkdir()
    (root / "file").write_bytes(b"inside")
    (tmp_path / "sibling").mkdir()
    path = (tmp_path / "sibling/../root/file" if sibling else root / "../root/file")
    assert _roots.path_info(path, roots=_roots.Roots((root,), "test")).st_size == 6


@pytest.mark.skipif(os.name == "nt", reason="POSIX link/.. follows the physical parent")
def test_posix_normalisation_cannot_cancel_a_link(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "file").write_bytes(b"inside")
    link = root / "link"
    link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(CodeRefused) as caught:
            _roots.path_info(link / "../file", roots=_roots.Roots((root,), "test"))
        assert caught.value.code == _roots.PATH_INVALID
    finally:
        link.unlink()


def test_normalised_outside_path_has_outside_code(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(CodeRefused) as caught:
        _roots.path_info(root / "../outside", roots=_roots.Roots((root,), "test"))
    assert caught.value.code == _roots.OUTSIDE_ROOT
