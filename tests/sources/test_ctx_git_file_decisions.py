# SPDX-License-Identifier: Apache-2.0
"""Repository rules stay native; configured external policy cannot hide review files."""
import os
import subprocess
from pathlib import Path

import pytest

from g6.sources.ctx_git import _config_policy as policy, config as cfg, ops, runner
from tests.sources import test_ctx_git_person_config as personal
from tests.sources.test_ctx_git_person_config import call, git, repository

person = personal.person


@pytest.mark.parametrize("key,value", [
    ("Core.ExcludesFile", ""), ("core..excludesFile", ""),
    ("core.named.excludesFile", ""),
    ("core.https://example.invalid/.excludesFile", ""),
    ("CORE.AttributesFile", os.devnull), ("core..attributesFile", os.devnull),
    ("core.named.attributesFile", os.devnull),
    ("core.https://example.invalid/.attributesFile", os.devnull),
    ("http.https://example.invalid/.sslCAInfo", ""),
    ("http..sslCAInfo", ""), ("http.https://example.invalid/.sslCAPath", ""),
    ("core.named.fsmonitor", "false"), ("core.named.hooksPath", "empty-hooks"),
    ("core.named.pager", ""), ("diff.named.external", ""),
    ("blame.named.ignoreRevsFile", ""), ("mailmap.named.file", os.devnull),
    ("fetch.named.fsck.skipList", os.devnull),
    ("gpg.ssh.named.allowedSignersFile", os.devnull),
    ("protocol.https://example.invalid/.allow", "never"),
    ("credential.https://example.invalid/.helper", ""),
    ("filter.https://example.invalid/.process", ""),
    ("diff.https://example.invalid/.textconv", ""),
    ("diff..textconv", ""), ("filter..process", ""),
    ("credential..helper", ""), ("gpg..program", ""),
    ("protocol..allow", "never"),
])
def test_denied_keys_keep_policy_in_every_section_spelling(key, value):
    # Omitting generic subsection matching must expose a concrete missing override.
    pairs = dict(policy.settings((key,), hooks="empty-hooks", template="empty-template"))
    assert pairs.get(key) == value


@pytest.mark.parametrize("key", ["filter.name=value.process", "diff.name\nvalue.textconv"])
def test_unencodable_denied_key_is_refused_instead_of_becoming_another_override(key):
    with pytest.raises(ValueError, match="spelling"):
        policy.settings((key,), hooks="empty-hooks", template="empty-template")


def test_blame_discards_configured_file_names_before_clearing_revisions(monkeypatch):
    # Empty --ignore-revs-file clears revision IDs AFTER earlier files were
    # opened. Negating the list option must first discard the file names.
    monkeypatch.setattr(cfg, "inside", lambda repo, raw: "a.txt")

    def run(*args, **kwargs):
        assert args == ("blame", "--no-ignore-revs-file", "--ignore-revs-file=",
                        "--line-porcelain", "HEAD", "--", "a.txt")
        assert kwargs == {"cut_ok": True}
        return runner.Done(0, b"", b"")

    result = ops.blame(run, Path("fixture-repo"), ops.Call(op="blame", file="a.txt"))
    assert result.reply["code"] == ops.READ


@pytest.mark.parametrize("name,content", [
    ("exclude", b"hidden.txt\n"), ("attributes", b"a.txt -diff\n"),
])
def test_repository_info_rules_keep_native_visibility(tmp_path, person, name, content):
    repo, config = repository(tmp_path)
    (repo / "a.txt").write_bytes(b"original content\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "fixture")
    (repo / "hidden.txt").write_bytes(b"review content\n")
    (repo / "visible.txt").write_bytes(b"visible content\n")
    (repo / "a.txt").write_bytes(b"review content\n")
    (repo / ".git/info" / name).write_bytes(content)
    assert cfg.repository(str(repo), config) == (repo, repo / ".git")
    result = call(repo, config, op="diff" if name == "attributes" else "status")
    assert result["code"] == ops.READ
    if name == "exclude":
        assert git(repo, "ls-files", "--others", "--exclude-standard").splitlines() == [b"visible.txt"]
        assert result["untracked"] == ["visible.txt"]
        assert result["modified"] == ["a.txt"]
    else:
        native = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "--")
        assert b"Binary files" in native and b"+review content" not in native
        assert result["patch"] == native.decode("utf-8")
        assert result["files"] == [{"status": "M", "path": "a.txt"}]


@pytest.mark.parametrize("name", ["exclude", "attributes"])
def test_comment_only_info_files_do_not_refuse_a_repository(tmp_path, person, name):
    repo, config = repository(tmp_path)
    (repo / ".git/info" / name).write_bytes(b"# ordinary git template comment\n\n")
    assert cfg.repository(str(repo), config) == (repo, repo / ".git")


