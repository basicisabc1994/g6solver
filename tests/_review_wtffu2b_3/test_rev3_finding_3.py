# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 3: a run's settled ending and report are replaced by a path refusal raised after them.

``_advance_inner`` now ends the run on any path refusal the step raises, and writes that refusal over the state and
``report.md``. Two things that used to cost no report now raise such a refusal after the result is settled: the removal of
the agent's temporary copy of its scout's work (``_finish``), and the delivery of the run's row (``_write_row``, whose
docstring still says "A row that cannot be written costs no report"). The second also runs on a mere repeat of an ended
run, where it destroys the report that "stands".
"""
import os

from g6.harness import completion_loop as loop
from g6.workspace._envelope import OUTSIDE_ROOT
from tests._review_wtffu2b_3.rev3_world import TASK, clean_reviewer, ended_clean, hard_link, log_rows, quiet_scouts
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _visible(tmp_path, session, *, linked):
    """The copy the agent was given, as ``_told`` records it; with ``linked`` a second name the agent made in it."""
    visible = tmp_path / "g6_scout_copy"
    (visible / "probes").mkdir(parents=True)
    (visible / "probes" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
    if linked:
        os.link(visible / "probes" / "test_a.py", visible / "probes" / "test_b.py")
    with loop._writing(session):
        loop._save(session, {"attempted": 0, "visible": str(visible)}, sources=())
    return visible


def test_control_the_agents_copy_is_removed_and_the_report_kept(disk_run, monkeypatch, tmp_path):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    visible = _visible(tmp_path, session, linked=False)
    step = loop.advance(repo, TASK, base, session)
    assert (step.action, step.code) == (loop.STOP, "") and "No probe set ran." in step.text
    assert not visible.exists()
    assert loop.read_report(session, loop._load(session)) == step.text


def test_a_link_in_the_agents_copy_does_not_replace_the_finished_report(disk_run, monkeypatch, tmp_path):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    _visible(tmp_path, session, linked=True)
    step = loop.advance(repo, TASK, base, session)
    state = loop._load(session)
    assert state.get("code") != OUTSIDE_ROOT, state.get("stop")
    assert "No probe set ran." in loop.read_report(session, state)
    assert step.code == ""


def test_control_a_repeat_delivers_the_owed_row_and_the_report_stands(disk_run):
    repo, base, session = disk_run
    session.mkdir(parents=True)
    ended_clean(repo, base, session, owed=True)
    step = loop.advance(repo, TASK, base, session)
    state = loop._load(session)
    assert step.note == loop.ALREADY_ENDED and state["ending"] == "clean" and "row_due" not in state
    assert session.name in log_rows()
    assert loop.read_report(session, state) == "CLEAN REPORT of the first patch"


def test_a_repeat_with_a_refused_row_log_does_not_destroy_the_report_that_stands(disk_run, tmp_path):
    repo, base, session = disk_run
    session.mkdir(parents=True)
    ended_clean(repo, base, session, owed=True)
    log = loop.store.home_path(*loop.FAILURES)
    hard_link(tmp_path, log)
    try:
        loop.advance(repo, TASK, base, session)
        state = loop._load(session)
    finally:
        log.unlink()
    assert state["ending"] == "clean", (state.get("code"), state.get("stop"))
    assert loop.read_report(session, state) == "CLEAN REPORT of the first patch"
    assert "row_due" in state        # the row stays owed, as the docstring of _write_row says


def test_control_a_clean_review_ends_clean_and_leaves_its_row(disk_run, key_file, monkeypatch):
    repo, base, session = disk_run
    clean_reviewer(monkeypatch, key_file)
    step = loop.advance(repo, TASK, base, session)
    assert (step.code, step.ending) == ("", "clean")
    assert session.name in log_rows()


def test_a_refused_row_log_does_not_cost_a_clean_review_its_report(disk_run, key_file, monkeypatch, tmp_path):
    repo, base, session = disk_run
    clean_reviewer(monkeypatch, key_file)
    log = loop.store.home_path(*loop.FAILURES)
    hard_link(tmp_path, log)
    try:
        step = loop.advance(repo, TASK, base, session)
        state = loop._load(session)
    finally:
        log.unlink()
    assert loop.attempted(state) == 1                       # the reviewer was asked
    assert (step.code, step.ending) == ("", "clean"), (step.code, step.ending, step.text[:200])
    assert "row_due" in state and state.get("row")          # the row waits, with why
