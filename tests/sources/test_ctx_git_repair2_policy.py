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
    ("core.excludesFile", os.devnull), ("core.attributesFile", os.devnull),
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
    monkeypatch.setattr(runner, "find_program", lambda _: "git")
    monkeypatch.setattr(runner, "git_program", lambda: "git")
    monkeypatch.setattr(runner, "refuse_gitlinks", lambda *args: None)

    def child(argv, *, env, input_bytes):
        calls.append((argv, env))
        command = git_command(argv)
        if "--system" in command or "--global" in command:
            out = b''
        elif "--list" in command:
            out = b'http.https://example.invalid/.sslCAInfo\0credential.https://example.invalid.helper\0'
        elif "--get-regexp" in command:
            out = b'g6.k1guard\0'
        else:
            out = b''
        return SimpleNamespace(returncode=0, stdout=out, stderr=b'')

    monkeypatch.setattr(runner.component_process, "run_contained", child)
    with runner.session(tmp_path, tmp_path / ".git") as run:
        run("status")
    assert calls
    for argv, env in calls:
        assert "mailmap.file=" + os.devnull in argv
        assert "blame.ignoreRevsFile=" in argv
        command = git_command(argv)
        if "--system" in command or "--global" in command:
            assert "--no-includes" in command
            assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
            continue
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert env["GIT_CONFIG_SYSTEM"] == os.devnull
    assert "http.https://example.invalid/.sslCAInfo=" in calls[-1][0]
    assert "credential.https://example.invalid.helper=" in calls[-1][0]


def test_inventory_detects_an_uncovered_documented_key():
    from g6.sources.ctx_git import _config_policy as policy

    assert policy.rule_for("newSection.someFile") is None


def test_clone_session_has_the_same_file_policy_before_its_first_command(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "find_program", lambda _: "git")
    monkeypatch.setattr(runner, "git_program", lambda: "git")
    monkeypatch.setattr(runner, "ssh_program", lambda: None)

    def child(argv, **kwargs):
        calls.append((argv, kwargs["env"]))
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    monkeypatch.setattr(runner.component_process, "run_contained", child)
    with runner.clone_session(tmp_path) as run:
        run("version")
    argv, env = calls[-1]
    assert "mailmap.file=" + os.devnull in argv
    assert any(arg.startswith("init.templateDir=") for arg in argv)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull


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
