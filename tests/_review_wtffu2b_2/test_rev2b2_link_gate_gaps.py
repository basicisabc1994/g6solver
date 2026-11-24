# SPDX-License-Identifier: Apache-2.0
"""Review 2 of wire-typed-fu-2 (tip 634e31b8): what the repaired link gate leaves open.

The repair checks the run folder and its ancestors (``bookkeeping_path``) and its test lists two components, ``run`` and
``parent``. The names G6 writes INSIDE a run are not checked: the new ``.write-version`` token, the retained spares
(``state.json.new``, ``task.json.new``), ``report.md``, and the generated folders ``rounds`` and ``sets``. Each is written,
or removed, through a link, so bytes outside G6_HOME change. ``plain_file`` (the project's own check) counts a hard link
as a link. The owing marker is still made through ``state_path``: with a linked ``owing`` the step now runs, sends, and
then answers that it did nothing.
"""
import os
import subprocess
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.core.llm import LLMError
from g6.core.model_calls import note_model_call
from g6.harness import completion_hook, completion_loop as loop
from g6.mcp import review_loop
from g6.workspace._envelope import WorkspaceRefused

TASK = "double returns twice its argument"
MINE = b"the person's own file\n"


def _snapshot(folder):
    return {str(path.relative_to(folder)): path.read_bytes() if path.is_file() else None
            for path in sorted(folder.rglob("*"))}


def _junction(link, target):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _unjunction(link):
    if link.is_symlink():
        link.unlink()
    elif os.path.lexists(link):
        os.rmdir(link)


def _file_link(kind, link, target):
    if kind == "hard":
        os.link(target, link)
        return
    try:
        link.symlink_to(target)
    except OSError as error:
        if getattr(error, "winerror", None) != 1314:
            raise
        pytest.skip("symlinks need a privilege this account does not hold")


def _attempt(act):
    """A refusal is a fine answer; so is a file system error. Only what is outside afterwards is judged."""
    try:
        return act()
    except (WorkspaceRefused, OSError, ValueError):
        return None


@pytest.fixture
def world(tmp_path, monkeypatch):
    home, outside = tmp_path / "home", tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    run = home / "completion_loop" / ("b" * 32)
    run.mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    code = repo / "mod.py"
    code.write_text("def double(x):\n    return x + 2\n", encoding="utf-8")

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                               *args], check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("add", "mod.py")
    git("commit", "-qm", "fixture base")
    base = git("rev-parse", "HEAD")
    code.write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
    # No confined run and no model here. The review loop is chosen, and the patch review at the loop's end is stood in.
    monkeypatch.setattr(loop.proposer, "boundary", lambda root: "")
    monkeypatch.setattr(loop, "_scouts_chosen", lambda: False)
    monkeypatch.setattr(loop, "review_patch", lambda *a, **k: {"assessment": {"report": "No review ran."}})

    def unreachable(*args, **kwargs):
        raise LLMError("the reviewer cannot be reached in this test")

    return SimpleNamespace(home=home, outside=outside, run=run, repo=repo, base=base,
                           critic=SimpleNamespace(complete=unreachable))


# --- controls -----------------------------------------------------------------------------------------------------------

def test_control_plain_run_saves_and_loads(world):
    loop._save(world.run, {"attempted": 0}, sources=())
    assert loop._load(world.run)["attempted"] == 0
    assert _snapshot(world.outside) == {}


def test_control_linked_run_folder_is_refused(world):
    """The component the repair's own test lists: refused, and nothing outside changes."""
    target = world.outside / "run"
    target.mkdir()
    link = world.home / "completion_loop" / ("c" * 32)
    _junction(link, target)
    try:
        before = _snapshot(world.outside)
        with pytest.raises(WorkspaceRefused) as refused:
            loop._save(link, {"attempted": 0}, sources=())
        assert refused.value.code == "G6_E_WORKSPACE_OUTSIDE_ROOT"
        assert _snapshot(world.outside) == before
    finally:
        _unjunction(link)


def test_control_review_step_keeps_its_patch_inside_the_run(world):
    w = world
    done = loop.advance(w.repo, TASK, w.base, w.run, critic=w.critic)
    assert done.action == loop.STOP and done.ending == "untested"
    assert (w.run / "rounds" / "round-1.patch").is_file()
    assert _snapshot(w.outside) == {}


# --- the token and the retained files of a run --------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["hard", "symlink"])
@pytest.mark.parametrize("name", [".write-version", "state.json.new", "state.sha256.new"])
def test_save_does_not_write_through_a_linked_run_file(world, kind, name):
    """FAILS at the tip: ``_save`` writes the token (and the spares) with ``write_bytes``, which follows the link, so the
    person's file outside G6_HOME now holds G6's bytes."""
    victim = world.outside / "victim.txt"
    victim.write_bytes(MINE)
    _file_link(kind, world.run / name, victim)
    _attempt(lambda: loop._save(world.run, {"attempted": 0}, sources=()))
    assert victim.read_bytes() == MINE


