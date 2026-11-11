# SPDX-License-Identifier: Apache-2.0
"""The project-scoped workflow record (design 17.2 "The record"; 17.3 "Workflow data and consent").

``$G6_HOME/workflow/<project-id>/tasks/<task-id>/runs/<run-id>.jsonl`` holds one hash-chained file per run. Each
event carries its project, task, run and event identities, its sources and the provenance of the observation, the
baseline and artefact digests, references to the artefacts it speaks of, and what was and was not observed. An event
holds codes, counts, seconds, ids and digests. It never holds task text, patch text, test output or an answer's text:
those stay in the artefacts a ``Ref`` points to.

Identities: the project id is the hash in the consent ledger's ``repository:<hash>`` source for the same path; the
task id is ``question_store.task_identity``; a run id is minted (a uuid4) and bound to its origin in the run's first
entry, so a restart finds the same run and a second session is a second run; an event id is the digest of its run,
its kind and the id its origin gave it, so a retried call is one event.
"""
from __future__ import annotations

import math
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from g6.core.cloud_consent import repository_source, sha256
from g6.core.ledger import LedgerError, digest
from g6.core.storage import state_path
from g6.harness.confirmed_expectation import TIME_FORMAT
from g6.harness.workflow import policy
from g6.harness.workflow._store import CODE, Chain, RecordUnreadable, plain
from g6.workforce.receipt_store import _locked

SCHEMA = "workflow.event.v1"
PROVENANCE = ("host_observed", "person_confirmed", "attributed_label", "model_inference", "rule_derived")
ORIGINS = ("loop_session", "review_job", "tool_calls")   # tool_calls: a task's calls that belong to no one review
REF_KINDS = ("review", "loop_state", "question", "receipt", "report")
RUN_STARTED = "run_started"
ANNOTATION_KINDS = ("annotation", "supersession")
MAX_TEXT = 200          # the longest string an event holds: a code, an id or a short label, never a document
MAX_ITEMS = 50
#: Names an event's data may not use: what they would hold belongs in the artefact a Ref points to.
TEXT_KEYS = frozenset({"text", "patch", "output", "answer", "task_text", "diff", "stdout", "stderr", "content",
                       "source", "report", "prompt"})
_HEX64, _HEX32 = re.compile(r"[0-9a-f]{64}\Z"), re.compile(r"[0-9a-f]{32}\Z")
_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_FIELDS = ("project", "task", "run", "event_id", "kind", "at", "provenance", "sources", "baseline_sha256",
           "artifact_sha256", "refs", "observed", "missing", "data")


class EventRefused(ValueError):
    """An event the record does not take. The message says which rule, never the value."""


def project_id(repo: str | os.PathLike) -> str:
    """The project's id: the hash in ``cloud_consent.repository_source(repo)``, so consent and workflow mean one
    thing by "this repository". A second worktree of one repository is a second project."""
    return repository_source(repo).split(":", 1)[1]


def workflow_source(project: str) -> str:
    """The consent source for everything the record holds about ``project``: its own grant, apart from the
    repository's."""
    if not isinstance(project, str) or not _HEX64.match(project):
        raise ValueError("a project id is the 64-hex hash project_id gives")
    return "workflow:" + project


def event_id(run: str, kind: str, native: str) -> str:
    return digest(["workflow.event", run, kind, native])


def now() -> str:
    return datetime.now(UTC).strftime(TIME_FORMAT)


@dataclass(frozen=True)
class Ref:
    """A pointer to an artefact G6 already keeps: its kind, its id there, and the digest of what was read."""

    kind: str
    id: str
    sha256: str

    def __post_init__(self) -> None:
        if self.kind not in REF_KINDS or not isinstance(self.id, str) or not _NAME.match(self.id) \
                or not isinstance(self.sha256, str) or not _HEX64.match(self.sha256):
            raise EventRefused("a reference names a known kind, an id and a SHA-256")


def _small(value: Any, depth: int = 0) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, int):
        return abs(value) < 2 ** 53
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return len(value) <= MAX_TEXT
    if isinstance(value, (list, tuple)) and depth == 0:
        return len(value) <= MAX_ITEMS and all(_small(item, 1) for item in value)
    return False


def _names(values: Any, *, required: bool = False) -> bool:
    return isinstance(values, tuple) and (bool(values) or not required) and len(values) <= MAX_ITEMS \
        and all(isinstance(v, str) and _NAME.match(v) for v in values)


