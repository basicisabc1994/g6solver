# SPDX-License-Identifier: Apache-2.0
"""``g6.core.storage.contained`` and ``plain_file``: the one check that a path a caller names stays inside a folder.

The archive's adversarial boundary suite (``workspace_manager``'s ``test_boundary_adversarial.py``), rewritten for the
shared helper: '..' at any depth, mixed separators, a symbolic link or a Windows junction out of the root, Unicode
forms and case on a case-insensitive file system; and the cases it did not have: a relative path joined onto the root,
never onto the folder G6 runs in (WORKSPACE_MANAGER-04), network and device paths refused before anything is
resolved, and a hard link, which resolving never shows. Each escape is refused; each path inside is allowed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from g6.core import storage
from g6.core.storage import PathRefused, contained, plain_file


def _junction(link: Path, target: Path) -> bool:
    if sys.platform != "win32":
        return False
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True,
                          check=False)
    return done.returncode == 0


def _symlink(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        return False
    return True


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "ws"
    folder.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("x", encoding="utf-8")
    return folder


@pytest.mark.parametrize("raw", ["../secret.txt", "../../../etc/passwd", "sub/../../secret.txt", "a/b/../../../x"])
def test_dot_dot_out_of_the_root_is_refused_at_any_depth(root: Path, raw: str) -> None:
    with pytest.raises(PathRefused, match="outside"):
        contained(root, raw)


def test_mixed_separators_do_not_hide_a_traversal(root: Path) -> None:
    with pytest.raises(PathRefused, match="outside"):
        contained(root, str(root) + "/sub/../../secret.txt")


@pytest.mark.skipif(sys.platform != "win32", reason="backslash separators are Windows paths")
def test_backslash_traversal_is_refused_on_windows(root: Path) -> None:
    with pytest.raises(PathRefused, match="outside"):
        contained(root, "sub\\..\\..\\secret.txt")


def test_a_symbolic_link_out_of_the_root_counts_as_outside(root: Path) -> None:
    if not _symlink(root / "lnk", root.parent / "outside"):
        pytest.skip("this account may not make symbolic links")
    with pytest.raises(PathRefused, match="outside"):
        contained(root, "lnk/secret.txt")


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows'")
def test_a_junction_out_of_the_root_counts_as_outside(root: Path) -> None:
    if not _junction(root / "jct", root.parent / "outside"):
        pytest.skip("junctions cannot be made here")
    with pytest.raises(PathRefused, match="outside"):
        contained(root, "jct/secret.txt")
    with pytest.raises(PathRefused, match="outside"):
        contained(root, "jct/new.txt")  # a file not yet there, under the junction


@pytest.mark.parametrize("name", ["café.txt", unicodedata.normalize("NFD", "café.txt")])
def test_unicode_names_inside_are_allowed_in_either_form(root: Path, name: str) -> None:
    assert contained(root, name).parent == root.resolve()


def test_a_unicode_name_does_not_smuggle_a_traversal(root: Path) -> None:
    with pytest.raises(PathRefused, match="outside"):
        contained(root, "../café-secret.txt")


@pytest.mark.skipif(sys.platform != "win32", reason="the case-insensitive file system here is Windows'")
def test_a_root_spelled_in_another_case_is_the_same_folder(root: Path) -> None:
    assert contained(root, str(root).upper() + "\\file.txt").parent == root.resolve()


def test_a_folder_beside_the_root_in_another_case_is_still_outside(root: Path) -> None:
    with pytest.raises(PathRefused, match="outside"):
        contained(root, str(root.parent / "OUTSIDE" / "file.txt"))


def test_a_relative_path_is_joined_onto_the_root_not_the_working_folder(root: Path, tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    # WORKSPACE_MANAGER-04: the archive resolved Path(target) against the process's working folder
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert contained(root, "notes/a.txt") == root.resolve() / "notes" / "a.txt"


def test_an_absolute_path_is_allowed_only_inside(root: Path) -> None:
    assert contained(root, str(root / "a.txt")) == root.resolve() / "a.txt"
    assert contained(root, ".") == root.resolve()
    with pytest.raises(PathRefused, match="outside"):
        contained(root, str(root.parent / "outside" / "secret.txt"))


@pytest.mark.parametrize("raw", ["\\\\attacker.example\\share\\x.txt", "//attacker.example/share/x.txt",
                                 "\\\\?\\C:\\Windows\\x.txt", "\\\\.\\PhysicalDrive0"])
def test_a_network_or_device_path_is_refused_before_anything_is_resolved(root: Path, raw: str,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    # resolving \\host\share on Windows opens it, which reaches the host over SMB (and offers it a login)
    def never(self: Path, strict: bool = False) -> Path:
        raise AssertionError("resolved a network or device path")

    monkeypatch.setattr(Path, "resolve", never)
    with pytest.raises(PathRefused, match="network or device"):
        contained(root, raw)


@pytest.mark.parametrize("raw", ["", "  ", "a\x00b.txt", "a\nb.txt", "C:secret.txt", "notes.txt:hidden", "CON",
                                 "sub/nul.txt", "LPT1.log", "aux"])
def test_empty_control_drive_stream_and_device_names_are_refused(root: Path, raw: str) -> None:
    with pytest.raises(PathRefused):
        contained(root, raw)


def test_a_plain_file_is_one_kept_under_one_name(root: Path) -> None:
    (root / "a.txt").write_text("a", encoding="utf-8")
    assert plain_file(root / "a.txt").st_size == 1
    with pytest.raises(PathRefused, match="not a file"):
        plain_file(root / "missing.txt")
    with pytest.raises(PathRefused, match="not a regular file"):
        plain_file(root)


def test_a_hard_link_to_a_file_elsewhere_is_not_a_plain_file(root: Path) -> None:
    # resolving a hard link gives its own name, so containment alone never sees that it is a file kept elsewhere
    try:
        os.link(root.parent / "outside" / "secret.txt", root / "looks-local.txt")
    except OSError:
        pytest.skip("hard links cannot be made here")
    assert contained(root, "looks-local.txt") == root.resolve() / "looks-local.txt"
    with pytest.raises(PathRefused, match="link"):
        plain_file(root / "looks-local.txt")


def test_a_symbolic_link_is_not_a_plain_file(root: Path) -> None:
    (root / "a.txt").write_text("a", encoding="utf-8")
    if not _symlink(root / "b.txt", root / "a.txt"):
        pytest.skip("this account may not make symbolic links")
    with pytest.raises(PathRefused, match="link"):
        plain_file(root / "b.txt")


def test_state_path_still_refuses_an_escape_from_the_g6_home() -> None:
    with pytest.raises(ValueError, match="escapes"):
        storage.state_path("..", "elsewhere")
