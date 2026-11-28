# SPDX-License-Identifier: Apache-2.0
"""Native content settings survive; untracked policy cannot hide review files.

The literal inventory catches missing deny classes; the real Git cases catch
restoring null external configs, promoting tree values, or dropping includes.
"""
import os
import subprocess
import time

import pytest

from g6.core.outside import find_program
from g6.sources.ctx_git import CtxGitBlock, CtxGitInput, _config_policy, config as cfg, ops, runner

GIT = find_program("git")


@pytest.fixture
def person(tmp_path, monkeypatch):
    user = tmp_path / "person"
    user.mkdir()
    global_config, system_config = user / "global.cfg", user / "system.cfg"
    global_config.write_bytes(b"")
    system_config.write_bytes(b"")
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    for name in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(user))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system_config))
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(name, "Fixture Person")
    for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, "fixture@example.invalid")
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    return global_config, system_config


def git(repo, *args):
    if GIT is None:
        pytest.skip("git is not on PATH")
    return subprocess.run([GIT, "--no-pager", "-C", str(repo), *args],
                          capture_output=True, timeout=30, check=True).stdout


def repository(tmp_path):
    root = tmp_path / "root"
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    return repo, cfg.GitConfig((root,), ("example.invalid",), "test")


def call(repo, config, op="status", **kwargs):
    return CtxGitBlock(config=config).infer(CtxGitInput(op=op, repo=str(repo), **kwargs)).unwrap()


@pytest.mark.parametrize("key,value", [
    ("core.hooksPath", "empty-hooks"), ("core.fsmonitor", "false"),
    ("core.pager", ""), ("core.sshCommand", ""), ("core.askPass", ""),
    ("credential.helper", ""), ("credential.https://example.invalid.helper", ""),
    ("filter.named.clean", ""), ("filter.named.smudge", ""), ("filter.named.process", ""),
    ("filter.named.required", "false"),
    ("diff.named.textconv", ""), ("diff.named.command", ""), ("diff.external", ""),
    ("merge.named.driver", ""), ("gpg.program", ""), ("gpg.ssh.program", ""),
    ("gpg.x509.program", ""), ("protocol.allow", "never"), ("protocol.ext.allow", "never"),
    ("http.proxy", ""), ("http.https://example.invalid/.proxy", ""),
    ("http.https://example.invalid/.sslVerify", "true"),
    ("http.https://example.invalid/.followRedirects", "false"),
    ("remote.origin.proxy", ""), ("mailmap.file", os.devnull),
    ("blame.ignoreRevsFile", ""),
    ("core.excludesFile", ""), ("core.attributesFile", os.devnull),
    ("http.sslCAInfo", ""), ("http.sslCAPath", ""),
])
def test_deny_list_covers_program_transport_proxy_and_existing_policy(key, value):
    # Missing a key class must fail even if the documentation inventory omits it.
    settings = dict(_config_policy.settings((key,), hooks="empty-hooks", template="empty-template"))
    assert settings.get(key) == value, key


@pytest.mark.parametrize("key", ["include.path", "includeIf.gitdir:never/.path",
                                "core.excludesFile", "core.attributesFile",
                                "url.https://example.invalid/.insteadOf",
                                "url.https://example.invalid/.pushInsteadOf"])
def test_additive_includes_rewrites_and_personal_files_require_classification(key):
    rule = _config_policy.rule_for(key)
    assert rule is not None and rule.reason
    if key in {"core.excludesFile", "core.attributesFile"}:
        assert rule.action == "override"
        expected = "" if key == "core.excludesFile" else os.devnull
        assert dict(_config_policy.settings(hooks="empty-hooks", template="empty-template"))[key] == expected
    else:
        assert rule.action == "classified"


@pytest.mark.parametrize("local,global_value,want", [("true", "false", True), ("false", "true", False)])
def test_repository_precedence_matches_native_status(tmp_path, person, local, global_value, want):
    repo, config = repository(tmp_path)
    git(repo, "config", "core.autocrlf", local)
    (repo / "a.txt").write_bytes(b"one\r\ntwo\r\n")
    git(repo, "-c", "core.autocrlf=true", "add", "a.txt")
    git(repo, "-c", "core.autocrlf=true", "commit", "-qm", "fixture")
    person[0].write_bytes(f"[core]\nautocrlf = {global_value}\n".encode())
    future = time.time() + 30
    os.utime(repo / "a.txt", (future, future))
    assert (git(repo, "status", "--porcelain") == b"") is want
    assert call(repo, config)["clean"] is want


