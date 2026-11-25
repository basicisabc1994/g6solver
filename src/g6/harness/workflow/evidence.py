# SPDX-License-Identifier: Apache-2.0
"""Small observed counts and task-local citations shared by the card and export.

Readers never mint events. A citation hashes the event's canonical body, so it
can be resolved from the inspection file without trusting report wording.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
import re

from g6.core.cloud_consent import sha256
from g6.core.ledger import digest
from g6.harness.workflow import policy
from g6.harness.workflow.record import Event, ProjectRecord


def _count(value) -> int:
    return len(value) if isinstance(value, (list, tuple, dict)) else 0


def source_digests(root, checks: Iterable[str]) -> dict[str, str]:
    """Hash regular test files inside the named tree; never retain their text."""
    top = Path(root).resolve()
    found, files = {}, {}
    for check in checks:
        file = check.partition("::")[0]
        path = top / file
        try:
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(top):
                if file not in files:
                    files[file] = sha256(path.read_bytes())
                found[check] = files[file]
        except OSError:
            continue
    return found


def outcome_sources(statuses: Mapping, sources: Mapping) -> dict:
    """Flat bounded pairs fit the record's data-only scalar/list schema."""
    names = sorted(n for n, v in statuses.items() if v in ("passed", "failed"))[:50]
    return {"check_sources_sha256": [sha256(n) + ":" + sources[n] for n in names if sources.get(n)]}


def executed(run: Mapping) -> int:
    """Actual terminal outcomes, never the number of selected files or node ids."""
    if "executed_tests" in run:
        return max(0, int(run["executed_tests"]))
    if "passed" in run or "failed" in run:
        return max(0, int(run.get("passed") or 0)) + max(0, int(run.get("failed") or 0))
    # Legacy outcome maps, unlike a list of selections, describe observations.
    tests = run.get("tests")
    return sum(v in (True, False, "passed", "failed") for v in tests.values()) if isinstance(tests, Mapping) else 0


def repair_blocked(assessment: Mapping) -> bool:
    """An integrity problem or a weakening detection cannot supply a repair pass."""
    return bool(assessment.get("outcome") == "test_integrity_compromised" or assessment.get("integrity_reasons")
                or any(r.get("stage") == "integrity" or "weaken" in str(r.get("code", ""))
                       for r in assessment.get("reasons") or []))