def test_tracked_ignore_and_attributes_still_control_the_tree(tmp_path, person):
    repo, config = repository(tmp_path)
    (repo / ".gitignore").write_bytes(b"*.log\n")
    (repo / ".gitattributes").write_bytes(b"*.txt text eol=lf\n")
    (repo / "a.txt").write_bytes(b"one\r\n")
    git(repo, "-c", "core.autocrlf=false", "add", ".")
    git(repo, "commit", "-qm", "fixture")
    (repo / "debug.log").write_bytes(b"noise\n")
    assert call(repo, config)["clean"] is True
    (repo / "a.txt").write_bytes(b"visible change\r\n")
    result = call(repo, config, op="diff")
    assert result["code"] == "G6_E_GIT_READ"
    assert "+visible change" in result["patch"]


def test_invalid_native_tree_value_refuses_instead_of_falling_back(tmp_path, person):
    repo, _ = repository(tmp_path)
    person[1].write_bytes(b"[core]\nautocrlf = true\n")
    person[0].write_bytes(b"[core]\nautocrlf = false\nautocrlf = unsafe-value\n")
    with pytest.raises(cfg.GitRefused) as caught:
        with runner.session(repo, repo / ".git") as run:
            run("status", "--porcelain")
    assert caught.value.code == runner.FAILED


def test_native_boolean_last_value_and_personal_include_stay_native(tmp_path, person):
    repo, _ = repository(tmp_path)
    included = person[0].parent / "read.cfg"
    included.write_bytes(b"[core]\nsymlinks\nautocrlf = true\nautocrlf = input\n")
    person[0].write_bytes(f"[include]\npath = {included.as_posix()}\n".encode())
    # Git init can write this local value on Windows. Make the precedence
    # conflict explicit on every platform instead of assuming global wins.
    git(repo, "config", "core.symlinks", "false")
    assert git(repo, "config", "--type=bool", "--get", "core.symlinks").strip() == b"false"
    # Explicit --global queries default to skipping includes. Read the same
    # personal include as native repository queries and G6's discovery query.
    assert git(repo, "config", "--global", "--includes", "--type=bool", "--get", "core.symlinks").strip() == b"true"
    for context, want in ((runner.session(repo, repo / ".git"), "local\tfalse"),
                          (runner.clone_session(tmp_path), "global\ttrue")):
        with context as run:
            assert run("config", "--show-scope", "--type=bool", "--get", "core.symlinks").text.strip() == want
            assert run("config", "--get", "core.autocrlf").text.strip() == "input"
            assert run("config", "--get", "include.path").text.strip() == included.as_posix()
            assert run("config", "--get", "core.fsmonitor").text.strip() == "false"


@pytest.mark.parametrize("key,value", [
    ("autocrlf", "true"), ("autocrlf", "false"), ("autocrlf", "input"),
    ("autocrlf", "planted-command"), ("autocrlf", "warn"),
    ("eol", "lf"), ("eol", "crlf"), ("eol", "native"), ("eol", "true"),
    ("safecrlf", "true"), ("safecrlf", "false"), ("safecrlf", "warn"), ("safecrlf", "input"),
    ("symlinks", "true"), ("symlinks", "false"), ("symlinks", "YES"), ("symlinks", "off"),
    ("ignorecase", "on"), ("ignorecase", "0"), ("ignorecase", "1"), ("ignorecase", "warn"),
    ("symlinks", '""'),
])
def test_native_content_values_match_git_without_becoming_command_settings(tmp_path, person, key, value):
    repo, config = repository(tmp_path)
    (repo / "visible.txt").write_bytes(b"ordinary content\n")
    person[0].write_bytes(f"[core]\n{key} = {value}\n".encode())
    native = subprocess.run([personal.GIT, "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, timeout=30, check=False)
    result = call(repo, config)
    if native.returncode:
        assert result["code"] == runner.FAILED
    else:
        assert result["code"] == ops.READ
        assert result["untracked"] == ["visible.txt"]
        with runner.session(repo, repo / ".git") as run:
            # init may probe symlinks/ignorecase into local config. Preserve
            # exactly the value AND scope native Git selected for this repo.
            native_setting = git(repo, "config", "--show-scope", "--get", f"core.{key}")
            assert run("config", "--show-scope", "--get", f"core.{key}").out == native_setting
            assert run("config", "--get", "core.fsmonitor").text.strip() == "false"
    # Clone has no local config: every raw personal spelling must remain
    # global there, even when init supplied a local value in the repo above.
    native_global = git(repo, "config", "--global", "--show-scope", "--get", f"core.{key}")
    assert native_global.startswith(b"global\t")
    with runner.clone_session(tmp_path) as run:
        assert run("config", "--show-scope", "--get", f"core.{key}").out == native_global
        assert run("config", "--get", "core.fsmonitor").text.strip() == "false"
