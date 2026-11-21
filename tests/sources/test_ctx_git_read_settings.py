# SPDX-License-Identifier: Apache-2.0
"""External tree-reading settings preserve clean copies without importing programs.

Removing the carry breaks the CRLF control; broadening its keys, scopes or values
breaks the independent allowlist, include and audit controls below.
"""
import json
import os
import subprocess

import pytest

from g6.core.git_env import safe_git_env
from g6.core.outside import find_program
from g6.sources.ctx_git import _config_policy, ops, runner

GIT = find_program("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not on PATH")


@pytest.fixture
def external(tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    global_config, system_config = user / "global.cfg", user / "system.cfg"
    for path in (global_config, system_config):
        path.write_bytes(b"")
    for name in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(user))
    for name, path in (("GIT_CONFIG_GLOBAL", global_config), ("GIT_CONFIG_SYSTEM", system_config)):
        monkeypatch.setenv(name, str(path))
    for name in tuple(os.environ):
        if name.startswith("GIT_") and name not in {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"}:
            monkeypatch.delenv(name)
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(name, "Fixture Person")
    for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, "fixture@example.invalid")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))
    return global_config, system_config


def git(where, *args):
    return subprocess.run([GIT, "--no-pager", "-C", str(where), *args], check=True,
                          capture_output=True, timeout=30, env=dict(os.environ)).stdout


def read_settings(where):
    env = safe_git_env(where, ceiling=where)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull})
    return runner._read_tree_settings(where, env, 10)


@pytest.mark.parametrize("scope", ["global", "system"])
def test_crlf_staged_with_explicit_autocrlf_stays_clean_through_ctx_git(tmp_path, external, scope):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_bytes(b"first\r\nsecond\r\n")
    git(repo, "-c", "core.autocrlf=true", "add", "a.txt")
    git(repo, "-c", "core.autocrlf=true", "commit", "-q", "-m", "fixture")
    assert git(repo, "show", "HEAD:a.txt") == b"first\nsecond\n"
    external[0 if scope == "global" else 1].write_bytes(b"[core]\nautocrlf = true\n")
    with runner.session(repo, repo / ".git") as run:
        assert ops.status_of(run)["clean"] is True
        assert run("config", "--get", "core.autocrlf").text.strip() == "true"
    (repo / "a.txt").write_bytes(b"changed\r\nsecond\r\n")
    with runner.session(repo, repo / ".git") as run:
        assert ops.status_of(run)["modified"] == ["a.txt"]


def test_only_the_fixed_five_keys_can_be_carried(tmp_path, external):
    included = tmp_path / "included.cfg"
    included.write_bytes(b"[core]\nautocrlf = input\neol = lf\n")
    external[0].write_text(
        '[core]\nautocrlf = true\nsafecrlf = warn\nsymlinks = false\nignorecase = true\n'
        'fsmonitor = planted-monitor\nhooksPath = planted-hooks\n'
        f'[include]\npath = "{included.as_posix()}"\n'
        '[filter "planted"]\nclean = planted-clean\nprocess = planted-process\n'
        '[credential]\nhelper = planted-helper\n[remote "origin"]\nurl = https://example.invalid/\n',
        encoding="utf-8", newline="\n")
    external[1].write_bytes(b"[core]\neol = crlf\nautocrlf = false\n")
    carried = dict(read_settings(tmp_path))
    assert carried == {"core.autocrlf": "true", "core.eol": "crlf", "core.safecrlf": "warn",
                       "core.symlinks": "false", "core.ignorecase": "true"}
    assert set(carried) <= {"core.autocrlf", "core.eol", "core.safecrlf", "core.symlinks", "core.ignorecase"}
    assert all(_config_policy.rule_for(key) is None for key in carried)


@pytest.mark.parametrize("key,value,want", [
    ("autocrlf", "true", "true"), ("autocrlf", "false", "false"), ("autocrlf", "input", "input"),
    ("autocrlf", "planted-command", None), ("autocrlf", "warn", None),
    ("eol", "lf", "lf"), ("eol", "crlf", "crlf"), ("eol", "native", "native"),
    ("eol", "true", None),
    ("safecrlf", "true", "true"), ("safecrlf", "false", "false"), ("safecrlf", "warn", "warn"),
    ("safecrlf", "input", None),
    ("symlinks", "true", "true"), ("symlinks", "false", "false"),
    ("symlinks", "YES", "true"), ("symlinks", "off", "false"),
    ("ignorecase", "on", "true"), ("ignorecase", "0", "false"),
    ("ignorecase", "1", "true"), ("ignorecase", "warn", None),
    ("symlinks", '""', "false"),
])
def test_values_are_validated_and_rejections_audited(tmp_path, external, key, value, want):
    external[0].write_bytes(f"[core]\n{key} = {value}\n".encode())
    carried = dict(read_settings(tmp_path))
    if want is not None:
        assert carried == {f"core.{key}": want}
    else:
        assert carried == {}
        entries = [json.loads(line) for line in runner.audit_path().read_text(encoding="utf-8").splitlines()]
        assert any(entry.get("key") == f"core.{key}" and entry.get("scope") == "global"
                   and entry.get("decision") == "dropped" and entry.get("reason") for entry in entries)
        assert value not in runner.audit_path().read_text(encoding="utf-8")


def test_invalid_last_value_drops_an_earlier_system_value(tmp_path, external):
    external[1].write_bytes(b"[core]\nautocrlf = true\n")
    external[0].write_bytes(b"[core]\nautocrlf = false\nautocrlf = unsafe-value\n")
    assert read_settings(tmp_path) == ()


def test_bare_boolean_and_last_valid_value_have_git_semantics(tmp_path, external):
    external[0].write_bytes(b"[core]\nsymlinks\nautocrlf = true\nautocrlf = input\n")
    assert dict(read_settings(tmp_path)) == {"core.symlinks": "true", "core.autocrlf": "input"}


def test_repository_settings_are_not_imported_and_policy_keys_cannot_be_overridden(tmp_path, external, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "input")
    assert read_settings(repo) == ()
    external[0].write_bytes(b"[core]\nautocrlf = true\n")
    original = _config_policy.rule_for
    monkeypatch.setattr(_config_policy, "rule_for", lambda key: (
        _config_policy.Rule(key, "override", "false", "fixture policy reservation")
        if key.lower() == "core.autocrlf" else original(key)))
    assert read_settings(tmp_path) == ()


def test_clone_also_carries_read_settings_without_following_includes(tmp_path, external, monkeypatch):
    external[0].write_bytes(b"[core]\nautocrlf = input\n[include]\npath = absent.cfg\n")
    monkeypatch.setattr(runner, "ssh_program", lambda: None)
    with runner.clone_session(tmp_path) as run:
        assert run("config", "--get", "core.autocrlf").text.strip() == "input"
        assert run("config", "--get", "include.path", ok=(0, 1)).code == 1
