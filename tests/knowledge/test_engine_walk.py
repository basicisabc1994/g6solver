# SPDX-License-Identifier: Apache-2.0
"""The walker excludes before it reads: credentials, ignored files, links, placeholders, oversized and unsupported
files are listed with why and never opened; what was enumerated and what was read are counted apart."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from g6._context_engine import _walk
from g6._context_engine._walk import MAX_FILE_BYTES, walk
from tests.knowledge.corpus.rig import link_out, make


def _spy(monkeypatch) -> list[str]:
    opened: list[str] = []
    real = _walk.read_file

    def recording(path, limit, expected=None):
        opened.append(os.path.basename(path))
        return real(path, limit, expected)

    monkeypatch.setattr(_walk, "read_file", recording)
    return opened


def _why(walked) -> dict[str, str]:
    return {e.relative: e.why for e in walked.excluded}


def test_credentials_and_ignored_files_are_excluded_with_reasons(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    (rig.folder / ".ssh").mkdir()
    (rig.folder / ".ssh" / "config").write_text("Host planted\n", encoding="utf-8")
    (rig.folder / "credentials.json").write_text("{}", encoding="utf-8")
    opened = _spy(monkeypatch)
    # git's answer is doubled here so the case does not depend on a git program; the real one is the next test
    monkeypatch.setattr(_walk, "tracked", lambda binding, root: (frozenset(
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and "scratch" not in p.parts), ""))
    walked = walk(rig.binding, rig.folder)
    why = _why(walked)
    assert why[".env"] == "credential" and why["key.pem"] == "credential" and why["credentials.json"] == "credential"
    assert why[".ssh/"] == "credential"
    assert why["scratch/ignored.md"] == "ignored" and walked.ignored_by == "git" and walked.note == ""
    assert not {".env", "key.pem", "credentials.json", "config", "ignored.md"} & set(opened)
    assert [f.relative for f in walked.files] == sorted(f for f in rig.planted["indexable"] if f != "scratch/ignored.md")
    assert set(opened) == {os.path.basename(f.relative) for f in walked.files}


def test_git_ignored_files_come_from_git_itself(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("not reached: no git program on this machine")
    rig = make(tmp_path, monkeypatch)
    made = subprocess.run(["git", "init", "-q", str(rig.project)], capture_output=True, check=False)
    assert made.returncode == 0, made.stderr
    walked = walk(rig.binding, rig.folder)
    if walked.ignored_by != "git":
        pytest.skip(f"not reached: git did not answer through ctx_git here ({walked.note})")
    why = _why(walked)
    assert why["scratch/ignored.md"] == "ignored"
    assert "docs/quokka.md" in {f.relative for f in walked.files}


def test_size_and_count_caps_apply_before_any_read(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    opened = _spy(monkeypatch)
    walked = walk(rig.binding, rig.folder)
    assert _why(walked)["big.txt"] == "too_large" and "big.txt" not in opened
    assert (rig.folder / "big.txt").stat().st_size > MAX_FILE_BYTES
    opened.clear()
    capped = walk(rig.binding, rig.folder, max_files=3)
    assert len(capped.files) == 3 and len(opened) == 3
    over = [e.relative for e in capped.excluded if e.why == "over_file_limit"]
    assert len(over) == len(walked.files) - 3 and not {os.path.basename(p) for p in over} & set(opened)


def test_enumerated_and_read_are_reported_separately(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    walked = walk(rig.binding, rig.folder)
    assert walked.enumerated > walked.read == len(walked.files)
    assert walked.read_bytes == sum((rig.folder / f.relative).stat().st_size for f in walked.files)
    assert walked.read_bytes == sum(f.size for f in walked.files)
    assert _why(walked)["blob.bin"] == "unsupported"


@pytest.mark.skipif(sys.platform != "win32", reason="not reached: cloud placeholder attributes are a Windows fact")
def test_placeholder_file_is_never_opened(tmp_path, monkeypatch):
    import ctypes

    rig = make(tmp_path, monkeypatch)
    target = rig.folder / "docs" / "quokka.md"
    offline = 0x1000
    assert ctypes.windll.kernel32.SetFileAttributesW(str(target), offline), "the offline attribute could not be set"
    opened = _spy(monkeypatch)
    walked = walk(rig.binding, rig.folder)
    assert _why(walked)["docs/quokka.md"] == "placeholder" and "quokka.md" not in opened
    assert "docs/quokka.md" not in {f.relative for f in walked.files}


def test_link_inside_root_pointing_out_is_refused(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret-notes.md").write_text("# outside the root\n", encoding="utf-8")
    if not link_out(rig.folder / "linked", outside):
        pytest.skip("not reached: this account may not make a folder link")
    opened = _spy(monkeypatch)
    walked = walk(rig.binding, rig.folder)
    assert _why(walked)["linked"] == "link" and "secret-notes.md" not in opened
    assert not any(f.relative.startswith("linked") for f in walked.files)
    hard = rig.folder / "docs" / "hard.md"
    try:
        os.link(outside / "secret-notes.md", hard)
    except OSError:
        return
    assert _why(walk(rig.binding, rig.folder))["docs/hard.md"] == "link"


def test_without_git_the_walk_says_ignored_files_were_not_excluded(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    walked = walk(rig.binding, rig.folder)   # the scratch project is no git repository
    assert walked.ignored_by == "builtin" and "ignored files were not excluded" in walked.note
    assert "scratch/ignored.md" in {f.relative for f in walked.files}
    (rig.folder / "node_modules" / "pkg").mkdir(parents=True)
    (rig.folder / "node_modules" / "pkg" / "readme.md").write_text("# vendored\n", encoding="utf-8")
    assert _why(walk(rig.binding, rig.folder))["node_modules/"] == "ignored"