def review_data(assessment: Mapping, repo=None) -> dict:
    """Counts, model names and check digests only; no repository text."""
    scope = assessment.get("scope") or {}
    completion = assessment.get("completion") or {}
    defects = completion.get("defects_reproduced") or {}
    runs = [r for r in scope.get("checks_run") or [] if r.get("check") != "stated checks at the base commit"]
    requirements = assessment.get("requirements") or []
    # Only the stated checks' own terminal status counts. Base runs and a skipped,
    # absent or renamed check cannot supply the pass for an earlier failure.
    statuses = {}
    for requirement in requirements:
        for check in requirement.get("checks") or []:
            if isinstance(check, dict) and check.get("id") and check.get("observation") is None:
                statuses.setdefault(str(check["id"]), []).append(check.get("verdict"))
    sources, captured, executed_names = {}, set(), set()
    for run in runs:
        sources.update(run.get("check_sources") or {})
        if "check_sources" in run:
            captured.update(run.get("tests") or [])
        for name, verdict in (run.get("executed_outcomes") or {}).items():
            statuses.setdefault(name, []).append(verdict)
            executed_names.add(name)
    # A file/class selection is not an additional test beside its observed nodes.
    from g6.workforce.repo_tests import report_covers

    statuses = {name: values for name, values in statuses.items() if name in executed_names
                or not any(report_covers(name, node) for node in executed_names)}
    terminal = {n: "failed" if "failed" in v else "passed" if v and all(x == "passed" for x in v) else "unknown"
                for n, v in statuses.items()}
    terminal = {n: terminal[n] for n in sorted(terminal) if terminal[n] in ("passed", "failed")}
    terminal = dict(list(terminal.items())[:50])
    if any(n not in sources and any(report_covers(c, n) for c in captured) for n in terminal):
        raise ValueError("executed check source identity was not captured")
    # Direct adapter callers also record observations (including imported assessments).
    # A runner's earlier capture always wins over this fallback read.
    if repo is not None:
        fallback = [n for n in terminal if not any(report_covers(c, n) for c in captured)]
        sources = {**source_digests(repo, fallback), **sources}
    failed = sorted(sha256(n) for n, v in terminal.items() if v == "failed")
    passed = sorted(sha256(n) for n, v in terminal.items() if v == "passed")
    activities = completion.get("activities") or []
    proposer = next((str(a["model"])[:100] for a in activities if a.get("model") and a.get("model_calls")), "")
    data = {"requirements": len(requirements),
            "not_established": _count(assessment.get("requirements_without_adequate_check")),
            "reproduced": _count(defects.get("stated_and_repository_checks")),
            "unconfirmed": _count(defects.get("proposed_checks")),
            "integrity_reasons": sum(r.get("stage") == "integrity" for r in assessment.get("reasons") or []),
            "checks_run": sum(executed(r) > 0 for r in runs), "checks_skipped": _count(scope.get("checks_skipped")),
            "failed_sha256": failed[:50], "passed_sha256": passed[:50],
            "questions": _count(completion.get("open_questions")), "unknowns": _count(assessment.get("unknowns")),
            "model_calls": int((scope.get("search") or {}).get("model_calls") or 0),
            "proposer": proposer,
            "person_checks": _count((assessment.get("confirmed_expectations") or {}).get("expectations")),
            "isolation": str(((scope.get("environment") or {}).get("isolation") or {}).get("backend") or "")[:100]}
    data.update(outcome_sources(terminal, sources))
    data["repair_blocked"] = repair_blocked(assessment)
    data["review_returned"] = any(a.get("model_calls") and a.get("result") == "replied" for a in activities)
    if not data["checks_run"] and not data["review_returned"]:
        data["unchecked_reason"] = next((str(r.get("verdict")) for r in runs if r.get("verdict")), "check_not_recorded")[:100]
    for tier, name in ((1, "stated_checks"), (2, "repository_checks"), (3, "deeper_checks")):
        data[name] = sum(executed(r) for r in runs if r.get("tier") == tier)
    return data


def bundle(events: Iterable[Event], *, status: str = "kept") -> dict:
    rows = list(events) if status == "kept" else []
    return {"status": status, "events": [e.to_body() for e in rows],
            "justification": [{"event_id": e.event_id, "kind": e.kind, "sha256": digest(e.to_body())} for e in rows]}


def read(repo, task: str, *, note: str = "", origin_kind: str = "", origin_id: str = "") -> dict:
    """Read only this task's stored events; off and failed writes cite nothing."""
    if not policy.enabled()[0]:
        return bundle([], status="off")
    if note:
        return bundle([], status="write_failed")
    try:
        from g6.core import cloud_consent as cloud
        from g6.harness.workflow.adapters import identities
        from g6.harness.workflow.record import project_id, workflow_source

        # A local record being on grants no disclosure. Jobs and nested loop
        # captures retain the enclosing client's identity through their parent.
        capture = cloud.current_capture()
        while capture is not None and capture.client is None:
            capture = capture.parent
        if capture is not None and not cloud.is_granted(workflow_source(project_id(repo)), capture.client[0]):
            return bundle([], status="withheld")

        project, task_id = identities(repo, task)
        record = ProjectRecord(project)
        rows = record.events(task=task_id)
        held = bundle([*rows, *record.annotations(task_id)])
        if origin_kind or origin_id:
            runs = {e.run for e in rows if e.kind == "run_started" and e.data.get("origin_kind") == origin_kind
                    and e.data.get("origin_sha256") == sha256(origin_id)}
            held["selected_run"] = next(iter(runs)) if len(runs) == 1 else ""
        return held
    except Exception:  # noqa: BLE001 - missing custody never manufactures evidence
        return bundle([], status="unavailable")


