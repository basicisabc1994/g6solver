# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 1: a task binding cut between its two replacements leaves the session stuck, worded as a sharing refusal.

Review 3's finding 9 at its class: another unreadable file. ``task.json`` is published as ``state.json`` is
(``cloud.write_retained``: content, then receipt), by ``_record``, by each prompt that adds to a task not yet started, by
``_again`` and by ``review_loop._asked``. Repair 3 made a run's unreadable *state* its own refusal (``G6_E_NO_CLEAN_REVIEW``,
passed over by the next prompt). The binding beside it still answers with the made-up ``G6_E_CLOUD_CONSENT_REQUIRED`` and
``cloud.WITHHELD`` ("Use g6_cloud_grant ..."), and nothing gets past it:

* the Stop hook now puts that in its envelope on every turn (``on_stop``'s new ``code_of`` handler), so the person is told
  to grant sharing, which changes nothing;
* every later prompt raises in ``on_prompt`` (``_task``) before it can arm a new loop, so the session never recovers;
* over MCP every call of the session is ``G6_E_CLOUD_CONSENT_REQUIRED`` with no way on named.

On Windows the tear needs no kill: ``os.replace`` onto a receipt another process has open (a Stop hook reading
``task.sha256`` while a prompt adds to the task, an indexer, a scanner) fails after the content was replaced.
"""
import os

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from tests._review_wtffu2b_3.rev3_world import TASK, ended_clean, hook_session, quiet_scouts
from tests._review_wtffu2b4.rev4_world import SESSION, retold, torn
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _prompt(name, repo, text="and now halve it"):
    try:
        return hook.on_prompt({"session_id": name, "cwd": str(repo), "prompt": text})
    except (ValueError, OSError) as exc:
        return exc


def test_control_a_torn_state_is_its_own_refusal_and_the_next_prompt_gets_past_it(disk_run, monkeypatch):
    """Review 3's finding 9, repaired: the same tear in the run's state."""
    repo, base, _ = disk_run
    quiet_scouts(monkeypatch)
    event, run = hook_session(monkeypatch, repo, "rev4-f1-control")
    ended_clean(repo, base, run, owed=False)
    torn(run / "state.json", (run / "state.json").read_bytes() + b" ")
    said = hook.on_stop(event)["systemMessage"]
    assert loop.NO_CLEAN_REVIEW in said and "g6_cloud_grant" not in said
    assert isinstance(_prompt("rev4-f1-control", repo), dict)
    assert hook._task(hook.folder("rev4-f1-control")).get("run") == 1


def test_stop_hook_does_not_word_a_torn_task_binding_as_a_sharing_refusal(disk_run, monkeypatch):
    repo, base, _ = disk_run
    quiet_scouts(monkeypatch)
    event, run = hook_session(monkeypatch, repo, "rev4-f1-stop")
    session = hook.folder("rev4-f1-stop")
    torn(session / "task.json", retold(session / "task.json"))
    said = hook.on_stop(event).get("systemMessage", "")
    assert said, "the turn ended with nothing said"
    assert cloud.REQUIRED not in said and "g6_cloud_grant" not in said, said


def test_a_prompt_gets_past_a_torn_task_binding(disk_run, monkeypatch):
    repo, base, _ = disk_run
    quiet_scouts(monkeypatch)
    event, run = hook_session(monkeypatch, repo, "rev4-f1-prompt")
    session = hook.folder("rev4-f1-prompt")
    torn(session / "task.json", retold(session / "task.json"))
    replies = [_prompt("rev4-f1-prompt", repo) for _ in range(3)]
    assert all(isinstance(reply, dict) for reply in replies), repr(replies)      # each prompt raises at this commit
    said = hook.on_stop(event).get("systemMessage", "")
    assert cloud.REQUIRED not in said, said                                      # and the stop after them is tested


def test_mcp_does_not_answer_a_torn_task_binding_as_consent_required(disk_run, monkeypatch):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    first = review_loop.step(str(repo), TASK, SESSION, base)
    assert first["ok"] is True
    torn(session / "task.json", retold(session / "task.json"))
    replies = [review_loop.step(str(repo), TASK, SESSION, base) for _ in range(2)]
    assert all(reply.get("code") != cloud.REQUIRED for reply in replies), replies[-1].get("error")
    assert all("g6_cloud_grant" not in (reply.get("error") or "") for reply in replies), replies[-1].get("error")


@pytest.mark.skipif(os.name != "nt", reason="a replacement onto an open file fails only on Windows")
def test_windows_a_reader_of_the_receipt_does_not_leave_the_task_binding_torn(disk_run, monkeypatch):
    """How the tear arises with no process killed: the receipt is open for reading, as ``_task`` opens it, while a prompt
    adds to the task."""
    repo, base, _ = disk_run
    quiet_scouts(monkeypatch)
    event, run = hook_session(monkeypatch, repo, "rev4-f1-reader")
    session = hook.folder("rev4-f1-reader")
    with open(session / "task.sha256", "rb") as reader:      # Path.read_text opens it with these sharing rights
        reader.read(1)
        first = _prompt("rev4-f1-reader", repo)
    after = _prompt("rev4-f1-reader", repo, "and then one more thing")
    assert isinstance(after, dict), f"while it was read: {first!r}; ever after: {after!r}"
    assert hook._task(session)["task"].startswith(TASK)
