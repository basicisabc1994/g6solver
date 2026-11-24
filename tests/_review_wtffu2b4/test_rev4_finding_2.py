# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 2: on Windows a reader that has a retained file open breaks the writer's publication.

Review 3's suspicion, shown. ``cloud.write_retained`` replaces the content and then its receipt with ``os.replace``, and on
Windows a replacement onto a file another handle has open without delete sharing fails (``PermissionError``, WinError 5).
Python's own ``open`` gives no delete sharing, so every reader G6 runs without the step's lock is such a handle (the reply's
snapshot after its bounded wait, ``on_prompt``'s and ``_again``'s ``_load``, ``_task`` in a Stop hook), and so is an indexer
or a scanner looking at a file just written.

* The receipt open: the content is replaced, the receipt is not, the save raises, and the run is unreadable from then on
  (review 3's finding 9 state, with no process killed): no report, the send count unknown.
* The content open while a request is reserved: nothing is replaced and the save raises, which stops the send as it should.
  ``run_critic`` then records that ``OSError`` as the reviewer's failure, so the run ends for good as "a review failed",
  cause ``reviewer_unreachable``, and its report says a reviewer "read the patch once", with no request sent.

Both are as they were at a3d8927a (the publication and the unlocked readers are the parent's); this round keeps the
unlocked readers and adds one (``_again``).
"""
import os

import pytest

from g6.harness import completion_loop as loop
from tests._review_wtffu2b_3.rev3_world import TASK
from tests._review_wtffu2b4.rev4_world import clean_critic, rows_of
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run

windows = pytest.mark.skipif(os.name != "nt", reason="a replacement onto an open file fails only on Windows")


def _in_flight(run, sent):
    run.mkdir(parents=True, exist_ok=True)
    with loop._writing(run):
        loop._save(run, {"attempted": sent, "sets": {}}, sources=())


def _count_one_more(run):
    with loop._writing(run):
        state = loop._load(run)
        state["attempted"] += 1
        loop._save(run, state)


def test_control_a_save_with_no_reader_publishes_and_reads_back(disk_run):
    repo, base, session = disk_run
    _in_flight(session, 1)
    _count_one_more(session)
    state = loop._load(session)
    assert state.readable and loop.attempted(state) == 2


@windows
def test_a_reader_of_the_receipt_does_not_leave_the_run_unreadable(disk_run):
    repo, base, session = disk_run
    _in_flight(session, 1)
    raised = None
    with open(session / "state.sha256", "rb") as reader:     # as Path.read_text opens it in _read_publication
        reader.read(1)
        try:
            _count_one_more(session)
        except OSError as exc:
            raised = exc
    state = loop._load(session)                               # the reader has let go
    assert state.readable, f"the save raised {type(raised).__name__}; the run reads {dict(state)!r} from now on"
    assert loop.attempted(state) in (1, 2)


def test_control_a_clean_review_ends_clean_with_one_request(disk_run, key_file, monkeypatch):
    repo, base, session = disk_run
    key_file("G6_LOOP_MODE=review\n")
    caller, sent = clean_critic()
    _in_flight(session, 0)
    step = loop.advance(repo, TASK, base, session, critic=caller)
    state = loop._load(session)
    assert (step.code, step.ending, len(sent), loop.attempted(state)) == ("", "clean", 1, 1)
    assert [row["cause"] for row in rows_of(session)] == [loop.FOUND_NOTHING]


@windows
def test_a_reservation_that_could_not_be_saved_is_not_a_reviewer_that_failed(disk_run, key_file, monkeypatch):
    repo, base, session = disk_run
    key_file("G6_LOOP_MODE=review\n")
    caller, sent = clean_critic()
    _in_flight(session, 0)
    real, held = loop._save_reservation, []

    def reserve(run, state, **kwargs):
        if not held:                                          # a reader has the content open as the first request is reserved
            held.append(open(run / "state.json", "rb"))
            try:
                return real(run, state, **kwargs)
            finally:
                held[0].close()
        return real(run, state, **kwargs)

    monkeypatch.setattr(loop, "_save_reservation", reserve)
    step = loop.advance(repo, TASK, base, session, critic=caller)
    state = loop._load(session)
    assert sent == [] and loop.attempted(state) == 0          # the send was stopped, as it should be
    said = [(one["verdict"], one["error"][:40]) for one in state.get("reviews", [])]
    assert not state.get("done"), (f"no request went out and the run ended for good: code {step.code}, stop "
                                   f"{state.get('stop')!r}, reviews {said}, row {[r['cause'] for r in rows_of(session)]}")
    assert "read the patch once" not in step.text
