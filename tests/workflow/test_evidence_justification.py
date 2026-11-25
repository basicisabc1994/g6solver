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
    sources = evidence.source_digests(repo, nodes)
    value["scope"]["checks_run"] = [patch_review._record(1, "stated checks and extra tests", nodes,
                                                       {"verdict": "failed", "passed": 1, "failed": 1}, sources=sources)]
    assert adapters.observe_review(repo, "inventory", "job", {"assessment": value},
                                   started_at=at(), finished_at=at(1)) == ""
    state = {"reviews": [{"patch": sha("patch"), "results": statuses, "verdict": "defect", "check_sources": sources}]}
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
            sources = evidence.source_digests(repo, [node])
            value["scope"]["checks_run"] = [patch_review._record(1, "stated checks and extra tests", [node],
                {"verdict": verdict, "passed": number, "failed": 1 - number}, sources=sources)]
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


# Review 3: written first; runtime execution belongs to the lead's lane.
def test_imported_check_identity_includes_its_body_before_execution(home, tmp_path):
    from g6.harness import patch_review
    from tests.harness.test_patch_review import result

    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    (repo / "test_case.py").write_text("from checks_impl import test_case\n", encoding="utf-8")
    body = repo / "checks_impl.py"
    body.write_text("def test_case():\n    assert False\n", encoding="utf-8")
    collector = evidence.source_digests(repo, [node])
    before = evidence.capture_sources(repo, [node])
    first = patch_review._record(1, "checks", [node], result(failed=[node]), sources=before)
    body.write_text("def test_case():\n    assert True\n", encoding="utf-8")
    assert evidence.source_digests(repo, [node]) == collector
    assert evidence.capture_sources(repo, [node]) != before
    # The earlier capture survives a later edit; an unchanged capture is stable.
    assert first["check_sources"][node] == before[node]
    assert evidence.capture_sources(repo, [node]) == evidence.capture_sources(repo, [node])


def test_unknown_loading_is_not_an_executed_repair_pass(home, tmp_path):
    from g6.harness import patch_review
    from tests.harness.test_patch_review import result

    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    (repo / "test_case.py").write_text("def test_case():\n    assert True\n", encoding="utf-8")
    sources = evidence.source_digests(repo, [node])
    for known in (False, True):
        ran = result(passed=[node])
        ran.update(loaded_complete=known, loaded_outside={})
        run = patch_review._record(1, "checks", ["test_case.py"], ran, sources=sources)
        assert run["executed_outcomes"] == ({node: "passed"} if known else {})
        value = assessment()
        value["requirements"] = [{"id": "R1", "checks": [{"id": "test_case.py",
            "verdict": "passed" if known else "changed_code_not_loaded"}]}]
        value["requirements_without_adequate_check"] = [] if known else ["R1"]
        value["scope"]["checks_run"] = [run]
        data = evidence.review_data(value, repo)
        assert data["passed_sha256"] == ([sha(node)] if known else [])
        assert data["stated_checks"] == int(known)
        assert data["not_established"] == int(not known)
        if not known:
            assert "changed_code_not_loaded" in data["unchecked_reason"]


