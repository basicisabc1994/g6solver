# SPDX-License-Identifier: Apache-2.0
"""Writer identity must not alter the run's retained public fields."""
import json
from pathlib import Path

import pytest

from g6.core import cloud_consent as cloud
from g6.core.ledger import digest
from g6.harness import completion_loop as loop
from g6.mcp import review_loop
from g6.mcp.cloud_consent import withheld_reply
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run
from tests.harness.test_review_refused_after_send import _advance, _refused_after


@pytest.mark.parametrize("reviewed", [False, True])
def test_new_run_keeps_base_state_shape(tmp_path, reviewed):
    session = tmp_path / "run"
    state = {"attempted": 0}
    if reviewed:
        state["reviewed"] = "prior patch"
    with loop._writing(session):
        loop._save(session, state, sources=())
    retained = json.loads((session / "state.json").read_text(encoding="utf-8"))
    want = {"attempted": 0, "requests_sent": 0}
    if reviewed:
        want["reviewed"] = "prior patch"
    assert {key: value for key, value in retained.items() if key != "consent_sources"} == want


def test_republishing_same_snapshot_keeps_state_bytes(tmp_path):
    session = tmp_path / "run"
    loop._save(session, {"attempted": 1, "reviews": []}, sources=())
    before = (session / "state.json").read_bytes()
    loop._save(session, loop._load(session), sources=())
    assert (session / "state.json").read_bytes() == before


def test_same_bytes_from_new_operation_still_expire_old_snapshot(tmp_path):
    session = tmp_path / "run"
    loop._save(session, {"attempted": 1, "reviews": [{"patch": "kept"}]}, sources=())
    stale = loop._load(session)
    with loop._writing(session):
        loop._save(session, loop._load(session), sources=())
    stale.update(attempted=3, reviews=[], row_due="obsolete debt")
    loop._save(session, stale, sources=())
    retained = loop._load(session)
    assert retained["attempted"] == 3
    assert retained["reviews"] == [{"patch": "kept"}] and "row_due" not in retained


@pytest.mark.parametrize("sends, code", [(0, cloud.REQUIRED), (1, cloud.REQUIRED),
                                        (2, cloud.STATE_UNAVAILABLE)])
def test_direct_operation_retains_refusal_fields(tmp_path, monkeypatch, sends, code):
    done, retained = _advance(tmp_path, monkeypatch, _refused_after(sends, code))
    want_code = loop.CONSENT_STATE_AFTER_SEND if code == cloud.STATE_UNAVAILABLE else code
    assert retained["stop"] == done.text
    assert retained["code"] == done.code == want_code
    assert retained["attempted"] == retained["requests_sent"] == sends
    assert retained["done"] is True and retained["ending"] == "untested"


@pytest.mark.parametrize("code", [cloud.REQUIRED, cloud.STATE_UNAVAILABLE, loop.CONSENT_STATE_AFTER_SEND])
@pytest.mark.parametrize("total", [None, 0, 2])
def test_completed_refusal_and_metadata_keep_retained_count(disk_run, monkeypatch, code, total):
    repo, base, session = disk_run
    state = {} if total is None else {"attempted": total}
    with loop._writing(session):
        loop._finish(session, state, "old wording", code, "untested")
    # Poison the step's earlier report and leave accounting entirely to retained state.
    monkeypatch.setattr(loop, "advance", lambda *a, **k:
                        loop.Step(loop.STOP, "The review was not sent.", code, ending="untested"))
    with cloud.collect_sources() as capture:
        reply = review_loop.step(str(repo), "task", "disk-review2", base)
        metadata = cloud.output_metadata(reply)
        body, _, frozen_metadata = cloud.freeze_output(reply)
    assert reply["requests_sent"] == metadata["requests_sent"] == total
    assert json.loads(body)["requests_sent"] == frozen_metadata["requests_sent"] == total
    job = {"digest": digest(reply), "job_id": "refused-review", "status": "completed",
           "metadata": metadata, "review_unsent": capture.review_unsent}
    rebuilt = withheld_reply(reply, job=job)["result"]
    assert rebuilt["requests_sent"] == total
    assert ("not sent" in reply["report"].lower()) == (total == 0)


def test_relative_run_reservation_stays_at_its_resolved_folder(tmp_path, monkeypatch):
    from g6.core.model_calls import note_model_call

    started, scratch = tmp_path / "started", tmp_path / "scratch"
    started.mkdir()
    scratch.mkdir()
    monkeypatch.chdir(started)
    state = {}
    with loop._writing(Path("run")), loop._run_count(Path("run"), state, fresh=True):
        loop._save(Path("run"), state, sources=())
        monkeypatch.chdir(scratch)
        note_model_call()
    assert list(scratch.iterdir()) == []
    retained = loop._load(started / "run")
    assert retained["attempted"] == retained["requests_sent"] == 1
