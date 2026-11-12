# SPDX-License-Identifier: Apache-2.0
"""Indexing jobs for the optional backends (design 12.5 item 2): a source's passages copied into the managed
Elasticsearch index with per-item acknowledgements, and the readiness a person is told afterwards.

``_``-prefixed, so it never registers as a component. The local lexical index is written when a source is added and
is the only required backend; a job never blocks it and never changes what it serves. A job writes only to a backend
the preview the person accepted for the source lists (``Registration.backends``): no argument adds one. A job's state
is kept in the project's registry; a job a stopped server left queued or running is run again by the next server
when its state is asked for (:func:`ensure_running`), and every write is an upsert under the passage id, so running
a job twice gives the same index. A job ends ``published`` when every passage of the source was read back from the
backend, ``partial`` when the server was unreachable or some items were not acknowledged or not read back (the
route then stays not ready and a search over it says ``unavailable``), and ``failed`` when it could not run at all
or was stopped. Files that could not be read again are listed in ``unread``; nothing is invented for them.

A stop stops: whether the source may still be used (the registry's one predicate, with the time, and the generation
the job is for) is asked before the files are read again, for every file, before every batch sent, and once more
before a copy is recorded as complete. A source revoked or expired while a job runs gets no further batch, and its
copy is never recorded as ready. What was sent before the stop stays in the server and is listed by :func:`held`.
"""
from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.knowledge import _engine as engine
from g6.knowledge._index import IndexRefused
from g6.knowledge._registry import ProjectStore, Refused, may_use
from g6.knowledge._scope import Binding
from g6.knowledge._types import now
from g6.knowledge._walk import Excluded

BACKENDS = tuple(engine.OPTIONAL_BACKENDS)
JOB_UNKNOWN = "G6_E_KNOWLEDGE_JOB"
NOT_ACCEPTED = "G6_E_KNOWLEDGE_BACKEND_NOT_ACCEPTED"
_RUNNING: dict[str, threading.Thread] = {}
_STARTING = threading.Lock()


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    registration_id: str
    generation: int
    state: Literal["queued", "running", "published", "partial", "failed"]
    backends: dict[str, dict[str, Any]]
    unread: tuple[Excluded, ...] = ()
    started_at: str = ""
    finished_at: str = ""


def elastic_gate() -> Any:
    """The action gate for the managed Elasticsearch writer: ``g6.action_gating``'s decision on each write, with the
    closed-loop plan the gate requires. A write goes ahead only on the gate's allow; as the gate stands, that needs
    the server's API key in the person's key file, which is their approval to write to that server. A removal names
    no key: a key or a spend approval is not deletion consent, so the gate decides a removal on its own terms."""
    from g6.knowledge.ctx_elastic.elastic import ELASTIC_KEY
    from g6.sources import _gate

    def ask(described: dict[str, Any]) -> dict[str, Any]:
        removing = described.get("action_params", {}).get("op") == "remove"
        action = {**described, "verification_contract": _gate.plan(
            "every item acknowledged in the bulk reply and found again by id",
            "the bulk reply's own items, then a read-back by id",
            "before the copy is recorded as complete for the generation",
            "an item that is failed or unknown after the retries",
            "the copy is not recorded as complete; the local index stays the one in use")}
        return _gate.ask(action, None if removing else ELASTIC_KEY.name)

    return ask


def _save(store: ProjectStore, job: Job) -> Job:
    store.put_job(job.id, job.model_dump_json())
    return job


def status(binding: Binding, job_id: str) -> Job:
    raw = ProjectStore(binding).job(job_id)
    if raw is None:
        raise Refused(JOB_UNKNOWN, "no such indexing job in this project")
    return Job.model_validate_json(raw)


class _Stop(Exception):
    """The source may no longer be used: the job ends here."""


def _still(store: ProjectStore, registration_id: str, generation: int, clock: Callable[[], datetime]) -> str:
    """Why the job's source may no longer be used at this moment (``""``: it may): the registry's one predicate with
    the time, and the published generation still being the one the job is for."""
    why = may_use(store.registration(registration_id), clock())
    if why:
        return why
    current = store.published()
    return "" if current is not None and current.number == generation else "superseded"


