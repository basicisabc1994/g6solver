# SPDX-License-Identifier: Apache-2.0
"""Project, run and event identities (design 17.2): one repository is one project however its path is written, and a
job id or a task-hash and base pair is never a workflow-run id."""
from __future__ import annotations

import os
import re

import pytest

from g6.core.cloud_consent import repository_source
from g6.core.ledger import digest
from g6.harness.workflow import record as rec
from tests.workflow.kit import BASE, PROJECT, TASK, started


def test_project_id_matches_the_consent_source_hash(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    project = rec.project_id(repo)
    assert re.fullmatch(r"[0-9a-f]{64}", project)
    assert repository_source(repo) == "repository:" + project
    assert rec.workflow_source(project) == "workflow:" + project
    with pytest.raises(ValueError):
        rec.workflow_source("not-a-project")


def test_project_id_same_for_case_and_trailing_separator(tmp_path):
    repo = tmp_path / "Repo"
    repo.mkdir()
    project = rec.project_id(repo)
    assert rec.project_id(str(repo) + os.sep) == project
    assert rec.project_id(str(repo) + os.sep + ".") == project
    assert rec.project_id(repo / "sub" / "..") == project
    if os.path.normcase("A") == "a":                 # Windows: one folder whatever the letter case
        assert rec.project_id(str(repo).upper()) == project == rec.project_id(str(repo).lower())
    other = tmp_path / "other"
    other.mkdir()
    assert rec.project_id(other) != project


def test_job_id_and_task_base_pair_are_never_a_run_id(home):
    record = rec.ProjectRecord(PROJECT)
    job = "job-0123456789abcdef"
    run = started(record, job, kind="review_job")
    assert re.fullmatch(r"[0-9a-f]{32}", run)
    assert run not in (job, TASK, BASE, f"{TASK}:{BASE}", digest([TASK, BASE]), digest([TASK, BASE])[:32])
    first = record.events(run=run)[0]
    assert first.kind == "run_started" and job not in str(first.to_body())      # the origin is kept as a digest
    assert first.data["origin_kind"] == "review_job"


def test_event_id_is_stable_and_distinct():
    one = rec.event_id("a" * 32, "tool_call", "request-1")
    assert one == rec.event_id("a" * 32, "tool_call", "request-1") and re.fullmatch(r"[0-9a-f]{64}", one)
    assert len({one, rec.event_id("b" * 32, "tool_call", "request-1"), rec.event_id("a" * 32, "loop_round", "request-1"),
                rec.event_id("a" * 32, "tool_call", "request-2")}) == 4


def test_unknown_origin_kind_is_refused(home):
    with pytest.raises(rec.EventRefused):
        rec.ProjectRecord(PROJECT).start_run(TASK, "somewhere", "x", baseline_sha256=BASE)
    with pytest.raises(ValueError):
        rec.ProjectRecord("../escape")
