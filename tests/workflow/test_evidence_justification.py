"""Citations resolve to task-local record entries before rendering."""
from __future__ import annotations

import json

from g6.harness import patch_report
from g6.harness.workflow import adapters, evidence
from g6.harness.workflow.record import ProjectRecord
from tests.harness.test_evidence_card import assessment
from tests.workflow.kit import Step, at, make_repo, sha


def test_finished_review_records_counts_without_task_or_check_text(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    value = assessment()
    reply = {"assessment": value, "artifact_digest": sha("patch")}
    assert adapters.observe_review(repo, "fixture task", "job", reply, started_at=at(), finished_at=at(1)) == ""
    project, task = adapters.identities(repo, "fixture task")
    rows = ProjectRecord(project).events(task=task)
    snapshot = evidence.read(repo, "fixture task")
    value["workflow_evidence"] = snapshot
    cited = patch_report.card(value, None)["justification"]
    known = {e.event_id: e for e in rows}
    assert cited and all(c["event_id"] in known for c in cited)
    assert cited == evidence.bundle(rows)["justification"]
    text = json.dumps([e.to_body() for e in rows])
    assert "fixture task" not in text and "test_a::test_one" not in text


def test_unchecked_replay_is_one_event_with_a_stable_code(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    session = tmp_path / "session"
    step = Step("stop", "untested", "G6_E_NO_BOUNDARY")
    for _ in range(2):
        assert adapters.observe_unchecked(repo, "fixture task", session, step, at=at()) == ""
    rows = evidence.read(repo, "fixture task")["events"]
    unchecked = [e for e in rows if e["kind"] == "turn_unchecked"]
    assert len(unchecked) == 1 and unchecked[0]["data"]["reason"] == step.code


def test_failed_write_has_no_citations(home, tmp_path, monkeypatch):
    from g6.mcp import workflow

    repo = make_repo(tmp_path / "repo")
    reply = {"assessment": assessment()}
    monkeypatch.setattr(adapters, "observe_review", lambda *a, **k: "write failed")
    seen = []
    workflow.finishing(lambda result, job: seen.append(result) or result, str(repo), "fixture task")(reply, "job")
    assert seen[0]["assessment"]["card"]["justification"] == []
    assert "write_failed" in seen[0]["assessment"]["card"]["checked"]["reason"]


def test_unshared_record_does_not_withhold_the_mandatory_review(home, tmp_path):
    from g6.core import cloud_consent as cloud

    repo = make_repo(tmp_path / "repo")
    assert adapters.observe_review(repo, "fixture task", "job", {"assessment": assessment()},
                                   started_at=at(), finished_at=at(1)) == ""
    with cloud.collect_sources() as outer:
        outer.client = ("openai:codex", None)
        with cloud.collect_sources():
            held = evidence.read(repo, "fixture task")
        assert held["justification"] == []
        assert held["events"] == []
        assert held["status"] == "withheld"
        assert not any(s.source_id.startswith("workflow:") for s in outer.sources)
    project, task = adapters.identities(repo, "fixture task")
    assert len(ProjectRecord(project).events(task=task)) == 3


# Review 1 regressions: appended before the product repairs; the lead runs these.
def test_replay_card_reads_current_custody(home, tmp_path, monkeypatch):
    from contextlib import nullcontext

    from g6.harness import completion_hook, completion_loop
    from g6.harness.workflow import lifecycle
    from g6.mcp import review_loop

    repo = make_repo(tmp_path / "repo")
    session = tmp_path / "session"
    state = {"reviews": [{"n": 1, "file": "case", "patch": sha("first"),
                           "results": {"case": "failed"}, "verdict": "defect"}], "rounds": []}
    completion_loop._record_report(repo, "fixture task", "HEAD", session, state, Step("stop", "open"), "")
    saved = "# Done\n" + "\n".join(patch_report.card_lines(patch_report.card({}, state)))
    monkeypatch.setattr(completion_hook, "folder", lambda _: session)
    monkeypatch.setattr(completion_loop, "_held", lambda _: nullcontext())
    monkeypatch.setattr(review_loop, "_asked", lambda *a: {"repo": str(repo), "task": "fixture task", "base": "HEAD"})
    monkeypatch.setattr(completion_loop, "_load", lambda _: state)
    monkeypatch.setattr(completion_loop, "advance", lambda *a, **k: completion_loop.Step(
        "stop", saved, ending="open", note=completion_loop.ALREADY_ENDED))
    kept = review_loop._step(str(repo), "fixture task", "session")
    assert kept["card"]["caught"]["reproduced"] == 1
    project, task = adapters.identities(repo, "fixture task")
    lifecycle.delete(project, scope="task", ident=task, by="fixture")
    deleted = review_loop._step(str(repo), "fixture task", "session")
    assert deleted["card"]["caught"]["reproduced"] is None
    assert deleted["card"]["justification"] == []
    assert "- **Caught:** not recorded." in deleted["report"]


def test_review_card_is_bound_to_its_job_not_start_order(home, tmp_path, monkeypatch):
    from g6.mcp import workflow

    repo = make_repo(tmp_path / "repo")
    for overlap in (False, True):
        task = "overlap" if overlap else "sequential"
        assert adapters.observe_review(repo, task, "other", {"assessment": assessment()},
                                       started_at=at(30 if overlap else 0), finished_at=at(40)) == ""
        times = iter([at(0 if overlap else 50), at(100)])
        monkeypatch.setattr(workflow, "now", lambda: next(times))
        current = assessment()
        current["scope"]["artifact_digest"] = sha("current")
        current["completion"]["defects_reproduced"]["stated_and_repository_checks"] = []
        reply = workflow.finishing(lambda value, job: value, str(repo), task)({"assessment": current}, "current")
        assert reply["card"]["caught"]["reproduced"] == 0
        assert reply["card"]["checked"]["artifact"] == sha("current")


def test_loop_evidence_repairs_need_different_artifacts_on_all_projections(home, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from g6.harness.workflow import record_md
    from g6.workforce import coding_certificate

    repo = make_repo(tmp_path / "repo")
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *a: repo)
    for same in (False, True):
        task = "same" if same else "different"
        state = {"reviews": [
            {"n": 1, "file": "case", "patch": sha("A"), "results": {"case": "failed"}, "verdict": "defect"},
            {"n": 2, "file": "other", "patch": sha("B"),
             "results": {"case": "did not run", "other": "failed"}, "verdict": "defect"},
            {"n": 3, "file": "last", "patch": sha("A" if same else "C"),
             "results": {"case": "passed", "other": "passed"}, "verdict": "none"}], "rounds": []}
        assert adapters.observe_loop(repo, task, tmp_path / task, state, Step("stop", "fixed"), at=at()) == ""
        held = evidence.read(repo, task)
        expected = 1 if same else 2
        card = patch_report.card({}, {**state, "workflow_evidence": held})
        assert card["repaired"] == expected
        rows = [evidence.Event.from_body(body) for body in held["events"]]
        assert next(e for e in rows if e.kind == "loop_ended").data["repaired"] == expected
        assert record_md.counts(rows)["repaired"] == expected
        assert f"{expected} repaired" in record_md.render(rows)
        missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": task})
        cited = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
        ids = {ref["event_id"] for ref in cited["events"]}
        assert record_md.counts([e for e in rows if e.event_id in ids])["repaired"] == expected


def test_checked_turn_needs_a_run_or_returned_review(home, tmp_path):
    from g6.harness import completion_loop
    from g6.harness.workflow import record_md

    repo = make_repo(tmp_path / "repo")
    for name, reviews, expected in (
        ("none", [], 0),
        ("refused", [{"verdict": "failed", "results": {}, "error": completion_loop.TOO_LONG}], 0),
        ("returned", [{"verdict": "none", "results": {}}], 1),
        ("ran", [{"verdict": "failed", "results": {"case": "failed"}}], 1),
    ):
        with adapters.hook_observation():
            assert adapters.observe_loop(repo, name, tmp_path / name, {"reviews": reviews, "rounds": []},
                                         Step("stop", "untested"), at=at()) == ""
        rows = [evidence.Event.from_body(body) for body in evidence.read(repo, name)["events"]]
        counts = record_md.counts(rows)
        assert counts["checked"] == expected
        assert counts["turns"] == counts["checked"] + counts["unchecked"] == 1
        card = patch_report.card({}, {"reviews": reviews, "workflow_evidence": evidence.bundle(rows)})
        assert bool(card["checked"]["runs"]) == bool(expected)
        if name == "refused":
            assert counts["unchecked_reasons"] == {"review_not_run": 1}


def test_certificate_labels_follow_task_custody(home, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from g6.harness.workflow import lifecycle
    from g6.workforce import coding_certificate
    from tests.workflow.kit import event

    repo = make_repo(tmp_path / "repo")
    assert adapters.observe_review(repo, "fixture task", "job", {"assessment": assessment()},
                                   started_at=at(), finished_at=at(1)) == ""
    project, task = adapters.identities(repo, "fixture task")
    record = ProjectRecord(project)
    target = next(e for e in record.events(task=task) if e.kind == "review_finished")
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *a: repo)
    missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": "fixture task"})
    def cited():
        return coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})["events"]
    assert {r["event_id"] for r in cited()} == {e.event_id for e in record.events(task=task)}
    label = event(target.run, "annotation", "label", project=project, task=task,
                  provenance="attributed_label", data={"target": target.event_id, "by": "fixture", "note": "intended"})
    assert record.annotate(label)
    assert label.event_id in {r["event_id"] for r in cited()}
    lifecycle.delete(project, scope="task", ident=task, by="fixture")
    assert cited() == []


