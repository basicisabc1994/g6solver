# SPDX-License-Identifier: Apache-2.0
"""MCP wording and receipts follow the retained snapshot after a late worker."""
import threading

import pytest

from g6.core import cloud_consent as cloud
from g6.core.llm import FakeLLM, LLMError
from g6.harness import completion_loop as loop
from g6.mcp import review_loop
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


@pytest.mark.parametrize("send_before_reply", [False, True])
def test_late_worker_reply_uses_one_retained_snapshot(disk_run, monkeypatch, send_before_reply):
    repo, base, session = disk_run
    release = threading.Event()
    received, workers, errors = [], [], []

    def worker():
        try:
            assert release.wait(10), "late worker never released"
            FakeLLM(lambda *args: received.append(args) or "reply").complete("system", "late request")
        except BaseException as exc:
            errors.append(exc)

    def scout(*args):
        thread = threading.Thread(target=worker)
        workers.append(thread)
        thread.start()
        raise cloud._refusal(LLMError(f"{cloud.REQUIRED}: next request refused"))

    def join_workers():
        release.set()
        for thread in workers:
            thread.join(10)
            assert not thread.is_alive()

    real_advance = loop.advance

    def advance(*args, **kwargs):
        result = real_advance(*args, **kwargs)
        if send_before_reply:
            join_workers()  # The real operation lock has already been released.
        return result

    monkeypatch.setattr(loop, "_scout", scout)
    monkeypatch.setattr(loop, "advance", advance)
    try:
        with cloud.collect_sources():
            reply = review_loop.step(str(repo), "double returns twice its argument", "disk-review2", base)
            metadata = cloud.output_metadata(reply)
        assert reply["code"] == cloud.REQUIRED
        assert reply["requests_sent"] == metadata["requests_sent"] == int(send_before_reply)
        if send_before_reply:
            assert "not sent" not in reply["report"].lower()
            assert "up to 1 request(s) of this run may have gone out" in reply["report"]
            assert "not sent" not in reply["error"].lower()
        else:
            assert reply["report"].startswith("The review was not sent.")
    finally:
        join_workers()
    assert not errors, errors
    assert len(received) == loop._load(session)["attempted"] == 1
    repeated = review_loop.step(str(repo), "double returns twice its argument", "disk-review2", base)
    assert repeated["requests_sent"] == 1
    assert "up to 1 request(s) of this run may have gone out" in repeated["report"]
    assert "not sent" not in repeated["report"].lower()


def test_mcp_uses_retained_report_even_when_step_text_is_old(disk_run, monkeypatch):
    repo, base, session = disk_run
    with loop._writing(session):
        loop._finish(session, {"attempted": 2}, "retained report", loop.NO_CLEAN_REVIEW, "untested")
    monkeypatch.setattr(loop, "advance", lambda *a, **k:
                        loop.Step(loop.STOP, "earlier report", loop.NO_CLEAN_REVIEW, ending="untested"))
    reply = review_loop.step(str(repo), "task", "disk-review2", base)
    assert reply["report"] == "retained report"
    assert reply["requests_sent"] == 2


@pytest.mark.parametrize("total", [None, 0, 2])
def test_retained_count_alone_controls_consent_wording(disk_run, monkeypatch, total):
    repo, base, session = disk_run
    state = {} if total is None else {"attempted": total}
    with loop._writing(session):
        loop._finish(session, state, "The review was not sent.", cloud.REQUIRED, "untested")
    monkeypatch.setattr(loop, "advance", lambda *a, **k:
                        loop.Step(loop.STOP, "The review was not sent.", cloud.REQUIRED, ending="untested"))
    reply = review_loop.step(str(repo), "task", "disk-review2", base)
    assert ("not sent" in reply["report"].lower()) == (total == 0)
    if total is None:
        assert reply["requests_sent"] is None
        assert "no count" in reply["report"]
    else:
        assert reply["requests_sent"] == total


def test_mcp_continuation_uses_retained_text(disk_run, monkeypatch):
    repo, base, session = disk_run
    with loop._writing(session):
        loop._save(session, {"attempted": 1, "reply_text": "repair the retained finding"}, sources=())
    monkeypatch.setattr(loop, "advance", lambda *a, **k: loop.Step(loop.CONTINUE, "earlier finding"))
    reply = review_loop.step(str(repo), "task", "disk-review2", base)
    assert reply["text"] == "repair the retained finding"
