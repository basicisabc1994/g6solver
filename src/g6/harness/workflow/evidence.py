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


def read(repo, task: str, *, note: str = "") -> dict:
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
        return bundle(ProjectRecord(project).events(task=task_id))
    except Exception:  # noqa: BLE001 - missing custody never manufactures evidence
        return bundle([], status="unavailable")


def matched(held: Mapping) -> list[dict]:
    """Only bodies whose ids, kinds and digests match the supplied citations."""
    refs = {(r.get("event_id"), r.get("kind"), r.get("sha256")) for r in held.get("justification") or []}
    return [body for body in held.get("events") or []
            if (body.get("event_id"), body.get("kind"), digest(body)) in refs]