def test_absent_job_identity_never_borrows_task_counts(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    for number, job in enumerate(("one", "two")):
        assert adapters.observe_review(repo, "fixture task", job, {"assessment": assessment()},
                                       started_at=at(number * 10), finished_at=at(number * 10 + 1)) == ""
    missing = evidence.read(repo, "fixture task", origin_kind="review_job", origin_id="absent")
    for held in (missing, evidence.read(repo, "fixture task")):
        card = patch_report.card({"workflow_evidence": held})
        assert card["caught"]["reproduced"] is None
        assert card["justification"] == []


def test_review_card_excludes_other_loop_and_future_review_counts():
    from dataclasses import replace

    from tests.workflow.kit import event

    check = sha("case")
    earlier = event("a" * 32, "review_finished", data={"reproduced": 0})
    current = event("b" * 32, "review_finished", seconds=10, data={"reproduced": 0})
    loop_failed = replace(event("c" * 32, "loop_round", "one", data={"round": 1, "failed_sha256": [check]}),
                          artifact_sha256=sha("first"))
    loop_passed = replace(event("c" * 32, "loop_round", "two", data={"round": 2, "passed_sha256": [check]}),
                          artifact_sha256=sha("second"))
    future = event("d" * 32, "review_finished", seconds=100, data={"failed_sha256": [check], "reproduced": 1})
    for distractors in ([], [loop_failed, loop_passed], [future]):
        held = {**evidence.bundle([earlier, current, *distractors]), "selected_run": current.run}
        card = patch_report.card({"workflow_evidence": held})
        assert card["repaired"] == 0
        assert card["open"] == 0