def matched(held: Mapping) -> list[dict]:
    """Only bodies whose ids, kinds and digests match the supplied citations."""
    refs = {(r.get("event_id"), r.get("kind"), r.get("sha256")) for r in held.get("justification") or []}
    return [body for body in held.get("events") or []
            if (body.get("event_id"), body.get("kind"), digest(body)) in refs]


def check_ran(review: Mapping) -> bool:
    """A test result or a returned reviewer, never an attempted pre-call refusal."""
    return (any(value in ("passed", "failed") for value in (review.get("results") or {}).values())
            or bool(review.get("review_returned"))
            or "review_returned" not in review and review.get("verdict") in ("none", "defect", "clean"))


def checked(events: Iterable[Event]) -> bool:
    """The common coverage rule; aggregate review attempts alone establish nothing."""
    return any((e.kind == "loop_round" and bool(e.data.get("checked")))
               or (e.kind == "loop_ended" and e.data.get("probes", 0) > 0)
               or (e.kind == "review_finished" and (e.data.get("checks_run", 0) > 0
                                                   or bool(e.data.get("review_returned")))) for e in events)


def check_state(events: Iterable[Event]) -> dict[str, set[tuple[str, str]]]:
    """Same task/check, failed then passed on different bytes. No report-ending inference.

    Explicit loop round numbers establish order even within one wall-clock second.
    Separate review jobs need strictly ordered finish times; run-start order is irrelevant.
    """
    rows = [e for e in events if e.kind in ("loop_round", "review_finished")]
    failures: dict[tuple[str, str], list[Event]] = {}
    passes: dict[tuple[str, str], list[Event]] = {}
    for e in rows:
        for name in e.data.get("failed_sha256", e.data.get("open_sha256")) or []:
            failures.setdefault((e.task, name), []).append(e)
        for name in e.data.get("passed_sha256") or []:
            passes.setdefault((e.task, name), []).append(e)

    def later(failed: Event, passed: Event) -> bool:
        if failed.kind == passed.kind == "loop_round" and failed.run == passed.run:
            return 0 < failed.data.get("round", 0) < passed.data.get("round", 0)
        return failed.at < passed.at

    def source(event: Event, check: str) -> str:
        return next((p.partition(":")[2] for p in event.data.get("check_sources_sha256") or []
                     if p.partition(":")[0] == check), "")

    def resolved(failed: Event, passed: Event, check: str) -> bool:
        before, after = source(failed, check), source(passed, check)
        same_source = before == after and (not before or re.fullmatch(r"[0-9a-f]{64}", before) is not None)
        return bool(failed.artifact_sha256 and passed.artifact_sha256
                    and failed.artifact_sha256 != passed.artifact_sha256 and later(failed, passed)
                    and same_source and not passed.data.get("repair_blocked")
                    and passed.data.get("outcome") != "test_integrity_compromised"
                    and not passed.data.get("integrity_reasons"))

    repaired = {key for key, failed in failures.items()
                if any(resolved(f, p, key[1]) for f in failed for p in passes.get(key, ()))}
    still = {key for key, failed in failures.items()
             if any(not any(resolved(f, p, key[1]) for p in passes.get(key, ())) for f in failed)}
    return {"reproduced": set(failures), "repaired": repaired, "open": still}


def finding_labels(events: Iterable[Event], targets: Iterable[str]) -> list[dict]:
    """Only retained, attributed annotations for the finding events in this projection."""
    wanted = set(targets)
    return [{"event_id": e.event_id, "target": e.data["target"], "by": e.data.get("by") or "not recorded",
             "note": e.data["note"]} for e in events if e.kind == "annotation" and e.data.get("target") in wanted
            and e.provenance in ("attributed_label", "person_confirmed") and e.data.get("note")]
