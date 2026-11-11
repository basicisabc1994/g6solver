# SPDX-License-Identifier: Apache-2.0
"""Adapters that write event boundaries from G6's own review paths into the workflow record (design 17.2 "Observe").

Each ``observe_*`` function swallows its own failure and returns the reason as a short fixed sentence ("" when it
recorded): a record that cannot be written never fails, slows or changes a review. With the record off
(``policy.enabled``) nothing is written at all.

What is recorded is what G6 itself did: a run for each loop session and each review job, its rounds, its end, the
moment its report was returned, and the tool calls that named the task. Questions are never copied: they are read from
the question store each time (``question_events``). Work outside these paths is not observed, and a task with no
events says so (``NO_OBSERVATION``); nothing is inferred from elapsed time or from a file's modification time.

Clocks: an event's ``at`` is wall time to the second. A duration is measured on one process's monotonic clock and
kept only when the run began and ended in that process; otherwise it is absent, and reads as not measured.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from collections.abc import Callable, Mapping
from functools import wraps
from pathlib import Path
from typing import Any

from g6.core.cloud_consent import sha256
from g6.core.ledger import digest
from g6.harness import question_store as qs
from g6.harness.workflow import policy
from g6.harness.workflow.record import Event, ProjectRecord, Ref, event_id, now, project_id, workflow_source

#: One token per process: a run that began under another token spanned a restart, so its duration is not measured.
PROCESS = uuid.uuid4().hex
NO_OBSERVATION = "no_g6_observation"
CALLS = "tool_calls"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_CODE = re.compile(r"\[(G6_E_[A-Z0-9_]+)\]")
_started: dict[str, float] = {}      # run id -> this process's monotonic clock when it began the run


def _never_fails(fn: Callable[..., str]) -> Callable[..., str]:
    @wraps(fn)
    def guarded(*args: Any, **kwargs: Any) -> str:
        on, why = policy.enabled()
        if not on:
            return why
        try:
            return fn(*args, **kwargs) or ""
        except Exception as exc:  # noqa: BLE001 - the record never fails the review it observes
            return f"the workflow record was not written ({type(exc).__name__})"
    return guarded


def identities(repo: str | os.PathLike, task: str) -> tuple[str, str]:
    """The project id and the task id for a repository folder and a task's text (A's ``task_identity``)."""
    from g6.workforce import repo_tests

    return project_id(repo), qs.task_identity(repo_tests.checked_repo(repo), task)


def _hex(value: Any) -> str:
    return value if isinstance(value, str) and _HEX64.match(value) else ""


def _number(value: Any) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(
        value) < 1e12 else None


class _Run:
    """One run being written: its record, ids and the events' common fields."""

    def __init__(self, repo: str | os.PathLike, task: str, kind: str, origin: str, *, at: str, base: str = "") -> None:
        self.project, self.task = identities(repo, task)
        self.record = ProjectRecord(self.project)
        self.baseline = sha256(base) if base else ""
        self.sources = (workflow_source(self.project),)
        self.run = self.record.start_run(self.task, kind, origin, baseline_sha256=self.baseline, at=at,
                                         extra={"process": PROCESS})
        first = self.record.events(task=self.task, run=self.run)[0]
        if first.data.get("process") == PROCESS:
            _started.setdefault(self.run, time.monotonic())
        else:
            _started.pop(self.run, None)
            self.write("run_resumed", PROCESS, at=at)

    def seconds(self) -> float | None:
        """Since this process began the run; None when another process did."""
        began = _started.get(self.run)
        return round(max(0.0, time.monotonic() - began), 3) if began is not None else None

    def write(self, kind: str, native: str, *, at: str, data: Mapping | None = None, artifact: str = "", refs=(),
              missing=(), provenance: str = "host_observed") -> bool:
        return self.record.append(Event(
            project=self.project, task=self.task, run=self.run, event_id=event_id(self.run, kind, native), kind=kind,
            at=at, provenance=provenance, sources=self.sources, baseline_sha256=self.baseline,
            artifact_sha256=_hex(artifact), refs=tuple(refs), missing=tuple(missing),
            data={k: v for k, v in (data or {}).items() if v is not None}))


def _saved_ref(job_id: str, saved: Any) -> Ref | None:
    """A reference to the review G6 saved for this job, by the digest of the saved file; None when there is none."""
    try:
        return Ref("review", job_id, sha256(Path(saved).read_bytes())) if isinstance(saved, str) and saved else None
    except (OSError, ValueError):
        return None


