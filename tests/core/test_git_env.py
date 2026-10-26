# SPDX-License-Identifier: Apache-2.0
"""g6.core.git_env: git in a working copy someone else may have controlled starts no program the copy names.

Unit checks of the environment and the parsers, then plants with real git, each beside a control showing plain git
runs the planted program: a promisor remote reached over ``ext::`` (each of the two transport guards holds alone), and a
nested repository's own filter (refused, and skipped by ``safe_git_args`` even where the refusal is bypassed). Offline.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from g6.core import git_env
from g6.core.git_env import (
    DRIVER_QUERY,
    UnsafeGitConfig,
    driver_settings,
    refuse_gitlinks,
    safe_git,
    safe_git_args,
    safe_git_env,
)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _settings(env: dict[str, str]) -> dict[str, str]:
    return {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}


def test_the_environment_keeps_nothing_of_gits_own_and_allows_no_transport(tmp_path, monkeypatch) -> None:
    for key in ("GIT_DIR", "GIT_CONFIG_PARAMETERS", "GIT_EXTERNAL_DIFF", "GIT_SSH_COMMAND", "GIT_ALLOW_PROTOCOL"):
        monkeypatch.setenv(key, "planted")
    env = safe_git_env(tmp_path, (("filter.x.clean", ""),), ceiling=tmp_path.parent)
    assert not {"GIT_DIR", "GIT_CONFIG_PARAMETERS", "GIT_EXTERNAL_DIFF", "GIT_SSH_COMMAND"} & set(env)
    assert env["GIT_NO_LAZY_FETCH"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ALLOW_PROTOCOL"] not in ("planted", "") and "_" in env["GIT_ALLOW_PROTOCOL"]  # names no transport
    assert env["GIT_CEILING_DIRECTORIES"] == str(tmp_path.parent) and env["TMP"] == str(tmp_path)
    settings = _settings(env)
    assert settings["core.hooksPath"] == str(tmp_path / "hooks") and not any((tmp_path / "hooks").iterdir())
    for key, value in (("core.fsmonitor", "false"), ("protocol.allow", "never"), ("commit.gpgSign", "false"),
                       ("submodule.recurse", "false"), ("diff.ignoreSubmodules", "all"), ("filter.x.clean", "")):
        assert settings[key] == value


def test_no_signing_program_and_no_editor_is_started(tmp_path) -> None:
    """A tag (``tag.gpgSign``, ``tag.forceSignAnnotated``) or a push (``push.gpgSign``) would start the signing program
    a repository's config names, as a commit would; an editor (``core.editor``, ``sequence.editor``) is a program too:
    ``:`` is git's own "no editor"."""
    settings = _settings(safe_git_env(tmp_path))
    for key, value in (("tag.gpgSign", "false"), ("tag.forceSignAnnotated", "false"), ("push.gpgSign", "false"),
                       ("core.editor", ":"), ("sequence.editor", ":")):
        assert settings.get(key) == value, key


def test_no_signature_check_starts_a_program(tmp_path) -> None:
    """Reading history can verify signatures (``log.showSignature``, ``merge.verifySignatures``), and git picks the
    verifier from the signature's own type: ``gpg.program``, ``gpg.x509.program`` or ``gpg.ssh.program``. An empty
    program is one git cannot start."""
    settings = _settings(safe_git_env(tmp_path))
    for key, value in (("log.showSignature", "false"), ("merge.verifySignatures", "false"), ("gpg.program", ""),
                       ("gpg.x509.program", ""), ("gpg.ssh.program", "")):
        assert settings.get(key) == value, key


@needs_git
@pytest.mark.skipif(os.name == "nt", reason="git starts a verifier without a shell; the planted one is a shell script")
def test_a_planted_signature_verifier_runs_under_plain_git_log_and_not_under_safe_git(tmp_path) -> None:
    repo = _repo(tmp_path / "repo")
    marker = tmp_path / "marker"
    verifier = tmp_path / "verify.sh"
    verifier.write_text(f"#!/bin/sh\necho ran >> '{marker}'\nexit 1\n", encoding="utf-8")
    verifier.chmod(0o755)
    tree = _git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    parent = _git(repo, "rev-parse", "HEAD").stdout.strip()
    body = (f"tree {tree}\nparent {parent}\nauthor t <t@example.test> 0 +0000\ncommitter t <t@example.test> 0 +0000\n"
            "gpgsig -----BEGIN SIGNED MESSAGE-----\n MIIB\n -----END SIGNED MESSAGE-----\n\nsigned\n")
    sha = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"], input=body,
                         capture_output=True, text=True, timeout=60, check=True).stdout.strip()
    _git(repo, "update-ref", "HEAD", sha)
    _git(repo, "config", "log.showSignature", "true")
    _git(repo, "config", "gpg.x509.program", str(verifier))
    _git(repo, "log", "-1")
    assert marker.exists()                     # survival control: plain git runs the planted verifier
    marker.unlink()
    with safe_git(repo) as env:
        _git(repo, "log", "-1", env=env)
        _git(repo, "log", "-1", "--show-signature", env=env)
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_driver_settings_empty_each_driver_and_refuse_an_unconfirmed_git() -> None:
    found = b"g6.k1guard\0filter.evil.clean\0diff.d.textconv\0"
    assert driver_settings(0, found) == (("filter.evil.clean", ""), ("filter.evil.required", "false"),
                                         ("diff.d.textconv", ""))
    for code, said in ((0, b"filter.evil.clean\0"), (1, found), (None, found)):
        with pytest.raises(UnsafeGitConfig, match="2.31"):
            driver_settings(code, said)


