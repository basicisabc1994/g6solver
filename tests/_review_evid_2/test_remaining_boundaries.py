"""Review 2: offline reproductions; all durable state stays beside this file."""
import copy
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

import pytest

from g6.harness import patch_report, patch_review
from g6.core.ledger import digest
from g6.harness.workflow import adapters, evidence, lifecycle
from g6.harness.workflow.record import ProjectRecord
from g6.mcp import review_reply, workflow
from g6.metaharness.jobs import Jobs
from g6.safety_engine.weakening import detect_test_weakening
from g6.workforce import repo_tests
from tests.harness.test_evidence_card import assessment
from tests.workflow.kit import at, make_repo


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / "disk" / uuid4().hex
    path.mkdir(parents=True)
    return path.resolve()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    mkdir = os.mkdir
    monkeypatch.setattr(os, "mkdir", lambda path, mode=0o777, **kw: mkdir(path, 0o777, **kw))
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_WORKFLOW_RECORD", raising=False)
    monkeypatch.delenv("G6_WORKFLOW_VIEW", raising=False)
    monkeypatch.setenv("G6_CLAUDE", r"C:\nonexistent\g6-no-host.exe")
    monkeypatch.setenv("G6_CODEX", r"C:\nonexistent\g6-no-host.exe")


def completed_job(repo):
    value = assessment()
    value["card"] = patch_report.card(value)
    value["report"] = patch_report.safe_render(value)
    jobs = Jobs()
    started = jobs.start(lambda: {"ok": True, "assessment": copy.deepcopy(value)},
                         compact=workflow.finishing(review_reply.finish, str(repo), "task"))
    ident = started["job_id"]
    jobs._jobs[ident].result(timeout=20)
    assert jobs.status(ident)["status"] == "completed"
    return jobs, ident


