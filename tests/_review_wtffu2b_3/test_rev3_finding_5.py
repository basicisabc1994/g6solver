# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 5: a run the new terminal path ends leaves no row and owes none.

The module's docstring says "A run that ends leaves a row in FAILURES", for the loop that learns from them, and every
other refusal ends through ``refused``, which owes the row before anything that can fail. A review-until-clean run ended
by a path refusal in its body, after its reviewer was asked, has no row in the log, no mark in ``owing`` and no
``row_due`` in its state.
"""
from g6.core.model_calls import note_model_call
from g6.harness import completion_loop as loop
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused
from tests._review_wtffu2b_3.rev3_world import TASK, clean_reviewer, log_rows
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def test_control_a_clean_review_leaves_its_row(disk_run, key_file, monkeypatch):
    repo, base, session = disk_run
    clean_reviewer(monkeypatch, key_file)
    loop.advance(repo, TASK, base, session)
    assert session.name in log_rows()


def test_a_run_ended_by_a_body_refusal_leaves_or_owes_its_row(disk_run, key_file, monkeypatch):
    repo, base, session = disk_run
    clean_reviewer(monkeypatch, key_file)

    def review_round(*args, **kwargs):
        note_model_call()
        raise WorkspaceRefused(OUTSIDE_ROOT, "refused output")

    monkeypatch.setattr(loop, "review_round", review_round)
    step = loop.advance(repo, TASK, base, session)
    state = loop._load(session)
    assert (step.action, step.code) == (loop.STOP, OUTSIDE_ROOT) and state["done"] and loop.attempted(state) == 1
    owed = loop.store.home_path(*loop.OWING, session.name).exists() or "row_due" in state
    assert session.name in log_rows() or owed