def test_finished_adapters_never_capture_replacement_source(home, tmp_path, monkeypatch):
    from g6.harness import patch_review

    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    (repo / "test_case.py").write_text("def test_case():\n    assert True\n", encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("post-run source read")
    monkeypatch.setattr(evidence, "source_digests", forbidden)
    value = assessment()
    value["requirements"] = [{"id": "R1", "checks": [{"id": node, "verdict": "failed"}]}]
    value["scope"]["checks_run"] = [patch_review._record(1, "checks", [node],
        {"verdict": "failed", "passed": 0, "failed": 1})]
    assert evidence.review_data(value, repo)["check_sources_sha256"] == []
    assert adapters.observe_loop(repo, "task", tmp_path / "session", {"reviews": [
        {"patch": sha("A"), "results": {node: "failed"}, "verdict": "defect"}]},
        Step("stop", "open"), at=at()) == ""
    row = next(e for e in evidence.read(repo, "task")["events"] if e["kind"] == "loop_round")
    assert row["data"]["check_sources_sha256"] == []


def test_truncated_findings_stay_incomplete_on_every_projection(home, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from g6.harness import patch_review
    from g6.harness.workflow import record_md
    from g6.workforce import coding_certificate

    repo = make_repo(tmp_path / "repo")
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *a: repo)
    for number in (50, 51):
        nodes = [f"test_case.py::test_case[{i:03}]" for i in range(number)]
        for turn in range(2):
            statuses = dict.fromkeys(nodes if turn == 0 else nodes[:50], "failed" if turn == 0 else "passed")
            value = assessment()
            value["requirements"] = [{"id": "R1", "checks": [{"id": n, "verdict": v} for n, v in statuses.items()]}]
            value["scope"]["artifact_digest"] = sha(str(turn))
            value["scope"]["checks_run"] = [patch_review._record(1, "checks", nodes,
                {"verdict": "failed" if turn == 0 else "passed", "passed": 50 if turn else 0,
                 "failed": number if not turn else 0})]
            value["completion"]["defects_reproduced"]["stated_and_repository_checks"] = (
                [{"check": n} for n in nodes] if not turn else [])
            assert adapters.observe_review(repo, str(number), str(turn), {"assessment": value},
                started_at=at(turn * 2), finished_at=at(turn * 2 + 1)) == ""
        held = evidence.read(repo, str(number), origin_kind="review_job", origin_id="1")
        rows = [evidence.Event.from_body(e) for e in held["events"]]
        card = patch_report.card({"workflow_evidence": held})
        assert card["open"] == number - 50
        assert card.get("identities_incomplete", False) == (number > 50)
        assert record_md.counts(rows)["open"] == number - 50
        text = record_md.render(rows)
        assert ("incomplete" in text) == (number > 50)
        assert ("repaired in a recorded review" in text) == (number == 50)
        missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": str(number)})
        cited = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
        assert cited.get("identities_incomplete", False) == (number > 50)


def test_missing_or_dynamic_test_body_identity_never_repairs(home, tmp_path):
    from g6.harness import patch_review
    from g6.harness.workflow import record_md
    from tests.harness.test_patch_review import result

    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    for source in ("from missing_impl import test_case\n",
                   "import importlib\ntest_case = importlib.import_module('body').test_case\n"):
        (repo / "test_case.py").write_text(source, encoding="utf-8")
        sources = evidence.capture_sources(repo, [node])
        assert sources == {node: ""}
        for turn in range(2):
            value = assessment()
            verdict = "failed" if turn == 0 else "passed"
            value["requirements"] = [{"id": "R1", "checks": [{"id": node, "verdict": verdict}]}]
            value["scope"]["artifact_digest"] = sha(str(turn))
            ran = result(failed=[node]) if turn == 0 else result(passed=[node])
            value["scope"]["checks_run"] = [patch_review._record(1, "checks", [node], ran, sources=sources)]
            assert adapters.observe_review(repo, source, str(turn), {"assessment": value},
                started_at=at(turn * 2), finished_at=at(turn * 2 + 1)) == ""
        rows = [evidence.Event.from_body(e) for e in evidence.read(repo, source)["events"]]
        assert record_md.counts(rows)["repaired"] == 0


def test_imported_test_alias_and_relative_body_are_captured(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    package = repo / "cases"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "test_case.py").write_text("from .body import case as test_case\n", encoding="utf-8")
    body = package / "body.py"
    body.write_text("def case():\n    assert False\n", encoding="utf-8")
    node = "cases/test_case.py::test_case"
    before = evidence.capture_sources(repo, [node])
    body.write_text("def case():\n    assert True\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) != before


def test_loop_identity_cap_retains_unresolved_count_and_marker(home, tmp_path):
    from g6.harness.workflow import record_md

    repo = make_repo(tmp_path / "repo")
    for number in (50, 51):
        names = [f"case-{i:03}" for i in range(number)]
        reviews = [{"patch": sha("before"), "verdict": "defect", "results": dict.fromkeys(names, "failed")},
                   {"patch": sha("after"), "verdict": "none", "results": dict.fromkeys(names[:50], "passed")}]
        state = {"reviews": reviews}
        assert adapters.observe_loop(repo, str(number), tmp_path / str(number), state, Step("stop", "open"), at=at()) == ""
        held = evidence.read(repo, str(number))
        card = patch_report.card({}, {**state, "workflow_evidence": held})
        assert card["open"] == number - 50
        assert card.get("identities_incomplete", False) == (number > 50)
        rows = [evidence.Event.from_body(e) for e in held["events"]]
        ending = next(e for e in rows if e.kind == "loop_ended")
        assert ending.data["open"] == record_md.counts(rows)["open"] == number - 50


def test_omitted_identities_are_not_added_again_when_later_retained():
    from dataclasses import replace

    from tests.workflow.kit import event

    first = replace(event("a" * 32, "review_finished", data={
        "failed_sha256": [sha("known")], "identities_incomplete": True, "failed_identities_omitted": 1}),
        artifact_sha256=sha("first"))
    later = replace(event("b" * 32, "review_finished", seconds=1,
        data={"failed_sha256": [sha("previously omitted")]}), artifact_sha256=sha("later"))
    counts = evidence.check_counts([first, later])
    assert counts["reproduced"] == counts["open"] == 2


def test_loading_rejected_requirement_cannot_be_overridden_by_node_phases(home, tmp_path):
    from g6.harness import patch_review
    from tests.harness.test_patch_review import result

    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    (repo / "test_case.py").write_text("def test_case():\n    assert True\n", encoding="utf-8")
    sources = evidence.source_digests(repo, [node])
    for known in (True, False):
        value = assessment()
        value["requirements"] = [{"id": "R1", "checks": [{"id": "test_case.py",
            "verdict": "passed" if known else "changed_code_not_loaded"}]}]
        value["scope"]["checks_run"] = [patch_review._record(1, "checks", ["test_case.py"],
            result(passed=[node]), sources=sources)]
        data = evidence.review_data(value, repo)
        assert data["passed_sha256"] == ([sha(node)] if known else [])
        assert data["stated_checks"] == int(known)

# Build 1 amendment 1: written before the repair; the lead runs these.
def test_machinery_identity_tracks_roles_and_preserves_product_repairs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    node = "test_case.py::test_case"
    collectors = (
        "from oracle import checked\nfrom module import VALUE\n@checked\ndef test_case():\n    assert VALUE > 0\n",
        "from oracle import observed\ndef test_case(observed):\n    assert observed == 2\n",
        "import pytest\nfrom oracle import cases\n@pytest.mark.parametrize('value', cases())\n"
        "def test_case(value):\n    assert value == 2\n",
        "from forwarding import case as test_case\n",
    )
    oracle = ("import pytest\nfrom module import VALUE\n"
              "def checked(fn):\n    return fn\n@pytest.fixture\ndef observed():\n    return VALUE\n"
              "def cases():\n    return [VALUE]\ndef case():\n    assert VALUE == 2\n")
    (repo / "forwarding.py").write_text("from oracle import case\n", encoding="utf-8")
    for collector in collectors:
        (repo / "test_case.py").write_text(collector, encoding="utf-8")
        (repo / "oracle.py").write_text(oracle, encoding="utf-8")
        (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
        before = evidence.capture_sources(repo, [node])
        assert before[node]
        (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
        assert evidence.capture_sources(repo, [node]) == before
        (repo / "oracle.py").write_text(oracle + "# changed machinery\n", encoding="utf-8")
        assert evidence.capture_sources(repo, [node]) != before


def test_relative_machinery_identity_uses_package_and_rejects_escape(tmp_path):
    repo = tmp_path / "repo"
    package = repo / "pkg" / "sub"
    package.mkdir(parents=True)
    for folder in (package, package.parent):
        (folder / "__init__.py").write_text("", encoding="utf-8")
    collector = package / "test_case.py"
    collector.write_text("from ..checks_impl import case as test_case\n", encoding="utf-8")
    body = package.parent / "checks_impl.py"
    body.write_text("def case():\n    assert False\n", encoding="utf-8")
    shadow = repo / "checks_impl.py"
    shadow.write_text("def case():\n    assert True\n", encoding="utf-8")
    node = "pkg/sub/test_case.py::test_case"
    before = evidence.capture_sources(repo, [node])
    assert before[node]
    shadow.write_text("# changed shadow\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) == before
    body.write_text("def case():\n    assert True\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) != before
    collector.write_text("from ....checks_impl import case as test_case\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) == {node: ""}


def test_first_review_card_uses_executed_identity_counts():
    from g6.harness.workflow import record_md
    from tests.workflow.kit import event

    row = event("a" * 32, "review_finished", data={
        "reproduced": 1, "failed_sha256": [sha("case[0]"), sha("case[1]")], "stated_checks": 2})
    card = patch_report.card({"workflow_evidence": evidence.bundle([row])})
    assert card["open"] == evidence.check_counts([row])["open"] == record_md.counts([row])["open"] == 2


def test_conftest_and_transitive_machinery_are_in_identity(tmp_path):
    repo = tmp_path / "repo"
    cases = repo / "cases"
    cases.mkdir(parents=True)
    (cases / "test_case.py").write_text("def test_case(observed):\n    assert observed == 2\n", encoding="utf-8")
    (repo / "conftest.py").write_text("from forwarding import observed\n", encoding="utf-8")
    (repo / "forwarding.py").write_text("from oracle import observed\n", encoding="utf-8")
    (repo / "oracle.py").write_text("import pytest\nfrom decorators import checked\n"
        "@pytest.fixture\n@checked\ndef observed():\n    return 2\n", encoding="utf-8")
    helper = repo / "decorators.py"
    helper.write_text("def checked(fn):\n    return fn\n", encoding="utf-8")
    node = "cases/test_case.py::test_case"
    before = evidence.capture_sources(repo, [node])
    assert before[node]
    helper.write_text("def checked(fn):\n    return fn\n# changed\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) != before
    before = evidence.capture_sources(repo, [node])
    (repo / "conftest.py").write_text("from forwarding import observed\n# changed\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) != before


def test_unresolved_machinery_is_incomplete_but_body_imports_are_excluded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    node = "test_case.py::test_case"
    for source in (
        "import oracle\n@getattr(oracle, 'checked')\ndef test_case():\n    pass\n",
        "import importlib\nchecked = importlib.import_module('oracle').checked\n"
        "@checked\ndef test_case():\n    pass\n",
        "from missing import checked\n@checked\ndef test_case():\n    pass\n",
        "if True:\n    from oracle import checked\n@checked\ndef test_case():\n    pass\n",
    ):
        (repo / "test_case.py").write_text(source, encoding="utf-8")
        assert evidence.capture_sources(repo, [node]) == {node: ""}
    (repo / "test_case.py").write_text("from module import VALUE\ndef test_case():\n    assert VALUE == 2\n",
                                       encoding="utf-8")
    before = evidence.capture_sources(repo, [node])
    assert before[node]
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) == before


def test_parametrize_aliases_values_indirect_and_fixture_chains(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_case.py").write_text(
        "from pytest import mark, param\nimport oracle as o\nfrom forwarding import observed\n"
        "@mark.parametrize('observed', [param(o.VALUE)], indirect=True)\n"
        "def test_case(observed):\n    assert observed == 2\n", encoding="utf-8")
    (repo / "forwarding.py").write_text("from fixtures import observed\n", encoding="utf-8")
    (repo / "fixtures.py").write_text("import pytest\nfrom dependencies import seed\n"
        "@pytest.fixture\ndef observed(request, seed):\n    return request.param\n", encoding="utf-8")
    (repo / "dependencies.py").write_text("import pytest\n@pytest.fixture\ndef seed():\n    return 1\n",
                                          encoding="utf-8")
    (repo / "oracle.py").write_text("VALUE = 2\n", encoding="utf-8")
    node = "test_case.py::test_case"
    before = evidence.capture_sources(repo, [node])
    assert before[node]
    for relative in ("oracle.py", "dependencies.py", "forwarding.py"):
        path = repo / relative
        original = path.read_text(encoding="utf-8")
        path.write_text(original + "# changed\n", encoding="utf-8")
        assert evidence.capture_sources(repo, [node]) != before
        path.write_text(original, encoding="utf-8")



def test_shared_open_counts_retain_aggregate_only_findings():
    from g6.harness.workflow import record_md
    from tests.workflow.kit import event

    row = event("a" * 32, "review_finished", data={"reproduced": 1})
    counts = evidence.check_counts([row])
    card = patch_report.card({"workflow_evidence": evidence.bundle([row])})
    assert counts["open"] == record_md.counts([row])["open"] == card["open"] == 1
    assert counts["repaired"] == 0


def test_imported_class_and_aliased_parametrize_are_static_machinery(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_case.py").write_text("from oracle import Cases as TestCases\n", encoding="utf-8")
    (repo / "oracle.py").write_text(
        "from pytest import mark\ncases = mark.parametrize\nfrom data import VALUES\n"
        "@cases('value', VALUES)\nclass Cases:\n"
        "    def test_case(self, value):\n        assert value == 2\n", encoding="utf-8")
    data = repo / "data.py"
    data.write_text("VALUES = [2]\n", encoding="utf-8")
    node = "test_case.py::TestCases::test_case"
    before = evidence.capture_sources(repo, [node])
    assert before[node]
    data.write_text("VALUES = [3]\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) != before


def test_runtime_collection_and_wildcard_machinery_are_incomplete(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    node = "test_case.py::test_case"
    for source in ("from oracle import *\n", "exec('def test_case(): pass')\n",
                   "from importlib import import_module as load\nload('oracle')\n",
                   "from oracle import checked\nchecked = checked()\n@checked\ndef test_case(): pass\n"):
        (repo / "test_case.py").write_text(source, encoding="utf-8")
        assert evidence.capture_sources(repo, [node]) == {node: ""}
    (repo / "test_case.py").write_text("def test_case(): pass\n", encoding="utf-8")
    (repo / "conftest.py").write_text("pytest_plugins = ['oracle']\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) == {node: ""}


def test_imported_autouse_and_named_fixtures_are_machinery(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_case.py").write_text("def test_case(observed):\n    assert observed == 2\n", encoding="utf-8")
    (repo / "conftest.py").write_text("from fixtures import automatic, named\n", encoding="utf-8")
    (repo / "fixtures.py").write_text(
        "import pytest\nfrom oracle import seed\n@pytest.fixture(autouse=True)\n"
        "def automatic(seed):\n    assert seed == 2\n@pytest.fixture(name='observed')\n"
        "def named():\n    return 2\n", encoding="utf-8")
    helper = repo / "oracle.py"
    helper.write_text("import pytest\n@pytest.fixture\ndef seed():\n    return 2\n", encoding="utf-8")
    node = "test_case.py::test_case"
    before = evidence.capture_sources(repo, [node])
    assert before[node]
    helper.write_text("import pytest\n@pytest.fixture\ndef seed():\n    return 3\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) != before


def test_ambiguous_absolute_machinery_cannot_lend_a_shadow_digest(tmp_path):
    repo = tmp_path / "repo"
    cases = repo / "cases"
    cases.mkdir(parents=True)
    (cases / "test_case.py").write_text("from oracle import case as test_case\n", encoding="utf-8")
    for path in (repo / "oracle.py", cases / "oracle.py"):
        path.write_text("def case():\n    assert True\n", encoding="utf-8")
    node = "cases/test_case.py::test_case"
    assert evidence.capture_sources(repo, [node]) == {node: ""}


# Repair 1 amendment 1: test-first regressions; execution belongs to the lead.
def test_literal_fixture_access_tracks_sources_and_dynamic_access_withholds(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    node = "test_case.py::test_case"
    collector = repo / "test_case.py"
    collector.write_text("def test_case(request):\n    assert request.getfixturevalue('observed') == 2\n",
                         encoding="utf-8")
    (repo / "conftest.py").write_text("from oracle import observed\n", encoding="utf-8")
    oracle = repo / "oracle.py"
    original = "import pytest\nfrom module import VALUE\n@pytest.fixture\ndef observed():\n    return VALUE\n"
    oracle.write_text(original, encoding="utf-8")
    before = evidence.capture_sources(repo, [node])
    assert before[node]
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) == before
    oracle.write_text(original + "# changed fixture\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) != before
    for access in ("request.getfixturevalue(name)", "lookup('observed')",
                   "getattr(request, 'getfixturevalue')('observed')"):
        collector.write_text("def test_case(request):\n    name = 'observed'\n"
                             "    lookup = request.getfixturevalue\n    assert " + access + " == 2\n",
                             encoding="utf-8")
        assert evidence.capture_sources(repo, [node]) == {node: ""}


def test_class_fixture_imports_autouse_and_marks_bind_machinery(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    node = "test_case.py::TestCase::test_case"
    oracle = repo / "oracle.py"
    for mark in ("", "    pytestmark = pytest.mark.usefixtures('automatic')\n",
                 "    @pytest.mark.usefixtures('automatic')\n"):
        (repo / "test_case.py").write_text(
            "import pytest\nclass TestCase:\n    from oracle import automatic\n" + mark +
            "    def test_case(self):\n        pass\n", encoding="utf-8")
        original = ("import pytest\nfrom module import VALUE\n@pytest.fixture(autouse=" +
                    str(not mark) + ")\ndef automatic(self):\n    self.observed = VALUE\n")
        oracle.write_text(original, encoding="utf-8")
        before = evidence.capture_sources(repo, [node])
        assert before[node]
        (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
        assert evidence.capture_sources(repo, [node]) == before
        oracle.write_text(original + "# changed class machinery\n", encoding="utf-8")
        assert evidence.capture_sources(repo, [node]) != before


def test_executed_and_defining_fixture_chains_both_enter_identity(tmp_path):
    repo = tmp_path / "repo"
    cases = repo / "cases"
    cases.mkdir(parents=True)
    node = "cases/test_case.py::test_case"
    (repo / "checks_impl.py").write_text(
        "from static_fixtures import observed\ndef test_case(observed):\n    assert observed == 2\n",
        encoding="utf-8")
    fixture = "import pytest\n@pytest.fixture\ndef observed():\n    return 2\n"
    for name in ("static_fixtures.py", "executed_fixtures.py", "outer_fixtures.py"):
        (repo / name).write_text(fixture, encoding="utf-8")
    (repo / "conftest.py").write_text("from outer_fixtures import observed\n", encoding="utf-8")
    for scope in ("collector", "conftest"):
        (cases / "test_case.py").write_text(
            "from checks_impl import test_case\n" +
            ("from executed_fixtures import observed\n" if scope == "collector" else ""), encoding="utf-8")
        (cases / "conftest.py").write_text(
            "from executed_fixtures import observed\n" if scope == "conftest" else "", encoding="utf-8")
        before = evidence.capture_sources(repo, [node])
        assert before[node]
        for name in ("static_fixtures.py", "executed_fixtures.py"):
            path = repo / name
            path.write_text(fixture + "# changed fixture chain\n", encoding="utf-8")
            assert evidence.capture_sources(repo, [node]) != before
            path.write_text(fixture, encoding="utf-8")


def test_parametrization_factory_and_decorator_defaults_are_sources(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    node = "test_case.py::test_case"
    data = repo / "oracle.py"
    for signature in ("values=EXPECTED", "*, values=EXPECTED"):
        for role in ("factory", "decorator"):
            (repo / "factory.py").write_text(
                "import pytest\nfrom oracle import EXPECTED\n" +
                (f"def cases({signature}):\n    return values\n" if role == "factory" else
                 f"def cases({signature}):\n    return pytest.mark.parametrize('value', values)\n"),
                encoding="utf-8")
            (repo / "test_case.py").write_text(
                "import pytest\nfrom factory import cases\nfrom module import VALUE\n" +
                ("@pytest.mark.parametrize('value', cases())\n" if role == "factory" else "@cases()\n") +
                "def test_case(value):\n    assert VALUE == value\n", encoding="utf-8")
            data.write_text("EXPECTED = [2]\n", encoding="utf-8")
            before = evidence.capture_sources(repo, [node])
            # A custom decorator may supply names the static reader cannot establish.
            # In that case it must withhold; it may never retain an unchanged identity.
            if role == "factory":
                assert before[node]
            (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
            assert evidence.capture_sources(repo, [node]) == before
            data.write_text("EXPECTED = [3]\n", encoding="utf-8")
            after = evidence.capture_sources(repo, [node])
            assert not after[node] or after != before


def test_review_totals_use_executed_identities_for_every_selection(home, tmp_path):
    from types import SimpleNamespace

    from g6.harness import patch_review
    from g6.harness.workflow import record_md
    from g6.workforce import coding_certificate
    from tests.harness.test_patch_review import result

    repo = make_repo(tmp_path / "repo")
    nodes = ["test_case.py::TestCase::test_case[0]", "test_case.py::TestCase::test_case[1]"]
    for selection in (["test_case.py"], ["test_case.py::TestCase"], nodes):
        value = assessment()
        value["requirements"] = [{"id": "R1", "checks": [{"id": n, "verdict": "failed"} for n in selection]}]
        value["completion"]["defects_reproduced"]["stated_and_repository_checks"] = [{"check": n} for n in selection]
        value["scope"]["checks_run"] = [patch_review._record(1, "checks", selection, result(failed=nodes))]
        assert evidence.review_data(value)["reproduced"] == 2
        task = str(selection)
        assert adapters.observe_review(repo, task, "job", {"assessment": value},
                                       started_at=at(), finished_at=at(1)) == ""
        held = evidence.read(repo, task, origin_kind="review_job", origin_id="job")
        rows = [evidence.Event.from_body(e) for e in held["events"]]
        card = patch_report.card({"workflow_evidence": held})
        assert card["caught"]["reproduced"] == card["open"] == record_md.counts(rows)["reproduced"] == 2
        assert "2 reproduced" in record_md.render(rows)
        # The certificate companion cites the same observations and shared counts.
        from unittest.mock import patch
        with patch.object(coding_certificate.bench, "_repo", return_value=repo):
            missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": task})
            companion = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
        assert companion["events"] == held["justification"]
        assert adapters.observe_review(repo, task, "later-job", {"assessment": value},
                                       started_at=at(2), finished_at=at(3)) == ""
        later = evidence.read(repo, task, origin_kind="review_job", origin_id="later-job")
        later_rows = [evidence.Event.from_body(e) for e in later["events"]]
        later_card = patch_report.card({"workflow_evidence": later})
        assert later_card["caught"]["reproduced"] == later_card["open"] == record_md.counts(later_rows)["reproduced"] == 2


def test_inherited_class_fixtures_and_overrides_bind_both_sources(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "base.py").write_text("class Base:\n    from inherited import observed\n"
        "    def test_case(self, observed):\n        assert observed == 2\n", encoding="utf-8")
    fixture = "import pytest\n@pytest.fixture\ndef observed(self):\n    return 2\n"
    for name in ("inherited.py", "override.py"):
        (repo / name).write_text(fixture, encoding="utf-8")
    node = "test_case.py::TestCase::test_case"
    for override in ("", "    from override import observed\n"):
        (repo / "test_case.py").write_text("from base import Base\nclass TestCase(Base):\n" +
            (override or "    def test_case(self, observed):\n        assert observed == 2\n"), encoding="utf-8")
        before = evidence.capture_sources(repo, [node])
        assert before[node]
        for name in (("inherited.py", "override.py") if override else ("inherited.py",)):
            path = repo / name
            path.write_text(fixture + "# changed inherited machinery\n", encoding="utf-8")
            assert evidence.capture_sources(repo, [node]) != before
            path.write_text(fixture, encoding="utf-8")


def test_module_and_class_marks_resolve_in_the_collecting_class(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    node = "test_case.py::TestCase::test_case"
    original = "import pytest\n@pytest.fixture\ndef observed(self):\n    return 2\n"
    oracle = repo / "oracle.py"
    for level in ("module", "class", "decorator"):
        source = "import pytest\n"
        if level == "module":
            source += "pytestmark = pytest.mark.usefixtures('observed')\n"
        if level == "decorator":
            source += "@pytest.mark.usefixtures('observed')\n"
        source += "class TestCase:\n    from oracle import observed\n"
        if level == "class":
            source += "    pytestmark = pytest.mark.usefixtures('observed')\n"
        source += "    def test_case(self):\n        pass\n"
        (repo / "test_case.py").write_text(source, encoding="utf-8")
        oracle.write_text(original, encoding="utf-8")
        before = evidence.capture_sources(repo, [node])
        assert before[node]
        oracle.write_text(original + "# changed mark-requested fixture\n", encoding="utf-8")
        assert evidence.capture_sources(repo, [node]) != before
    (repo / "test_case.py").write_text(
        "class TestCase:\n    exec('from oracle import observed')\n    def test_case(self):\n        pass\n",
        encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) == {node: ""}


def test_literal_repository_plugin_fixture_sources_are_bound(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    node = "test_case.py::test_case"
    (repo / "test_case.py").write_text("def test_case(observed):\n    assert observed == 2\n", encoding="utf-8")
    (repo / "conftest.py").write_text("pytest_plugins = ['local_plugin']\n", encoding="utf-8")
    (repo / "local_plugin.py").write_text("from oracle import observed\n", encoding="utf-8")
    oracle = repo / "oracle.py"
    original = "import pytest\nfrom module import VALUE\n@pytest.fixture\ndef observed():\n    return VALUE\n"
    oracle.write_text(original, encoding="utf-8")
    before = evidence.capture_sources(repo, [node])
    assert before[node]
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) == before
    oracle.write_text(original + "# changed plugin fixture\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) != before


def test_executed_outcomes_without_capture_drive_shared_review_totals():
    from g6.harness import patch_review
    from g6.harness.workflow import record_md
    from tests.harness.test_patch_review import result
    from tests.workflow.kit import event

    failed = ["test_case.py::TestCase::test_case[0]", "test_case.py::TestCase::test_case[1]"]
    passed = "test_case.py::TestCase::test_passed"
    skipped = "test_case.py::TestCase::test_skipped"
    for selection in (["test_case.py"], ["test_case.py::TestCase"], failed):
        run = patch_review._record(1, "checks", selection,
                                   result(failed=failed, passed=[passed], skipped=[skipped]))
        assert run["executed_outcomes"] == {**dict.fromkeys(failed, "failed"), passed: "passed"}
        assert run["executed_tests"] == 3
        assert "check_sources" not in run  # No post-run source capture.
        value = assessment()
        value["requirements"] = [{"id": "R1", "checks": [{"id": n, "verdict": "failed"} for n in selection]}]
        value["scope"]["checks_run"] = [run, run]  # Repeated observations do not add identities.
        data = evidence.review_data(value)
        assert data["failed_sha256"] == sorted(sha(n) for n in failed)
        assert data["passed_sha256"] == [sha(passed)]
        assert data["check_sources_sha256"] == []
        row = event("a" * 32, "review_finished", data=data)
        shared = evidence.check_counts([row])
        card = patch_report.card({"workflow_evidence": evidence.bundle([row])})
        assert data["reproduced"] == shared["reproduced"] == shared["open"] == 2
        assert card["caught"]["reproduced"] == card["open"] == record_md.counts([row])["reproduced"] == 2