@_never_fails
def observe_review(repo: str | os.PathLike, task: str, job_id: str, reply: Any, *, started_at: str, finished_at: str,
                   seconds: float | None = None, saved: str | None = None) -> str:
    """One finished review job: its run, what it ended with (codes and counts), and the moment its report returned.
    ``saved``: where the whole review was saved, when it was; a review whose file could not be saved is recorded
    with ``review_ref`` missing."""
    run = _Run(repo, task, "review_job", job_id, at=started_at)
    reply = reply if isinstance(reply, dict) else {}
    assessment = reply.get("assessment") if isinstance(reply.get("assessment"), dict) else None
    scope = (assessment or {}).get("scope") if isinstance((assessment or {}).get("scope"), dict) else {}
    search = scope.get("search") if isinstance(scope.get("search"), dict) else {}
    refused = _CODE.match(str(reply.get("error") or ""))
    ref = _saved_ref(job_id, saved) if assessment else None
    reasons = [r for r in (assessment or {}).get("reasons") or [] if isinstance(r, dict)]
    data = {"outcome": str((assessment or {}).get("outcome") or "")[:80] or None,
            "state": str(reply.get("completion_state") or "")[:40] or None,
            "refusal_code": refused[1] if refused else None,
            "review_seconds": _number(scope.get("seconds")), "model_calls": _number(search.get("model_calls")),
            "integrity_reasons": sum(r.get("stage") == "integrity" for r in reasons),
            "requirements_failed": sum(isinstance(r, dict) and r.get("status") == "failed"
                                       for r in (assessment or {}).get("requirements") or [])}
    missing = [name for name, absent in (("review_ref", assessment is not None and ref is None),
                                         ("model_calls", assessment is not None and data["model_calls"] is None),
                                         ("review_seconds", assessment is not None and data["review_seconds"] is None))
               if absent]
    run.write("review_finished", job_id, at=finished_at, data=data, refs=[ref] if ref else (), missing=missing,
              artifact=reply.get("artifact_digest") or scope.get("artifact_digest") or "")
    run.write("report_returned", job_id, at=finished_at,
              data={"run_seconds": _number(seconds) if seconds is not None and seconds >= 0 else None},
              missing=() if seconds is not None and seconds >= 0 else ("run_seconds",))
    return ""


@_never_fails
def observe_loop(repo: str | os.PathLike, task: str, session: Path, state: Mapping, step: Any, *, at: str,
                 base: str = "") -> str:
    """One step of a completion loop, from its saved state: the run, each review round once, and, when the loop has
    stopped, its end and the moment its report was returned. Replaying the same state adds nothing."""
    if getattr(step, "action", "") == "wait":          # another step owns the state; this one did nothing
        return ""
    run = _Run(repo, task, "loop_session", Path(session).name, at=at, base=base)
    reviews = [one for one in state.get("reviews") or [] if isinstance(one, dict)]
    rounds = [one for one in state.get("rounds") or [] if isinstance(one, dict)]
    for number, review in enumerate(reviews, 1):
        failing = (rounds[number - 1].get("failing") or []) if number <= len(rounds) else None
        results = review.get("results") if isinstance(review.get("results"), Mapping) else None
        still_open = sorted(sha256(str(name)) for name, verdict in (results or {}).items() if verdict == "failed")[:50]
        run.write("loop_round", str(number), at=at, artifact=review.get("patch") or "",
                  data={"round": number, "verdict": str(review.get("verdict") or "")[:40] or None,
                        "open": len(still_open) if results is not None else None, "open_sha256": still_open or None,
                        "failing": len(failing) if failing is not None else None,
                        "failing_sha256": sorted(sha256(str(name)) for name in failing)[:50] if failing else None,
                        "repair_applied": bool(number <= len(rounds) and rounds[number - 1].get("tree") is not None)},
                  missing=() if failing is not None else ("failing",))
    if getattr(step, "action", "") == "stop":
        ending = str(getattr(step, "ending", "") or state.get("ending") or "")[:40]
        code = str(getattr(step, "code", "") or state.get("code") or "")[:80]
        last = reviews[-1].get("patch") if reviews else ""
        native = digest([len(reviews), ending, code])
        run.write("loop_ended", native, at=at, artifact=last or "",
                  data={"ending": ending or None, "code": code or None, "reviews": len(reviews)})
        seconds = run.seconds()
        run.write("report_returned", native, at=at, artifact=last or "", data={"run_seconds": seconds},
                  refs=_report_ref(session), missing=() if seconds is not None else ("run_seconds",))
    return ""


@_never_fails
def observe_step(repo: str | os.PathLike, task: str, session: Path, step: Any, *, base: str = "", at: str = "") -> str:
    """`observe_loop` for a step whose state is read from the session's folder, as the hook and the MCP step do."""
    from g6.harness import completion_loop

    return observe_loop.__wrapped__(repo, task, session, completion_loop._load(Path(session)), step, at=at or now(),
                                    base=base)


def _report_ref(session: Path) -> tuple[Ref, ...]:
    try:
        report = Path(session) / "report.md"
        return (Ref("report", Path(session).name, sha256(report.read_bytes())),) if report.is_file() else ()
    except OSError:
        return ()


