# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 3: in the scouts' loop a resumed refusal leaves its row and its words on a run that ended otherwise.

The lead's question of the resumed refusal: can a resumed run write a cause that belongs to another ending. The
review-until-clean loop cannot: ``_review_end`` owes the row again with the run's own cause (the control). The scouts' loop
ends through ``_finish`` alone, which owes nothing, so a refusal interrupted before its report was published (its row
already owed or delivered, ``stop`` already the refusal's words) and resumed to a full report keeps both:

* the row in ``FAILURES``, which the self-improvement loop reads, says ``no_boundary`` (``changeable`` false) and "G6's
  completion loop did not run ... asked no model. Nothing was tested." of a run that ran and reported;
* the settled state's ``stop`` is that refusal text beside ``code`` "" and a report.

``refused`` was resumable at a3d8927a too; repair 3 routes the body refusals and the consent refusal of every loop through
the same ``_refused``, so each of them now has this window in the scouts' loop.
"""
import pytest

from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.harness import completion_loop as loop
from tests._review_wtffu2b_3.rev3_world import TASK, quiet_scouts
from tests._review_wtffu2b4.rev4_world import ProcessDied, clean_critic, rows_of
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _killed_before_the_report(monkeypatch, repo, base, session, **sessions):
    """A step refused for want of a boundary, its process ended as it published the refusal's report."""
    monkeypatch.setattr(loop.proposer, "boundary", lambda root: "no AppContainer here")
    real = loop.store.write_bytes

    def dies(target, body):
        if target.name == "report.md":
            raise ProcessDied("killed before the report was published")
        return real(target, body)

    monkeypatch.setattr(loop.store, "write_bytes", dies)
    with pytest.raises(ProcessDied):
        loop.advance(repo, TASK, base, session, **sessions)
    monkeypatch.setattr(loop.store, "write_bytes", real)
    state = loop._load(session)
    assert not state.get("done") and "no operating-system boundary" in state["stop"]
    assert [row["cause"] for row in rows_of(session)] == [loop.UNCONFINED]


def test_control_resumed_to_the_same_refusal_keeps_that_refusals_row(disk_run, monkeypatch):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    _killed_before_the_report(monkeypatch, repo, base, session)
    step = loop.advance(repo, TASK, base, session)
    assert step.code == ISOLATION_UNAVAILABLE and loop._load(session)["done"]
    assert [row["cause"] for row in rows_of(session)] == [loop.UNCONFINED]


def test_control_review_loop_resumed_to_a_clean_end_writes_its_own_cause(disk_run, key_file, monkeypatch):
    repo, base, session = disk_run
    key_file("G6_LOOP_MODE=review\n")
    caller, sent = clean_critic()
    _killed_before_the_report(monkeypatch, repo, base, session, critic=caller)
    monkeypatch.setattr(loop.proposer, "boundary", lambda root: "")
    step = loop.advance(repo, TASK, base, session, critic=caller)
    state = loop._load(session)
    assert (step.code, step.ending, state["stop"]) == ("", "clean", loop.CLEAN)
    assert [row["cause"] for row in rows_of(session)] == [loop.FOUND_NOTHING]


def test_scouts_loop_resumed_to_a_report_does_not_keep_the_refusals_row_and_words(disk_run, monkeypatch):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    _killed_before_the_report(monkeypatch, repo, base, session)
    monkeypatch.setattr(loop.proposer, "boundary", lambda root: "")      # the boundary is there on the next stop
    step = loop.advance(repo, TASK, base, session)
    assert step.code == "" and "What the completion loop tested" in step.text      # the run ran and reported
    loop.advance(repo, TASK, base, session)                                          # a repeat settles what is owed
    state = loop._load(session)
    assert state["done"] and state["code"] == ""
    said = [(row["cause"], row["stop"][:60]) for row in rows_of(session)]
    assert loop.UNCONFINED not in [cause for cause, _ in said], f"the log says of this run: {said}"
    assert "did not run" not in state.get("stop", ""), state["stop"]
