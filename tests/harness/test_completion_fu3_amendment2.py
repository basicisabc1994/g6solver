# SPDX-License-Identifier: Apache-2.0
"""Scouts endings keep their own debt without borrowing reviewer evidence."""
from contextlib import contextmanager

import pytest

from g6.core.model_calls import note_model_call
from g6.harness import completion_loop as loop
from g6.mcp import review_loop
from tests._review_wtffu2b_3.rev3_world import TASK, quiet_scouts
from tests._review_wtffu2b4.rev4_world import rows_of
from tests._review_wtffu2b4.test_rev4_finding_3 import _killed_before_the_report
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


@pytest.mark.parametrize("interrupted", [False, True])
def test_scouts_end_keeps_partial_history_without_inventing_review_results(disk_run, monkeypatch, interrupted):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    if interrupted:
        _killed_before_the_report(monkeypatch, repo, base, session)
        monkeypatch.setattr(loop.proposer, "boundary", lambda _: "")
    state = loop._load(session)
    prior = [{"patch": "prior", "verdict": "defect"}]
    with loop._writing(session):
        loop._save(session, {**state, "attempted": 4, "reviews": prior}, sources=())

    result = loop.advance(repo, TASK, base, session)
    saved = loop._load(session)
    assert result.action == loop.STOP and saved["done"] and not result.code
    assert saved["reviews"] == prior and loop.attempted(saved) == 4
    assert saved["stop"] == "no probe set could run"
    assert "row_due" not in saved and "row" not in saved
    assert not loop.store.home_path(*loop.OWING, session.name).exists()
    rows = rows_of(session)
    if interrupted:
        assert len(rows) == 1 and rows[0]["cause"] == loop.FOUND_NOTHING
        assert rows[0]["reviews"] == [] and not rows[0]["independently_observed"]
        assert rows[0]["tree_changed"] is None
    else:
        assert rows == []
        assert not loop.store.home_path(*loop.FAILURES).exists()


def test_scouts_row_construction_failure_does_not_block_ending(disk_run, monkeypatch):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    _killed_before_the_report(monkeypatch, repo, base, session)
    monkeypatch.setattr(loop.proposer, "boundary", lambda _: "")

    def broken(*args, **kwargs):
        raise KeyError("missing row field")

    monkeypatch.setattr(loop, "_failure_row", broken)
    result = loop.advance(repo, TASK, base, session)
    saved = loop._load(session)
    assert result.action == loop.STOP and saved["done"] and not result.code
    assert saved["stop"] == "no probe set could run"
    assert "KeyError" in saved["row_build"] and "missing row field" in saved["row_build"]
    assert "row_due" not in saved
    assert [row["cause"] for row in rows_of(session)] == [""]


def test_reservation_flush_is_an_oserror_and_dispatch_stays_blocked(disk_run, monkeypatch):
    _, _, session = disk_run
    dispatched = []

    def failed(*args):
        raise OSError("storage flush failed")

    monkeypatch.setattr(loop, "_sync_state", failed)
    with loop._writing(session), loop._run_count(session, {}, fresh=True):
        with pytest.raises(OSError, match="storage flush failed") as caught:
            note_model_call()
            dispatched.append("sent")
    assert caught.value.code == loop.RESERVATION_UNSAVED
    assert dispatched == [] and not loop._load(session).get("done")


def test_real_scout_does_not_treat_unsaved_reservation_as_host_failure(disk_run, monkeypatch):
    repo, base, session = disk_run
    dispatched = []

    @contextmanager
    def copy(change, seconds):
        yield repo

    def failed(*args):
        raise OSError("storage flush failed")

    def host(*args):
        note_model_call()
        dispatched.append("sent")

    def unexpected(*args, **kwargs):
        raise AssertionError("an unsaved reservation must not proceed to probes")

    monkeypatch.setattr(loop, "_base_copy", copy)
    monkeypatch.setattr(loop, "_sync_state", failed)
    monkeypatch.setattr(loop, "run_set", unexpected)
    result = loop.advance(repo, TASK, base, session, scout=host)
    saved = loop._load(session)
    assert result.code == loop.RESERVATION_UNSAVED and dispatched == []
    assert not saved.get("done") and not saved.get("sets")
    assert rows_of(session) == []


def test_unreadable_mcp_reply_puts_recovery_outside_the_ending_note(disk_run, monkeypatch):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    review_loop.step(str(repo), TASK, "disk-review2", base)
    (session / "state.json").write_bytes(b'{"attempted":0}')
    reply = review_loop.step(str(repo), TASK, "disk-review2", base)
    assert reply["code"] == loop.NO_CLEAN_REVIEW
    assert reply.get("requests_sent") is None
    assert not reply.get("ending") and not reply.get("note")
    assert "new session" in reply["recovery"]
    assert "cannot be read" in reply["error"] and "unknown" in reply["error"]
