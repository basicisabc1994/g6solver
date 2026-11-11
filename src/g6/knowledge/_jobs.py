# SPDX-License-Identifier: Apache-2.0
"""Indexing jobs for the optional backends (design 12.5 item 2): a source's passages copied into the managed
Elasticsearch index with per-item acknowledgements, and the readiness a person is told afterwards.

``_``-prefixed, so it never registers as a component. The local lexical index is written when a source is added and
is the only required backend; a job never blocks it and never changes what it serves. A job's state is kept in the
project's registry, so a server that restarts can run it again: every write is an upsert under the passage id, so
running a job twice gives the same index. A job ends ``published`` when every passage of the source was read back
from the backend, ``partial`` when the server was unreachable or some items were not acknowledged or not read back
(the route then stays not ready and a search over it says ``unavailable``), and ``failed`` when it could not run at
all. Files that could not be read again are listed in ``unread``; nothing is invented for them.
"""
from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.knowledge import _engine as engine
from g6.knowledge._index import IndexRefused
from g6.knowledge._registry import ProjectStore, Refused, may_use
from g6.knowledge._scope import Binding
from g6.knowledge._types import now
from g6.knowledge._walk import Excluded

BACKENDS = ("elastic_managed",)
JOB_UNKNOWN = "G6_E_KNOWLEDGE_JOB"
_RUNNING: dict[str, threading.Thread] = {}


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


def _passages(binding: Binding, store: ProjectStore, registration: Any, generation: int):
    """The registration's passages of ``generation``, each rebuilt from its file as it is now, and the files that
    could not be read again."""
    found, unread = [], []
    for member in store.members(generation, registration.id):
        document, _ = engine._reread(binding, registration, member)
        if document is None:
            unread.append(Excluded(relative=member.relative, why="unreadable"))
            continue
        for pid, (start, end) in zip(member.passage_ids, member.spans, strict=True):
            passage = document.passage(start, end) if end <= len(document.text) else None
            if passage is None or passage.id != pid:
                unread.append(Excluded(relative=member.relative, why="unreadable"))
                break
            found.append(passage)
    return found, tuple(unread)


def run(binding: Binding, job_id: str, *, gate: Any = None, caller: Any = None) -> Job:
    """Do the job's work now, in this thread. Safe to run again."""
    from g6.knowledge.ctx_elastic import elastic, managed, writer

    store = ProjectStore(binding)
    job = status(binding, job_id)
    registration = store.registration(job.registration_id)
    current = store.published()
    if may_use(registration, datetime.now(UTC)) or current is None or current.number != job.generation:
        return _save(store, job.model_copy(update={"state": "failed", "finished_at": now(), "backends": {
            name: {"state": "refused", "reason": "the source or the generation is no longer the one the job was for"}
            for name in job.backends}}))
    job = _save(store, job.model_copy(update={"state": "running", "started_at": job.started_at or now()}))
    passages, unread = _passages(binding, store, registration, job.generation)
    report: dict[str, dict[str, Any]] = {}
    for name in job.backends:
        if name != "elastic_managed":
            report[name] = {"state": "unsupported", "reason": "no job writes this backend"}
            continue
        try:
            target = writer.pin(binding.project, caller=caller)
            for other in store.generations():   # an older generation's copy is superseded by this write
                if other.number != job.generation and "elastic_managed" in other.backends:
                    store.backend_state(other.number, name, "unavailable", {"superseded_by": job.generation},
                                        registration_id=registration.id)
            acks = writer.write(target, [managed.encode(p, registration.id, job.generation) for p in passages],
                                gate=gate, caller=caller)
            back = writer.read_back(target, [p.id for p in passages], caller=caller)
        except writer.Held as held:
            report[name] = {"state": "refused", "reason": held.code, "gate": held.gate.get("decision"),
                            "dry_run": held.dry_run}
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
        state = "answered" if complete else "partial"
        store.backend_state(job.generation, name, state, receipt, registration_id=registration.id)
        report[name] = {"state": state, **receipt}
    states = {entry["state"] for entry in report.values()}
    overall = "published" if states == {"answered"} else "failed" if states <= {"unsupported"} else "partial"
    return _save(store, job.model_copy(update={"state": overall, "backends": report, "unread": unread,
                                               "finished_at": now()}))


def start(binding: Binding, registration_id: str, backends: Sequence[str], *, wait: bool = False, gate: Any = None,
          caller: Any = None) -> Job:
    """Queue a job that copies the source's passages of the published generation into ``backends``, and run it in a
    worker thread of this process (``wait`` runs it here instead)."""
    store = ProjectStore(binding)
    registration = store.registration(registration_id)
    generation = store.published()
    if may_use(registration, datetime.now(UTC)) or generation is None:
        raise Refused(JOB_UNKNOWN, "a job needs an active source in a published generation")
    wanted = list(dict.fromkeys(backends))
    if not wanted or any(name not in BACKENDS for name in wanted):
        raise Refused(JOB_UNKNOWN, f"a job writes one of these optional backends: {', '.join(BACKENDS)}")
    job = _save(store, Job(id="job-" + secrets.token_hex(8), registration_id=registration_id,
                           generation=generation.number, state="queued",
                           backends={name: {"state": "queued"} for name in wanted}))
    if wait:
        return run(binding, job.id, gate=gate, caller=caller)
    thread = threading.Thread(target=run, args=(binding, job.id), kwargs={"gate": gate, "caller": caller},
                              name=f"g6-knowledge-{job.id}", daemon=True)
    _RUNNING[job.id] = thread
    thread.start()
    return job


def resume(binding: Binding, *, gate: Any = None, caller: Any = None) -> list[Job]:
    """Run again every job a stopped server left queued or running; a job whose generation is no longer the
    published one ends ``failed`` and changes nothing."""
    store = ProjectStore(binding)
    out = []
    for raw in store.jobs():
        job = Job.model_validate_json(raw)
        alive = job.id in _RUNNING and _RUNNING[job.id].is_alive()
        if job.state in ("queued", "running") and not alive:
            out.append(run(binding, job.id, gate=gate, caller=caller))
    return out


__all__ = ["BACKENDS", "JOB_UNKNOWN", "Job", "resume", "run", "start", "status"]
