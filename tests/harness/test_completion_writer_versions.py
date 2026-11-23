# SPDX-License-Identifier: Apache-2.0
"""Old snapshots cannot replace a newer operation's retained work on disk."""
import contextvars
import json
import threading

import pytest

from g6.harness import completion_loop as loop
from g6.workspace._envelope import WorkspaceRefused


@pytest.mark.parametrize("late_first", [False, True])
@pytest.mark.parametrize("copied_run", [False, True])
def test_stale_writers_preserve_reviews_and_debt(tmp_path, monkeypatch, late_first, copied_run):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    session = loop.state_path(*loop.OWING).parent / "run"
    old_row = json.dumps({"session": "run", "cause": "old"})
    new_row = json.dumps({"session": "run", "cause": "cloud_consent_missing"})
    first = {"attempted": 1, "reviews": [{"patch": "first"}],
             "row_due": old_row, "row": "old retry reason"}
    with loop._writing(session), loop._run_count(session, first, fresh=False):
        loop._save(session, first, sources=())
        stale = json.loads(json.dumps(first))
        copied = contextvars.copy_context()
    # This stale writer can raise the bound, but cannot publish its old work.
    stale["attempted"] = 9

    def late_write():
        if copied_run:
            copied.run(loop._save, session, stale, sources=())
        else:
            loop._save(session, stale, sources=())

    if late_first:
        late_write()
    current = {"attempted": 2, "reviews": [{"patch": "first"}, {"patch": "newer"}],
               "row_due": new_row, "row": "new retry reason"}
    with loop._writing(session):
        loop._save(session, current, sources=())
    if not late_first:
        late_write()
    retained = loop._load(session)
    assert retained["reviews"] == [{"patch": "first"}, {"patch": "newer"}]
    assert retained["row_due"] == new_row
    assert retained["row"] == "new retry reason"
    assert retained["attempted"] == retained[loop.SENT] == 9
    mark = loop.state_path(*loop.OWING, session.name)
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.touch()
    loop.settle()
    rows = [json.loads(line) for line in loop.state_path(*loop.FAILURES).read_text().splitlines()]
    assert rows == [{"session": "run", "cause": "cloud_consent_missing"}]
    delivered = loop._load(session)
    assert "row_due" not in delivered and "row" not in delivered
    assert not mark.exists()


@pytest.mark.parametrize("copied_run", [False, True])
def test_stale_writer_cannot_resurrect_delivered_debt_or_removed_review(tmp_path, copied_run):
    session = tmp_path / "run"
    state = {"attempted": 1, "reviews": [{"patch": "obsolete-clean"}],
             "row_due": "obsolete row", "row": "obsolete retry", "stop": loop.CLEAN}
    with loop._writing(session), loop._run_count(session, state, fresh=False):
        loop._save(session, state, sources=())
        stale = json.loads(json.dumps(state))
        copied = contextvars.copy_context()
    with loop._writing(session):
        loop._save(session, {"attempted": 2, "reviews": []}, sources=())
    if copied_run:
        copied.run(loop._save, session, stale, sources=())
    else:
        loop._save(session, stale, sources=())
    retained = loop._load(session)
    assert retained["reviews"] == []
    assert all(key not in retained for key in ("row_due", "row", "stop"))
    assert retained["attempted"] == 2


def test_expired_lease_cannot_change_even_its_latest_snapshot(tmp_path):
    session = tmp_path / "run"
    state = {"attempted": 1, "reviews": [{"patch": "kept"}], "row": "kept retry"}
    with loop._writing(session), loop._run_count(session, state, fresh=False):
        loop._save(session, state, sources=())
        copied = contextvars.copy_context()
    state.update(attempted=3, reviews=[], row="expired retry")
    copied.run(loop._save, session, state, sources=())
    retained = loop._load(session)
    assert retained["reviews"] == [{"patch": "kept"}]
    assert retained["row"] == "kept retry"
    assert retained["attempted"] == 3


def test_fresh_standalone_snapshot_can_publish_new_work(tmp_path):
    session = tmp_path / "run"
    loop._save(session, {"attempted": 1, "reviews": [{"patch": "first"}]}, sources=())
    fresh = loop._load(session)
    fresh.update(reviews=[{"patch": "first"}, {"patch": "newer"}], row_due="new row", row="new retry")
    loop._save(session, fresh, sources=())
    retained = loop._load(session)
    assert retained["reviews"] == [{"patch": "first"}, {"patch": "newer"}]
    assert (retained["row_due"], retained["row"]) == ("new row", "new retry")


