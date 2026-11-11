# SPDX-License-Identifier: Apache-2.0
"""Refresh (design 12.5 item 3, 12.6): re-read the files a person accepted, and publish what changed as a new
generation without ever exposing a half state.

``_``-prefixed, so it never registers as a component. The order is fixed. (1) Each active registration's folder is
walked again and compared with its membership by file hash. (2) Every changed or removed file is tombstoned at once,
before anything is staged: from that moment its old passages are denied, whatever happens next. (3) The next generation
is staged as a copy of the current index; the old passages of changed and removed files are taken out and the new
passages (new ids, since an id binds the file's hash) are added, each step leaving a per-item receipt
(``pending``, ``acknowledged``, ``read_back``). (4) The generation is published in one transaction, only when the
index read back every listed passage. A crash at any point leaves the previous generation published, the changed
files denied and a staging generation the next refresh marks abandoned; running refresh again completes the work.

A file that is new to the folder is not indexed: the person accepted a list, and a different list needs a new
preview. A refresh that fails reports the content as unavailable and serves no changed passage.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core.storage import PathRefused, contained
from g6.knowledge import _engine as engine
from g6.knowledge._index import IndexRefused
from g6.knowledge._registry import Member, ProjectStore, Receipt, Refused, Registration, is_kept, source_uri
from g6.knowledge._scope import Access, ScopeDenied, inside
from g6.knowledge._types import now, sha256_hex
from g6.knowledge._view import label_view
from g6.knowledge._walk import MAX_FILE_BYTES, Excluded, read_file, walk

REFRESH_FAILED = "G6_E_KNOWLEDGE_REFRESH"


class Reread(BaseModel):
    """The receipt of one re-read of a registration: the source version it saw, and when."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    registration_id: str
    at: str
    basis: Literal["file_hash", "commit"]
    commit: str = ""
    unchanged: int = 0
    changed: int = 0
    added: int = 0
    removed: int = 0
    failed: tuple[Excluded, ...] = ()


def commit_of(binding, root: Path) -> str:
    """The commit the folder is at, when it is in a git repository under the permitted roots and its files there
    are exactly that commit's (nothing modified, nothing untracked); ``""`` otherwise."""
    top = next((f for f in (root, *root.parents)
                if any(f == r or r in f.parents for r in binding.roots) and (f / ".git").exists()), None)
    if top is None:
        return ""
    try:
        from g6.sources.ctx_git import config as git_config, runner
        from g6.sources.ctx_git.config import GitConfig, GitRefused

        repo, gitdir = git_config.repository(str(top), GitConfig(roots=binding.roots, remote_hosts=(),
                                                                 source="the Context Engine's host binding"))
        prefix = root.relative_to(repo).as_posix() or "."
        with runner.session(repo, gitdir) as run:
            if run("status", "--porcelain", "--", prefix).out.strip():
                return ""
            head = run("rev-parse", "HEAD").text.strip()
    except (GitRefused, OSError, ValueError):
        return ""
    return head if len(head) in (40, 64) and all(c in "0123456789abcdef" for c in head) else ""


def _compare(store: ProjectStore, acc: Access, registration: Registration,
             members: list[Member]) -> tuple[dict[str, Any], Reread]:
    """What a re-walk of ``registration`` shows against its membership."""
    at = now()
    try:
        root = inside(acc.binding, registration.root)
        walked = walk(acc.binding, root)
    except ScopeDenied:
        gone = {m.uri: m for m in members}
        return ({"root": None, "unchanged": [], "changed": [], "removed": list(gone.values()), "added": []},
                Reread(registration_id=registration.id, at=at, basis="file_hash", removed=len(gone),
                       failed=(Excluded(relative=".", why="outside_root"),)))
    listed = {f.relative: f for f in walked.files}
    known = {m.relative: m for m in members}
    unchanged = [m for rel, m in known.items() if rel in listed and listed[rel].sha256 == m.file_sha256]
    changed = [(m, listed[rel]) for rel, m in known.items() if rel in listed and listed[rel].sha256 != m.file_sha256]
    removed = [m for rel, m in known.items() if rel not in listed]
    added = [f for rel, f in listed.items() if rel not in known]
    commit = commit_of(acc.binding, root)
    failed = tuple(e for e in walked.excluded if e.relative in known)
    return ({"root": root, "unchanged": unchanged, "changed": changed, "removed": removed, "added": added},
            Reread(registration_id=registration.id, at=at, basis="commit" if commit else "file_hash", commit=commit,
                   unchanged=len(unchanged), changed=len(changed), added=len(added), removed=len(removed),
                   failed=failed))


