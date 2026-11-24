# SPDX-License-Identifier: Apache-2.0
"""Hook messages and unknown counts retain their older public contracts."""
import json

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.harness.workflow import policy
from g6.mcp import review_loop
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def hook_run(tmp_path, monkeypatch, step):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(hook, "_here", lambda *a: (tmp_path, "base"))
    monkeypatch.setattr(policy, "enabled", lambda: (False, ""))
    monkeypatch.setattr(loop, "advance", lambda *a, **kw: step)
    session = hook.folder("amendment")
    hook._record(session, str(tmp_path), "task")
    return session, {"session_id": "amendment", "cwd": str(tmp_path)}


def test_hook_partial_publication_still_reports_the_retained_send_bound(tmp_path, monkeypatch):
    step = loop.Step(loop.STOP, "The review was not sent.", cloud.REQUIRED, ending="untested")
    session, event = hook_run(tmp_path, monkeypatch, step)
    loop._save(session, {"attempted": 2}, sources=())

    reply = hook.on_stop(event)

    assert "systemMessage" in reply
    assert "up to 2 request(s) of this run may have gone out" in reply["systemMessage"]
    assert "not sent" not in reply["systemMessage"].lower()


def test_hook_repeat_shows_the_current_no_run_notice_without_the_old_report(tmp_path, monkeypatch):
    step = loop.Step(loop.STOP, note=loop.ALREADY_ENDED, ending="clean")
    session, event = hook_run(tmp_path, monkeypatch, step)
    loop._finish(session, {"attempted": 1}, "old report\nsecond line", ending="clean")

    reply = hook.on_stop(event)

    assert "did not run" in reply["systemMessage"]
    assert "\n" not in reply["systemMessage"]
    assert "old report" not in reply["systemMessage"]
    assert (session / hook.TURN_ENDED).is_file()


def test_hook_partial_report_keeps_the_message_envelope_without_unretained_words(tmp_path, monkeypatch):
    step = loop.Step(loop.STOP, "earlier report")
    session, event = hook_run(tmp_path, monkeypatch, step)
    loop._save(session, {"attempted": 2}, sources=())

    reply = hook.on_stop(event)

    assert "systemMessage" in reply
    assert reply["systemMessage"] == cloud.WITHHELD
    assert "earlier report" not in reply["systemMessage"]


@pytest.mark.parametrize("completed", [False, True])
def test_unknown_count_preserves_completed_legacy_replies_only(disk_run, monkeypatch, completed):
    repo, base, session = disk_run
    if completed:
        loop._finish(session, {}, "old wording", cloud.REQUIRED, "untested")
    step = loop.Step(loop.STOP, code=cloud.REQUIRED if completed else loop.NO_PYTHON, ending="untested")
    monkeypatch.setattr(loop, "advance", lambda *a, **kw: step)

    with cloud.collect_sources():
        reply = review_loop.step(str(repo), "task", "disk-review2", base)
        metadata = cloud.output_metadata(reply)
        body, _, frozen_metadata = cloud.freeze_output(reply)

    if completed:
        assert reply["requests_sent"] is None
        assert metadata["requests_sent"] is None
        assert json.loads(body)["requests_sent"] is frozen_metadata["requests_sent"] is None
        assert "no count" in reply["report"] and "not sent" not in reply["report"].lower()
    else:
        assert "requests_sent" not in reply
        assert "requests_sent" not in metadata
        assert "requests_sent" not in json.loads(body)