@pytest.mark.parametrize("kind", ["hard", "symlink"])
def test_finish_does_not_write_the_report_through_a_link(world, kind):
    """FAILS at the tip: ``_finish`` writes ``report.md`` through the link."""
    victim = world.outside / "victim.txt"
    victim.write_bytes(MINE)
    _file_link(kind, world.run / "report.md", victim)

    def act():
        with loop._writing(world.run):
            loop._finish(world.run, {"attempted": 0}, "a report", loop.NO_CLEAN_REVIEW, "untested")

    _attempt(act)
    assert victim.read_bytes() == MINE


@pytest.mark.parametrize("kind", ["hard", "symlink"])
def test_mcp_step_does_not_write_the_task_through_a_link(world, kind):
    """FAILS at the tip: the MCP step's first call writes ``task.json.new`` through the link (the repository's path, the
    task's words and the base commit land in the person's file)."""
    victim = world.outside / "victim.txt"
    victim.write_bytes(MINE)
    folder = completion_hook.folder("mcp:rev2b2-task")
    folder.mkdir(parents=True)
    _file_link(kind, folder / "task.json.new", victim)
    _attempt(lambda: review_loop.step(str(world.repo), TASK, "rev2b2-task", world.base))
    assert victim.read_bytes() == MINE


# --- the generated folders of a run, through the real public entry -----------------------------------------------------

def test_review_step_does_not_write_the_patch_through_a_linked_rounds_folder(world):
    """FAILS at the tip: ``review_round`` keeps ``rounds/round-1.patch`` through the junction, so the patch lands outside."""
    w = world
    link = w.run / "rounds"
    _junction(link, w.outside)
    try:
        done = _attempt(lambda: loop.advance(w.repo, TASK, w.base, w.run, critic=w.critic))
        assert _snapshot(w.outside) == {}, (done, sorted(_snapshot(w.outside)))
    finally:
        _unjunction(link)


def test_review_step_does_not_remove_a_file_through_a_linked_sets_folder(world):
    """FAILS at the tip: ``review_round`` unlinks ``sets/critic/<its test>`` through the junction: a file outside is gone."""
    w = world
    victim = w.outside / "critic" / loop.critic_file(1)
    victim.parent.mkdir()
    victim.write_bytes(MINE)
    link = w.run / "sets"
    _junction(link, w.outside)
    try:
        _attempt(lambda: loop.advance(w.repo, TASK, w.base, w.run, critic=w.critic))
        assert victim.is_file(), "a file outside G6_HOME was removed through the run's linked sets folder"
    finally:
        _unjunction(link)


# --- the owing marker ---------------------------------------------------------------------------------------------------

def _one_send_then_refused(sent):
    """As tests/harness/test_review_refused_after_send._refused_after(1, REQUIRED): one counted request, then a refusal."""
    def review(change, task, critic, shipped, session, reviews, deadline):
        note_model_call()
        sent.append(1)
        raise LLMError(f"{cloud.REQUIRED}: the provider's sharing refused the next one")
    return review


def test_control_refused_step_ends_the_loop_once_with_a_plain_owing_folder(world, monkeypatch):
    w, sent = world, []
    monkeypatch.setattr(loop, "review_round", _one_send_then_refused(sent))
    critic = SimpleNamespace(complete=lambda *a, **k: "none")
    first = loop.advance(w.repo, TASK, w.base, w.run, critic=critic)
    second = loop.advance(w.repo, TASK, w.base, w.run, critic=critic)
    assert (first.action, first.code) == (loop.STOP, cloud.REQUIRED)
    assert second.note == loop.ALREADY_ENDED and len(sent) == 1
    assert loop._load(w.run)["attempted"] == 1


def test_linked_owing_folder_is_not_answered_as_busy_after_a_send(world, monkeypatch):
    """FAILS at the tip: ``settle`` now passes a linked ``owing`` over, so the step runs and sends; ``_owe_row`` then makes
    its mark through ``state_path``, whose refusal is no ``OSError``, and the step lock turns it into G6_E_LOOP_BUSY:
    "this one did nothing", with a request counted, nothing ended, and every repeat sending again. (At the base the
    call failed in ``settle`` before anything was sent.)"""
    w, sent = world, []
    monkeypatch.setattr(loop, "review_round", _one_send_then_refused(sent))
    critic = SimpleNamespace(complete=lambda *a, **k: "none")
    link = w.home / "completion_loop" / "owing"
    _junction(link, w.outside)
    try:
        first = loop.advance(w.repo, TASK, w.base, w.run, critic=critic)
        second = loop.advance(w.repo, TASK, w.base, w.run, critic=critic)
        assert _snapshot(w.outside) == {}
    finally:
        _unjunction(link)
    said = [(one.action, one.code) for one in (first, second)]
    assert (loop.WAIT, loop.BUSY) not in said or not sent, (said, len(sent), loop._load(w.run).get("attempted"))
    assert len(sent) <= 1, (said, len(sent))
