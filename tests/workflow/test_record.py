# SPDX-License-Identifier: Apache-2.0
"""The project-scoped workflow record (design 17.2 "The record"): identities, retries, restarts, what an event may
hold, and a store that is unreadable rather than empty when it is damaged."""
from __future__ import annotations

import dataclasses
import os

import pytest

from g6.harness.workflow import record as rec
from tests.workflow.kit import BASE, OTHER_PROJECT, OTHER_TASK, PROJECT, TASK, event, started


def test_event_round_trips(home):
    record = rec.ProjectRecord(PROJECT)
    run = started(record)
    one = event(run, "loop_round", "1", seconds=5, data={"round": 1, "open_findings": 2, "seconds": 1.5},
                refs=[rec.Ref("loop_state", "round-1", BASE)], missing=["provider_calls"], observed=["tool_calls"])
    assert record.append(one) is True
    kinds = [e.kind for e in rec.ProjectRecord(PROJECT).events(task=TASK)]
    assert kinds == ["run_started", "loop_round"]
    assert rec.ProjectRecord(PROJECT).events(run=run)[1] == one
    assert one.to_body()["schema"] == rec.SCHEMA


def test_store_works_when_g6_home_does_not_exist_yet(home):
    assert not home.exists()
    record = rec.ProjectRecord(PROJECT)
    assert record.events() == () and record.runs(TASK) == () and record.tasks() == ()
    assert not home.exists()                       # reading creates nothing
    started(record)
    assert (home / "workflow" / PROJECT / "tasks" / TASK / "runs").is_dir()


def test_retry_with_same_event_id_is_not_appended_twice(home):
    record = rec.ProjectRecord(PROJECT)
    run = started(record)
    one = event(run, "tool_call", "request-7", seconds=1)
    assert record.append(one) is True
    assert record.append(one) is False
    assert record.append(dataclasses.replace(one, at=event(run, "x", seconds=9).at)) is False   # the retried call
    assert [e.kind for e in record.events(run=run)].count("tool_call") == 1


def test_restart_finds_the_same_run_by_origin(home):
    run = started(rec.ProjectRecord(PROJECT), "session-a")
    again = started(rec.ProjectRecord(PROJECT), "session-a", seconds=600)
    assert again == run and rec.ProjectRecord(PROJECT).runs(TASK) == (run,)
    assert len(rec.ProjectRecord(PROJECT).events(run=run)) == 1


def test_second_session_on_same_task_is_a_second_run_not_a_second_task(home):
    record = rec.ProjectRecord(PROJECT)
    first, second = started(record, "session-a"), started(record, "session-b", seconds=60)
    assert first != second and record.runs(TASK) == (first, second) and record.tasks() == (TASK,)
    assert started(record, "session-a", kind="review_job") not in (first, second)   # the origin's kind is part of it


@pytest.mark.parametrize("data", [{"text": "x"}, {"patch": "x"}, {"output": "x"}, {"answer": "x"}, {"n": "y" * 201},
                                  {"n": {"text": "x"}}, {"n": ["y" * 201]}, {"n": object()}, {"n": float("nan")},
                                  {1: "x"}, {"n": list(range(51))}])
def test_event_data_refuses_text_fields(data):
    with pytest.raises(rec.EventRefused) as refused:
        event("a" * 32, "loop_round", data=data)
    assert "y" * 50 not in str(refused.value)        # what was refused is never echoed


def test_event_without_sources_is_refused():
    one = event("a" * 32, "loop_round")
    for sources in ((), ("",), ("has a space",), "workflow:x"):
        with pytest.raises(rec.EventRefused):
            dataclasses.replace(one, sources=sources)


@pytest.mark.parametrize("provenance", ["person_confirmed,model_inference", ("person_confirmed", "model_inference"),
                                        "", "confirmed", None])
def test_provenance_kinds_stay_distinct(provenance):
    assert len(set(rec.PROVENANCE)) == 5
    with pytest.raises(rec.EventRefused):
        event("a" * 32, "loop_round", provenance=provenance)


