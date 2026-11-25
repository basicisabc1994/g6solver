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


# Review 2 amendment regressions, written before the product repair. Lead execution only.
def test_producer_inventory_records_test_file_digests(home, tmp_path):
    """Both outcome writers retain per-check file hashes, never test source text."""
    from g6.harness import patch_review

    repo = make_repo(tmp_path / "repo")
    source = b"def test_one():\n    assert True\ndef test_two():\n    assert True\n"
    (repo / "test_case.py").write_bytes(source)
    nodes = ["test_case.py::test_one", "test_case.py::test_two"]
    statuses = dict(zip(nodes, ("failed", "passed"), strict=True))
    value = assessment()
    value["requirements"] = [{"id": "R1", "checks": [{"id": n, "verdict": v} for n, v in statuses.items()]}]
    value["scope"]["checks_run"] = [patch_review._record(1, "stated checks and extra tests", nodes,
                                                       {"verdict": "failed", "passed": 1, "failed": 1})]
    assert adapters.observe_review(repo, "inventory", "job", {"assessment": value},
                                   started_at=at(), finished_at=at(1)) == ""
    state = {"reviews": [{"patch": sha("patch"), "results": statuses, "verdict": "defect"}]}
    assert adapters.observe_loop(repo, "inventory", tmp_path / "session", state, Step("stop", "clean"), at=at()) == ""
    held = evidence.read(repo, "inventory")
    outcomes = [e["data"] for e in held["events"] if e["kind"] in ("review_finished", "loop_round")]
    assert len(outcomes) == 2
    for data in outcomes:
        assert set(data["check_sources_sha256"]) == {sha(n) + ":" + evidence.sha256(source) for n in nodes}
        assert {p.partition(":")[0] for p in data["check_sources_sha256"]} == set(data["passed_sha256"] + data["failed_sha256"])
    assert source.decode() not in json.dumps(held)


def test_review_repairs_bind_source_and_integrity(home, tmp_path):
    from g6.harness import patch_review
    from g6.harness.workflow import record_md

    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    for mode in ("same", "changed", "compromised", "weakened"):
        path = repo / "test_case.py"
        path.write_text("def test_case():\n    assert 1 == 2\n", encoding="utf-8")
        for number, verdict in enumerate(("failed", "passed")):
            if number and mode == "changed":
                path.write_text("def test_case():\n    assert True\n", encoding="utf-8")
            value = assessment()
            value["requirements"] = [{"id": "R1", "checks": [{"id": node, "verdict": verdict}]}]
            value["scope"]["artifact_digest"] = sha(str(number))
            value["scope"]["checks_run"] = [patch_review._record(1, "stated checks and extra tests", [node],
                {"verdict": verdict, "passed": number, "failed": 1 - number})]
            if number and mode == "compromised":
                value["outcome"] = "test_integrity_compromised"
            if number and mode == "weakened":
                value["reasons"] = [{"code": "test_weakened", "node": node}]
            assert adapters.observe_review(repo, mode, str(number), {"assessment": value},
                                           started_at=at(number * 2), finished_at=at(number * 2 + 1)) == ""
        held = evidence.read(repo, mode, origin_kind="review_job", origin_id="1")
        expected = int(mode == "same")
        assert patch_report.card({"workflow_evidence": held})["repaired"] == expected
        rows = [evidence.Event.from_body(e) for e in held["events"]]
        assert record_md.counts(rows)["repaired"] == expected
        assert f"{expected} repaired" in record_md.render(rows)


def test_digest_presence_is_symmetric_and_legacy_records_keep_the_rule():
    from dataclasses import replace

    from tests.workflow.kit import event

    check = sha("case")
    for before, after, expected in ((None, None, 1), ("A", "A", 1), ("A", "B", 0),
                                    (None, "A", 0), ("A", None, 0)):
        rows = []
        for number, source in enumerate((before, after)):
            data = {"failed_sha256" if number == 0 else "passed_sha256": [check]}
            if source:
                data["check_sources_sha256"] = [check + ":" + sha(source)]
            rows.append(replace(event(str(number) * 32, "review_finished", seconds=number, data=data),
                                artifact_sha256=sha(str(number))))
        assert len(evidence.check_state(rows)["repaired"]) == expected


