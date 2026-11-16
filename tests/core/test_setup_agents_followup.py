# SPDX-License-Identifier: Apache-2.0
"""Fail closed on Markdown containers; cleanup must not chmod somebody else's file."""
from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from g6.cli import setup
from g6.mcp.instructions import AGENTS_BEGIN, AGENTS_END, agents_block


@pytest.mark.parametrize("indent", [" ", "   ", "    ", "\t"])
@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
def test_indented_markers_are_preserved_when_appending(indent, newline):
    example = newline.join([indent + AGENTS_BEGIN, indent + "old example", indent + AGENTS_END, ""])
    data = example.encode()
    result = setup._agents_update(data, agents_block())
    assert result.startswith(data)
    assert result.count(AGENTS_BEGIN.encode()) == 2


@pytest.mark.parametrize("shape", [
    "- ```md", "+ ~~~", "* ```", "1. ```", "12) ~~~", "> ```", " > ~~~", "\t```", "    ~~~",
    "- Example:\n\n  ```md", "1. Example:\n\n   ~~~", "- Outer:\n  > ```md",
])
@pytest.mark.parametrize("position", ["append", "before_block", "inside_block"])
def test_uncertain_fence_refuses_any_write(shape, position):
    block = f"{AGENTS_BEGIN}\nstale\n{AGENTS_END}\n"
    data = {"append": shape + "\n", "before_block": shape + "\n" + block,
            "inside_block": f"{AGENTS_BEGIN}\n{shape}\n{AGENTS_END}\n"}[position].encode()
    with pytest.raises(setup._LeftAlone, match="code.*shape|uncertain"):
        setup._agents_update(data, agents_block())


def test_uncertain_shape_after_the_block_is_outside_the_edit():
    tail = b"\n\n- ```md\n  my example\n"
    data = f"{AGENTS_BEGIN}\nstale\n{AGENTS_END}".encode() + tail
    assert setup._agents_update(data, agents_block()) == agents_block().encode() + tail


def test_marker_suffix_whitespace_and_initial_bom_are_allowed():
    data = f"\ufeff{AGENTS_BEGIN} \t\rold\r{AGENTS_END}\t\r".encode()
    assert setup._agents_update(data, agents_block()) == b"\xef\xbb\xbf" + agents_block().encode().replace(b"\n", b"\r") + b"\r"


@pytest.mark.parametrize("mode,links,inode,expected_chmod", [
    (stat.S_IFREG, 1, 42, True), (stat.S_IFREG, 2, 42, False), (stat.S_IFREG, 1, 43, False),
    (stat.S_IFLNK, 1, 42, False), (stat.S_IFDIR, 1, 42, False),
])
def test_cleanup_chmods_only_the_recorded_single_link_plain_file(monkeypatch, mode, links, inode, expected_chmod):
    # Deterministic name-swap at the OS boundary; no actual link or permission change.
    status = SimpleNamespace(st_mode=mode, st_nlink=links, st_dev=7, st_ino=inode)
    chmods, unlinks = [], []
    monkeypatch.setattr(os, "lstat", lambda name: status)
    monkeypatch.setattr(os, "chmod", lambda *args: chmods.append(args))
    monkeypatch.setattr(os, "unlink", lambda name: unlinks.append(name))
    setup._remove("temporary-name", lambda message: None, identity=(7, 42))
    assert bool(chmods) is expected_chmod
    assert unlinks == ["temporary-name"]


def test_cleanup_without_identity_never_chmods(monkeypatch):
    chmods = []
    monkeypatch.setattr(os, "chmod", lambda *args: chmods.append(args))
    monkeypatch.setattr(os, "unlink", lambda name: None)
    setup._remove("temporary-name", lambda message: None)
    assert not chmods


def test_cleanup_reparse_file_is_not_chmodded(monkeypatch):
    status = SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1, st_dev=7, st_ino=42,
                             st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
    chmods, unlinks = [], []
    monkeypatch.setattr(os, "lstat", lambda name: status)
    monkeypatch.setattr(os, "chmod", lambda *args: chmods.append(args))
    monkeypatch.setattr(os, "unlink", lambda name: unlinks.append(name))
    setup._remove("temporary-name", lambda message: None, identity=(7, 42))
    assert not chmods
    assert unlinks == ["temporary-name"]


def test_cleanup_lstat_failure_still_tries_unlink_and_names_a_failure(monkeypatch):
    def denied(*args):
        raise PermissionError("synthetic refusal")

    monkeypatch.setattr(os, "lstat", denied)
    monkeypatch.setattr(os, "unlink", denied)
    said = []
    setup._remove("temporary-name", said.append, identity=(7, 42))
    assert len(said) == 1 and "could not remove" in said[0]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode tightening")
def test_mode_tightened_after_copymode_refuses_the_replace(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    target = tmp_path / "AGENTS.md"
    target.write_bytes(b"# Mine\n")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    real = shutil.copymode

    def tighten(src, dst):
        real(src, dst)
        Path(src).chmod(0o600)

    target.chmod(0o644)
    monkeypatch.setattr(shutil, "copymode", tighten)
    setup.codex_agents(lambda prompt: "y", lambda text: None)
    assert target.read_bytes() == b"# Mine\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
