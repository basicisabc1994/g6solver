# SPDX-License-Identifier: Apache-2.0
"""Personal native Git settings retain precedence, validation and command policy.

Real repositories and planted configs exercise the replacement for the removed carry.
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from g6.core.outside import find_program
from g6.sources.ctx_git import _config_policy, config as cfg, ops, runner

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


def repository(where):
    repo = where if (where / ".git").is_dir() else where / "repo"
    if not (repo / ".git").is_dir():
        repo.mkdir(exist_ok=True)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        subprocess.run([GIT, "init", "-q", "-b", "main", str(repo)], env=env,
                       check=True, capture_output=True, timeout=30)
        # Remove platform-dependent init defaults so this fixture isolates the person's config.
        (repo / ".git/config").write_bytes(
            b"[core]\nrepositoryformatversion = 0\nbare = false\nfilemode = false\nlogallrefupdates = true\n")
    return repo


def read_settings(where):
    repo = repository(where)
    found = {}
    with runner.session(repo, repo / ".git") as run:
        for name in ("autocrlf", "eol", "safecrlf", "symlinks", "ignorecase"):
            key = f"core.{name}"
            raw = run("config", "--get", key, ok=(0, 1))
            if raw.code == 1:
                continue
            value = raw.text.strip()
            if name in {"symlinks", "ignorecase"} or (name in {"autocrlf", "safecrlf"}
                                                       and value not in {"input", "warn"}):
                value = run("config", "--type=bool", "--get", key).text.strip()
            found[key] = value
    return found


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
    repo = repository(tmp_path)
    # Personal includes and native last-value precedence replace the old five-key carry.
    assert read_settings(repo) == {"core.autocrlf": "input", "core.eol": "lf", "core.safecrlf": "warn",
                                   "core.symlinks": "false", "core.ignorecase": "true"}
    with runner.session(repo, repo / ".git") as run:
        assert run("config", "--get", "remote.origin.url").text.strip() == "https://example.invalid/"
        for key, want in {"core.fsmonitor": "false", "filter.planted.clean": "",
                          "filter.planted.process": "", "credential.helper": "", "core.sshCommand": ""}.items():
            assert run("config", "--get", key).text.strip() == want
            assert _config_policy.rule_for(key).action == "override"
        hooks = run("config", "--get", "core.hooksPath").text.strip()
        assert hooks != "planted-hooks" and Path(hooks).is_dir() and list(Path(hooks).iterdir()) == []


@pytest.mark.parametrize("key,value,want", [
    ("autocrlf", "true", "true"), ("autocrlf", "false", "false"), ("autocrlf", "input", "input"),
    ("autocrlf", "planted-command", None), ("autocrlf", "warn", None),
    ("eol", "lf", "lf"), ("eol", "crlf", "crlf"), ("eol", "native", "native"),
    ("eol", "true", "true"),
    ("safecrlf", "true", "true"), ("safecrlf", "false", "false"), ("safecrlf", "warn", "warn"),
    ("safecrlf", "input", None),
    ("symlinks", "true", "true"), ("symlinks", "false", "false"),
    ("symlinks", "YES", "true"), ("symlinks", "off", "false"),
    ("ignorecase", "on", "true"), ("ignorecase", "0", "false"),
    ("ignorecase", "1", "true"), ("ignorecase", "warn", None),
    ("symlinks", '""', "false"),
])
def test_values_use_gits_own_config_validation(tmp_path, external, key, value, want):
    external[0].write_bytes(f"[core]\n{key} = {value}\n".encode())
    if want is not None:
        assert read_settings(tmp_path) == {f"core.{key}": want}
    else:
        with pytest.raises(cfg.GitRefused) as caught:
            read_settings(tmp_path)
        assert caught.value.code == runner.FAILED


def test_invalid_last_value_never_falls_back_to_an_earlier_system_value(tmp_path, external):
    external[1].write_bytes(b"[core]\nautocrlf = true\n")
    external[0].write_bytes(b"[core]\nautocrlf = false\nautocrlf = unsafe-value\n")
    with pytest.raises(cfg.GitRefused) as caught:
        with runner.session(repository(tmp_path), tmp_path / "repo/.git") as run:
            run("status", "--porcelain")
    assert caught.value.code == runner.FAILED


def test_bare_boolean_and_last_valid_value_have_git_semantics(tmp_path, external):
    external[0].write_bytes(b"[core]\nsymlinks\nautocrlf = true\nautocrlf = input\n")
    assert dict(read_settings(tmp_path)) == {"core.symlinks": "true", "core.autocrlf": "input"}


def test_repository_settings_keep_native_precedence_and_policy_keys_cannot_be_overridden(tmp_path, external, monkeypatch):
    repo = repository(tmp_path)
    git(repo, "config", "core.autocrlf", "input")
    external[0].write_bytes(b"[core]\nautocrlf = true\n")
    with runner.session(repo, repo / ".git") as run:
        # Harmless repository values retain Git's native precedence.
        assert run("config", "--get", "core.autocrlf").text.strip() == "input"
    original = _config_policy.rule_for
    monkeypatch.setattr(_config_policy, "rule_for", lambda key: (
        _config_policy.Rule(key, "override", "false", "fixture policy reservation")
        if key.lower() == "core.autocrlf" else original(key)))
    with runner.session(repo, repo / ".git") as run:
        assert run("config", "--get", "core.autocrlf").text.strip() == "false"


def test_clone_also_carries_read_settings_without_following_includes(tmp_path, external, monkeypatch):
    repository(tmp_path)
    own = external[0].parent / "included.cfg"
    own.write_bytes(b"[core]\nautocrlf = input\n[filter \"included\"]\nprocess = planted-process\n")
    external[0].write_bytes(b"[core]\nautocrlf = false\n[include]\npath = included.cfg\n")
    monkeypatch.setattr(runner, "ssh_program", lambda: None)
    with runner.clone_session(tmp_path) as run:
        assert run("config", "--get", "core.autocrlf").text.strip() == "input"
        assert run("config", "--get", "include.path").text.strip() == "included.cfg"
        assert run("config", "--get", "filter.included.process").text.strip() == ""


@pytest.mark.parametrize("origin", ["local", "repository", "personal-chain", "global", "system"])
def test_repository_controlled_include_never_reaches_a_command(tmp_path, external, origin):
    repo = repository(tmp_path)
    (repo / "a.txt").write_bytes(b"tracked content\n")
    git(repo, "add", "a.txt")
    marker = tmp_path / "program-ran"
    program = tmp_path / "marker.py"
    program.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    command = " ".join(shlex.quote(path) for path in (Path(sys.executable).as_posix(), program.as_posix()))
    planted = repo / "tree-controlled.cfg"
    content = ('[g6fixture]\nrepositoryMarker = reached\n'
               f'[core]\nfsmonitor = {json.dumps(command)}\n')
    owner = repo / ".git/config" if origin in {"local", "repository"} else external[1 if origin == "system" else 0]
    with owner.open("ab") as config:
        if origin in {"local", "global", "system"}:
            config.write(content.encode())
        else:
            planted.write_bytes(content.encode())
            included = planted
            if origin == "personal-chain":
                included = external[0].parent / "personal-include.cfg"
                included.write_bytes(f'[include]\npath = "{planted.as_posix()}"\n'.encode())
            config.write(f'[include]\npath = "{included.as_posix()}"\n'.encode())
    # The same config must start the marker under native Git: a dormant fixture
    # would miss a lost deny rule. G6 must retain the harmless key as well.
    git(repo, "status", "--porcelain")
    assert marker.is_file(), "native Git must run the planted program"
    marker.unlink()
    try:
        with runner.session(repo, repo / ".git") as run:
            assert run("config", "--get", "g6fixture.repositoryMarker").text.strip() == "reached"
            run("status", "--porcelain")
    except cfg.GitRefused as caught:
        assert caught.code == runner.UNSAFE
    assert not marker.exists(), "G6 ran a repository-controlled program"


@pytest.mark.parametrize("spelling", [b'autocrlf = "tr" "ue"', b'autocrlf = "true\\nfsmonitor=x"',
                                     b'eol = "lf\\\""', b'eol = true'])
@pytest.mark.parametrize("clone", [False, True])
def test_native_spelling_is_read_by_git_without_eager_value_rejection(tmp_path, external, spelling, clone,
                                                                    monkeypatch):
    repo = repository(tmp_path)
    external[0].write_bytes(b"[core]\n" + spelling + b"\n")
    key = "core." + spelling.decode().partition(" =")[0]
    native = git(repo, "config", "--null", "--get", key)
    monkeypatch.setattr(runner, "ssh_program", lambda: None)
    context = runner.clone_session(tmp_path) if clone else runner.session(repo, repo / ".git")
    with context as run:
        assert run("config", "--null", "--get", key).out == native
        assert run("config", "--get", "core.fsmonitor").text.strip() == "false"


def test_policy_reservation_overrides_personal_read_setting(tmp_path, external, monkeypatch):
    repo = repository(tmp_path)
    external[0].write_bytes(b"[core]\nautocrlf = true\n")
    original = _config_policy.rule_for
    monkeypatch.setattr(_config_policy, "rule_for", lambda key: (
        _config_policy.Rule(key, "override", "false", "fixture policy reservation")
        if key.lower() == "core.autocrlf" else original(key)))
    with runner.session(repo, repo / ".git") as run:
        assert run("config", "--get", "core.autocrlf").text.strip() == "false"
