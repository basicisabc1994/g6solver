# SPDX-License-Identifier: Apache-2.0
"""Review 2 of wire-typed-fu-2 (tip 634e31b8): reply paths around a late worker's send, and the refusal replies.

The change words the MCP reply from the retained state read at reply time under the writer lock. Two reply paths are
left behind: the Stop hook still words its reply from the step's earlier text, and the MCP reply read itself gives up
with "this one did nothing" when the late worker's reservation holds the lock at that instant. The outside-root refusal
the repair carries through MCP is lost when the workflow record is on, and is labelled ``wait``. A reply with a code now
carries ``requests_sent: null`` where the key used to be absent.
"""
import os
import subprocess
import threading

import pytest

from g6.core import cloud_consent as cloud
from g6.core.llm import FakeLLM, LLMError
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop, workflow
from g6.workspace._envelope import OUTSIDE_ROOT

TASK = "double returns twice its argument"


def _repo(tmp_path, *, changed):
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
    if changed:
        code.write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
    return repo, base


@pytest.fixture
def run_world(tmp_path, key_file, monkeypatch):
    """As tests/harness/test_completion_review2_on_disk.disk_run: only host execution and scout output are substituted."""
    key_file("G6_LOOP_MODE=scouts\nG6_LOOP_SCOUTS=before_fix\n")
    repo, base = _repo(tmp_path, changed=True)
    monkeypatch.setattr(loop.proposer, "boundary", lambda root: "")
    monkeypatch.setattr(loop, "scout_host", lambda: object())
    monkeypatch.setattr(loop, "critic_host", lambda: None)
    monkeypatch.setattr(loop, "review_patch", lambda *a, **k: {"assessment": {"report": "No probe set ran."}})
    return repo, base


class LateWorker:
    """A scout whose host leaves one worker behind and is then refused sharing; the worker sends when released."""

    def __init__(self, monkeypatch):
        self.release, self.received, self.threads, self.errors = threading.Event(), [], [], []
        monkeypatch.setattr(loop, "_scout", self.scout)

    def _work(self):
        try:
            assert self.release.wait(20), "late worker never released"
            FakeLLM(lambda *args: self.received.append(args) or "reply").complete("system", "late request")
        except BaseException as exc:    # noqa: BLE001 - shown by the test
            self.errors.append(exc)

    def scout(self, *args):
        thread = threading.Thread(target=self._work)
        self.threads.append(thread)
        thread.start()      # carries the run's own reservation observer
        raise cloud._refusal(LLMError(f"{cloud.REQUIRED}: the next request was refused"))

    def join(self):
        self.release.set()
        for thread in self.threads:
            thread.join(20)
            assert not thread.is_alive()


# --- controls -----------------------------------------------------------------------------------------------------------

def test_control_mcp_reply_after_a_late_send_counts_it(run_world, monkeypatch):
    repo, base = run_world
    late, real_advance = LateWorker(monkeypatch), loop.advance

    def advance(*args, **kwargs):
        result = real_advance(*args, **kwargs)
        late.join()     # the step's lock is released; its late worker sends before the reply is worded
        return result

    monkeypatch.setattr(loop, "advance", advance)
    reply = review_loop.step(str(repo), TASK, "rev2b2-control", base)
    assert not late.errors, late.errors
    assert reply["code"] == cloud.REQUIRED and reply["requests_sent"] == len(late.received) == 1
    assert "not sent" not in reply["report"].lower()


def test_control_stop_hook_with_no_late_send_says_not_sent(run_world, monkeypatch):
    repo, base = run_world
    late = LateWorker(monkeypatch)
    try:
        session = hook.folder("rev2b2-hook-control")
        assert hook._record(session, str(repo), TASK) is not None
        said = hook.on_stop({"session_id": "rev2b2-hook-control", "cwd": str(repo), "last_assistant_message": "done"})
        assert said["systemMessage"].startswith("The review was not sent.")
        assert loop._load(session)["attempted"] == 0
    finally:
        late.join()


# --- the Stop hook's reply -----------------------------------------------------------------------------------------------

def test_stop_hook_reply_after_a_late_send_does_not_say_not_sent(run_world, monkeypatch):
    """FAILS at the tip: the hook shows ``step.text`` ("The review was not sent. ...") while the run's retained state counts
    the request that went out before the reply was made. The MCP reply for the same schedule is the control above."""
    repo, base = run_world
    late, real_advance = LateWorker(monkeypatch), loop.advance

    def advance(*args, **kwargs):
        result = real_advance(*args, **kwargs)
        late.join()
        return result

    monkeypatch.setattr(loop, "advance", advance)
    session = hook.folder("rev2b2-hook")
    assert hook._record(session, str(repo), TASK) is not None
    said = hook.on_stop({"session_id": "rev2b2-hook", "cwd": str(repo), "last_assistant_message": "done"})
    assert not late.errors, late.errors
    assert len(late.received) == loop._load(session)["attempted"] == 1
    assert "not sent" not in said["systemMessage"].lower(), said["systemMessage"]