@_never_fails
def observe_call(repo: str | os.PathLike, task: str, tool: str, request_id: str, args_sha256: str, *,
                 started_at: str, seconds: float | None) -> str:
    """One tool call that named this task. A retried request (the same ``request_id``) is one event."""
    run = _Run(repo, task, CALLS, "calls", at=started_at)
    run.write("tool_call", f"{tool}:{request_id}", at=started_at,
              data={"tool": str(tool)[:80], "args_sha256": _hex(args_sha256) or None,
                    "seconds": _number(seconds) if seconds is not None and seconds >= 0 else None},
              missing=() if seconds is not None and seconds >= 0 else ("seconds",))
    return ""


@_never_fails
def observe_dialog(repo: str | os.PathLike, task: str, dialog: str, result: str, *, at: str, seconds: float | None,
                   native: str = "") -> str:
    """One dialog G6 opened about this task, and the person's time in it: an approval asked for (``approval_shown``)
    or the feedback question (``feedback_shown``). Counted as G6's own friction whatever the person chose."""
    run = _Run(repo, task, CALLS, "calls", at=at)
    kind = "feedback_shown" if dialog == "feedback" else "approval_shown"
    run.write(kind, f"{dialog}:{native or at}", at=at,
              data={"dialog": str(dialog)[:40], "result": str(result)[:40],
                    "seconds": _number(seconds) if seconds is not None and seconds >= 0 else None},
              missing=() if seconds is not None and seconds >= 0 else ("seconds",))
    return ""


@_never_fails
def observe_task(repo: str | os.PathLike, task: str, kind: str, native: str, *, at: str, data: Mapping | None = None,
                 provenance: str = "host_observed") -> str:
    """One event about the task that belongs to no review run (a feedback invitation, an answer's option code)."""
    _Run(repo, task, CALLS, "calls", at=at).write(kind, native, at=at, data=data, provenance=provenance)
    return ""


@_never_fails
def observe_project(repo: str | os.PathLike, kind: str, native: str, *, at: str, data: Mapping | None = None) -> str:
    """An inspection or a correction of the whole project record: counted once, against the project's most recent
    run (it has no task of its own). With no run there is nothing to count it against, and nothing is written."""
    record = ProjectRecord(project_id(repo))
    newest = max((e for e in record.events() if e.kind == "run_started"), key=lambda e: e.at, default=None)
    if newest is None:
        return "no run to count it against"
    record.append(Event(project=record.project, task=newest.task, run=newest.run, kind=kind,
                        event_id=event_id(newest.run, kind, native), at=at, provenance="host_observed",
                        sources=(workflow_source(record.project),), baseline_sha256=newest.baseline_sha256,
                        artifact_sha256="", data={"scope": "project", **(data or {})}))
    return ""


def question_events(project: str, task: str) -> tuple[Event, ...]:
    """A's question events for the task, read from its store each time and written nowhere: each showing of a form
    (with the person's time in it) and each answer. ``qs.StoreUnreadable`` when that store cannot be read."""
    store = qs.TaskQuestions(task)
    if not store.exists():
        return ()
    state, run, sources = store.state(), digest(["questions", task])[:32], (workflow_source(project),)
    found = []
    for number, shown in enumerate(state.shown):
        sha = _hex(shown.get("question_sha256"))
        found.append(Event(
            project=project, task=task, run=run, kind="question_shown", provenance="host_observed", sources=sources,
            event_id=event_id(run, "question_shown", f"{sha}:{number}"), at=str(shown.get("shown_at") or now()),
            baseline_sha256="", artifact_sha256="", refs=(Ref("question", sha, sha),) if sha else (),
            data={"result": str(shown.get("result") or "")[:40], "seconds": _number(shown.get("seconds"))},
            missing=() if _number(shown.get("seconds")) is not None else ("seconds",)))
    for answer in state.answers:
        found.append(Event(
            project=project, task=task, run=run, kind="question_answered", provenance="person_confirmed",
            sources=sources, event_id=event_id(run, "question_answered", answer.sha256),
            at=answer.answered_at, baseline_sha256="", artifact_sha256="",
            refs=(Ref("question", answer.question_sha256, answer.sha256),), data={"option": str(answer.option)[:40]}))
    if state.pending is not None:
        asked = state.pending
        found.append(Event(
            project=project, task=task, run=run, kind="question_pending", provenance="host_observed", sources=sources,
            event_id=event_id(run, "question_pending", asked.question.sha256), at=asked.asked_at or now(),
            baseline_sha256="", artifact_sha256="",
            refs=(Ref("question", asked.question.sha256, asked.question.sha256),), data={"number": asked.number}))
    return tuple(found)


def task_view(project: str, task: str) -> tuple[Event, ...]:
    """Everything G6 observed for the task: its recorded events and A's question events, oldest first. Empty when G6
    observed nothing, which is "unknown", never "nothing happened"."""
    events = [*ProjectRecord(project).events(task=task), *question_events(project, task)]
    return tuple(sorted(events, key=lambda e: e.at))        # a stable sort: the written order within one second


__all__ = ["CALLS", "NO_OBSERVATION", "PROCESS", "identities", "observe_call", "observe_dialog", "observe_loop", "observe_project",
           "observe_review", "observe_step", "observe_task", "question_events", "task_view"]
