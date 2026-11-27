"""Offline, real-disk counterexamples for claude/wire-evidence review 1."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import os
import tempfile

import pytest

from g6.harness import completion_loop as loop, patch_report
from g6.harness.workflow import adapters, evidence, lifecycle, record_md
from g6.harness.workflow.record import ProjectRecord
from g6.mcp import workflow
from g6.workforce import coding_certificate, repo_tests
from tests.harness.test_evidence_card import assessment
from tests.workflow.kit import Step, T0, at, event, make_repo, sha


@pytest.fixture
def tmp_path():
    # Avoid pytest's Windows mode-0700 temp directory, inaccessible to this sandbox.
    path = Path(__file__).parent / "disk" / uuid4().hex
    path.mkdir(parents=True)
    return path.resolve()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    # Keep git's TemporaryDirectory and all other scratch writes in the allowed tree.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    # Python 3.12's Windows 0700 ACL excludes this sandbox token. Preserve
    # inherited ACLs on new fixture directories; no evidence logic is changed.
    mkdir = os.mkdir
    monkeypatch.setattr(os, "mkdir", lambda path, mode=0o777, **kw: mkdir(path, 0o777, **kw))
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_WORKFLOW_RECORD", raising=False)
    monkeypatch.delenv("G6_WORKFLOW_VIEW", raising=False)
    monkeypatch.setenv("G6_CLAUDE", r"C:\nonexistent\g6-no-host.exe")
    monkeypatch.setenv("G6_CODEX", r"C:\nonexistent\g6-no-host.exe")


@pytest.mark.parametrize("overlap", [False, True], ids=["sequential_control", "overlapping_jobs"])
def test_current_review_card_uses_current_job(tmp_path, monkeypatch, overlap):
    repo = make_repo(tmp_path / "repo")
    earlier = assessment()
    earlier["scope"]["artifact_digest"] = sha("other job patch")
    assert adapters.observe_review(repo, "task", "other-job", {"assessment": earlier},
                                   started_at=at(30 if overlap else 0), finished_at=at(40)) == ""
    times = iter([at(0 if overlap else 50), at(100)])
    monkeypatch.setattr(workflow, "now", lambda: next(times))
    current = assessment()
    current["scope"]["artifact_digest"] = sha("current job patch")
    current["scope"]["checks_run"] = []
    current["completion"]["defects_reproduced"]["stated_and_repository_checks"] = []
    current["outcome"] = "insufficient_evidence"
    result = workflow.finishing(lambda reply, job: reply, str(repo), "task")(
        {"assessment": current}, "current-job")
    assert result["card"]["caught"]["reproduced"] == 0
    assert result["card"]["checked"]["artifact"] == current["scope"]["artifact_digest"]


@pytest.mark.parametrize("returns_to_original", [False, True], ids=["different_artifact_control", "same_artifact_pass"])
def test_loop_repairs_require_a_different_artifact(tmp_path, returns_to_original):
    repo = make_repo(tmp_path / "repo")
    reviews = [
        {"n": 1, "file": "case", "patch": sha("A"), "results": {"case": "failed"}, "verdict": "defect"},
        {"n": 2, "file": "other", "patch": sha("B"),
         "results": {"case": "did not run", "other": "failed"}, "verdict": "defect"},
        {"n": 3, "file": "last", "patch": sha("A" if returns_to_original else "C"),
         "results": {"case": "passed", "other": "passed"}, "verdict": "none"},
    ]
    state = {"reviews": reviews, "rounds": []}
    assert adapters.observe_loop(repo, "task", tmp_path / "session", state, Step("stop", "fixed"), at=at()) == ""
    held = evidence.read(repo, "task")
    card = patch_report.card({}, {**state, "workflow_evidence": held})
    expected = 1 if returns_to_original else 2
    assert card["repaired"] == expected


@pytest.mark.parametrize("too_long", [False, True], ids=["no_review_control", "refused_before_model_call"])
def test_pre_call_refusal_does_not_count_as_checked(tmp_path, too_long):
    repo = make_repo(tmp_path / "repo")
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    reviews = []
    if too_long:
        class NeverCalled:
            def complete(self, *args, **kwargs):
                raise AssertionError("No model call is permitted")

        failing, text = loop.review_round(repo_tests.change(repo, "HEAD"), "x" * 500_000,
                                         NeverCalled(), SimpleNamespace(critic="fixed", sources=()),
                                         tmp_path / "session", reviews, float("inf"))
        assert not failing and not text
        assert reviews[0]["error"] == loop.TOO_LONG and reviews[0]["results"] == {}
    state = {"reviews": reviews, "rounds": []}
    with adapters.hook_observation():
        assert adapters.observe_loop(repo, "task", tmp_path / "session", state, Step("stop", "untested"), at=at()) == ""
    project, task = adapters.identities(repo, "task")
    assert record_md.counts(ProjectRecord(project).events(task=task))["checked"] == 0


@pytest.mark.parametrize("labelled", [False, True], ids=["observations_control", "person_label"])
def test_certificate_cites_all_task_observations_and_labels(tmp_path, monkeypatch, labelled):
    repo = make_repo(tmp_path / "repo")
    assert adapters.observe_review(repo, "task", "job", {"assessment": assessment()},
                                   started_at=at(), finished_at=at(1)) == ""
    project, task = adapters.identities(repo, "task")
    record = ProjectRecord(project)
    observed = record.events(task=task)
    expected = {e.event_id for e in observed}
    if labelled:
        target = next(e for e in observed if e.kind == "review_finished")
        label = event(target.run, "annotation", "person-label", project=project, task=task,
                      provenance="attributed_label", data={"target": target.event_id,
                                                           "note": "intended behaviour", "by": "Owner"})
        assert record.annotate(label)
        assert record.annotations(task) == (label,)
        expected.add(label.event_id)
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *args: repo)
    missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": "task"})
    result = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
    assert {r["event_id"] for r in result["events"]} == expected


@pytest.mark.parametrize("days", [30, 31], ids=["retained_export", "expired_export"])
def test_readable_export_own_retention_control(tmp_path, days):
    repo = make_repo(tmp_path / "repo")
    assert adapters.observe_review(repo, "task", "job", {"assessment": assessment()},
                                   started_at=at(), finished_at=at(1)) == ""
    project, _ = adapters.identities(repo, "task")
    from datetime import timedelta

    path = lifecycle.export(project, now=T0)
    readable = path.with_name("RECORD.md")
    assert "1 reproduced findings" in readable.read_text(encoding="utf-8")
    lifecycle.expire(project, now=T0 + timedelta(days=days))
    assert readable.exists() == (days == 30)


@pytest.mark.parametrize("action", ["keep", "delete"], ids=["retained_control", "deleted_history"])
def test_replayed_card_respects_current_record_custody(tmp_path, monkeypatch, action):
    from g6.harness import completion_hook
    from g6.mcp import review_loop

    repo = make_repo(tmp_path / "repo")
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    session = completion_hook.folder("mcp:replay")
    session.mkdir(parents=True)
    asked = review_loop._asked(str(repo), "task", "HEAD", session)
    patch = loop._patch_now(repo, asked["base"])
    state = {"reviews": [{"n": 1, "file": "case", "patch": patch,
                          "verdict": "defect", "results": {"case": "failed"}}], "rounds": []}
    loop._record_report(repo, "task", asked["base"], session, state, Step("stop", "open"), "")
    card = patch_report.card({}, state)
    assert card["caught"]["reproduced"] == 1
    loop._finish(session, state, "# Done\n" + "\n".join(patch_report.card_lines(card)),
                 ending="open", patch=patch)
    if action == "delete":
        project, task = adapters.identities(repo, "task")
        assert lifecycle.delete(project, scope="task", ident=task, by="Owner")["status"] == "deleted"
        assert ProjectRecord(project).events() == ()
    result = review_loop.step(str(repo), "task", "replay")
    assert result["action"] == "stop"
    assert result["card"]["caught"]["reproduced"] == (1 if action == "keep" else None)


@pytest.mark.parametrize("mode", ["local", "unshared", "off"])
def test_fresh_reply_custody_storage_and_decided_fields_control(tmp_path, monkeypatch, mode):
    from copy import deepcopy
    from g6.core import cloud_consent as cloud

    repo = make_repo(tmp_path / "repo")
    if mode == "off":
        monkeypatch.setenv("G6_WORKFLOW_RECORD", "off")
    value = assessment()
    original = deepcopy(value)
    reply = {"ok": True, "completion_state": "qualified-draft", "request_id": "request",
             "artifact_digest": value["scope"]["artifact_digest"], "assessment": value}
    with cloud.collect_sources() as capture:
        if mode == "unshared":
            capture.client = ("openai:codex", None)
        result = workflow.finishing(lambda body, job: body, str(repo), "task")(reply, "job")
    for key in ("ok", "completion_state", "request_id", "artifact_digest"):
        assert result[key] == reply[key]
    assert {k: result["assessment"][k] for k in original} == original
    held = result["assessment"]["workflow_evidence"]
    assert held["status"] == {"local": "kept", "unshared": "withheld", "off": "off"}[mode]
    if mode != "local":
        assert held["events"] == held["justification"] == []
        assert result["card"]["caught"]["reproduced"] is None
    project, task = adapters.identities(repo, "task")
    root = ProjectRecord(project).root
    assert root.is_dir() == (mode != "off")
    if root.exists():
        assert tmp_path in root.parents
        assert all(tmp_path in p.parents for p in root.rglob("*"))
    assert not (repo / "workflow").exists()