# --- the MCP reply read against a reservation in flight -------------------------------------------------------------------

def test_mcp_reply_is_not_did_nothing_while_a_late_reservation_is_in_flight(run_world, monkeypatch):
    """FAILS at the tip: the reply read takes the writer lock with no wait. A late worker's reservation holds it for its
    load, write and three flushes; a reply read at that instant says G6_E_LOOP_BUSY, "this one did nothing", action wait,
    with no requests_sent, for a step that ran, ended the loop and whose run has a request going out."""
    repo, base = run_world
    late, real_advance, real_sync = LateWorker(monkeypatch), loop.advance, loop._sync_state
    in_flight, finish = threading.Event(), threading.Event()

    def sync(session):
        real_sync(session)
        if threading.current_thread() in late.threads:      # the reservation: lock held, the request not yet let go
            in_flight.set()
            assert finish.wait(20), "the reply never came"

    def advance(*args, **kwargs):
        result = real_advance(*args, **kwargs)
        late.release.set()
        assert in_flight.wait(20), "the late reservation never started"
        return result

    monkeypatch.setattr(loop, "_sync_state", sync)
    monkeypatch.setattr(loop, "advance", advance)
    try:
        reply = review_loop.step(str(repo), TASK, "rev2b2-busy", base)
    finally:
        finish.set()
        late.join()
    assert not late.errors, late.errors
    assert "did nothing" not in reply["error"], reply
    assert reply.get("action") == loop.STOP and "requests_sent" in reply, reply


# --- the outside-root refusal over MCP ----------------------------------------------------------------------------------

@pytest.fixture
def linked_parent(tmp_path, run_world):
    """The repair's own ``parent`` component: G6_HOME/completion_loop is a link out of the home."""
    home, outside = tmp_path / "g6-home", tmp_path / "outside"      # key_file's home
    outside.mkdir()
    link = home / "completion_loop"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        yield outside
    finally:
        if link.is_symlink():
            link.unlink()
        else:
            os.rmdir(link)


def test_control_outside_root_refusal_reaches_the_client_with_the_record_off(run_world, linked_parent, monkeypatch):
    repo, base = run_world
    monkeypatch.delenv("G6_WORKFLOW_RECORD", raising=False)
    reply = workflow.stepped(review_loop.step, str(repo), TASK, "rev2b2-outside", base)
    assert reply["code"] == OUTSIDE_ROOT and list(linked_parent.iterdir()) == []


def test_outside_root_refusal_reaches_the_client_with_the_record_on(run_world, linked_parent, monkeypatch):
    """FAILS at the tip: ``workflow.stepped`` (what the tool's job runs) asks ``completion_hook.folder`` again for the
    record, outside the adapter's guard, so the refusal reply is thrown away and the job ends in a raw WorkspaceRefused."""
    repo, base = run_world
    monkeypatch.setenv("G6_WORKFLOW_RECORD", "on")
    reply = workflow.stepped(review_loop.step, str(repo), TASK, "rev2b2-outside", base)
    assert reply["code"] == OUTSIDE_ROOT


def test_outside_root_refusal_is_not_labelled_wait(run_world, linked_parent, monkeypatch):
    """FAILS at the tip: the tool's own words for ``wait`` are "this call did nothing: another step of this session is
    running, so wait for that step's result before you call again" (or a question for the person). A path that is refused
    for good is neither, and a client that follows the label calls again for ever."""
    repo, base = run_world
    monkeypatch.delenv("G6_WORKFLOW_RECORD", raising=False)
    reply = review_loop.step(str(repo), TASK, "rev2b2-outside", base)
    assert reply["code"] == OUTSIDE_ROOT
    assert reply.get("action") != loop.WAIT, reply.get("action")


# --- the count on a reply with a code -------------------------------------------------------------------------------------

def test_nothing_spent_reply_does_not_carry_a_null_count(tmp_path, key_file):
    """FAILS at the tip: a first call with no Python changed says "nothing was tested and nothing was spent" and now also
    ``requests_sent: null`` (at the base the key was absent when there was no count): a field of the reply that held an
    integer or was not there is now sometimes null, here beside words that say the count is zero."""
    key_file("")
    repo, base = _repo(tmp_path, changed=False)
    reply = review_loop.step(str(repo), TASK, "rev2b2-nothing", base)
    assert reply["code"] == loop.NO_PYTHON and "nothing was spent" in reply["error"]
    assert "requests_sent" not in reply or type(reply["requests_sent"]) is int, reply["requests_sent"]
