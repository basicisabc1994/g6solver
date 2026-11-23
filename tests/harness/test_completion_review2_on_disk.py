# SPDX-License-Identifier: Apache-2.0
"""Review 2's crash and competing-writer cases with real retained state and locks."""
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from g6.core import cloud_consent as cloud
from g6.core.ledger import digest
from g6.core.llm import LLMError
from g6.core.model_calls import counting, note_model_call
from g6.harness import completion_hook, completion_loop as loop
from g6.harness.workflow.obstacle import obstacles
from g6.mcp import review_loop
from g6.mcp.cloud_consent import withheld_reply


@pytest.fixture
def disk_run(tmp_path, key_file, monkeypatch):
    """Only host execution and scout output are substituted; storage stays real."""
    key_file("G6_LOOP_MODE=scouts\nG6_LOOP_SCOUTS=before_fix\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    code = repo / "mod.py"
    code.write_text("def double(x):\n    return x + 2\n", encoding="utf-8")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init", "-q")
    git("add", "mod.py")
    git("commit", "-qm", "fixture base")
    base = git("rev-parse", "HEAD")
    code.write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
    # No model-written code is executed. The fake scouts return no runnable set.
    monkeypatch.setattr(loop.proposer, "boundary", lambda root: "")
    monkeypatch.setattr(loop, "scout_host", lambda: object())
    monkeypatch.setattr(loop, "critic_host", lambda: None)
    monkeypatch.setattr(loop, "review_patch", lambda *a, **k: {"assessment": {"report": "No probe set ran."}})
    return repo, base, completion_hook.folder("mcp:disk-review2")


def _left_out(where):
    return {"left_out": "OSError: host could not launch", "where": str(where), "at_base": None, "refused": []}


def _read(session):
    body = (session / "state.json").read_bytes()
    assert (session / "state.sha256").read_text(encoding="ascii") == cloud.sha256(body)
    return json.loads(body)


def _not_below(state, sends):
    for field in (loop.SENT, "attempted"):
        recorded = state.get(field)
        assert recorded is None or (type(recorded) is int and recorded >= sends), (field, recorded, sends)


def test_scout_checkpoint_then_crash_never_restores_an_unsent_run(disk_run, monkeypatch):
    repo, base, session = disk_run
    snapshots = []

    class ProcessDied(BaseException):
        pass

    def scout(change, task, text, where, host, deadline):
        if where.name == "scout":
            return _left_out(where)
        note_model_call()
        snapshots.append(_read(session))
        raise ProcessDied("process died after the second scout sent")

    monkeypatch.setattr(loop, "_scout", scout)
    with counting() as sent:
        with pytest.raises(ProcessDied, match="second scout sent"):
            loop.advance(repo, "double returns twice its argument", base, session, scout=object())
        assert sent() == 1
        snapshots.append(_read(session))

        def refused(*args):
            raise LLMError(f"{cloud.REQUIRED}: sharing revoked")

        monkeypatch.setattr(loop, "_scout", refused)
        with cloud.collect_sources() as capture:
            reply = review_loop.step(str(repo), "double returns twice its argument", "disk-review2", base)
            metadata = cloud.output_metadata(reply)
        snapshots.append(_read(session))
        job = {"digest": digest(reply), "job_id": "disk-review2-job", "status": "completed",
               "metadata": metadata, "review_unsent": capture.review_unsent}
        # Retain and reload the job too, rather than rebuilding from a live object.
        job_path = session / "saved-job.json"
        job_path.write_text(json.dumps(job), encoding="utf-8")
        rebuilt = withheld_reply(reply, job=json.loads(job_path.read_text(encoding="utf-8")))["result"]
        assert sent() == 1
        for state in snapshots:
            _not_below(state, sent())
        assert "not sent" not in reply["report"].lower(), reply
        assert "not sent" not in rebuilt["report"].lower(), rebuilt
        assert obstacles(None, reply, ())[0].kind == "refused_run"


def test_prepare_cannot_overwrite_a_concurrent_advance_send(disk_run, monkeypatch):
    repo, base, session = disk_run
    prepared, release = threading.Event(), threading.Event()
    calls = 0
    guard = threading.Lock()

    def scout(change, task, text, where, host, deadline):
        nonlocal calls
        if where.name == "scout":
            with guard:
                calls += 1
                first = calls == 1
            note_model_call()
            if first:
                prepared.set()
                assert release.wait(20), "held scout was not released"
        return _left_out(where)

    monkeypatch.setattr(loop, "_scout", scout)
    with counting(before_call=lambda: None) as sent:
        with ThreadPoolExecutor(max_workers=2) as pool:
            preparing = pool.submit(loop.prepare, repo, "double returns twice its argument", base, session, scout=object())
            try:
                assert prepared.wait(10), "prepare never reached its first send"
                held_state = _read(session)
                advancing = pool.submit(loop.advance, repo, "double returns twice its argument", base, session,
                                        scout=object())
                competing = advancing.result(timeout=10)
            finally:
                release.set()
            assert preparing.result(timeout=10) == ""
            assert advancing.done()
        state = _read(session)
        assert sent() >= 1
        _not_below(held_state, 1)
        _not_below(state, sent())
        assert (competing.action, competing.code, competing.text) == (loop.WAIT, loop.BUSY, loop.ALREADY_RUNNING)
        assert sent() == 1, "the refused competing advance must not send"