def test_refuse_gitlinks_reads_the_index_and_a_tree() -> None:
    index = b"100644 aa 0\tcalc.py\x00160000 bb 0\tvendor/lib\x00"
    tree = b"100644 blob aa\tcalc.py\x00160000 commit cc\tsub\x00"
    refuse_gitlinks(b"100644 aa 0\tcalc.py\x00", b"040000 tree dd\tdir\x00", b"")
    with pytest.raises(UnsafeGitConfig, match=r"submodule.*\(sub, vendor/lib\)"):
        refuse_gitlinks(index, tree)


def test_status_and_diff_never_enter_a_submodule() -> None:
    assert safe_git_args("status", "--porcelain") == ("status", "--ignore-submodules=all", "--porcelain")
    assert safe_git_args("diff", "--name-only", "HEAD")[:2] == ("diff", "--ignore-submodules=all")
    assert safe_git_args("ls-files", "-z") == ("ls-files", "-z") and safe_git_args() == ()
    assert DRIVER_QUERY[0] == "config"


# --- plants, with real git ------------------------------------------------------------------------------------------


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60, check=False,
                          env=env)


def _marker_program(tmp_path: Path) -> tuple[Path, list[str]]:
    marker, script = tmp_path / "marker", tmp_path / "plant.py"
    script.write_text(f"import pathlib, sys\npathlib.Path(r'{marker}').open('a').write(' '.join(sys.argv) + '\\n')\n"
                      "if sys.argv[-1] in ('clean', 'smudge'):\n    sys.stdout.write(sys.stdin.read())\n",
                      encoding="utf-8")
    return marker, [Path(sys.executable).as_posix(), script.as_posix()]


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false", "commit", "-qm", "c")
    return path


@pytest.fixture
def promisor(tmp_path) -> tuple[Path, Path]:
    """One blob deleted, and a planted promisor remote ``ext::<program>`` with ``protocol.ext.allow=always``."""
    repo = _repo(tmp_path / "repo")
    marker, argv = _marker_program(tmp_path)
    oid = _git(repo, "rev-parse", "HEAD:a.txt").stdout.strip()
    loose = repo / ".git" / "objects" / oid[:2] / oid[2:]
    loose.chmod(0o666)
    loose.unlink()
    url = "ext::" + " ".join(arg.replace("%", "%%").replace(" ", "% ") for arg in [*argv, "ext"])
    for key, value in (("extensions.partialClone", "evil"), ("remote.evil.promisor", "true"),
                       ("remote.evil.url", url), ("protocol.ext.allow", "always")):
        _git(repo, "config", key, value)
    return repo, marker


@needs_git
def test_a_planted_editor_runs_under_plain_git_and_not_under_safe_git(tmp_path) -> None:
    repo = _repo(tmp_path / "repo")
    marker, argv = _marker_program(tmp_path)
    _git(repo, "config", "core.editor", " ".join(argv))
    commit = ("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "--allow-empty")
    plain = {k: v for k, v in os.environ.items() if k not in ("GIT_EDITOR", "VISUAL", "EDITOR")}  # outrank core.editor
    _git(repo, *commit, env=plain)
    assert marker.exists()                     # survival control: plain git starts the planted editor
    marker.unlink()
    with safe_git(repo) as env:
        _git(repo, *commit, env=env)
    assert not marker.exists(), marker.read_text(encoding="utf-8")


@needs_git
def test_a_promisor_remote_runs_its_program_under_plain_git(promisor) -> None:
    """Survival control."""
    repo, marker = promisor
    _git(repo, "cat-file", "-p", "HEAD:a.txt")
    assert marker.exists()


@needs_git
@pytest.mark.parametrize("dropped", [None, "GIT_ALLOW_PROTOCOL", "GIT_NO_LAZY_FETCH"])
def test_no_missing_object_is_fetched_and_each_guard_holds_alone(promisor, dropped: str | None) -> None:
    repo, marker = promisor
    with safe_git(repo) as env:
        env.pop(dropped or "", None)
        done = _git(repo, "cat-file", "-p", "HEAD:a.txt", env=env)
    assert done.returncode != 0 and not marker.exists(), marker.read_text(encoding="utf-8")


@pytest.fixture
def nested(tmp_path) -> tuple[Path, Path]:
    """A nested repository ``sub/`` added as a gitlink, its own config naming a filter, one of its files stat-dirty."""
    repo = _repo(tmp_path / "repo")
    marker, argv = _marker_program(tmp_path)
    sub = _repo(repo / "sub")
    _git(repo, "add", "sub")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false", "commit", "-qm", "s")
    (sub / ".gitattributes").write_text("* filter=evil2\n", encoding="utf-8")
    _git(sub, "config", "filter.evil2.clean", " ".join(f'"{arg}"' for arg in argv) + " clean")
    later = time.time() + 5
    os.utime(sub / "a.txt", (later, later))
    return repo, marker


@needs_git
def test_a_nested_repositorys_filter_runs_under_plain_git(nested) -> None:
    """Survival control."""
    repo, marker = nested
    _git(repo, "status", "--porcelain")
    assert marker.exists()


@needs_git
def test_a_copy_holding_a_gitlink_is_refused(nested) -> None:
    repo, marker = nested
    with pytest.raises(UnsafeGitConfig, match=r"submodule.*\(sub\)"):
        with safe_git(repo):
            pass
    assert not marker.exists()


@needs_git
def test_status_and_diff_skip_the_submodule_even_without_the_refusal(nested, tmp_path) -> None:
    repo, marker = nested
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env = safe_git_env(scratch)   # the refusal bypassed: the second layer on its own
    for args in (("status", "--porcelain"), ("diff", "--name-only", "HEAD")):
        assert _git(repo, *safe_git_args(*args), env=env).returncode == 0
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_the_code_is_stable() -> None:
    assert git_env.SUBMODULE_REFUSED == "G6_E_GIT_SUBMODULE_REFUSED"