def _passages(binding: Binding, store: ProjectStore, registration: Any, generation: int, still: Callable[[], str]):
    """The registration's passages of ``generation``, each read again through the engine's one re-read, and the
    files that could not be read again. ``still`` is asked before every file."""
    found, unread = [], []
    documents: dict[str, Any] = {}
    for member in store.members(generation, registration.id):
        why = still()
        if why:
            raise _Stop(why)
        read = [engine.reread_passage(binding, registration, member, pid, documents) for pid in member.passage_ids]
        if any(passage is None for passage in read):
            unread.append(Excluded(relative=member.relative, why="unreadable"))
            continue
        found.extend(read)
    return found, tuple(unread)


def run(binding: Binding, job_id: str, *, gate: Any = None, caller: Any = None,
        clock: Callable[[], datetime] = engine._utc) -> Job:
    """Do the job's work now, in this thread. Safe to run again."""
    from g6.knowledge.ctx_elastic import elastic, managed, writer

    store = ProjectStore(binding)
    job = status(binding, job_id)
    registration = store.registration(job.registration_id)

    def still() -> str:
        return _still(store, job.registration_id, job.generation, clock)

    def ended(why: str) -> Job:
        return _save(store, job.model_copy(update={"state": "failed", "finished_at": now(), "backends": {
            name: {"state": "refused", "reason": f"the source or the generation is no longer the one the job was "
                                                 f"for ({why}); nothing more was sent"} for name in job.backends}}))

    if still():
        return ended(still() or "changed")
    job = _save(store, job.model_copy(update={"state": "running", "started_at": job.started_at or now()}))
    try:
        passages, unread = _passages(binding, store, registration, job.generation, still)
    except _Stop as stop:
        return ended(str(stop))
    report: dict[str, dict[str, Any]] = {}
    stopped = False
    for name in job.backends:
        if name not in registration.backends:   # what the person accepted is what happens: no argument adds a backend
            report[name] = {"state": "refused", "reason": NOT_ACCEPTED}
            stopped = True
            continue
        if name != "elastic_managed":
            report[name] = {"state": "unsupported", "reason": "no job writes this backend"}
            continue
        target = None
        try:
            target = writer.pin(binding.project, caller=caller)
            for other in store.generations():   # an older generation's copy is superseded by this write
                if other.number != job.generation and "elastic_managed" in other.backends:
                    store.backend_state(other.number, name, "unavailable", {"superseded_by": job.generation},
                                        registration_id=registration.id)
            acks = writer.write(target, [managed.encode(p, registration.id, job.generation) for p in passages],
                                gate=gate, caller=caller, still=still)
            back = writer.read_back(target, [p.id for p in passages], caller=caller)
        except writer.Stopped as stop:
            stopped = True
            report[name] = {"state": "refused", "reason": f"stopped: the source may no longer be used ({stop.why})",
                            "sent_before_the_stop": stop.sent, "index": target.concrete_index if target else ""}
            try:   # what the server already holds is recorded as held, never as ready
                store.backend_state(job.generation, name, "unavailable", {"stopped": stop.why, "sent": stop.sent},
                                    registration_id=registration.id)
            except Refused:
                pass
            continue
        except writer.Held as held_back:
            report[name] = {"state": "refused", "reason": held_back.code, "gate": held_back.gate.get("decision"),
                            "dry_run": held_back.dry_run}
            continue
        except (elastic.ElasticError, IndexRefused) as exc:
            report[name] = {"state": "unavailable", "reason": exc.code}
            store.backend_state(job.generation, name, "unavailable", {"reason": exc.code},
                                registration_id=registration.id)
            continue
        acknowledged = sum(1 for a in acks if a.state == "acknowledged")
        read = sum(1 for ok in back.values() if ok)
        complete = bool(passages) and acknowledged == len(passages) == read and not unread
        receipt = {"written": len(passages), "acknowledged": acknowledged, "read_back": read,
                   "failed": sum(1 for a in acks if a.state == "failed"),
                   "unknown": sum(1 for a in acks if a.state == "unknown"), "index": target.concrete_index}
        why = still()   # readiness is recorded only if the source may still be used now
        if why:
            stopped = True
            report[name] = {"state": "refused", "reason": f"stopped: the source may no longer be used ({why})",
                            "sent_before_the_stop": acknowledged, "index": target.concrete_index}
            try:
                store.backend_state(job.generation, name, "unavailable", {"stopped": why, "sent": acknowledged},
                                    registration_id=registration.id)
            except Refused:
                pass
            continue
        state = "answered" if complete else "partial"
        store.backend_state(job.generation, name, state, receipt, registration_id=registration.id)
        report[name] = {"state": state, **receipt}
    states = {entry["state"] for entry in report.values()}
    overall = ("published" if states == {"answered"} else "failed" if stopped or states <= {"unsupported"}
               else "partial")
    return _save(store, job.model_copy(update={"state": overall, "backends": report, "unread": unread,
                                               "finished_at": now()}))