def test_nosystem_is_preserved(tmp_path, person, monkeypatch):
    person[1].write_bytes(b"[core]\nautocrlf = true\n")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo, config = repository(tmp_path)
    (repo / "a.txt").write_bytes(b"one\r\n")
    git(repo, "-c", "core.autocrlf=true", "add", "a.txt")
    git(repo, "-c", "core.autocrlf=true", "commit", "-qm", "fixture")
    future = time.time() + 30
    os.utime(repo / "a.txt", (future, future))
    assert git(repo, "status", "--porcelain").strip() == b"M a.txt"
    assert call(repo, config)["modified"] == ["a.txt"]


@pytest.mark.parametrize("default", [False, True])
def test_personal_ignore_and_attributes_cannot_hide_review_content(tmp_path, person, default, monkeypatch):
    user = person[0].parent
    (user / "git").mkdir()
    ignore = user / "git/ignore" if default else user / "ignore"
    attributes = user / "git/attributes" if default else user / "attributes"
    ignore.write_bytes(b"*.log\n")
    attributes.write_bytes(b"*.txt text eol=lf\n")
    included = user / "included.cfg"
    included.write_bytes(("[core]\nautocrlf = false\n" + ("" if default else
                         f"excludesFile = {ignore.as_posix()}\nattributesFile = {attributes.as_posix()}\n")).encode())
    person[0].write_bytes(f"[include]\npath = {included.as_posix()}\n".encode())
    if default:
        # Exercise Git's default global location as well as the explicit selector.
        (user / ".gitconfig").write_bytes(person[0].read_bytes())
        monkeypatch.delenv("GIT_CONFIG_GLOBAL")
    repo, config = repository(tmp_path)
    (repo / "a.txt").write_bytes(b"one\r\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "fixture")
    (repo / "debug.log").write_bytes(b"noise\n")
    future = time.time() + 30
    os.utime(repo / "a.txt", (future, future))
    assert git(repo, "status", "--porcelain") == b""
    result = call(repo, config)
    assert result["code"] == ops.READ
    assert result["clean"] is False
    assert result["untracked"] == ["debug.log"]
    assert result["modified"] == ["a.txt"]
    with runner.session(repo, repo / ".git") as run:
        # The include's content-reading setting still has native scope.
        assert run("config", "--get", "core.autocrlf").text.strip() == "false"


def test_system_tls_backend_survives_but_configured_ca_file_is_disabled(tmp_path, person):
    ca = person[0].parent / "ca.pem"
    ca.write_bytes(b"synthetic CA; not used by a network call\n")
    person[1].write_bytes(f"[http]\nsslBackend = schannel\nsslCAInfo = {ca.as_posix()}\n".encode())
    repo, _ = repository(tmp_path)
    with runner.session(repo, repo / ".git") as run:
        assert run("config", "--get", "http.sslBackend").text.strip() == "schannel"
        assert run("config", "--get", "http.sslCAInfo").text.strip() == ""
        assert run("config", "--get", "http.sslVerify").text.strip() == "true"


def test_programs_from_personal_includes_are_disabled_for_sessions_and_clone(tmp_path, person, monkeypatch):
    included = person[0].parent / "programs.cfg"
    included.write_bytes(b'[core]\nfsmonitor = planted\n[filter "named"]\nprocess = planted\nrequired = true\n'
                         b'[gpg "custom"]\nprogram = planted\n[protocol "ext"]\nallow = always\n')
    person[0].write_bytes(f"[include]\npath = {included.as_posix()}\n".encode())
    repo, _ = repository(tmp_path)
    monkeypatch.setattr(runner, "ssh_program", lambda: None)
    for context in (runner.session(repo, repo / ".git"), runner.clone_session(tmp_path)):
        with context as run:
            assert run("config", "--get", "core.fsmonitor").text.strip() == "false"
            assert run("config", "--get", "filter.named.process").text.strip() == ""
            assert run("config", "--get", "filter.named.required").text.strip() == "false"
            assert run("config", "--get", "gpg.custom.program").text.strip() == ""
            assert run("config", "--get", "protocol.ext.allow").text.strip() == "never"


@pytest.mark.parametrize("scope", ["local", "global"])
@pytest.mark.parametrize("rewrite", ["insteadOf", "pushInsteadOf"])
def test_additive_url_rewrites_refuse_network_before_resolver_and_gate(tmp_path, person, scope, rewrite):
    repo, config = repository(tmp_path)
    (repo / "a.txt").write_bytes(b"one\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "fixture")
    git(repo, "remote", "add", "origin", "https://example.invalid/project")
    options = ("--global",) if scope == "global" else ()
    git(repo, "config", *options, f"url.https://other.invalid/.{rewrite}", "https://example.invalid/")

    def forbidden(*args, **kwargs):
        pytest.fail("additive rewrites must be refused before resolver and gate")

    block = CtxGitBlock(config=config, resolver=forbidden, gate=forbidden)
    result = block.infer(CtxGitInput(op="push", repo=str(repo), branch="main")).unwrap()
    assert result["code"] == ops.STEERED


@pytest.mark.parametrize("key", ["core.excludesFile", "core.attributesFile", "blame.ignoreRevsFile"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("spelling", ["absolute", "relative", "tilde"])
def test_repository_file_targets_outside_roots_are_refused_or_neutralised(tmp_path, person, key, nested, spelling,
                                                                        monkeypatch):
    repo, config = repository(tmp_path)
    outside = person[0].parent / "outside.txt" if spelling == "tilde" else tmp_path / "outside.txt"
    entry = ("~/outside.txt" if spelling == "tilde" else
             os.path.relpath(outside, repo).replace("\\", "/") if spelling == "relative" else outside.as_posix())
    section, variable = key.split(".")
    text = f"[{section}]\n{variable} = {entry}\n".encode()
    if nested:
        (repo / ".git/extra.cfg").write_bytes(text)
        git(repo, "config", "include.path", "extra.cfg")
    else:
        git(repo, "config", key, entry)

    original = cfg._metadata_info

    def checked(path, name, roots):
        # A policy decision must not inspect the outside target, even when
        # it is missing, present, or contains invalid revisions.
        assert path != outside
        return original(path, name, roots)

    monkeypatch.setattr(cfg, "_metadata_info", checked)
    if key == "core.excludesFile":
        (repo / "hidden.txt").write_bytes(b"ordinary content\n")
    elif key == "core.attributesFile":
        (repo / "a.txt").write_bytes(b"one\n")
        git(repo, "add", "a.txt")
        git(repo, "commit", "-qm", "fixture")
        (repo / "a.txt").write_bytes(b"visible change\n")
    hiding_content = (b"hidden.txt\n" if key == "core.excludesFile" else b"a.txt -diff\n"
                      if key == "core.attributesFile" else b"# comment\n")
    for content in (None, hiding_content, b"not-a-revision\n"):
        if content is not None:
            outside.write_bytes(content)
        result = call(repo, config, op="diff" if key == "core.attributesFile" else "status")
        if key == "blame.ignoreRevsFile":
            assert result["code"] == cfg.OUTSIDE_ROOT
        else:
            assert result["code"] == ops.READ
            if key == "core.excludesFile":
                assert "hidden.txt" in result["untracked"]
            else:
                assert "+visible change" in result["patch"]


@pytest.mark.parametrize("key,section,variable", [
    ("core.excludesfile", "core", "excludesFile"),
    ("core.attributesfile", "core", "attributesFile"),
])
def test_outside_tree_file_policy_is_collected_without_target_io(tmp_path, person, key, section, variable,
                                                               monkeypatch):
    repo, config = repository(tmp_path)
    (repo / ".git/extra.cfg").write_bytes(f"[{section}]\n{variable} = ../../outside\n".encode())
    git(repo, "config", "include.path", "extra.cfg")
    original = cfg._metadata_info

    def checked(path, name, roots):
        assert path != tmp_path / "outside"
        return original(path, name, roots)

    monkeypatch.setattr(cfg, "_metadata_info", checked)
    file_policy = set()
    assert cfg.repository(str(repo), config, file_policy=file_policy) == (repo, repo / ".git")
    assert file_policy == {key}


@pytest.mark.parametrize("scope", ["global", "system"])
@pytest.mark.parametrize("text", [
    b'[core "x"]\nautocrlf = true\n',
    b'[core ""]\nautocrlf = true\n',
    b'[Core]\nAutoCRLF = Input\n',
    b'[core]\nautocrlf = "true\\nfsmonitor=x"\n',
    b'[core]\nautocrlf = "tr" "ue"\n',
    b'[core]\neol = "lf\\\""\n',
    b'[core]\nautocrlf = true\nfsmonitor = planted\npager = planted\n',
])
def test_native_spelling_cannot_inject_a_program_or_replace_deny_keys(tmp_path, person, scope, text):
    repo, _ = repository(tmp_path)
    person[0 if scope == "global" else 1].write_bytes(text)
    # Config queries do not validate tree-reading values. They must retain
    # the deny policy even with an invalid value or a misleading subsection.
    with runner.clone_session(tmp_path) as run:
        assert run("config", "--get", "core.fsmonitor").text.strip() == "false"
        assert run("config", "--get", "core.pager").text.strip() == ""


def test_agent_config_injection_cannot_override_native_deny_policy(tmp_path, person, monkeypatch):
    repo, _ = repository(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.fsmonitor'='planted' 'core.autocrlf'='input'")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.pager")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "planted")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "core.eol")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "crlf")
    with runner.session(repo, repo / ".git") as run:
        assert run("config", "--get", "core.fsmonitor").text.strip() == "false"
        assert run("config", "--get", "core.pager").text.strip() == ""
        assert run("config", "--get", "core.autocrlf", ok=(0, 1)).code == 1
        assert run("config", "--get", "core.eol", ok=(0, 1)).code == 1


def test_repository_relative_untracked_files_cannot_hide_review_content(tmp_path, person):
    repo, config = repository(tmp_path)
    (repo / ".git/own.ignore").write_bytes(b"*.log\n")
    (repo / ".git/own.attributes").write_bytes(b"*.txt text eol=lf\n")
    (repo / ".git/extra.cfg").write_bytes(b"[core]\nexcludesFile = .git/own.ignore\n"
                                          b"attributesFile = .git/own.attributes\n")
    git(repo, "config", "include.path", "extra.cfg")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "a.txt").write_bytes(b"one\r\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "fixture")
    (repo / "debug.log").write_bytes(b"noise\n")
    future = time.time() + 30
    os.utime(repo / "a.txt", (future, future))
    assert git(repo, "status", "--porcelain") == b""
    result = call(repo, config)
    assert result["code"] == ops.READ
    assert result["clean"] is False
    assert result["untracked"] == ["debug.log"]
    assert result["modified"] == ["a.txt"]
    assert "+one" in call(repo, config, op="diff")["patch"]


@pytest.mark.parametrize("key,op", [("push.pushOption", "push"),
                                   ("remote.origin.serverOption", "fetch"),
                                   ("remote.origin.tagOpt", "pull")])
def test_network_steering_refuses_before_dirty_check_resolver_and_gate(tmp_path, person, key, op):
    repo, config = repository(tmp_path)
    (repo / "a.txt").write_bytes(b"one\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "fixture")
    git(repo, "remote", "add", "origin", "https://example.invalid/project")
    git(repo, "config", key, "--tags" if key.endswith("tagOpt") else "planted")
    (repo / "a.txt").write_bytes(b"dirty\n")

    def forbidden(*args, **kwargs):
        pytest.fail("network config refusal must precede resolver and gate")

    block = CtxGitBlock(config=config, resolver=forbidden, gate=forbidden)
    result = block.infer(CtxGitInput(op=op, repo=str(repo), branch="main")).unwrap()
    assert result["code"] == ops.STEERED


def test_blame_clears_all_configured_ignore_revision_files(tmp_path, person):
    repo, config = repository(tmp_path)
    (repo / "a.txt").write_bytes(b"one\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "fixture")
    # A person's list must be cleared too, even if empty -c values do not
    # reset this Git version's config accumulator.
    person[0].write_bytes(f"[blame]\nignoreRevsFile = {(tmp_path / 'absent').as_posix()}\n".encode())
    result = call(repo, config, op="blame", file="a.txt")
    assert result["code"] == ops.READ