@dataclass(frozen=True)
class Event:
    project: str
    task: str
    run: str
    event_id: str
    kind: str
    at: str
    provenance: str
    sources: tuple[str, ...]
    baseline_sha256: str
    artifact_sha256: str
    refs: tuple[Ref, ...] = ()
    observed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        def hexes(pattern, *names):
            return all(isinstance(getattr(self, n), str) and pattern.match(getattr(self, n)) for n in names)
        if not hexes(_HEX64, "project", "task", "event_id") or not hexes(_HEX32, "run"):
            raise EventRefused("an event names its project, task, run and event by their ids")
        if not all(isinstance(getattr(self, n), str) and (not getattr(self, n) or _HEX64.match(getattr(self, n)))
                   for n in ("baseline_sha256", "artifact_sha256")):
            raise EventRefused("a baseline or artefact digest is a SHA-256, or empty when there is none")
        if not isinstance(self.kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.kind):
            raise EventRefused("an event's kind is a short lower-case name")
        try:
            datetime.strptime(self.at, TIME_FORMAT)
        except (TypeError, ValueError):
            raise EventRefused("an event's time is UTC, to the second") from None
        if not isinstance(self.provenance, str) or self.provenance not in PROVENANCE:
            raise EventRefused("an event has exactly one provenance: " + ", ".join(PROVENANCE))
        if not _names(self.sources, required=True):
            raise EventRefused("an event names at least one source; an observation with no source is not recorded")
        if not isinstance(self.refs, tuple) or not all(isinstance(ref, Ref) for ref in self.refs) \
                or len(self.refs) > MAX_ITEMS:
            raise EventRefused("an event's references are Ref records")
        if not _names(self.observed) or not _names(self.missing):
            raise EventRefused("what was and was not observed is listed by short names")
        if not isinstance(self.data, Mapping) or len(self.data) > MAX_ITEMS:
            raise EventRefused("an event's data is a small mapping")
        for key, value in self.data.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) or key in TEXT_KEYS:
                raise EventRefused("an event's data uses short field names, and none that would hold text: "
                                   "task, patch, output and answer text stay in the artefact a reference points to")
            if not _small(value):
                raise EventRefused(f"an event's data holds codes, counts, seconds, ids and digests of at most "
                                   f"{MAX_TEXT} characters, never a document")
        object.__setattr__(self, "data", dict(self.data))

    def to_body(self) -> dict:
        body = {name: getattr(self, name) for name in _FIELDS}
        body.update(schema=SCHEMA, sources=list(self.sources), observed=list(self.observed),
                    missing=list(self.missing), refs=[[r.kind, r.id, r.sha256] for r in self.refs],
                    data={k: list(v) if isinstance(v, tuple) else v for k, v in self.data.items()})
        return body

    @classmethod
    def from_body(cls, body: Mapping) -> Event:
        try:
            if set(body) != {*_FIELDS, "schema"} or body["schema"] != SCHEMA:
                raise EventRefused("an entry of another schema")
            return cls(**{**{name: body[name] for name in _FIELDS}, "sources": tuple(body["sources"]),
                          "observed": tuple(body["observed"]), "missing": tuple(body["missing"]),
                          "refs": tuple(Ref(*ref) for ref in body["refs"])})
        except (TypeError, KeyError) as exc:
            raise EventRefused("an entry that does not read as an event") from exc


