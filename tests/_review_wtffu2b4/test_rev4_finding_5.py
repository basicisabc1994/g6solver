# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 5: over MCP a run that cannot be read answers the same refusal for good and names no way on.

Review 3's findings 9 and 4 at their class, on the other surface. Repair 3 lets the Stop hook's next prompt pass over a run
whose publication cannot be read. An MCP session has no next prompt: every call of that session is
``G6_E_NO_CLEAN_REVIEW`` "The retained completion state cannot be read now ...", with no note, no ``recovery`` and no word
of a new session (an ended run's repeat has all three), and "now" reads as something a retry clears. It never clears.

This round also widens what cannot be read: a first save cut short after its write token alone (``.write-version``, before
any content, so before the request it reserves could be sent) was a fresh run at ad9e6b4d and is now such a run.
"""
from g6.core.model_calls import note_model_call
from g6.harness import completion_loop as loop
from g6.mcp import review_loop
from tests._review_wtffu2b_3.rev3_world import TASK, quiet_scouts
from tests._review_wtffu2b4.rev4_world import SESSION, ProcessDied, torn
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _way_on(reply):
    said = " ".join(str(reply.get(key) or "") for key in ("note", "recovery", "error", "report"))
    return "new session" in said


def test_control_a_repeat_of_an_ended_run_names_a_new_session(disk_run, monkeypatch):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    review_loop.step(str(repo), TASK, SESSION, base)
    repeat = review_loop.step(str(repo), TASK, SESSION, base)
    assert repeat["note"] == loop.ALREADY_ENDED and _way_on(repeat)


def test_mcp_names_a_way_on_from_a_run_it_cannot_read(disk_run, monkeypatch):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch, sends=True)
    review_loop.step(str(repo), TASK, SESSION, base)
    torn(session / "state.json", (session / "state.json").read_bytes() + b" ")
    replies = [review_loop.step(str(repo), TASK, SESSION, base) for _ in range(2)]
    assert all(reply["code"] == loop.NO_CLEAN_REVIEW and reply["requests_sent"] is None for reply in replies)
    assert _way_on(replies[-1]), {key: replies[-1].get(key) for key in ("note", "recovery", "error")}


def test_mcp_first_save_cut_short_before_anything_was_sent_does_not_end_the_session(disk_run, monkeypatch):
    repo, base, session = disk_run
    sent = []

    def scout(change, task, text, where, host, deadline):
        note_model_call()                      # reserves the request; the host is asked only once that is saved
        sent.append(where.name)
        return {"left_out": "OSError: host could not launch", "where": str(where), "at_base": None, "refused": []}

    monkeypatch.setattr(loop, "_scout", scout)
    real = loop.store.write_retained

    def dies(target, body):
        if target.name == "state.json":
            raise ProcessDied("killed after the write token, before any content")
        return real(target, body)

    monkeypatch.setattr(loop.store, "write_retained", dies)
    try:
        review_loop.step(str(repo), TASK, SESSION, base)
    except ProcessDied:
        pass
    monkeypatch.setattr(loop.store, "write_retained", real)
    assert sent == [] and sorted(p.name for p in session.iterdir() if p.is_file()) == [
        ".write-version", "task.json", "task.sha256"]
    reply = review_loop.step(str(repo), TASK, SESSION, base)
    assert reply.get("code") != loop.NO_CLEAN_REVIEW or _way_on(reply), reply.get("error")
