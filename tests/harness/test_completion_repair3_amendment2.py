# SPDX-License-Identifier: Apache-2.0
"""No-change replies preserve each surface's public contract."""
import pytest

from g6.harness import completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from tests.harness.test_completion_amendment_reply import hook_run
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


@pytest.mark.parametrize("retained_count", [None, 0, 3])
def test_no_python_mcp_omits_count_and_keeps_no_spend_words(disk_run, monkeypatch, retained_count):
    repo, base, session = disk_run
    if retained_count is not None:
        loop._save(session, {loop.ATTEMPTED: retained_count}, sources=())
    monkeypatch.setattr(loop, "advance", lambda *a, **k: loop.Step(loop.STOP, code=loop.NO_PYTHON))

    reply = review_loop.step(str(repo), "task", "disk-review2", base)

    assert reply["code"] == loop.NO_PYTHON
    assert "nothing was spent" in reply["error"]
    assert "requests_sent" not in reply
    if retained_count is not None:
        assert loop.attempted(loop._load(session)) == retained_count


def test_no_loop_hook_keeps_exact_words_and_marks_turn(tmp_path, monkeypatch):
    step = loop.Step(loop.STOP, code=loop.NO_PYTHON, note=loop.NOTHING_CHANGED, ending="untested")
    session, event = hook_run(tmp_path, monkeypatch, step)

    reply = hook.on_stop(event)

    assert reply == {"systemMessage": "G6's completion loop did not run: no Python changed against the base commit, "
                    "so nothing was tested."}
    assert (session / hook.TURN_ENDED).is_file()
