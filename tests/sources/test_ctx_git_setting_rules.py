# SPDX-License-Identifier: Apache-2.0
"""A native setting's value belongs to that key's rule, not its siblings'."""
import os
import subprocess

import pytest

from g6.core.outside import find_program
from g6.sources.ctx_git import config as cfg, runner

GIT = find_program("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not on PATH")


@pytest.fixture
def repository(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    for name in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "G6_HOME"):
        monkeypatch.setenv(name, str(tmp_path))
    for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        monkeypatch.setenv(name, os.devnull)
    repo = tmp_path / "repo"
    subprocess.run([GIT, "init", "-q", str(repo)], check=True, capture_output=True, timeout=30)
    return repo


BOOLEAN_SPELLINGS = ("true", "false", "yes", "YES", "no", "on", "off", "1", "0", "-1", '""', None)
ACCEPTED = [(key, value) for key in ("autocrlf", "safecrlf", "symlinks", "ignorecase")
            for value in BOOLEAN_SPELLINGS] + [
    ("autocrlf", "input"), ("safecrlf", "warn"),
    # Git's core.eol rule falls back to native for unrecognised values.
    ("eol", "lf"), ("eol", "crlf"), ("eol", "native"), ("eol", "true"),
    ("eol", "input"), ("eol", "warn"), ("eol", '"lf\\\""'),
]
SIBLING_ONLY = [("autocrlf", "warn"), ("autocrlf", "lf"),
                ("safecrlf", "input"), ("safecrlf", "lf"),
                ("symlinks", "input"), ("symlinks", "warn"), ("symlinks", "lf"),
                ("ignorecase", "input"), ("ignorecase", "warn"), ("ignorecase", "lf")]


def native_probe(repo):
    # git var reads and checks every core setting before it answers. It exits 1 without a
    # message when no editor is configured and the terminal is dumb (as on CI runners), so the
    # probe names an editor; the answer then depends only on the planted setting.
    return subprocess.run([GIT, "-C", str(repo), "var", "GIT_EDITOR"], capture_output=True,
                          timeout=30, env={**os.environ, "GIT_EDITOR": ":"})


def plant(repo, key, value):
    entry = key if value is None else f"{key} = {value}"
    (repo / ".git/config").write_text(
        f"[core]\nrepositoryformatversion = 0\nbare = false\n{entry}\n", encoding="utf-8")


@pytest.mark.parametrize("key,value", ACCEPTED)
def test_each_setting_accepts_its_native_spellings(repository, key, value):
    plant(repository, key, value)
    native = native_probe(repository)
    assert native.returncode == 0, native.stderr
    raw = subprocess.run([GIT, "-C", str(repository), "config", "--get", f"core.{key}"],
                         check=True, capture_output=True, timeout=30).stdout
    with runner.session(repository, repository / ".git") as run:
        assert run("config", "--get", f"core.{key}").out == raw


@pytest.mark.parametrize("key,value", SIBLING_ONLY)
def test_a_setting_refuses_values_valid_only_for_a_sibling(repository, key, value):
    plant(repository, key, value)
    native = native_probe(repository)
    assert native.returncode != 0, native.stdout
    assert b"bad" in native.stderr.lower() or b"invalid" in native.stderr.lower(), native.stderr
    with runner.session(repository, repository / ".git") as run:
        with pytest.raises(cfg.GitRefused) as caught:
            run("config", "--get", f"core.{key}")
    assert caught.value.code == runner.FAILED


@pytest.mark.parametrize("key,value", SIBLING_ONLY)
def test_raw_config_inspection_keeps_native_bytes_even_for_an_invalid_setting(repository, key, value):
    plant(repository, key, value)
    with runner.session(repository, repository / ".git") as run:
        assert run("config", "--null", "--get", f"core.{key}").out == value.encode() + b"\0"
