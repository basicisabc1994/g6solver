# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 8: two path refusals leave the Stop hook with no envelope.

The commit says the Stop hook's reply "keeps its envelope (systemMessage) on every path". Two path refusals leave
``on_stop`` as a raised ``WorkspaceRefused`` (``main`` then prints one line to stderr and exits 1): the marker of a turn
with no loop, behind a second name, and a linked run folder at the hook's first line.
"""
import hashlib
import os

import pytest

from g6.harness import completion_hook as hook, completion_loop as loop
from g6.harness.workflow import policy
from g6.workspace._envelope import WorkspaceRefused
from tests._review_wtffu2b_3.rev3_world import hard_link, hook_session
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _answer(event):
    """The hook's reply, or the refusal it raised in place of one (so each case fails at its own assertion)."""
    try:
        return hook.on_stop(event)
    except WorkspaceRefused as exc:
        return exc


def _no_loop_turn(monkeypatch):
    step = loop.Step(loop.STOP, code=loop.NO_PYTHON, note=loop.NOTHING_CHANGED, ending="untested")
    monkeypatch.setattr(loop, "advance", lambda *a, **kw: step)


def test_control_a_turn_with_no_loop_says_so_and_is_marked(disk_run, monkeypatch):
    repo, base, _ = disk_run
    event, run = hook_session(monkeypatch, repo, "rev3-f8-control")
    _no_loop_turn(monkeypatch)
    assert hook.on_stop(event) == {"systemMessage": loop.NOTHING_CHANGED}
    assert (run / hook.TURN_ENDED).is_file()


def test_stop_hook_keeps_its_envelope_when_the_turn_marker_is_refused(disk_run, monkeypatch, tmp_path):
    repo, base, _ = disk_run
    event, run = hook_session(monkeypatch, repo, "rev3-f8")
    _no_loop_turn(monkeypatch)
    victim = hard_link(tmp_path, run / hook.TURN_ENDED)
    try:
        reply = _answer(event)
    finally:
        (run / hook.TURN_ENDED).unlink()
    assert victim.read_bytes() == b"keep"
    assert isinstance(reply, dict) and "systemMessage" in reply, repr(reply)


def test_stop_hook_keeps_its_envelope_when_the_run_folder_is_linked(disk_run, monkeypatch, tmp_path):
    repo, base, session = disk_run
    monkeypatch.setattr(policy, "enabled", lambda: (False, ""))
    name = hashlib.sha256(b"rev3-f8-linked").hexdigest()[:32]
    outside, link = tmp_path / "outside", session.parent / name
    outside.mkdir()
    session.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"no symbolic links here: {exc}")
    try:
        reply = _answer({"session_id": "rev3-f8-linked", "cwd": str(repo)})
    finally:
        if link.is_symlink():
            link.unlink()
        else:
            os.rmdir(link)
    assert list(outside.iterdir()) == []
    assert isinstance(reply, dict) and "systemMessage" in reply, repr(reply)
