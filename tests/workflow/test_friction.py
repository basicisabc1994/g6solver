# SPDX-License-Identifier: Apache-2.0
"""G6's own friction counters (design 17.3; the self-friction row of 17.5): each kind counted apart, what was not
observed left unmeasured, and nothing inferred from elapsed time."""
from __future__ import annotations

import dataclasses

from g6.harness.workflow import record as rec
from g6.harness.workflow.friction import ALWAYS_MISSING, Friction, friction, per_checked_outcome
from tests.workflow.kit import event, sha

RUN, OTHER = "a" * 32, "b" * 32


def _run(run=RUN, *, returned: float | None = 30.0, kind="loop_session") -> list[rec.Event]:
    return [event(run, "run_started", data={"origin_kind": kind, "origin_sha256": sha(run)}),
            event(run, "loop_round", "1", seconds=5, data={"round": 1}),
            event(run, "report_returned", seconds=30, data={} if returned is None else {"run_seconds": returned},
                  missing=("run_seconds",) if returned is None else ())]


def _question(run, number, seconds, *, kind="question_shown", ident="q1") -> rec.Event:
    return event(run, kind, f"{ident}:{number}", seconds=number, data={} if seconds is None else {"seconds": seconds},
                 refs=[rec.Ref("question", sha(ident), sha(ident))])


def _call(request, *, tool="g6_review_patch", args="a", seconds: float | None = 0.5) -> rec.Event:
    return event(RUN, "tool_call", f"{tool}:{request}",
                 data={"tool": tool, "args_sha256": sha(args), **({} if seconds is None else {"seconds": seconds})})


def test_questions_and_person_seconds_come_from_shown_entries():
    found = friction([*_run(), _question(RUN, 1, 9.0), _question(RUN, 2, 4.5), _question(RUN, 3, 2.0, ident="q2"),
                      _question(RUN, 4, 6.0, kind="feedback_shown", ident="f1")])
    assert found.questions == 2 and found.question_seconds == 21.5
    unmeasured = friction([*_run(), _question(RUN, 1, 9.0), _question(RUN, 2, None)])
    assert unmeasured.question_seconds is None and "question_seconds" in unmeasured.missing
    assert friction(_run()).question_seconds == 0.0 and friction(_run()).questions == 0


def test_retry_is_counted_apart_from_the_logical_call():
    found = friction([*_run(), _call("r1"), _call("r2"), _call("r3", args="b"), _call("r4", tool="g6_premortem")])
    assert (found.tool_calls, found.tool_retries) == (4, 1)          # r2 repeats r1's arguments; the others do not
    assert friction([*_run(), _call("r1")]).tool_retries == 0


def test_negative_or_cross_restart_duration_is_unmeasured():
    measured = friction([*_run(), _call("r1", seconds=2.0)])
    assert measured.wall_seconds == 30.0 and measured.active_g6_seconds == 2.0 and measured.wait_seconds == 28.0
    restarted = friction([*_run(returned=None), _call("r1", seconds=2.0)])          # the run spanned a restart
    assert restarted.wall_seconds is None and restarted.wait_seconds is None
    assert {"wall_seconds", "wait_seconds"} <= set(restarted.missing)
    negative = friction([*_run(returned=-3.0), _call("r1", seconds=2.0)])           # a clock that went backwards
    assert negative.wall_seconds is None and "wall_seconds" in negative.missing
    absurd = friction([*_run(returned=1.0), _call("r1", seconds=2.0)])              # more active time than wall time
    assert absurd.wait_seconds is None and "wait_seconds" in absurd.missing
    two = friction([*_run(), *_run(OTHER, returned=None)])                          # one run of two unmeasured
    assert two.wall_seconds is None
    abandoned = friction(_run()[:2])                                                 # a run that never returned
    assert abandoned.wall_seconds is None and "wall_seconds" in abandoned.missing
    assert friction([*_run(), _call("r1", seconds=None)]).active_g6_seconds is None


def test_provider_calls_come_from_reviews_and_loop_rounds_or_are_unmeasured():
    review = event(OTHER, "review_finished", data={"outcome": "defect_detected", "model_calls": 2, "review_seconds": 8.0})
    found = friction([*_run(), review])
    assert found.provider_calls == 3 and found.provider_retries is None and "provider_retries" in found.missing
    unknown = friction([*_run(), dataclasses.replace(review, data={"outcome": "defect_detected"})])
    assert unknown.provider_calls is None and "provider_calls" in unknown.missing


def test_no_division_by_zero_with_zero_checked_outcomes():
    rows = [friction([*_run(), _call("r1")]), friction([*_run(OTHER), _call("r2")])]
    none = per_checked_outcome(rows, 0)
    assert none["tasks"] == 2 and none["totals"]["tool_calls"] == 2
    assert set(none["per_checked_outcome"].values()) == {None}
    one = per_checked_outcome(rows, 1)
    assert one["per_checked_outcome"]["tool_calls"] == 2.0 and one["per_checked_outcome"]["wall_seconds"] == 60.0


def test_failed_and_abandoned_runs_are_counted_not_excluded():
    failed = friction([*_run()[:2], event(RUN, "loop_ended", data={"ending": "open", "code": "G6_E_NO_CLEAN_REVIEW"}),
                       _call("r1")])
    abandoned = friction([*_run(OTHER)[:2], _call("r2"), _call("r3")])
    total = per_checked_outcome([failed, abandoned], 0)
    assert total["tasks"] == 2 and total["totals"]["tool_calls"] == 3
    assert total["totals"]["wall_seconds"] is None and "wall_seconds" in total["not_measured"]


def test_person_effort_is_never_inferred_from_elapsed_time():
    names = {f.name for f in dataclasses.fields(Friction)}
    assert not {n for n in names if any(word in n for word in ("person", "effort", "human", "minutes", "saved"))}
    assert "question_seconds" in names
    # hours pass between two events with nothing recorded: no counter grows, and nothing reads the gap
    quiet = [*_run()[:2], event(RUN, "loop_round", "2", seconds=9 * 3600, data={"round": 2})]
    found = friction(quiet)
    assert found.wall_seconds is None and found.active_g6_seconds is None and found.question_seconds == 0.0
    assert set(ALWAYS_MISSING) <= set(found.missing)


def test_friction_reads_no_file_and_no_clock():
    import ast
    from pathlib import Path

    from g6.harness.workflow import friction as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {
        alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert not names & {"st_mtime", "getmtime", "stat", "time", "os", "datetime", "monotonic", "Path"}