def test_event_identities_and_time_are_checked():
    one = event("a" * 32, "loop_round")
    for change in ({"project": "p"}, {"task": "t"}, {"run": "r"}, {"event_id": "e"}, {"at": "yesterday"},
                   {"kind": ""}, {"baseline_sha256": "b"}, {"refs": ("review",)}, {"missing": ("x" * 201,)}):
        with pytest.raises(rec.EventRefused):
            dataclasses.replace(one, **change)
    for ref in (("somewhere", "x", BASE), ("review", "has a space", BASE), ("review", "x", "not-a-digest")):
        with pytest.raises(rec.EventRefused):
            rec.Ref(*ref)


def test_append_needs_its_run_started_first(home):
    record = rec.ProjectRecord(PROJECT)
    with pytest.raises(rec.EventRefused):
        record.append(event("a" * 32, "loop_round"))
    with pytest.raises(rec.EventRefused):
        record.append(event(started(record), "loop_round", project=OTHER_PROJECT))
    assert not (home / "workflow" / OTHER_PROJECT).exists()


def _run_file(home, run, *, project=PROJECT, task=TASK):
    return home / "workflow" / project / "tasks" / task / "runs" / f"{run}.jsonl"


def test_run_file_copied_from_another_project_is_unreadable(home):
    run = started(rec.ProjectRecord(PROJECT))
    for project, task in ((OTHER_PROJECT, TASK), (PROJECT, OTHER_TASK)):
        target = _run_file(home, run, project=project, task=task)
        target.parent.mkdir(parents=True)
        target.write_bytes(_run_file(home, run).read_bytes())
        with pytest.raises(rec.RecordUnreadable) as unreadable:
            rec.ProjectRecord(project).events(task=task)
        assert unreadable.value.code == "G6_E_WORKFLOW_RECORD_UNREADABLE" and unreadable.value.code in str(unreadable.value)
        assert str(home) not in str(unreadable.value)


def test_run_file_under_another_runs_name_is_unreadable(home):
    record = rec.ProjectRecord(PROJECT)
    run = started(record)
    _run_file(home, "b" * 32).write_bytes(_run_file(home, run).read_bytes())
    with pytest.raises(rec.RecordUnreadable):
        record.events(task=TASK)


def test_torn_last_line_is_unreadable_not_empty(home):
    record = rec.ProjectRecord(PROJECT)
    run = started(record)
    record.append(event(run, "loop_round", "1", seconds=1))
    path = _run_file(home, run)
    path.write_bytes(path.read_bytes()[:-40])
    with pytest.raises(rec.RecordUnreadable):
        rec.ProjectRecord(PROJECT).events(run=run)
    with pytest.raises(rec.RecordUnreadable):
        rec.ProjectRecord(PROJECT).append(event(run, "loop_round", "2", seconds=2))


def test_link_in_place_of_a_run_file_is_refused(home, tmp_path):
    record = rec.ProjectRecord(PROJECT)
    run = started(record)
    path = _run_file(home, run)
    elsewhere = tmp_path / "elsewhere.jsonl"
    os.replace(path, elsewhere)
    os.link(elsewhere, path)                         # a second name for a file kept elsewhere
    with pytest.raises(rec.RecordUnreadable):
        rec.ProjectRecord(PROJECT).events(run=run)
    path.unlink()
    try:
        os.symlink(elsewhere, path)
    except OSError:
        pytest.skip("this account cannot make a symbolic link; the hard link case ran")
    with pytest.raises(rec.RecordUnreadable):
        rec.ProjectRecord(PROJECT).events(run=run)


def test_folder_in_place_of_a_run_file_is_unreadable(home):
    record = rec.ProjectRecord(PROJECT)
    started(record)
    (_run_file(home, "c" * 32)).mkdir()
    with pytest.raises(rec.RecordUnreadable):
        record.events(task=TASK)


def test_two_starts_of_one_origin_at_once_give_one_run(home):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as pool:
        runs = set(pool.map(lambda _: started(rec.ProjectRecord(PROJECT), "session-z"), range(16)))
    assert len(runs) == 1 and rec.ProjectRecord(PROJECT).runs(TASK) == tuple(runs)
