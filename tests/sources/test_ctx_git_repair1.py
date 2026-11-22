# SPDX-License-Identifier: Apache-2.0
"""Repository admission must bound and check everything git's config can include."""
import os

import pytest

from g6.sources.ctx_git import config as cfg


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git/objects/info").mkdir(parents=True)
    (repo / ".git/refs").mkdir()
    return root, repo, cfg.GitConfig((root,), (), "test")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


@pytest.mark.parametrize("section", ['include', 'InClUdE', 'includeIf "gitdir:never/"',
                                     'includeIf.never'])
@pytest.mark.parametrize("nested", [False, True])
def test_config_includes_are_confined_even_when_conditional(layout, section, nested):
    root, repo, config = layout
    outside = root.parent / "outside.cfg"
    write(outside, '[remote "origin"]\nurl = https://example.invalid/outside\n')
    text = f'[{section}]\n PaTh = "{outside.as_posix()}" # value\n'
    if nested:
        write(repo / ".git/config", '[include]\npath = admitted.cfg\n')
        write(repo / ".git/admitted.cfg", text)
    else:
        write(repo / ".git/config", text)
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.OUTSIDE_ROOT
    assert str(root) not in str(caught.value)


def test_common_config_and_worktree_config_are_checked(layout):
    root, repo, config = layout
    common = root / "common"
    (common / "objects").mkdir(parents=True)
    (common / "refs").mkdir()
    write(repo / ".git/commondir", str(common))
    write(common / "config", '[include]\npath = ../../outside.cfg\n')
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.OUTSIDE_ROOT


def test_worktree_config_include_is_checked(layout):
    _, repo, config = layout
    write(repo / ".git/config", '[extensions]\nworktreeConfig = true\n')
    write(repo / ".git/config.worktree", '[include]\npath = ../../../outside.cfg\n')
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.OUTSIDE_ROOT


@pytest.mark.parametrize("entry", ["~/outside.cfg", "~"])
def test_include_tilde_is_classified_without_reading_home(layout, monkeypatch, entry):
    root, repo, config = layout
    monkeypatch.setenv("HOME", str(root.parent / "home"))
    monkeypatch.setenv("USERPROFILE", str(root.parent / "home"))
    write(repo / ".git/config", f'[include]\npath = {entry}\n')
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.OUTSIDE_ROOT


@pytest.mark.parametrize("text", ['[include]\npath = "unterminated\n',
                                  '[include]\npath = "bad\\q"\n', '[include]\npath\n'])
def test_unclassifiable_include_is_refused(layout, text):
    _, repo, config = layout
    write(repo / ".git/config", text)
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID


def test_control_quoted_relative_includes_and_continuations(layout):
    _, repo, config = layout
    write(repo / ".git/config", '\ufeff[InClUdE]\nPaTh = "sub/"\\\n'
          + r'"a #; \"quoted\".cfg"' + ' ; comment\n')
    # A quote is legal in a POSIX filename only; use an escaped backslash-free name on Windows.
    if os.name == "nt":
        write(repo / ".git/config", '\ufeff[InClUdE]\nPaTh = "sub/"\\\n"a #;.cfg" ; comment\n')
        write(repo / '.git/sub/a #;.cfg', '[remote "origin"]\nurl = https://example.invalid/own\n')
    else:
        write(repo / '.git/sub/a #; "quoted".cfg', '[remote "origin"]\nurl = https://example.invalid/own\n')
    assert cfg.repository(str(repo), config)[0] == repo


def test_duplicate_alternate_lines_are_not_checked_again(layout, monkeypatch):
    _, repo, config = layout
    write(repo / ".git/objects/info/alternates", ".\n" * 100)
    original = cfg._metadata_entry
    calls = []

    def entry(text, base, name, roots):
        if name == "objects/info/alternates":
            calls.append(text)
        return original(text, base, name, roots)

    monkeypatch.setattr(cfg, "_metadata_entry", entry)
    assert cfg.repository(str(repo), config)[0] == repo
    assert calls == ["."]


def test_alternates_physical_lines_are_bounded(layout):
    _, repo, config = layout
    write(repo / ".git/objects/info/alternates", ".\n" * 1025)
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID
    assert "limit" in caught.value.reason


def test_admission_has_a_time_budget(layout, monkeypatch):
    _, repo, config = layout
    ticks = iter([0.0, *([1000.0] * 100)])
    monkeypatch.setattr(cfg, "monotonic", lambda: next(ticks), raising=False)
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID
    assert "time limit" in caught.value.reason


def test_alternate_lines_share_a_repository_budget(layout):
    root, repo, config = layout
    libraries = [root / f"lib{i}" for i in range(5)]
    for library in libraries:
        write(library / "info/alternates", "# counted\n" * 900)
    write(repo / ".git/objects/info/alternates", "".join(str(p) + "\n" for p in libraries))
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID
    assert "line limit" in caught.value.reason


@pytest.mark.parametrize("shape", ["loop", "depth", "files", "lines"])
def test_config_traversal_is_bounded(layout, shape):
    _, repo, config = layout
    first = repo / ".git/config"
    if shape == "loop":
        write(first, '[include]\npath = config\n')
    elif shape == "depth":
        for index in range(12):
            write(first if index == 0 else first.parent / f"config{index}",
                  f'[include]\npath = config{index + 1}\n')
    elif shape == "files":
        write(first, '[include]\n' + ''.join(f'path = config{i}\n' for i in range(64)))
    else:
        write(first, "#" + "x" * cfg.CONFIG_MAX_BYTES + "\n")
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID


def test_non_folder_alternate_reports_an_existing_entry(layout):
    _, repo, config = layout
    write(repo / ".git/objects/regular", "not a directory")
    write(repo / ".git/objects/info/alternates", "regular\n")
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID
    assert "existing entry" in caught.value.reason


def test_control_missing_inside_include_is_ignored(layout):
    _, repo, config = layout
    write(repo / ".git/config", '[include]\npath = missing.cfg\n')
    assert cfg.repository(str(repo), config)[0] == repo


def test_missing_alternates_consume_the_folder_budget(layout):
    root, repo, config = layout
    write(repo / ".git/objects/info/alternates", "".join(str(root / f"missing{i}") + "\n" for i in range(64)))
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID
    assert "missing targets count" in caught.value.reason


def test_missing_alternate_and_bom_are_admitted(layout):
    _, repo, config = layout
    write(repo / ".git/objects/info/alternates", "\ufeff# comment\nmissing\n")
    assert cfg.repository(str(repo), config)[0] == repo




def test_comment_lines_below_byte_budget_are_admitted(layout):
    _, repo, config = layout
    write(repo / ".git/config", "# counted\n" * 1025)
    assert cfg.repository(str(repo), config)[0] == repo