def _thread(binding: Binding, job_id: str, gate: Any, caller: Any) -> None:
    thread = threading.Thread(target=run, args=(binding, job_id), kwargs={"gate": gate, "caller": caller},
                              name=f"g6-knowledge-{job_id}", daemon=True)
    _RUNNING[job_id] = thread
    thread.start()


def start(binding: Binding, registration_id: str, backends: Sequence[str], *, wait: bool = False, gate: Any = None,
          caller: Any = None, clock: Callable[[], datetime] = engine._utc) -> Job:
    """Queue a job that copies the source's passages of the published generation into ``backends``, and run it in a
    worker thread of this process (``wait`` runs it here instead). Every backend named must be one the preview the
    person accepted for this source lists."""
    store = ProjectStore(binding)
    registration = store.registration(registration_id)
    generation = store.published()
    if may_use(registration, clock()) or generation is None:
        raise Refused(JOB_UNKNOWN, "a job needs an active source in a published generation")
    wanted = list(dict.fromkeys(backends))
    if not wanted or any(name not in BACKENDS for name in wanted):
        raise Refused(JOB_UNKNOWN, f"a job writes one of these optional backends: {', '.join(BACKENDS)}")
    if any(name not in registration.backends for name in wanted):
        raise Refused(NOT_ACCEPTED, "the preview the person accepted for this source does not list this backend, so "
                                    "nothing is written to it; add the folder again with the backend listed")
    job = _save(store, Job(id="job-" + secrets.token_hex(8), registration_id=registration_id,
                           generation=generation.number, state="queued",
                           backends={name: {"state": "queued"} for name in wanted}))
    if wait:
        return run(binding, job.id, gate=gate, caller=caller, clock=clock)
    with _STARTING:
        _thread(binding, job.id, gate, caller)
    return job


def _alive(job_id: str) -> bool:
    return job_id in _RUNNING and _RUNNING[job_id].is_alive()


def ensure_running(binding: Binding, job: Job, *, gate: Any = None, caller: Any = None) -> Job:
    """A job that is queued or running in the registry while no thread of this process runs it was left by a server
    that stopped: run it again here, in a worker thread. It does the work the person accepted and nothing else (the
    accepted backends, the gate and every stop check apply as on the first run)."""
    with _STARTING:
        if job.state in ("queued", "running") and not _alive(job.id):
            _thread(binding, job.id, gate, caller)
    return job


def resume(binding: Binding, *, gate: Any = None, caller: Any = None) -> list[Job]:
    """Run again, here and now, every job a stopped server left queued or running; a job whose generation is no
    longer the published one ends ``failed`` and changes nothing."""
    store = ProjectStore(binding)
    out = []
    for raw in store.jobs():
        job = Job.model_validate_json(raw)
        if job.state in ("queued", "running") and not _alive(job.id):
            out.append(run(binding, job.id, gate=gate, caller=caller))
    return out


def held(store: ProjectStore, registration_id: str) -> list[dict[str, Any]]:
    """Every optional backend G6's records say received passages of the registration, from the jobs that wrote
    them: the backend, its index, how many records were sent, and that G6 has not removed them. A deletion dialog
    and its receipt say this, because nothing in G6 removes a copy from the server yet."""
    found: dict[str, dict[str, Any]] = {}
    for raw in store.jobs():
        job = Job.model_validate_json(raw)
        if job.registration_id != registration_id:
            continue
        for name, entry in job.backends.items():
            sent = max(int(entry.get("acknowledged") or 0), int(entry.get("sent_before_the_stop") or 0))
            unsure = int(entry.get("unknown") or 0)
            if sent or unsure or entry.get("state") in ("queued", "running"):
                one = found.setdefault(name, {"backend": name, "index": "", "records_sent": 0, "state": "still held"})
                one["index"] = str(entry.get("index") or one["index"])
                one["records_sent"] = max(one["records_sent"], sent)
                if unsure or job.state in ("queued", "running"):
                    one["records_possibly_sent"] = True
    return [found[name] for name in sorted(found)]


__all__ = ["BACKENDS", "JOB_UNKNOWN", "NOT_ACCEPTED", "Job", "ensure_running", "held", "resume", "run", "start",
           "status"]