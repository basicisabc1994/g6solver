# SPDX-License-Identifier: Apache-2.0
"""Local inspection, correction, expiry and deletion of a project's workflow record (design 17.3).

Everything here is local code: no model, no network. ``export`` writes ``inspection.json`` in the project's folder
for the person to open. ``apply_corrections`` reads ``corrections.json`` from the same folder, which the person
writes; a correction appends an annotation or a supersession and never changes a recorded event. ``delete`` and
``expire`` remove files and leave the policy's minimal receipt. None of them touches a review, a question store, a
loop session or the consent ledger: those are kept, and removed, by their own rules.

Problems found in ``corrections.json`` are reported by line or entry number in fixed words. Its content is never
repeated in a message, a result or an exception.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from g6.core.cloud_consent import sha256
from g6.core.ledger import digest
from g6.harness.workflow import policy
from g6.harness.workflow._store import Chain, RecordOff, RecordUnreadable, admit, plain, write_whole
from g6.harness.workflow.record import (ANNOTATION_KINDS, TIME_FORMAT, Event, EventRefused, ProjectRecord, event_id,
                                workflow_source)
from g6.workforce.receipt_store import _locked

INSPECTION_SCHEMA = "workflow.inspection.v1"
CORRECTIONS_SCHEMA = "workflow.corrections.v1"
MAX_CORRECTIONS_BYTES = 1_000_000
MAX_NAME = 80
SCOPES = ("run", "task", "project")
HOW_TO_CORRECT = ("To correct an entry, write corrections.json in this folder (schema workflow.corrections.v1: "
                  '{"schema": ..., "corrections": [{"target": <event_id>, "action": "annotate" or "supersede", '
                  '"note": <at most 200 characters>, "by": <your name>}]}) and ask G6 to apply corrections. '
                  "A correction is added beside the entry; the entry itself is never changed.")
_HEX64, _HEX32 = re.compile(r"[0-9a-f]{64}\Z"), re.compile(r"[0-9a-f]{32}\Z")


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime(TIME_FORMAT)


def _age_days(at: str, now: datetime) -> float:
    return (now - datetime.strptime(at, TIME_FORMAT).replace(tzinfo=UTC)).total_seconds() / 86400


# -- receipts ---------------------------------------------------------------------------------------------------
def _receipt_path(record: ProjectRecord) -> Path:
    return record.root / policy.RECEIPTS


def receipts(project: str) -> tuple[dict, ...]:
    """The deletion and expiry receipts kept for ``project``, oldest first."""
    path = _receipt_path(ProjectRecord(project))
    if not plain(path):
        return ()
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        raise RecordUnreadable("its receipts cannot be read") from None
    if not all(isinstance(row, dict) and tuple(row) == policy.RECEIPT_FIELDS for row in rows):
        raise RecordUnreadable("its receipts hold something that is not a receipt")
    return tuple(rows)


def _write_receipts(record: ProjectRecord, rows: list[dict]) -> None:
    write_whole(_receipt_path(record), "".join(json.dumps(row) + "\n" for row in rows))


def _receipt(record: ProjectRecord, scope: str, ident: str, removed: int, now: datetime, reason: str) -> dict:
    """One receipt with the policy's fields and nothing else: the scope's id is kept as a digest only."""
    row = dict(zip(policy.RECEIPT_FIELDS, (policy.POLICY_VERSION, scope, sha256(ident), removed, _stamp(now), reason),
                   strict=True))
    try:
        kept = list(receipts(record.project))
    except RecordUnreadable:
        kept = []                                   # receipts G6 cannot read are replaced, never trusted
    _write_receipts(record, [*kept, row])
    return row


# -- removal ----------------------------------------------------------------------------------------------------
def _remove(paths) -> int:
    """Remove files (and folders, whole); the number of record files removed, sidecars not counted."""
    removed = 0
    for path in paths:
        if not os.path.lexists(path):
            continue
        counted = not path.name.endswith(policy.SIDECARS)
        if path.is_dir() and not path.is_symlink():
            removed += sum(1 for p in path.rglob("*") if p.is_file() and not p.name.endswith(policy.SIDECARS))
            shutil.rmtree(path)
        else:
            path.unlink()
            removed += counted
    return removed


def _prune(record: ProjectRecord) -> None:
    """Remove task folders that hold no record any more (only locks, or nothing)."""
    for task in record.tasks():
        folder = record.root / "tasks" / task
        if not any(p.is_file() and not p.name.endswith(policy.SIDECARS) for p in folder.rglob("*")):
            shutil.rmtree(folder, ignore_errors=True)


def _drop_export(record: ProjectRecord, now: datetime | None = None) -> int:
    """Remove ``inspection.json``: always when ``now`` is None (what it showed has changed), else only when it is
    past its period or G6 cannot tell when it was written."""
    path = record.root / policy.INSPECTION
    if not os.path.lexists(path):
        return 0
    if now is not None:
        try:
            written = json.loads(path.read_text(encoding="utf-8"))["written_at"]
            if _age_days(written, now) <= policy.RETENTION_DAYS["exports"]:
                return 0
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return _remove([path])


def _run_files(record: ProjectRecord, task: str, run: str) -> tuple[Path, ...]:
    return record.run_chain(task, run).files()


def _decision_chains(record: ProjectRecord) -> list[Chain]:
    from g6.harness.workflow.decisions import Decisions

    store = Decisions(record.project)
    return [store._chain("", "project"), *(store._chain(task, "task") for task in record.tasks())]


def _feedback_chains(record: ProjectRecord) -> list[Chain]:
    from g6.harness.workflow.feedback import FeedbackStore

    return [FeedbackStore(record.project)._chain]


#: Each file whose entries name the run or task they are about (``policy.DELETION`` "entries"): the scopes its entries
#: can be bound to, and where its chains are.
BINDINGS: dict[str, tuple[tuple[str, ...], Callable[[ProjectRecord], list[Chain]]]] = {
    policy.ANNOTATIONS: (("run", "task"), lambda record: [record.annotation_chain(t) for t in record.tasks()]),
    policy.DECISIONS: (("task",), _decision_chains),
    policy.FEEDBACK: (("task",), _feedback_chains),
}
assert set(BINDINGS) == {name for name, rule in policy.DELETION.items() if rule == "entries"}


def _unbound(scope: str, ident: str) -> Callable[[list[dict]], list[dict]]:
    """The entries not bound to that run or task: an entry is bound when its body names it, or when it closes an
    entry that is (a feedback invitation's close goes with the invitation)."""
    def kept(entries: list[dict]) -> list[dict]:
        gone = {e["id"] for e in entries if isinstance(e["body"], dict) and e["body"].get(scope) == ident}
        gone |= {e["id"] for e in entries if isinstance(e["body"], dict) and e["body"].get("invitation") in gone}
        return [e for e in entries if e["id"] not in gone]
    return kept


def _strip(scope: str, ident: str, chains: list[tuple[str, Chain]]) -> tuple[int, int, list[str]]:
    """Remove the entries bound to the run or task from each chain: (entries removed, files removed, the names of the
    files that could not be read and so were removed whole, since what they hold cannot be told apart)."""
    entries = files = 0
    unreadable: list[str] = []
    for name, chain in chains:
        try:
            gone = chain.keep(_unbound(scope, ident))
            entries += gone
            files += bool(gone) and not os.path.lexists(chain.path)       # its last entries went, and the file
        except RecordUnreadable:
            files += _remove(chain.files())
            unreadable.append(name)
    return entries, files, sorted(set(unreadable))


def _bound_chains(record: ProjectRecord, scope: str, targets: list[Path]) -> list[tuple[str, Chain]]:
    """Every chain of an "entries" file that can hold entries bound to this scope, outside what goes whole."""
    def inside(path: Path) -> bool:
        return any(path == t or t in path.parents for t in targets)
    return [(name, chain) for name, (scopes, chains) in BINDINGS.items() if scope in scopes
            for chain in chains(record) if not inside(chain.path) and chain.exists()]


def _holds(chains: list[tuple[str, Chain]], scope: str, ident: str) -> bool:
    for _name, chain in chains:
        try:
            entries = chain.entries()
        except RecordUnreadable:
            continue
        if len(_unbound(scope, ident)(entries)) != len(entries):
            return True
    return False


def _kept(scope: str) -> list[str]:
    """What a deletion of this scope keeps by design, from the policy's table."""
    return [name for name, rule in policy.DELETION.items() if rule == "kept" or rule == "person" and scope != "project"]


def delete(project: str, *, scope: str, ident: str = "", by: str, now: datetime | None = None) -> dict:
    """Remove one run, one task or the whole project record, with the export that depended on it, and leave one
    receipt. A run's or task's files go whole, and every entry bound to that run or task in the project's other files
    (``policy.DELETION``) is removed; what is kept by design is named in the reply (``kept``). ``by`` is who asked
    (required, and not kept). Works on a part of the record that cannot be read: such a file that could hold entries
    bound to the run or task is removed whole and named (``unreadable_removed``)."""
    record, moment = ProjectRecord(project), _now(now)

    def refused(why: str) -> dict:
        return {"status": "refused", "removed": 0, "why": why}

    if not isinstance(by, str) or not by.strip():
        return refused("a deletion names who asked for it")
    if scope == "project" and ident in ("", project):
        ident = project
        # the folder's own lock files stay (one may be held by the caller); they hold nothing
        targets = [p for p in record.root.iterdir() if p.name != policy.RECEIPTS and not (
            p.is_file() and p.name.endswith(policy.LOCK))] if record.root.is_dir() else []
        chains: list[tuple[str, Chain]] = []
    elif scope == "task" and isinstance(ident, str) and _HEX64.match(ident):
        targets = [record.root / "tasks" / ident] if (record.root / "tasks" / ident).is_dir() else []
        chains = _bound_chains(record, scope, targets)
        if not targets and not _holds(chains, scope, ident):
            return refused("no such task in this project's record")
    elif scope == "run" and isinstance(ident, str) and _HEX32.match(ident):
        tasks = [t for t in record.tasks() if os.path.lexists(record.run_chain(t, ident).path)]
        targets = [path for task in tasks for path in _run_files(record, task, ident)]
        chains = _bound_chains(record, scope, targets)
        if not tasks and not _holds(chains, scope, ident):
            return refused("no such run in this project's record")
    else:
        return refused("a deletion names a run, a task of this project, or the project")
    try:
        entries, files, unreadable = _strip(scope, ident, chains)
        removed = _remove(targets) + files + entries + _drop_export(record)
        _prune(record)
        receipt = _receipt(record, scope, ident, removed, moment, "deletion_requested")
    except OSError:
        return {"status": "incomplete", "removed": 0,
                "why": "a file could not be removed (it may be open elsewhere); nothing is reported as deleted"}
    return {"status": "deleted", "removed": removed, "receipt": receipt, "kept": _kept(scope),
            **({"unreadable_removed": unreadable} if unreadable else {})}


def expire(project: str, *, now: datetime | None = None) -> dict:
    """Remove what is past its period under the storage policy: a chained file goes whole once its newest entry is
    past the period for its kind; the export and old receipts likewise. One receipt when anything was removed."""
    record, moment, removed = ProjectRecord(project), _now(now), 0

    def old(entries_at: list[str], kind: str) -> bool:
        return bool(entries_at) and _age_days(max(entries_at), moment) > policy.RETENTION_DAYS[kind]

    for task in record.tasks():
        folder = record.root / "tasks" / task
        for run_file in sorted((folder / "runs").glob("*" + policy.RUN_SUFFIX)) if (folder / "runs").is_dir() else ():
            run = run_file.name.removesuffix(policy.RUN_SUFFIX)
            try:
                if old([e.at for e in record.events(task=task, run=run)], "events"):
                    removed += _remove(_run_files(record, task, run))
            except (RecordUnreadable, EventRefused):
                continue                            # a part G6 cannot read is left for the person to delete
        try:
            if old([e.at for e in record.annotations(task)], "annotations"):
                removed += _remove(record.annotation_chain(task).files())
        except (RecordUnreadable, EventRefused):
            pass
    for name, kind in ((policy.DECISIONS, "decisions"), (policy.FEEDBACK, "feedback")):
        removed += _expire_chain_files(record, name, kind, moment)
    removed += _drop_export(record, moment)
    _prune(record)
    try:
        kept = [row for row in receipts(project) if _age_days(row["at"], moment) <= policy.RETENTION_DAYS["receipts"]]
        if len(kept) != len(receipts(project)):
            _write_receipts(record, kept)
    except (RecordUnreadable, ValueError, TypeError):
        pass
    result = {"status": "expired" if removed else "nothing", "removed": removed}
    if removed:
        result["receipt"] = _receipt(record, "project", project, removed, moment, "expired")
    return result


def _expire_chain_files(record: ProjectRecord, name: str, kind: str, moment: datetime) -> int:
    """Chained stores later slices keep (decisions, feedback): each goes whole once its newest entry is past its
    period. Their entries carry ``at`` in the body."""
    removed = 0
    for path in [record.root / name, *sorted((record.root / "tasks").glob("*/" + name))]:
        if not os.path.lexists(path):
            continue
        chain = Chain(path, head=True)
        try:
            times = [entry["body"].get("at", "") for entry in chain.entries()]
            if times and all(times) and _age_days(max(times), moment) > policy.RETENTION_DAYS[kind]:
                removed += _remove(chain.files())
        except (RecordUnreadable, ValueError, TypeError, AttributeError):
            continue
    return removed


# -- inspection -------------------------------------------------------------------------------------------------
def _shown(event: Event) -> dict:
    body = event.to_body()
    return {key: body[key] for key in ("event_id", "kind", "at", "provenance", "data", "refs", "observed", "missing")}


def export(project: str, *, now: datetime | None = None) -> Path:
    """Write ``inspection.json`` in the project's folder (readable by its owner only, written whole and moved into
    place) and return where it is. It lists each task's runs in order, each run's recorded entries with their
    provenance (host observations apart from corrections and derived entries), and repeated rounds as rework."""
    record, moment = ProjectRecord(project), _now(now)
    _drop_export(record, moment)
    tasks = []
    for task in record.tasks():
        notes = record.annotations(task)
        runs = []
        for run in record.runs(task):
            observed, rounds = [], 0
            for event in record.events(task=task, run=run):
                row = _shown(event)
                if event.kind == "loop_round":
                    rounds += 1
                    if rounds > 1:
                        row["repeat"] = True
                later = [n.event_id for n in notes if n.kind == "supersession" and n.data.get("target") == event.event_id]
                if later:
                    row["superseded_by"] = later[-1]
                observed.append(row)
            runs.append({"run": run, "rounds": rounds, "rework_rounds": max(0, rounds - 1), "observed": observed,
                         "derived": [_shown(n) for n in notes if n.run == run]})
        tasks.append({"task": task, "runs": runs})
    body = {"schema": INSPECTION_SCHEMA, "policy_version": policy.POLICY_VERSION, "project": project,
            "written_at": _stamp(moment),
            "expires_at": _stamp(moment + timedelta(days=policy.RETENTION_DAYS["exports"])),
            "what_this_is": "What G6 recorded about its own review activity in this repository: codes, counts, "
                            "times and digests. It holds no task text, code or answers. Work outside G6 is not here.",
            "how_to_correct": HOW_TO_CORRECT, "tasks": tasks, "receipts": len(receipts(project))}
    path = record.root / policy.INSPECTION
    write_whole(path, json.dumps(body, indent=1) + "\n")
    return path


# -- corrections ------------------------------------------------------------------------------------------------
def _annotation(record: ProjectRecord, row: dict, events: dict[str, Event], moment: datetime) -> Callable[[], bool]:
    """Check one annotate or supersede row; return the write to make once every row has passed."""
    if set(row) != {"target", "action", "note", "by"}:
        raise ValueError("it has fields other than target, action, note and by")
    target = events.get(row["target"]) if isinstance(row["target"], str) else None
    if target is None:
        raise ValueError("its target is not the event_id of a recorded entry")
    note, by = row["note"], row["by"]
    if not isinstance(note, str) or not isinstance(by, str) or not by.strip() or len(by) > MAX_NAME:
        raise ValueError(f"it needs a note and the name of who made it (at most {MAX_NAME} characters)")
    kind = "annotation" if row["action"] == "annotate" else "supersession"
    try:
        event = Event(project=record.project, task=target.task, run=target.run, kind=kind,
                      event_id=event_id(target.run, kind, digest([target.event_id, note, by])), at=_stamp(moment),
                      provenance="attributed_label", sources=(workflow_source(record.project),),
                      baseline_sha256=target.baseline_sha256, artifact_sha256=target.artifact_sha256,
                      data={"target": target.event_id, "note": note, "by": by.strip()})
    except EventRefused:
        raise ValueError("its note is longer than 200 characters") from None
    admit(record.annotation_chain(target.task).path)        # before any row is applied: all of the file or none
    return lambda: record.annotate(event)


def _deletion(record: ProjectRecord, row: dict, events: dict[str, Event], moment: datetime) -> Callable[[], bool]:
    """Check one delete row (for a client with no dialog: the person writes the request themselves)."""
    if not set(row) <= {"action", "scope", "id", "by"} or not {"scope", "by"} <= set(row):
        raise ValueError("a deletion has scope and by, and id for a run or a task, and no other field")
    scope, ident, by = row["scope"], row.get("id", ""), row["by"]
    if not isinstance(by, str) or not by.strip() or len(by) > MAX_NAME:
        raise ValueError(f"it needs the name of who asked (at most {MAX_NAME} characters)")
    def held(scope: str) -> bool:     # a run's or task's files, or an entry bound to it in another file
        return isinstance(ident, str) and (any(e.run == ident for e in events.values()) if scope == "run" else
                                           ident in record.tasks()) or _holds(_bound_chains(record, scope, []), scope, ident)
    known = ident in ("", record.project) if scope == "project" else held(scope) if scope in ("task", "run") else False
    if not known:
        raise ValueError("its scope is run, task or project, and its id is that run's or task's id, as the inspection "
                         "file gives it")
    return lambda: delete(record.project, scope=scope, ident=ident, by=by, now=moment)["status"] == "deleted"


def _decision(record: ProjectRecord, row: dict, events: dict[str, Event], moment: datetime) -> Callable[[], bool]:
    """Check one decision row: the person adopts, dismisses or reopens a suggestion without a dialog."""
    from g6.harness.workflow import decisions

    if not set(row) <= {"action", "intervention", "decision", "scope", "task", "expires_days", "by"} \
            or not {"intervention", "decision", "task", "by"} <= set(row):
        raise ValueError("a decision has intervention, decision, task and by, and may have scope and expires_days")
    days, scope = row.get("expires_days"), row.get("scope", "task")
    shown = isinstance(row["intervention"], str) and any(
        e.kind == "suggestion_displayed" and e.data.get("intervention") == row["intervention"] and e.task == row["task"]
        for e in events.values())
    if not shown or row["decision"] not in decisions.DECISIONS or scope not in decisions.SCOPES \
            or not isinstance(row["by"], str) or not row["by"].strip() or len(row["by"]) > MAX_NAME \
            or not (days is None or type(days) is int and 0 < days <= policy.RETENTION_DAYS["decisions"]):
        raise ValueError("it names a suggestion G6 showed for that task, adopted, dismissed or reopened, and who decided")
    expires = _stamp(moment + timedelta(days=days)) if days else None
    admit(record.root / policy.DECISIONS)

    def write() -> bool:
        store = decisions.Decisions(record.project)
        mine = [body for body in store.held(task=row["task"]) if body["intervention"] == row["intervention"]]
        if mine and (mine[-1]["decision"], mine[-1]["scope"]) == (row["decision"], scope):
            return False               # the same file applied again adds nothing
        store.record(row["intervention"], row["decision"], task=row["task"], scope=scope, by=row["by"],
                     at=_stamp(moment), expires_at=expires)
        return True
    return write


def _feedback_choice(record: ProjectRecord, row: dict, events: dict[str, Event], moment: datetime) -> Callable[[], bool]:
    """Check one feedback row: the person opts in to, or out of, the feedback question."""
    from g6.harness.workflow.feedback import FeedbackStore

    if set(row) != {"action", "choice", "by"} or row["choice"] not in ("in", "out") \
            or not isinstance(row["by"], str) or not row["by"].strip() or len(row["by"]) > MAX_NAME:
        raise ValueError("a feedback entry has choice (in or out) and by, and no other field")
    admit(record.root / policy.FEEDBACK)

    def write() -> bool:
        store = FeedbackStore(record.project)
        latest = next((e["body"]["choice"] for e in reversed(store.entries()) if e["kind"] == "opt"), "")
        if latest == row["choice"]:
            return False               # the same file applied again adds nothing
        store.opt(row["choice"], by=row["by"], at=_stamp(moment), via="corrections")
        return True
    return write


#: Each action a corrections file may ask for: a checker that returns the write to make.
ACTIONS: dict[str, Callable] = {"annotate": _annotation, "supersede": _annotation, "delete": _deletion,
                                "decision": _decision, "feedback": _feedback_choice}
assert set(ANNOTATION_KINDS) == {"annotation", "supersession"}


def _read_corrections(path: Path) -> tuple[list, list[str]]:
    try:
        if path.stat().st_size > MAX_CORRECTIONS_BYTES:
            return [], [f"corrections.json is too large (more than {MAX_CORRECTIONS_BYTES} bytes); nothing was applied"]
        text = path.read_bytes().decode("utf-8-sig")       # a byte-order mark, as some editors write, is accepted
    except UnicodeError:
        return [], ["corrections.json is not UTF-8 text; nothing was applied"]
    except OSError:
        return [], ["corrections.json could not be read; nothing was applied"]
    try:
        body = json.loads(text)
    except json.JSONDecodeError as bad:
        return [], [f"corrections.json is not valid JSON at line {bad.lineno}; nothing was applied"]
    except (ValueError, RecursionError):
        return [], ["corrections.json is not valid JSON; nothing was applied"]
    if not isinstance(body, dict) or body.get("schema") != CORRECTIONS_SCHEMA or set(body) != {"schema", "corrections"}:
        return [], [f'corrections.json needs "schema": "{CORRECTIONS_SCHEMA}" and "corrections"; nothing was applied']
    if not isinstance(body["corrections"], list):
        return [], ['"corrections" must be a list; nothing was applied']
    return body["corrections"], []


def apply_corrections(project: str, *, now: datetime | None = None) -> dict:
    """Read the person's ``corrections.json`` and apply it, all of it or none of it. Applying the same file again
    adds nothing. The result counts and names problems by line or entry number, and never repeats the file."""
    record, moment = ProjectRecord(project), _now(now)
    path = record.root / policy.CORRECTIONS
    if not os.path.lexists(path):
        if record.root.is_dir():
            _drop_export(record, moment)
        return {"status": "absent", "applied": 0, "already": 0, "problems": []}
    _drop_export(record, moment)
    with _locked(record.root / "corrections"):
        try:
            plain(path)
        except RecordUnreadable:
            return {"status": "refused", "applied": 0, "already": 0,
                    "problems": ["corrections.json is not a plain file; nothing was applied"]}
        rows, problems = _read_corrections(path)
        writes: list[Callable[[], bool]] = []
        if not problems:
            events = {e.event_id: e for e in record.events()}
            for number, row in enumerate(rows, 1):
                try:
                    check = ACTIONS.get(row.get("action")) if isinstance(row, dict) and isinstance(
                        row.get("action"), str) else None
                    if check is None:
                        raise ValueError("its action is not one of " + ", ".join(sorted(ACTIONS)))
                    writes.append(check(record, row, events, moment))
                except ValueError as bad:                    # the checkers' own fixed words, never the row
                    problems.append(f"entry {number}: {bad}")
                except RecordOff as off:                     # it would add to the record, which is off
                    problems.append(f"entry {number}: {off.why}; G6 adds nothing to it (a deletion still applies)")
        if problems:
            return {"status": "refused", "applied": 0, "already": 0, "problems": problems[:20]}
        done = [write() for write in writes]
    if any(done):
        _drop_export(record)                                 # what an earlier export showed has changed
    return {"status": "applied" if any(done) else "nothing", "applied": sum(done), "already": len(done) - sum(done),
            "problems": []}


__all__ = ["ACTIONS", "BINDINGS", "CORRECTIONS_SCHEMA", "HOW_TO_CORRECT", "INSPECTION_SCHEMA", "MAX_CORRECTIONS_BYTES", "SCOPES",
           "apply_corrections", "delete", "expire", "export", "receipts"]
