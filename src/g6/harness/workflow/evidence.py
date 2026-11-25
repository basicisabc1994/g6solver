# SPDX-License-Identifier: Apache-2.0
"""Small observed counts and task-local citations shared by the card and export.

Readers never mint events. A citation hashes the event's canonical body, so it
can be resolved from the inspection file without trusting report wording.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from g6.core.cloud_consent import sha256
from g6.core.ledger import digest
from g6.harness.workflow import policy
from g6.harness.workflow.record import Event, ProjectRecord


def _count(value) -> int:
    return len(value) if isinstance(value, (list, tuple, dict)) else 0


def review_data(assessment: Mapping) -> dict:
    """Counts, model names and check digests only; no repository text."""
    scope = assessment.get("scope") or {}
    completion = assessment.get("completion") or {}
    defects = completion.get("defects_reproduced") or {}
    runs = scope.get("checks_run") or []
    requirements = assessment.get("requirements") or []
    # Only the stated checks' own terminal status counts. Base runs and a skipped,
    # absent or renamed check cannot supply the pass for an earlier failure.
    statuses = {}
    for requirement in requirements:
        for check in requirement.get("checks") or []:
            if isinstance(check, dict) and check.get("id") and check.get("observation") is None:
                statuses.setdefault(str(check["id"]), []).append(check.get("verdict"))
    failed = sorted(sha256(name) for name, values in statuses.items() if "failed" in values)
    passed = sorted(sha256(name) for name, values in statuses.items() if values and all(v == "passed" for v in values))
    activities = completion.get("activities") or []
    proposer = next((str(a["model"])[:100] for a in activities if a.get("model") and a.get("model_calls")), "")
    data = {"requirements": len(requirements),
            "not_established": _count(assessment.get("requirements_without_adequate_check")),
            "reproduced": _count(defects.get("stated_and_repository_checks")),
            "unconfirmed": _count(defects.get("proposed_checks")),
            "integrity_reasons": sum(r.get("stage") == "integrity" for r in assessment.get("reasons") or []),
            "checks_run": len(runs), "checks_skipped": _count(scope.get("checks_skipped")),
            "failed_sha256": failed[:50], "passed_sha256": passed[:50],
            "questions": _count(completion.get("open_questions")), "unknowns": _count(assessment.get("unknowns")),
            "model_calls": int((scope.get("search") or {}).get("model_calls") or 0),
            "proposer": proposer,
            "person_checks": _count((assessment.get("confirmed_expectations") or {}).get("expectations")),
            "isolation": str(((scope.get("environment") or {}).get("isolation") or {}).get("backend") or "")[:100]}
    for tier, name in ((1, "stated_checks"), (2, "repository_checks"), (3, "deeper_checks")):
        data[name] = sum(_count(r.get("tests")) for r in runs if r.get("tier") == tier
                         and r.get("check") != "stated checks at the base commit")
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
                                                   or e.data.get("model_calls", 0) > 0)) for e in events)


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

    def resolved(failed: Event, passed: Event) -> bool:
        return bool(failed.artifact_sha256 and passed.artifact_sha256
                    and failed.artifact_sha256 != passed.artifact_sha256 and later(failed, passed))

    repaired = {key for key, failed in failures.items()
                if any(resolved(f, p) for f in failed for p in passes.get(key, ()))}
    still = {key for key, failed in failures.items()
             if any(not any(resolved(f, p) for p in passes.get(key, ())) for f in failed)}
    return {"reproduced": set(failures), "repaired": repaired, "open": still}