def test_selected_runs_count_executed_tests_and_keep_an_unchecked_reason(home, tmp_path):
    from g6.harness import patch_review
    from g6.mcp import workflow

    repo = make_repo(tmp_path / "repo")
    for executed in (0, 2):
        value = assessment()
        value["completion"]["defects_reproduced"]["stated_and_repository_checks"] = []
        value["scope"]["checks_run"] = [patch_review._record(1, "stated checks and extra tests", ["test_case.py"],
            {"verdict": "passed" if executed else "no_tests_collected", "passed": executed, "failed": 0})]
        result = workflow.finishing(lambda reply, job: reply, str(repo), str(executed))({"assessment": value}, "job")
        held = evidence.read(repo, str(executed))
        rows = [evidence.Event.from_body(e) for e in held["events"]]
        assert sum(r["count"] for r in result["card"]["checked"]["runs"]) == executed
        assert evidence.checked(rows) == bool(executed)
        if not executed:
            assert result["card"]["checked"]["unchecked"]
            assert "no_tests_collected" in result["card"]["checked"]["reason"]


def test_completed_job_evidence_is_read_on_each_poll_for_both_details(home, tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from g6.harness.workflow import lifecycle
    from g6.mcp import review_reply, workflow
    from g6.metaharness.jobs import Jobs

    repo = make_repo(tmp_path / "repo")
    for action in ("retain", "delete", "expire", "off"):
        monkeypatch.delenv("G6_WORKFLOW_RECORD", raising=False)
        value = assessment()
        jobs = Jobs()
        try:
            ident = jobs.start(lambda: {"assessment": value},
                compact=workflow.finishing(review_reply.finish, str(repo), action))["job_id"]
            jobs._jobs[ident].result(timeout=20)
            for frozen in (jobs._jobs[ident].result(), jobs._compact[ident]):
                retained = frozen.read()["result"]
                assert "card" not in retained and "card" not in retained["assessment"]
                assert "workflow_evidence" not in retained["assessment"]
            first = jobs.status(ident)["result"]["assessment"]
            assert first["card"]["caught"]["reproduced"] == 1
            if action == "delete":
                project, task = adapters.identities(repo, action)
                lifecycle.delete(project, scope="task", ident=task, by="fixture")
            elif action == "expire":
                project, _ = adapters.identities(repo, action)
                lifecycle.expire(project, now=datetime.now(UTC) + timedelta(days=181))
            elif action == "off":
                monkeypatch.setenv("G6_WORKFLOW_RECORD", "off")
            compact = jobs.status(ident)["result"]["assessment"]
            full = jobs.status(ident, "full")["result"]["assessment"]
            assert compact["card"] == full["card"]
            assert compact["card"]["caught"]["reproduced"] == (1 if action == "retain" else None)
            assert full["outcome"] == value["outcome"]
            if action != "retain":
                assert full["card"]["justification"] == []
        finally:
            jobs.close()


def test_retained_finding_label_is_shown_and_cited_on_both_summaries(home, tmp_path):
    from g6.harness.workflow import record_md
    from tests.workflow.kit import event

    repo = make_repo(tmp_path / "repo")
    assert adapters.observe_review(repo, "labels", "job", {"assessment": assessment()},
                                   started_at=at(), finished_at=at(1)) == ""
    project, task = adapters.identities(repo, "labels")
    record = ProjectRecord(project)
    target = next(e for e in record.events(task=task) if e.kind == "review_finished")
    for labelled in (False, True):
        if labelled:
            label = event(target.run, "annotation", "label", project=project, task=task,
                          provenance="attributed_label", data={"target": target.event_id, "by": "fixture", "note": "intended"})
            assert record.annotate(label)
        held = evidence.read(repo, "labels", origin_kind="review_job", origin_id="job")
        card = patch_report.card({"workflow_evidence": held})
        texts = ["\n".join(patch_report.card_lines(card)), record_md.render([
            evidence.Event.from_body(e) for e in held["events"]])]
        for text in texts:
            assert ("Not labelled by you" in text) == (not labelled)
            if labelled:
                assert "intended" in text and label.event_id in text


def test_record_prefers_runner_source_capture_to_later_test_replacement(home, tmp_path, monkeypatch):
    from g6.harness import patch_review
    from g6.workforce import repo_tests

    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    path = repo / "test_case.py"
    original = b"def test_case():\n    assert False\n"
    path.write_bytes(original)

    def run(*args, **kwargs):
        path.write_bytes(b"def test_case():\n    assert True\n")
        return {"verdict": "failed", "passed": 0, "failed": 1,
                "test_outcomes": {node: {"setup": "passed", "call": "failed", "teardown": "passed"}}}

    monkeypatch.setattr(repo_tests, "run", run)
    sources = evidence.source_digests(repo, [node])
    ran = patch_review._execute(repo, [node], timeout_s=1)
    value = assessment()
    value["requirements"] = [{"id": "R1", "checks": [{"id": node, "verdict": "failed"}]}]
    value["scope"]["checks_run"] = [patch_review._record(1, "stated checks and extra tests", [node], ran, sources=sources)]
    assert adapters.observe_review(repo, "capture", "job", {"assessment": value},
                                   started_at=at(), finished_at=at(1)) == ""
    row = next(e for e in evidence.read(repo, "capture")["events"] if e["kind"] == "review_finished")
    assert row["data"]["check_sources_sha256"] == [sha(node) + ":" + evidence.sha256(original)]


def test_missing_runner_source_identity_cannot_be_written_as_an_outcome(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    value = assessment()
    value["requirements"] = [{"id": "R1", "checks": [{"id": node, "verdict": "passed"}]}]
    value["scope"]["checks_run"] = [{"tier": 1, "tests": [node], "passed": 1, "failed": 0,
                                      "check_sources": {}, "executed_outcomes": {node: "passed"}}]
    assert adapters.observe_review(repo, "missing", "job", {"assessment": value},
                                   started_at=at(), finished_at=at(1))
    state = {"reviews": [{"patch": sha("patch"), "verdict": "none", "results": {node: "passed"}, "check_sources": {}}]}
    assert adapters.observe_loop(repo, "missing", tmp_path / "session", state, Step("stop", "clean"), at=at())
    assert not any(e["kind"] in ("review_finished", "loop_round") for e in evidence.read(repo, "missing")["events"])


def test_one_finding_label_does_not_label_another_open_finding():
    from tests.workflow.kit import event

    first = event("a" * 32, "review_finished", data={"reproduced": 1, "failed_sha256": [sha("first")], "checks_run": 1})
    second = event("b" * 32, "review_finished", seconds=1,
                   data={"reproduced": 1, "failed_sha256": [sha("second")], "checks_run": 1})
    label = event(first.run, "annotation", "label", provenance="attributed_label",
                  data={"target": first.event_id, "by": "fixture", "note": "intended"})
    held = {**evidence.bundle([first, second, label]), "selected_run": second.run}
    text = "\n".join(patch_report.card_lines(patch_report.card({"workflow_evidence": held})))
    assert "intended" in text and label.event_id in text
    assert "Not labelled by you" in text


def test_model_attempt_without_a_reply_is_not_a_check(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    for returned in (False, True):
        value = assessment()
        value["scope"]["checks_run"] = []
        value["scope"]["search"]["model_calls"] = 1
        value["completion"]["activities"] = [{"activity": "model call", "model_calls": 1,
                                                "result": "replied" if returned else "error"}]
        assert adapters.observe_review(repo, str(returned), "job", {"assessment": value},
                                       started_at=at(), finished_at=at(1)) == ""
        held = evidence.read(repo, str(returned))
        assert evidence.checked([evidence.Event.from_body(e) for e in held["events"]]) == returned


# Amendment 2: reply schemas are independent of the new evidence observations.
def test_completed_full_review_preserves_the_producers_rendered_reply_shape(home, tmp_path, monkeypatch):
    from copy import deepcopy
    from pathlib import Path

    from g6.mcp import review_reply, workflow
    from g6.metaharness.jobs import Jobs

    monkeypatch.delenv("G6_WORKFLOW_VIEW", raising=False)
    repo = make_repo(tmp_path / "repo")
    fixture = Path(__file__).parents[1] / "core" / "data" / "worked_example_review_reply.json"
    legacy = json.loads(fixture.read_text(encoding="utf-8"))
    modern = {"ok": True, "assessment": assessment()}
    modern["assessment"]["card"] = patch_report.card(modern["assessment"])
    modern["assessment"]["report"] = patch_report.safe_render(modern["assessment"])
    for name, original in (("legacy", legacy), ("modern", modern)):
        jobs = Jobs()
        try:
            ident = jobs.start(lambda: deepcopy(original),
                compact=workflow.finishing(review_reply.finish, str(repo), name))["job_id"]
            jobs._jobs[ident].result(timeout=20)
            full = jobs.status(ident, "full")["result"]
            assert full.keys() == original.keys()
            assert full["assessment"].keys() == original["assessment"].keys()
            if name == "legacy":
                assert full == original
                assert json.loads((home / "reviews" / f"{ident}.json").read_text(encoding="utf-8")) == original
            else:
                compact = jobs.status(ident)["result"]
                assert "workflow_evidence" not in compact["assessment"]
                assert full["assessment"]["card"] == compact["assessment"]["card"]
                assert full["assessment"]["card"]["caught"]["reproduced"] == 1
            monkeypatch.setenv("G6_WORKFLOW_RECORD", "off")
            refreshed = jobs.status(ident, "full")["result"]
            if name == "legacy":
                assert refreshed == original
            else:
                assert refreshed.keys() == original.keys()
                assert refreshed["assessment"].keys() == original["assessment"].keys()
                assert refreshed["assessment"]["card"]["justification"] == []
        finally:
            monkeypatch.delenv("G6_WORKFLOW_RECORD", raising=False)
            jobs.close()


def test_review_check_source_capture_preserves_grounding_source_names(home, tmp_path, monkeypatch):
    from tests.harness.test_patch_review import ADD, BAD, GOOD, NEGATIVE, TEST, Recorded, _git, result, review

    repo = make_repo(tmp_path / "repo")
    (repo / "calc.py").write_text(BAD, encoding="utf-8")
    (repo / "test_calc.py").write_text(TEST, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "addition fixture")
    (repo / "calc.py").write_text(GOOD, encoding="utf-8")
    Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]), ("test_calc.py",): result([ADD, NEGATIVE])})
    sources = {"spec": "Refunds take 14 days."}
    for claim in ("Refunds are issued within 30 days.", ""):
        got = review(repo, claim=claim, sources=sources)
        assert got["assessment"]["outcome"] == "supported_within_scope"
        assert {run["tier"] for run in got["assessment"]["scope"]["checks_run"]} == {1, 2}
        assert all(run["check_sources"] for run in got["assessment"]["scope"]["checks_run"])
        grounding = got["grounding"]
        assert set(grounding["sources"]) == {"spec"}
        assert grounding["sources"]["spec"]["authority"] == "caller-supplied"
        assert grounding["decides_outcome"] is False
        assert grounding["status"] == ("run" if claim else "not run")
        if claim:
            assert grounding["completion_state"] == "blocked-escalated"
    assert sources == {"spec": "Refunds take 14 days."}