def test_stale_save_cannot_replace_retained_origins(tmp_path):
    from g6.core import cloud_consent as cloud

    session = tmp_path / "run"
    old = cloud.Source.text("host:old", "diagnostic", "old")
    newer = cloud.Source.text("host:newer", "diagnostic", "newer")
    stale = {"attempted": 1}
    loop._save(session, stale, sources=(old,))
    with loop._writing(session):
        loop._save(session, {"attempted": 2}, sources=(newer,))
    origins = loop._load(session)["consent_sources"]
    loop._save(session, stale, sources=(old,))
    assert loop._load(session)["consent_sources"] == origins


def test_expired_writer_cannot_create_old_work_in_an_empty_session(tmp_path):
    session = tmp_path / "run"
    state = {"attempted": 1, "reviews": [{"patch": "expired"}], "row_due": "expired row"}
    with loop._writing(session), loop._run_count(session, state, fresh=False):
        copied = contextvars.copy_context()
    copied.run(loop._save, session, state, sources=())
    retained = loop._load(session)
    assert retained["attempted"] == 1
    assert "reviews" not in retained and "row_due" not in retained


def test_expired_writer_obeys_new_operation_lock_then_preserves_its_work(tmp_path):
    session = tmp_path / "run"
    stale = {"attempted": 1, "reviews": [{"patch": "first"}], "row_due": "old row"}
    with loop._writing(session), loop._run_count(session, stale, fresh=False):
        loop._save(session, stale, sources=())
        copied = contextvars.copy_context()
    stale["attempted"] = 7
    entered, release = threading.Event(), threading.Event()
    errors = []

    def newer():
        try:
            with loop._writing(session):
                loop._save(session, {"attempted": 2, "reviews": [{"patch": "newer"}],
                                     "row_due": "new row", "row": "new retry"}, sources=())
                entered.set()
                assert release.wait(10)
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=newer)
    worker.start()
    try:
        assert entered.wait(10)
        with pytest.raises(WorkspaceRefused):
            copied.run(loop._save, session, stale, sources=())
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert not errors, errors
    copied.run(loop._save, session, stale, sources=())
    retained = loop._load(session)
    assert retained["attempted"] == 7
    assert retained["reviews"] == [{"patch": "newer"}]
    assert (retained["row_due"], retained["row"]) == ("new row", "new retry")


@pytest.mark.parametrize("retained", [False, True])
def test_expired_reservation_only_adds_a_send(tmp_path, retained):
    from g6.core import cloud_consent as cloud
    from g6.core.model_calls import note_model_call

    session = tmp_path / "run"
    state = {"attempted": 0, "reviews": [{"patch": "expired"}], "row_due": "expired row"}
    with loop._writing(session), loop._run_count(session, state, fresh=False):
        copied = contextvars.copy_context()
    if retained:
        source = cloud.Source.text("host:newer", "diagnostic", "newer")
        with loop._writing(session):
            loop._save(session, {"attempted": 2, "reviews": [{"patch": "newer"}],
                                 "row_due": "new row", "row": "new retry"}, sources=(source,))
        before = loop._load(session)
    copied.run(note_model_call)
    after = loop._load(session)
    if retained:
        assert after["attempted"] == after[loop.SENT] == 3
        assert all(after[key] == before[key] for key in ("reviews", "row_due", "row", "consent_sources"))
    else:
        assert after["attempted"] == after[loop.SENT] == 1
        assert "reviews" not in after and "row_due" not in after


def test_expired_standalone_count_context_cannot_publish_work(tmp_path):
    session = tmp_path / "run"
    state = {"attempted": 1, "reviews": [{"patch": "kept"}], "row": "kept retry"}
    loop._save(session, state, sources=())
    with loop._run_count(session, state, fresh=False):
        copied = contextvars.copy_context()
    state.update(attempted=3, reviews=[], row="expired retry")
    copied.run(loop._save, session, state, sources=())
    retained = loop._load(session)
    assert retained["reviews"] == [{"patch": "kept"}]
    assert retained["row"] == "kept retry"
    assert retained["attempted"] == 3
