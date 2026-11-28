# SPDX-License-Identifier: Apache-2.0
"""Git metadata uses captured roots, no-follow checks and the capped content reader."""
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.code import _roots
from g6.code._types import CodeRefused
from g6.sources.ctx_git import config

HERE = Path(__file__).resolve().parent


@pytest.fixture
def metadata(monkeypatch):
    """Synthetic metadata, with a real checked root; no links, large files or git subprocesses."""
    repo = HERE / "repair3-repo"
    git = repo / ".git"
    modes = {repo: stat.S_IFDIR, git: stat.S_IFDIR, git / "objects": stat.S_IFDIR,
             git / "objects" / "info": stat.S_IFDIR, git / "refs": stat.S_IFDIR}
    texts, followed, reads, sizes, links = {}, [], [], {}, {}
    original = os.lstat
    reader = _roots.read_text

    def lstat(path, *args, **kwargs):
        path = Path(path)
        if path in modes:
            mode = modes[path]
            if isinstance(mode, Exception):
                raise mode
            return SimpleNamespace(st_mode=mode, st_dev=1, st_ino=2, st_nlink=1,
                                   st_file_attributes=0, st_size=10)
        if path in texts:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_dev=1, st_ino=3, st_nlink=links.get(path, 1),
                                   st_file_attributes=0, st_size=sizes.get(path, 10))
        if path == repo or repo in path.parents:
            raise FileNotFoundError
        return original(path, *args, **kwargs)

    def following(path, kind):
        followed.append(path)
        if isinstance(modes.get(path), Exception):
            raise modes[path]
        return path in texts if kind == "file" else modes.get(path) == stat.S_IFDIR if kind == "dir" else (
            path in modes or path in texts)

    def read(path, cap, *, roots):
        reads.append((path, cap, roots))
        value = texts[path]
        if isinstance(value, Exception):
            raise value
        return value

    def plain_read(path, **kwargs):
        value = texts[path]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(config, "_confined", lambda p, *a: Path(os.path.normpath(p)))
    monkeypatch.setattr(Path, "exists", lambda p: following(p, "any"))
    monkeypatch.setattr(Path, "is_file", lambda p: following(p, "file"))
    monkeypatch.setattr(Path, "is_dir", lambda p: following(p, "dir"))
    monkeypatch.setattr(Path, "read_text", plain_read)
    monkeypatch.setattr(_roots, "read_text", read)
    return SimpleNamespace(repo=repo, git=git, modes=modes, texts=texts, followed=followed, reads=reads,
                           sizes=sizes, links=links, reader=reader, cfg=config.GitConfig((HERE,), (), "test"))


def test_all_git_metadata_reads_share_checked_roots_and_small_cap(metadata):
    m = metadata
    m.modes.pop(m.git)
    target = m.repo / "git-folder"
    m.modes[target] = stat.S_IFDIR
    m.texts[m.git] = "gitdir: git-folder"
    m.texts[target / "commondir"] = "."
    m.modes[target / "objects"] = stat.S_IFDIR
    m.modes[target / "objects" / "info"] = stat.S_IFDIR
    m.texts[target / "objects" / "info" / "alternates"] = "# none\n"
    assert config.repository(str(m.repo), m.cfg) == (m.repo, target)
    assert len(m.reads) == 3, "metadata bypassed the shared reader"
    assert all(cap == 64 * 1024 and roots is m.reads[0][2] for _, cap, roots in m.reads)
    assert not m.followed, "metadata used following existence/type checks"


@pytest.mark.parametrize("error", [PermissionError, ValueError, MemoryError])
@pytest.mark.parametrize("where", [".git", ".git/commondir", ".git/objects/info"])
def test_metadata_check_failures_are_relative_refusals(metadata, where, error):
    m = metadata
    m.modes[m.repo / where] = error("hidden-system-detail")
    with pytest.raises(config.GitRefused) as caught:
        config.repository(str(m.repo), m.cfg)
    assert caught.value.code == config.PATH_INVALID
    assert "hidden-system-detail" not in caught.value.reason
    assert str(HERE) not in caught.value.reason


@pytest.mark.parametrize("where", [".git", ".git/commondir", ".git/objects/info/alternates"])
def test_metadata_reader_resource_failure_is_a_git_refusal(metadata, where):
    m = metadata
    if where == ".git":
        m.modes.pop(m.git)
    m.texts[m.repo / where] = MemoryError("hidden-system-detail")
    with pytest.raises(config.GitRefused) as caught:
        config.repository(str(m.repo), m.cfg)
    assert caught.value.code == config.PATH_INVALID
    assert str(HERE) not in caught.value.reason


@pytest.mark.parametrize("where", ["commondir", "alternates"])
def test_metadata_targets_outside_the_checked_root_are_refused_before_read(metadata, where):
    m = metadata
    path = m.git / "commondir" if where == "commondir" else m.git / "objects" / "info" / "alternates"
    outside = HERE.parent / "repair3-outside"
    m.texts[path] = str(outside)
    # Simulate what the old unconfined reader would see if it follows the target.
    m.modes[outside] = stat.S_IFDIR
    m.texts[outside / "commondir"] = "."
    m.texts[outside / "info" / "alternates"] = ""
    with pytest.raises(config.GitRefused) as caught:
        config.repository(str(m.repo), m.cfg)
    assert caught.value.code == config.OUTSIDE_ROOT
    assert all(HERE == p or HERE in p.parents for p, _, _ in m.reads)


def test_control_plain_repository_needs_no_metadata_read(metadata):
    m = metadata
    assert config.repository(str(m.repo), m.cfg) == (m.repo, m.git)
    assert not m.reads


def test_control_shared_reader_refusal_keeps_outside_class(metadata):
    m = metadata
    m.texts[m.git / "commondir"] = CodeRefused(_roots.OUTSIDE_ROOT, "outside")
    with pytest.raises(config.GitRefused) as caught:
        config.repository(str(m.repo), m.cfg)
    assert caught.value.code == config.OUTSIDE_ROOT


@pytest.mark.parametrize("where", [".git", ".git/commondir", ".git/objects/info/alternates"])
@pytest.mark.parametrize("hazard", ["size", "hard_link"])
def test_metadata_size_and_link_checks_precede_the_content_open(metadata, where, hazard, monkeypatch):
    """Exercise the real capped reader with synthetic sizes; allocate no large buffer or disk file."""
    import builtins

    m = metadata
    target = m.repo / where
    m.modes.pop(target, None)
    m.texts[target] = "gitdir: git-folder" if where == ".git" else "# none\n" if where.endswith("alternates") else "."
    m.modes[m.repo / "git-folder"] = stat.S_IFDIR
    if hazard == "size":
        m.sizes[target] = 1 << 40
    else:
        m.links[target] = 2
    monkeypatch.setattr(_roots, "read_text", m.reader)
    original = builtins.open

    def opening(path, *args, **kwargs):
        if Path(path) == target:
            pytest.fail("metadata reached the content open before its cap or link check")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", opening)
    with pytest.raises(config.GitRefused) as caught:
        config.repository(str(m.repo), m.cfg)
    assert caught.value.code == config.PATH_INVALID
