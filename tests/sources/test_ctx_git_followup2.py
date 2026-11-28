# SPDX-License-Identifier: Apache-2.0
"""Metadata spellings and alternate graphs must be admitted before git can read them."""
import ast
import os
from pathlib import Path

import pytest

from g6.sources.ctx_git import config as cfg


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "root"
    repo = root / "repo"
    for name in ("objects/info", "refs"):
        (repo / ".git" / name).mkdir(parents=True)
    return root, repo, cfg.GitConfig((root,), (), "test")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def plant(repo, kind, entry):
    if kind == "gitdir":
        (repo / ".git").rename(repo / "metadata")
        write(repo / ".git", "gitdir: " + entry + "\n")
    elif kind == "commondir":
        write(repo / ".git" / kind, entry + "\n")
    else:
        write(repo / ".git/objects/info/alternates", entry + "\n")


@pytest.mark.parametrize("kind", ["gitdir", "commondir", "alternates"])
@pytest.mark.parametrize("entry", ["C:outside/objects", r"\outside", "/outside", r"\\server\share",
                                   r"\\?\C:\outside", r"\\.\NUL", '"objects"', "objects\x00bad"])
def test_ambiguous_entries_are_refused_before_target_io(layout, kind, entry, monkeypatch):
    if entry == "/outside" and os.name != "nt":
        pytest.skip("a POSIX absolute entry is unambiguous and receives the normal root check")
    root, repo, config = layout
    plant(repo, kind, entry)
    original = cfg._metadata_target

    def target(path, name, roots):
        assert Path(path) in (repo, repo / ".git"), "ambiguous metadata reached target I/O"
        return original(path, name, roots)

    monkeypatch.setattr(cfg, "_metadata_target", target)
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code in (cfg.OUTSIDE_ROOT, cfg.PATH_INVALID)
    assert str(root) not in caught.value.reason


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-root interpretation")
@pytest.mark.parametrize("kind", ["gitdir", "commondir", "alternates"])
def test_drive_relative_outside_entry_has_outside_code(layout, kind):
    root, repo, config = layout
    outside = str(root.parent / "outside")
    plant(repo, kind, outside[:2] + outside[3:])
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.OUTSIDE_ROOT


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-root interpretation")
@pytest.mark.parametrize("kind", ["gitdir", "commondir", "alternates"])
def test_drive_relative_inside_entry_still_has_invalid_spelling(layout, kind):
    _, repo, config = layout
    inside = str(repo / "metadata")
    plant(repo, kind, inside[:2] + inside[3:])
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID


@pytest.mark.parametrize("kind", ["gitdir", "commondir", "alternates"])
@pytest.mark.parametrize("absolute", [False, True])
def test_control_plain_relative_and_checked_absolute_entries_work(layout, kind, absolute):
    _, repo, config = layout
    target = repo / "metadata" if kind == "gitdir" else repo / ".git"
    if kind == "alternates":
        target /= "objects"
    entry = str(target) if absolute else "metadata" if kind == "gitdir" else "."
    plant(repo, kind, entry)
    assert cfg.repository(str(repo), config)[0] == repo


def test_mutual_alternate_loop_is_admitted_once_per_folder(layout, monkeypatch):
    root, repo, config = layout
    a, b = repo / ".git/objects", root / "borrowed"
    write(a / "info/alternates", str(b) + "\n")
    write(b / "info/alternates", str(a) + "\n")
    original = cfg._metadata_text
    reads = []

    def read(path, name, roots):
        if name == "objects/info/alternates":
            reads.append(path)
        return original(path, name, roots)

    monkeypatch.setattr(cfg, "_metadata_text", read)
    assert cfg.repository(str(repo), config)[0] == repo
    assert reads.count(a / "info/alternates") == reads.count(b / "info/alternates") == 1


@pytest.mark.parametrize("count,admitted", [(63, True), (64, False)])
def test_total_alternate_folder_budget_includes_primary(layout, count, admitted):
    root, repo, config = layout
    borrowed = [root / f"lib{i}" for i in range(count)]
    for folder in borrowed:
        folder.mkdir()
    write(repo / ".git/objects/info/alternates", "".join(str(p) + "\n" for p in borrowed))
    if admitted:
        assert cfg.repository(str(repo), config)[0] == repo
    else:
        with pytest.raises(cfg.GitRefused) as caught:
            cfg.repository(str(repo), config)
        assert caught.value.code == cfg.PATH_INVALID
        assert "64" in caught.value.reason and str(root) not in caught.value.reason


def test_alias_spelling_of_an_alternate_does_not_consume_budget(layout, monkeypatch):
    root, repo, config = layout
    folder = root / "borrowed"
    folder.mkdir()
    (folder / "child").mkdir()
    write(folder / "info/alternates", "# terminal\n")
    spellings = [str(folder), str(folder / "child/..")]
    write(repo / ".git/objects/info/alternates", "\n".join(spellings * 40) + "\n")
    original = cfg._metadata_text
    reads = []

    def read(path, name, roots):
        reads.append(path)
        return original(path, name, roots)

    monkeypatch.setattr(cfg, "_metadata_text", read)
    assert cfg.repository(str(repo), config)[0] == repo
    assert reads.count(folder / "info/alternates") == 1


def test_metadata_entry_reader_census():
    """Every entry-bearing metadata reader uses the shared spelling classifier."""
    tree = ast.parse(Path(cfg.__file__).read_text(encoding="utf-8"))
    readers = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            calls = [call.func.id for call in ast.walk(node)
                     if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)]
            if "_metadata_text" in calls or "_metadata_bytes" in calls:
                readers.add(node.name)
                assert "_metadata_entry" in calls, node.name
    assert readers == {"repository", "_common", "_alternates", "_includes"}
