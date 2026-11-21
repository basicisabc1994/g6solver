# SPDX-License-Identifier: Apache-2.0
"""Every documented file/program indirection has an explicit command-scope policy."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.sources.ctx_git import runner
from tests.sources._git_test_utils import git_command


def test_documented_path_command_keys_have_a_policy():
    from g6.sources.ctx_git import _config_policy as policy

    pinned = Path(__file__).with_name("data") / "git_path_command_keys.txt"
    keys = [line.split("\t", 1)[0] for line in pinned.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]
    assert len(keys) >= 80
    for key in keys:
        rule = policy.rule_for(key)
        assert rule is not None, f"documented indirection lacks policy: {key}"
        assert rule.reason.strip(), key
        assert rule.action in {"override", "classified", "exempt"}, key


@pytest.mark.parametrize("key,value", [
    ("mailmap.file", os.devnull), ("mailmap.blob", ""), ("blame.ignoreRevsFile", ""),
    ("core.excludesFile", ""), ("core.attributesFile", os.devnull),
    ("diff.orderFile", os.devnull), ("commit.template", os.devnull),
    ("http.https://example.invalid/.sslCAInfo", ""),
    ("http.https://example.invalid/.cookieFile", ""),
    ("credential.https://example.invalid.helper", ""),
    ("remote.origin.uploadpack", "git-upload-pack"),
    ("diff.named.textconv", ""), ("filter.named.process", ""),
])
def test_subsection_and_generic_overrides_are_command_scope(key, value):
    from g6.sources.ctx_git import _config_policy as policy

    settings = dict(policy.settings((key,), hooks="empty-hooks", template="empty-template"))
    assert settings[key] == value


def test_policy_reaches_the_first_git_query(tmp_path, monkeypatch):
    calls = []
    selectors = _personal_selectors(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "find_program", lambda _: "git")
    monkeypatch.setattr(runner, "git_program", lambda: "git")
    monkeypatch.setattr(runner, "refuse_gitlinks", lambda *args: None)

    def child(argv, *, env, input_bytes):
        calls.append((argv, env, _folders_now(argv, env)))
        command = git_command(argv)
        if "--system" in command or "--global" in command:
            out = b''
        elif "--list" in command:
            out = (b'http.https://example.invalid/.sslCAInfo\0credential.https://example.invalid.helper\0'
                   b'core.Named.excludesFile\0core..attributesFile\0core.https://example.invalid/.fsmonitor\0')
        elif "--get-regexp" in command:
            out = b'g6.k1guard\0'
        else:
            out = b''
        return SimpleNamespace(returncode=0, stdout=out, stderr=b'')

    monkeypatch.setattr(runner.component_process, "run_contained", child)
    with runner.session(tmp_path, tmp_path / ".git") as run:
        run("status")
    assert calls
    assert "--list" in git_command(calls[0][0])
    for index, (argv, env, folders) in enumerate(calls):
        _assert_fixed_policy(argv, env, folders)
        assert {key: env[key] for key in selectors} == selectors
        assert env["GIT_DIR"] == str(tmp_path / ".git")
        assert env["GIT_WORK_TREE"] == str(tmp_path)
        if index:
            assert "http.https://example.invalid/.sslCAInfo=" in argv
            assert "credential.https://example.invalid.helper=" in argv
            assert "core.Named.excludesFile=" in argv
            assert "core..attributesFile=" + os.devnull in argv
            assert "core.https://example.invalid/.fsmonitor=false" in argv


def test_inventory_detects_an_uncovered_documented_key():
    from g6.sources.ctx_git import _config_policy as policy

    assert policy.rule_for("newSection.someFile") is None


def test_clone_session_has_the_same_file_policy_before_its_first_command(tmp_path, monkeypatch):
    calls = []
    selectors = _personal_selectors(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "find_program", lambda _: "git")
    monkeypatch.setattr(runner, "git_program", lambda: "git")
    monkeypatch.setattr(runner, "ssh_program", lambda: None)

    def child(argv, **kwargs):
        calls.append((argv, kwargs["env"], _folders_now(argv, kwargs["env"])))
        out = (b'http.https://example.invalid/.sslCAInfo\0credential.https://example.invalid.helper\0'
               b'core.Named.excludesFile\0core..attributesFile\0core.https://example.invalid/.fsmonitor\0')
        return SimpleNamespace(returncode=0, stdout=out, stderr=b'')

    monkeypatch.setattr(runner.component_process, "run_contained", child)
    with runner.clone_session(tmp_path) as run:
        run("version")
    assert calls and "--list" in git_command(calls[0][0])
    for index, (argv, env, folders) in enumerate(calls):
        _assert_fixed_policy(argv, env, folders)
        assert {key: env[key] for key in selectors} == selectors
        assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
        if index:
            assert "http.https://example.invalid/.sslCAInfo=" in argv
            assert "credential.https://example.invalid.helper=" in argv
            assert "core.Named.excludesFile=" in argv
            assert "core..attributesFile=" + os.devnull in argv
            assert "core.https://example.invalid/.fsmonitor=false" in argv


def _personal_selectors(tmp_path, monkeypatch):
    selectors = {"GIT_CONFIG_NOSYSTEM": "0", "GIT_CONFIG_GLOBAL": str(tmp_path / "global.cfg"),
                 "GIT_CONFIG_SYSTEM": str(tmp_path / "system.cfg")}
    for key, value in selectors.items():
        monkeypatch.setenv(key, value)
    return selectors


_FOLDER_KEYS = ("core.hookspath", "init.templatedir")


def _folders_now(argv, env):
    # Whether each hooks/template folder a command names exists AT THE MOMENT the command
    # runs: the session's temporary folders are gone once it exits, so this is recorded here.
    named = [argv[i + 1].partition("=")[2] for i, arg in enumerate(argv[:-1])
             if arg == "-c" and argv[i + 1].partition("=")[0].lower() in _FOLDER_KEYS]
    named += [env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env.get("GIT_CONFIG_COUNT", "0")))
              if env[f"GIT_CONFIG_KEY_{i}"].lower() in _FOLDER_KEYS]
    return {value: bool(value) and Path(value).is_dir() for value in named}


def _assert_fixed_policy(argv, env, folders):
    # Independent literal inventory: apply the same assertions on the FIRST
    # discovery command and every later session/clone command, in argv AND env.
    fixed = {
        "core.fsmonitor": "false", "core.sshcommand": "", "core.gitproxy": "none",
        "core.alternaterefscommand": "", "core.excludesfile": "",
        "core.attributesfile": os.devnull, "core.askpass": "", "core.editor": ":", "core.pager": "",
        "credential.helper": "", "diff.external": "", "diff.orderfile": os.devnull,
        "commit.template": os.devnull, "commit.gpgsign": "false", "tag.gpgsign": "false",
        "tag.forcesignannotated": "false", "push.gpgsign": "false", "log.showsignature": "false",
        "merge.verifysignatures": "false", "gpg.program": "", "gpg.openpgp.program": "",
        "gpg.x509.program": "", "gpg.ssh.program": "", "gpg.ssh.defaultkeycommand": "",
        "gpg.ssh.allowedsignersfile": os.devnull, "gpg.ssh.revocationfile": os.devnull,
        "user.signingkey": "", "mailmap.file": os.devnull, "mailmap.blob": "", "blame.ignorerevsfile": "",
        "http.sslcainfo": "", "http.sslcapath": "", "http.sslcert": "", "http.sslkey": "",
        "http.cookiefile": "", "http.savecookies": "false", "http.proxysslcert": "",
        "http.proxysslkey": "", "http.proxysslcainfo": "", "http.pinnedpubkey": "", "http.proxy": "",
        "http.sslverify": "true", "http.followredirects": "false", "fsck.skiplist": os.devnull,
        "fetch.fsck.skiplist": os.devnull, "receive.fsck.skiplist": os.devnull, "gc.recentobjectshook": "",
        "gc.auto": "0", "maintenance.auto": "false", "sequence.editor": ":", "interactive.difffilter": "",
        "protocol.allow": "never", "fetch.bundleuri": "", "transfer.bundleuri": "false",
        "trace2.normaltarget": "0", "trace2.perftarget": "0", "trace2.eventtarget": "0",
    }
    command_pairs = dict((argv[i + 1].partition("=")[0].lower(), argv[i + 1].partition("=")[2])
                         for i, arg in enumerate(argv[:-1]) if arg == "-c")
    environment_pairs = {env[f"GIT_CONFIG_KEY_{i}"].lower(): env[f"GIT_CONFIG_VALUE_{i}"]
                         for i in range(int(env["GIT_CONFIG_COUNT"]))}
    for pairs in (command_pairs, environment_pairs):
        for key, value in fixed.items():
            assert pairs.get(key) == value, key
        for key in _FOLDER_KEYS:
            assert pairs.get(key) and folders.get(pairs[key]), key
    assert "--no-pager" in argv
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_NO_LAZY_FETCH"] == "1"


def test_small_patch_cap_does_not_refuse_policy_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "MAX_OUTPUT", 200)
    monkeypatch.setattr(runner, "find_program", lambda _: "git")
    monkeypatch.setattr(runner, "git_program", lambda: "git")
    patch = b"+ordinary patch line\n" * 100

    def child(argv, **kwargs):
        command = git_command(argv)
        if "--system" in command or "--global" in command:
            out = b""
        elif command[:2] == ("config", "--null") and "--list" in command:
            out = b"core.editor\0" * 30
        elif "--get-regexp" in command:
            out = b"g6.k1guard\0"
        elif command[0] == "diff":
            out = patch
        else:
            out = b""
        return SimpleNamespace(returncode=0, stdout=out, stderr=b"")

    monkeypatch.setattr(runner.component_process, "run_contained", child)
    with runner.session(tmp_path, tmp_path / ".git") as run:
        cut = run("diff", cut_ok=True)
        assert cut.cut and cut.out == patch[:200]
        with pytest.raises(runner.GitRefused) as caught:
            run("diff")
        assert caught.value.code == runner.TOO_LARGE


def test_policy_admission_output_is_still_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "MAX_ADMISSION_OUTPUT", 200, raising=False)
    monkeypatch.setattr(runner, "find_program", lambda _: "git")
    monkeypatch.setattr(runner, "git_program", lambda: "git")

    def child(argv, **kwargs):
        command = git_command(argv)
        out = b"" if "--system" in command or "--global" in command else b"core.editor\0" * 30
        return SimpleNamespace(returncode=0, stdout=out, stderr=b"")

    monkeypatch.setattr(runner.component_process, "run_contained", child)
    with pytest.raises(runner.GitRefused) as caught:
        with runner.session(tmp_path, tmp_path / ".git"):
            pytest.fail("oversized admission must not yield an operation runner")
    assert caught.value.code == runner.TOO_LARGE