def refresh(acc: Access, registration_id: str | None = None, *, clock: engine.Clock = engine._utc) -> dict[str, Any]:
    """Re-read the project's active registrations (or the one named) and publish what changed; see the module
    docstring. The reply holds counts and views of paths, never text."""
    store = ProjectStore(acc.binding)
    current = store.published()
    if current is None:
        return {"state": "not_published", "generation": None, "sources": [], "out_of_scope": []}
    # scope first, from the registry and the grant ledger alone: a source that may not be used at this time (it is
    # revoked, deleted or past its expiry) or that is not shared with this destination is not opened, and nothing
    # this reply says is computed from it
    readable, withheld, _ = engine.scope_of(store, acc, clock())
    registrations = [r for r in store.registrations() if r.id in readable
                     and (registration_id is None or r.id == registration_id)]
    if registration_id is not None and not registrations:
        raise Refused("G6_E_KNOWLEDGE_SOURCE", "no such source registration that this client may refresh now")
    members = store.members(current.number)
    found: dict[str, dict[str, Any]] = {}
    receipts: dict[str, Reread] = {}
    for registration in registrations:
        own = [m for m in members if m.registration_id == registration.id]
        found[registration.id], receipts[registration.id] = _compare(store, acc, registration, own)
    # (2) deny every changed or removed file's old passages before anything else happens
    for registration in registrations:
        seen = found[registration.id]
        for member in [m for m, _ in seen["changed"]] + seen["removed"]:
            store.changed(registration.id, member.uri)
    engine.checkpoint("refresh:tombstoned")
    touched = any(found[r.id]["changed"] or found[r.id]["removed"] for r in registrations)
    summary = [{"source": r.id, **receipts[r.id].model_dump(exclude={"registration_id", "failed"}),
                "failed": [{"path": label_view(e.relative), "why": e.why} for e in receipts[r.id].failed],
                "needs_a_new_preview": [label_view(f.relative) for f in found[r.id]["added"]]}
               for r in registrations]
    stale = engine._still_changed(store, current.number)
    if not touched and not any((m.registration_id, m.uri) in stale for m in members):
        for receipt in receipts.values():
            store.reread(receipt, current.number)
        return {"state": "unchanged", "generation": current.number, "sources": summary, "out_of_scope": sorted(withheld)}
    try:
        generation = _publish(store, acc, current.number, members, found, registrations)
    except (IndexRefused, Refused, OSError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        return {"state": "unavailable", "generation": current.number, "sources": summary, "out_of_scope": sorted(withheld),
                "reason": f"the refresh did not publish ({code}); the changed files' passages stay denied and the "
                          "rest is served from the previous generation. Run the refresh again."}
    for receipt in receipts.values():
        store.reread(receipt, generation)
    return {"state": "published", "generation": generation, "sources": summary, "out_of_scope": sorted(withheld)}


def _publish(store: ProjectStore, acc: Access, previous: int, members: list[Member],
             found: dict[str, dict[str, Any]], registrations: list[Registration]) -> int:
    staged = store.stage({"sqlite": True})
    try:
        return _stage_and_publish(store, staged, previous, members, found, registrations)
    except Exception:
        store.abandon(staged.number)   # nothing of a refresh that did not publish stays on disk
        raise


def _stage_and_publish(store: ProjectStore, staged: Any, previous: int, members: list[Member],
                       found: dict[str, dict[str, Any]], registrations: list[Registration]) -> int:
    path = store.index_path("sqlite", staged.number)
    before = store.index_path("sqlite", previous)
    if before.exists():
        shutil.copyfile(before, path)
    index = engine._lexical(store, staged.number)
    active = {r.id for r in store.registrations() if is_kept(r)}
    refreshed = {r.id for r in registrations}
    kept: list[Member] = []
    drop: list[str] = []
    stale = engine._still_changed(store, previous)
    for member in members:
        if member.registration_id not in active:
            drop.extend(member.passage_ids)
        elif member.registration_id not in refreshed and (member.registration_id, member.uri) in stale:
            # a file of a source this refresh does not re-read was observed changed: it stays denied (no passages)
            drop.extend(member.passage_ids)
            kept.append(member.model_copy(update={"generation": staged.number, "passage_ids": (), "spans": (),
                                                  "file_sha256": ""}))
        elif member.registration_id not in refreshed:
            kept.append(member.model_copy(update={"generation": staged.number}))
    additions: list[tuple[Registration, Member, Any]] = []
    for registration in registrations:
        seen = found[registration.id]
        kept.extend(m.model_copy(update={"generation": staged.number}) for m in seen["unchanged"])
        for member in seen["removed"]:
            drop.extend(member.passage_ids)
        for member, entry in seen["changed"]:
            drop.extend(member.passage_ids)
            additions.append((registration, member, entry))
    for ident in drop:
        store.receipt(Receipt(generation=staged.number, backend="sqlite", item=ident, action="remove",
                              state="pending", attempts=1))
    engine.checkpoint("refresh:pending")
    if drop:
        index.remove(drop)
    new_members: list[Member] = []
    for registration, member, entry in additions:
        root = found[registration.id]["root"]
        try:
            data = read_file(contained(root, entry.relative, refuse_links=True), MAX_FILE_BYTES)
        except (OSError, PathRefused):
            raise Refused(REFRESH_FAILED, "a file could not be read again") from None
        if sha256_hex(data) != entry.sha256:
            raise Refused(REFRESH_FAILED, "a file changed again while it was refreshed")
        uri = source_uri(registration.id, entry.relative)
        document, converter, notes = engine._document(data, uri, entry.media_type)
        passages, more = engine._passages(document) if document is not None else ([], ())
        for passage in passages:
            store.receipt(Receipt(generation=staged.number, backend="sqlite", item=passage.id, action="add",
                                  state="pending", attempts=1))
        for start in range(0, len(passages), engine.BATCH):
            index.add(passages[start:start + engine.BATCH])
        new_members.append(Member(
            registration_id=registration.id, generation=staged.number, uri=uri, relative=entry.relative,
            file_sha256=entry.sha256, size=entry.size, document_sha256=document.sha256 if document else "",
            passage_ids=tuple(p.id for p in passages), spans=tuple((p.start, p.end) for p in passages),
            media_type=entry.media_type, converter=converter, chunker=engine.CHUNKER, unread=(*notes, *more)))
        if registration.retention == "audit":
            store.retain(data, registration.id)
    store.receipts_to(staged.number, "acknowledged")
    engine.checkpoint("refresh:acknowledged")
    everything = [*kept, *new_members]
    listed = {pid for m in everything for pid in m.passage_ids}
    extra = sorted(index.held_ids() - listed)   # whatever an interrupted earlier step left in the copied index
    if extra:
        index.remove(extra)
    store.record(staged.number, everything)
    written = len({pid for m in everything for pid in m.passage_ids})
    held = index.status()["passages"] if path.exists() else 0
    store.receipts_to(staged.number, "read_back" if held == written else "failed")
    engine.checkpoint("refresh:read_back")
    return store.publish(staged.number, {"sqlite": {"written": written, "read_back": held}}).number


__all__ = ["REFRESH_FAILED", "Reread", "commit_of", "refresh"]
