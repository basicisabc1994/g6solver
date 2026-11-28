# SPDX-License-Identifier: Apache-2.0
"""Git config admission preserves bytes and bounds work without a config line cap."""
import os

import pytest

from g6.code import _roots
from g6.sources.ctx_git import _config_includes, config as cfg


@pytest.mark.parametrize("data", [
    b"[include]\n# comment\r[other]\npath = ../../../outside.cfg\n",
    b"[include]\nx = y\r[other]\npath = ../../../outside.cfg\n",
])
def test_only_lf_changes_config_sections(data):
    assert list(_config_includes.paths(data)) == [b"../../../outside.cfg"]


def test_unrelated_values_are_not_decoded_or_inspected():
    data = b'[user]\nname = Jos\xe9\nother = "bad\\q"\n[include]\npath = own.cfg\n'
    assert list(_config_includes.paths(data)) == [b"own.cfg"]


def test_crlf_continuation_and_quoted_path_preserve_lone_cr():
    data = b'[include]\r\npath = "sub/"\\\r\n"own.cfg" ; comment\r\n'
    assert list(_config_includes.paths(data)) == [b"sub/own.cfg"]
    assert list(_config_includes.paths(b'[include]\npath = "a\rb"\n')) == [b"a\rb"]


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "root"
    repo = root / "repo"
    (repo / ".git/objects").mkdir(parents=True)
    (repo / ".git/refs").mkdir()
    return repo, cfg.GitConfig((root,), (), "test")


def test_confined_bytes_preserve_config_input(layout):
    repo, config = layout
    data = b'[user]\nname = Jos\xe9\rraw\r\n'
    path = repo / ".git/config"
    path.write_bytes(data)
    roots = _roots.Roots(config.roots, "test")
    assert _roots.read_bytes(path, 1024, roots=roots) == data
    # Existing text callers retain their original decoding and newline convention.
    assert _roots.read_text(path, 1024, roots=roots) == '[user]\nname = Jos\ufffd\nraw\n'


def test_many_config_lines_are_admitted_but_config_bytes_are_capped(layout):
    repo, config = layout
    path = repo / ".git/config"
    prefix = b'[user]\nname = Jos\xe9\n'
    data = prefix + b'# ordinary config\n' * 5000
    path.write_bytes(data)
    assert cfg.repository(str(repo), config)[0] == repo
    path.write_bytes(prefix + b'# bounded config\n' * 66000)
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID


def test_config_parsing_checks_the_admission_deadline(layout, monkeypatch):
    repo, config = layout
    (repo / ".git/config").write_bytes(b'# ordinary config\n' * 5000)
    now = [0.0]

    def clock():
        now[0] += 0.05
        return now[0]

    monkeypatch.setattr(cfg, "monotonic", clock)
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID
    assert "time limit" in caught.value.reason


def test_non_filesystem_include_bytes_are_refused_before_target_io(layout, monkeypatch):
    repo, config = layout
    (repo / ".git/config").write_bytes(b'[include]\npath = bad\xff.cfg\n')
    # The fixture models a filesystem encoding that cannot represent this path.
    monkeypatch.setattr(cfg, "_include_path", lambda entry: entry.decode("utf-8", "strict"))
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID


@pytest.mark.skipif(os.name != "nt", reason="Git for Windows uses UTF-8 paths")
def test_invalid_windows_include_encoding_is_refused(layout):
    repo, config = layout
    (repo / ".git/config").write_bytes(b'[include]\npath = bad\xff.cfg\n')
    with pytest.raises(cfg.GitRefused) as caught:
        cfg.repository(str(repo), config)
    assert caught.value.code == cfg.PATH_INVALID