class ProjectRecord:
    """One project's record (the module's docstring). Reading creates nothing on disk."""

    def __init__(self, project: str) -> None:
        workflow_source(project)
        self.project = project
        for last in (False, False, True):
            try:
                self.root = state_path("workflow", project)
                break
            except ValueError:      # the state folder came into being between two looks at it: look again
                if last:
                    raise RecordUnreadable("G6's state folder could not be resolved") from None

    # -- where things are ---------------------------------------------------------------------------------------
    def _task_dir(self, task: str):
        if not isinstance(task, str) or not _HEX64.match(task):
            raise EventRefused("a task id is the SHA-256 question_store.task_identity gives")
        return self.root / "tasks" / task

    def run_chain(self, task: str, run: str) -> Chain:
        if not isinstance(run, str) or not _HEX32.match(run):
            raise EventRefused("a run id is the one start_run gave")
        return Chain(self._task_dir(task) / "runs" / (run + policy.RUN_SUFFIX),
                     lambda kind, ident, body, seen: self._rules(kind, ident, body, seen, task, run))

    def annotation_chain(self, task: str) -> Chain:
        return Chain(self._task_dir(task) / policy.ANNOTATIONS,
                     lambda kind, ident, body, seen: self._rules(kind, ident, body, seen, task, ""))

    def _rules(self, kind: str, ident: str, body: dict, seen: Mapping, task: str, run: str) -> list[str]:
        """An entry is an event bound to this project and task (and this run, in a run's file): a file copied from
        another project, task or run enforces nothing here."""
        try:
            event = Event.from_body(body)
        except EventRefused as refused:
            return [str(refused)]
        if (event.project, event.task, event.kind, event.event_id) != (self.project, task, kind, ident):
            return ["an entry bound to another project or task, or filed under another id: refused"]
        if run:
            if event.run != run:
                return ["an entry bound to another run: refused"]
            if (kind == RUN_STARTED) != (not seen):
                return ["a run's file begins with its one run_started entry"]
        elif kind not in ANNOTATION_KINDS or event.provenance != "attributed_label":
            return ["a correction is an annotation or a supersession, attributed to who made it"]
        return []

    def tasks(self) -> tuple[str, ...]:
        folder = self.root / "tasks"
        return tuple(sorted(p.name for p in folder.iterdir() if _HEX64.match(p.name))) if folder.is_dir() else ()

    def _run_ids(self, task: str) -> list[str]:
        folder = self._task_dir(task) / "runs"
        if not folder.is_dir():
            return []
        found = []
        for path in sorted(folder.iterdir()):
            if path.name.endswith(policy.SIDECARS):
                continue
            name = path.name.removesuffix(policy.RUN_SUFFIX)
            if not path.name.endswith(policy.RUN_SUFFIX) or not _HEX32.match(name) or not plain(path):
                raise RecordUnreadable("a run's place holds something that is not a run's file")
            found.append(name)
        return found

    # -- reading ------------------------------------------------------------------------------------------------
    def _read(self, chain: Chain) -> tuple[Event, ...]:
        return tuple(Event.from_body(entry["body"]) for entry in chain.entries())

    def runs(self, task: str) -> tuple[str, ...]:
        """The task's runs, oldest first."""
        firsts = [(self._read(self.run_chain(task, run))[:1], run) for run in self._run_ids(task)]
        return tuple(run for first, run in sorted(firsts, key=lambda pair: (pair[0][0].at if pair[0] else "", pair[1])))

    def events(self, *, task: str | None = None, run: str | None = None) -> tuple[Event, ...]:
        """Recorded run events, by run (oldest first) and then in the order written."""
        found: list[Event] = []
        for one in (task,) if task is not None else self.tasks():
            for name in self.runs(one):
                if run is None or name == run:
                    found.extend(self._read(self.run_chain(one, name)))
        return tuple(found)

    def annotations(self, task: str) -> tuple[Event, ...]:
        return self._read(self.annotation_chain(task))

    # -- writing ------------------------------------------------------------------------------------------------
    def start_run(self, task: str, origin_kind: str, origin_id: str, *, baseline_sha256: str,
                  at: str | None = None, extra: Mapping[str, Any] | None = None) -> str:
        """The run for this origin: the one already started for it, or a new one. The origin (a loop session or a
        review job) is kept as a digest in the run's first entry; the run id is minted, never derived from it.
        ``extra``: small values for a new run's first entry (which process began it, for its clock)."""
        if origin_kind not in ORIGINS or not isinstance(origin_id, str) or not origin_id:
            raise EventRefused("a run starts from a loop session, a review job or a task's tool calls, named by its id")
        origin = sha256(origin_id)
        folder = self._task_dir(task) / "runs"
        try:
            with _locked(folder / "runs"):         # one start at a time for a task: two callers get one run
                for run in self._run_ids(task):
                    first = self._read(self.run_chain(task, run))[:1]
                    if first and (first[0].data.get("origin_kind"), first[0].data.get("origin_sha256")) == (
                            origin_kind, origin):
                        return run
                run = uuid.uuid4().hex
                self.append(Event(project=self.project, task=task, run=run, kind=RUN_STARTED,
                                  event_id=event_id(run, RUN_STARTED, f"{origin_kind}:{origin}"), at=at or now(),
                                  provenance="host_observed", sources=(workflow_source(self.project),),
                                  baseline_sha256=baseline_sha256, artifact_sha256="",
                                  data={**(extra or {}), "origin_kind": origin_kind, "origin_sha256": origin}))
                return run
        except OSError:
            raise RecordUnreadable("it cannot be opened or written") from None

    def append(self, event: Event) -> bool:
        """Write one event to its run; False when that event id is already there (a retry writes nothing)."""
        if not isinstance(event, Event) or event.project != self.project:
            raise EventRefused("an event is written to its own project's record")
        chain = self.run_chain(event.task, event.run)
        if event.kind != RUN_STARTED and not chain.exists():
            raise EventRefused("an event is written to a run that start_run began")
        return self._write(chain, event)

    def annotate(self, event: Event) -> bool:
        """Write one correction (an annotation or a supersession) beside the task's runs; it changes no run entry."""
        if not isinstance(event, Event) or event.project != self.project or event.kind not in ANNOTATION_KINDS:
            raise EventRefused("a correction is an annotation or a supersession for this project")
        return self._write(self.annotation_chain(event.task), event)

    @staticmethod
    def _write(chain: Chain, event: Event) -> bool:
        with chain.open() as held:
            if any(entry["id"] == event.event_id for entry in held.entries()):
                return False
            try:
                return held.append(event.kind, event.event_id, event.to_body())
            except LedgerError as refused:
                raise EventRefused(str(refused)[:300]) from None


__all__ = ["ANNOTATION_KINDS", "CODE", "Event", "EventRefused", "ORIGINS", "PROVENANCE", "ProjectRecord",
           "REF_KINDS", "RUN_STARTED", "RecordUnreadable", "Ref", "SCHEMA", "TIME_FORMAT", "event_id", "now",
           "project_id", "workflow_source"]