def observe_pytest(repo, selection, scratch):
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    return subprocess.run([sys.executable, "-m", "pytest", selection, "-q", "-p", "no:cacheprovider",
                           "--basetemp", str(scratch)], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("action", ["keep", "delete", "off", "expire"],
                         ids=["retained_control", "deleted_record", "record_off", "expired_record"])
def test_job_poll_refreshes_record_custody(tmp_path, monkeypatch, action):
    repo = make_repo(tmp_path / "repo")
    jobs, ident = completed_job(repo)
    try:
        assert jobs.status(ident)["result"]["card"]["caught"]["reproduced"] == 1
        project, task = adapters.identities(repo, "task")
        if action == "delete":
            assert lifecycle.delete(project, scope="task", ident=task, by="Owner")["status"] == "deleted"
            assert ProjectRecord(project).events() == ()
        elif action == "off":
            monkeypatch.setenv("G6_WORKFLOW_RECORD", "off")
        elif action == "expire":
            result = lifecycle.expire(project, now=datetime.now(UTC) + timedelta(days=181))
            assert result["status"] == "expired"
            assert ProjectRecord(project).events() == ()
        fresh = evidence.read(repo, "task", origin_kind="review_job", origin_id=ident)
        expected = 1 if action == "keep" else None
        assert patch_report.card({"workflow_evidence": fresh})["caught"]["reproduced"] == expected
        polled = jobs.status(ident)["result"]
        assert polled["card"]["caught"]["reproduced"] == expected
    finally:
        jobs.close()


@pytest.mark.parametrize("detail", ["compact", "full"], ids=["compact_control", "full_reply"])
def test_job_details_agree_on_observed_evidence(tmp_path, detail):
    repo = make_repo(tmp_path / "repo")
    jobs, ident = completed_job(repo)
    try:
        compact = jobs.status(ident)["result"]["assessment"]
        assert compact["card"]["caught"]["reproduced"] == 1
        selected = jobs.status(ident, detail)["result"]["assessment"]
        assert selected["card"] == compact["card"]
    finally:
        jobs.close()


@pytest.mark.parametrize("verdict", ["passed", "no_tests_collected"],
                         ids=["ran_control", "nothing_collected"])
def test_selected_check_without_execution_is_not_checked(tmp_path, verdict):
    repo = make_repo(tmp_path / "repo")
    (repo / "test_case.py").write_text("def test_case():\n    assert True\n" if verdict == "passed" else
                                       "# no collected tests\n", encoding="utf-8")
    observed = observe_pytest(repo, "test_case.py", tmp_path / "child")
    assert observed.returncode == (0 if verdict == "passed" else 5), observed.stdout + observed.stderr
    value = assessment()
    # _record is the producer of scope.checks_run; tests lists selections,
    # including when pytest collected no tests. No confinement is requested.
    run = patch_review._record(1, "stated checks and extra tests", ["test_case.py"],
                               {"verdict": verdict, "passed": int(verdict == "passed"), "failed": 0})
    value["scope"]["checks_run"] = [run]
    value["completion"]["defects_reproduced"]["stated_and_repository_checks"] = []
    value["requirements"] = [{"id": "R1", "checks": [{"id": "test_case.py", "verdict": verdict}]}]
    value["scope"]["environment"] = {}
    value["outcome"] = "supported_within_scope" if verdict == "passed" else "insufficient_evidence"
    result = workflow.finishing(lambda body, job: body, str(repo), "task")({"assessment": value}, "job")
    project, task = adapters.identities(repo, "task")
    rows = ProjectRecord(project).events(task=task)
    actual = evidence.checked(rows), sum(r["count"] for r in result["card"]["checked"]["runs"])
    assert actual == (verdict == "passed", int(verdict == "passed"))


@pytest.mark.parametrize("changed_check", [False, True], ids=["same_check_control", "replaced_check"])
def test_repair_requires_the_same_check_bytes(tmp_path, monkeypatch, changed_check):
    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    test = repo / "test_case.py"
    test.write_text("from module import VALUE\ndef test_case():\n    assert VALUE == 2\n", encoding="utf-8")
    original_source = test.read_text(encoding="utf-8")
    before = patch_review._check_shas(repo, [node])[node]
    observed = observe_pytest(repo, node, tmp_path / "before")
    assert observed.returncode == 1, observed.stdout + observed.stderr
    first = assessment()
    first["requirements"] = [{"id": "R1", "checks": [{"id": node, "verdict": "failed"}]}]
    first["scope"]["checks_run"] = [patch_review._record(1, "stated checks and extra tests", [node],
                                                       {"verdict": "failed", "passed": 0, "failed": 1})]
    first["scope"]["artifact_digest"] = digest(repo_tests.snapshot(repo))
    assert adapters.observe_review(repo, "task", "first", {"assessment": first},
                                   started_at=at(), finished_at=at(1)) == ""
    if changed_check:
        test.write_text("def test_case():\n    assert True\n", encoding="utf-8")
    else:
        (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    after = patch_review._check_shas(repo, [node])[node]
    assert (before != after) == changed_check
    observed = observe_pytest(repo, node, tmp_path / "after")
    assert observed.returncode == 0, observed.stdout + observed.stderr
    second = assessment()
    second["requirements"] = [{"id": "R1", "checks": [{"id": node, "verdict": "passed"}]}]
    second["scope"]["checks_run"] = [patch_review._record(1, "stated checks and extra tests", [node],
                                                        {"verdict": "passed", "passed": 1, "failed": 0})]
    second["scope"]["artifact_digest"] = digest(repo_tests.snapshot(repo))
    second["completion"]["defects_reproduced"]["stated_and_repository_checks"] = []
    if changed_check:
        scan = detect_test_weakening({"test_case.py": original_source},
                                    {"test_case.py": test.read_text(encoding="utf-8")})
        assert scan.weakened and scan.reasons
        second["outcome"] = "test_integrity_compromised"
        second["reasons"] = [{"stage": "integrity", "code": "test_weakened", "detail": reason}
                             for reason in scan.reasons]
    times = iter([at(2), at(3)])
    monkeypatch.setattr(workflow, "now", lambda: next(times))
    result = workflow.finishing(lambda body, job: body, str(repo), "task")({"assessment": second}, "second")
    assert result["assessment"]["outcome"] == second["outcome"]
    assert result["card"]["repaired"] == int(not changed_check)


@pytest.mark.parametrize("surface", ["card", "record"])
@pytest.mark.parametrize("labelled", [False, True], ids=["unlabelled_control", "retained_person_label"])
def test_card_does_not_deny_a_retained_person_label(tmp_path, labelled, surface):
    repo = make_repo(tmp_path / "repo")
    assert adapters.observe_review(repo, "task", "job", {"assessment": assessment()},
                                   started_at=at(), finished_at=at(1)) == ""
    project, task = adapters.identities(repo, "task")
    record = ProjectRecord(project)
    target = next(e for e in record.events(task=task) if e.kind == "review_finished")
    if labelled:
        (record.root / "corrections.json").write_text(json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA,
            "corrections": [{"action": "annotate", "target": target.event_id,
                             "note": "intended behaviour", "by": "Owner"}]}), encoding="utf-8")
        assert lifecycle.apply_corrections(project)["applied"] == 1
        label, = record.annotations(task)
    held = evidence.read(repo, "task", origin_kind="review_job", origin_id="job")
    if labelled:
        assert label.event_id in {ref["event_id"] for ref in held["justification"]}
    if surface == "card":
        card = patch_report.card({"workflow_evidence": held})
        text = "\n".join(patch_report.card_lines(card))
    else:
        path = lifecycle.export(project)
        inspection = json.loads(path.read_text(encoding="utf-8"))
        if labelled:
            assert inspection["tasks"][0]["runs"][0]["derived"][0]["data"]["note"] == "intended behaviour"
        text = path.with_name("RECORD.md").read_text(encoding="utf-8")
    assert ("Not labelled by you" in text) == (not labelled)
