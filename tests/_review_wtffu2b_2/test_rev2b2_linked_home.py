# SPDX-License-Identifier: Apache-2.0
"""Review 2 of wire-typed-fu-2 (tip 634e31b8): a G6_HOME that is reached through a link.

At the base (a3d8927a) ``completion_hook.folder`` was ``state_path("completion_loop", name)``: ``state_path`` resolves the
home first, so a home behind a junction or a symbolic link (a profile kept on another drive, ``/home`` -> ``/var/home``,
macOS ``/var`` -> ``/private/var``) is a home like any other and only links BELOW it are refused. At the tip the folder is
``bookkeeping_path(g6_home() / "completion_loop", name)``: the gate lstat-checks every ancestor up to the drive root, so
the home's own link (or any link above it) refuses every completion-loop entry.
"""
import hashlib
import os
import subprocess

import pytest

from g6.core.storage import state_path
from g6.harness import completion_hook, completion_loop as loop
from g6.mcp import review_loop
from g6.workspace._envelope import WorkspaceRefused


def _link(link, target):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _unlink(link):
    if link.is_symlink():
        link.unlink()
    else:
        os.rmdir(link)


@pytest.fixture
def linked_home(tmp_path, monkeypatch):
    real, home = tmp_path / "real-home", tmp_path / "home"
    real.mkdir()
    _link(home, real)
    monkeypatch.setenv("G6_HOME", str(home))
    try:
        yield home, real
    finally:
        _unlink(home)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def double(x):\n    return x + 2\n", encoding="utf-8")

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                               *args], check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("add", "mod.py")
    git("commit", "-qm", "fixture base")
    return repo, git("rev-parse", "HEAD")


def test_control_state_path_accepts_a_linked_home(linked_home):
    """Control: the rest of G6's state (and the base's run folder) lives happily under a linked home."""
    home, real = linked_home
    path = state_path("completion_loop", "a" * 32)
    assert path == real.resolve() / "completion_loop" / ("a" * 32)


def test_control_plain_home_folder_is_given(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "plain"))
    name = hashlib.sha256(b"mcp:plain").hexdigest()[:32]
    assert completion_hook.folder("mcp:plain") == (tmp_path / "plain").resolve() / "completion_loop" / name


def test_run_folder_under_a_linked_home_is_not_refused(linked_home):
    """FAILS at the tip: WorkspaceRefused G6_E_WORKSPACE_OUTSIDE_ROOT ('home' is a linked path component)."""
    home, real = linked_home
    name = hashlib.sha256(b"mcp:linked").hexdigest()[:32]
    try:
        folder = completion_hook.folder("mcp:linked")
    except WorkspaceRefused as exc:
        pytest.fail(f"the run folder of a linked home is refused: {exc}")
    assert folder == real.resolve() / "completion_loop" / name


def test_mcp_step_runs_under_a_linked_home(linked_home, repo):
    """FAILS at the tip: the MCP step answers G6_E_WORKSPACE_OUTSIDE_ROOT, action wait, for a home the base served.

    No Python changed against the base commit, so a served step is the NO_PYTHON stop and nothing is spent."""
    root, base = repo
    reply = review_loop.step(str(root), "double returns twice its argument", "linked", base)
    assert reply.get("code") != "G6_E_WORKSPACE_OUTSIDE_ROOT", reply
    assert reply.get("code") == loop.NO_PYTHON, reply


def test_control_mcp_step_runs_under_a_plain_home(tmp_path, monkeypatch, repo):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "plain"))
    root, base = repo
    reply = review_loop.step(str(root), "double returns twice its argument", "plain", base)
    assert reply.get("code") == loop.NO_PYTHON, reply


def test_advance_at_the_base_folder_of_a_linked_home_still_works(linked_home, repo):
    """Control: the folder the base derived (the resolved one) is still served at the tip, so only the derivation moved."""
    root, base = repo
    session = state_path("completion_loop", hashlib.sha256(b"mcp:linked").hexdigest()[:32])
    done = loop.advance(root, "double returns twice its argument", base, session)
    assert (done.action, done.code) == (loop.STOP, loop.NO_PYTHON)
