# SPDX-License-Identifier: Apache-2.0
"""The Context Engine's first path (design 12.9 step 1): preview a folder, commit it after the person accepted, search
it, fetch one passage, revoke it, list the sources.

``_``-prefixed, so it never registers as a component. Everything here runs on this machine: the index is a local
SQLite file per project and generation, and nothing is sent to a model. Whether a result may leave the machine is
decided per source and per destination: for a destination other than ``"local"``, a source not granted to that
provider through the client's dialog (``g6.core.cloud_consent``) contributes nothing to the reply but its registration
id and the provider, in ``withheld``. No result is written to a file for a destination that was refused it.

``search`` runs in a fixed order: (1) the published generation, or ``not_published``; (2) the lexical search on that
generation's file; (3) :meth:`ProjectStore.use` drops revoked, expired and unknown passages; (4) for a non-local
destination, sources not granted to that provider are dropped and listed in ``withheld``; (5) each surviving hit's
file is read again, converted and compared with the hit, and a mismatch writes a ``changed`` tombstone and drops it;
(6) ``use`` is called again immediately before the return; (7) items carry redacted views only. "Checked" on an item
means exactly step 5: the passage was re-read and compared in the call that returned it.
"""
from __future__ import annotations

import json
import secrets
import shutil
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from g6.core import cloud_consent
from g6.core.storage import PathRefused, contained, state_path
from g6.knowledge._envelope import (MAX_TOP_K, BackendReport, Checked, Contribution, Denied, PassageItem,
                                    SearchRequest, SearchResponse)
from g6.knowledge._index import IndexRefused
from g6.knowledge._registry import Member, ProjectStore, Refused, Registration, source_uri
from g6.knowledge._scope import Access, Binding, ScopeDenied, bound, consent_scope, inside
from g6.knowledge._types import Document, Passage, now, passage_problems, sha256_hex
from g6.knowledge._view import label_view, view_of
from g6.knowledge._walk import MAX_FILE_BYTES, Excluded, FileEntry, Walked, read_file, walk

PREVIEW_CHANGED = "G6_E_KNOWLEDGE_PREVIEW_CHANGED"
INPUT = "G6_E_KNOWLEDGE_INPUT"
INDEX = "G6_E_KNOWLEDGE_INDEX"
CHUNKER = "words:200:20"
#: Passages added to the index in one write.
BATCH = 500
#: The lexical route asks for this many candidates per wanted result, since later steps drop some.
OVERSAMPLE = 4
Clock = Callable[[], datetime]


def _utc() -> datetime:
    return datetime.now(UTC)


def checkpoint(name: str) -> None:
    """Named points between the engine's steps. It does nothing; tests replace it to force an order of events."""


class Preview(BaseModel):
    """What a person is asked to accept: the exact file list and its digest, and what accepting does."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project: str
    root: str
    label: str
    files: tuple[FileEntry, ...]
    excluded: tuple[Excluded, ...]
    backends: tuple[str, ...] = ("sqlite",)
    destinations: tuple[str, ...] = ("local",)
    effects: tuple[str, ...] = ("the listed files are indexed on this machine", "nothing is sent to a model")
    retention: str = "none"
    expires_at: str = ""
    nonce: str
    note: str = ""
    sha256: str = ""
    listing_path: str = ""


def _digest(preview: Preview) -> str:
    body = preview.model_dump(exclude={"sha256", "listing_path"})
    return sha256_hex(json.dumps(body, sort_keys=True, separators=(",", ":")))


def _label(binding: Binding, real: Path) -> str:
    if binding.start is not None and (real == binding.start or binding.start in real.parents):
        return real.relative_to(binding.start).as_posix() or "."
    root = next(r for r in binding.roots if real == r or r in real.parents)
    return (Path(root.name) / real.relative_to(root)).as_posix()


def _preview_of(binding: Binding, walked: Walked, *, nonce: str, expires_at: str, retention: str) -> Preview:
    real = Path(walked.root)
    made = Preview(project=binding.project, root=walked.root, label=_label(binding, real), files=walked.files,
                   excluded=walked.excluded, retention=retention, expires_at=expires_at, nonce=nonce, note=walked.note)
    return made.model_copy(update={"sha256": _digest(made)})


def excluded_counts(excluded: Sequence[Excluded]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in excluded:
        out[item.why] = out.get(item.why, 0) + 1
    return dict(sorted(out.items()))


def preview(binding: Binding, path: str, *, expires_days: int = 0, retention: str = "none",
            clock: Clock = _utc) -> Preview:
    """Walk the folder ``path`` names and say exactly what would be indexed. Nothing is registered or indexed; the
    full list is written to a file under ``$G6_HOME/knowledge/previews/`` for the person to open."""
    bound(binding)
    if retention not in ("none", "audit"):
        raise Refused(INPUT, "retention is 'none' or 'audit'")
    if not isinstance(expires_days, int) or isinstance(expires_days, bool) or not 0 <= expires_days <= 3650:
        raise Refused(INPUT, "expires_days is a whole number of days from 0 (no expiry) to 3650")
    walked = walk(binding, inside(binding, path))
    expires_at = (clock().astimezone(UTC) + timedelta(days=expires_days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if expires_days else ""
    made = _preview_of(binding, walked, nonce=secrets.token_hex(16), expires_at=expires_at, retention=retention)
    listing = state_path("knowledge", "previews", f"{made.sha256}.md")
    listing.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Preview {made.sha256}", "", f"Folder: {made.root}", f"Files to index: {len(made.files)} "
             f"({sum(f.size for f in made.files)} bytes)", f"Not indexed: {len(made.excluded)}",
             f"Ignored files decided by: {walked.ignored_by}" + (f" ({walked.note})" if walked.note else ""),
             f"Retention: {made.retention}", f"Expires: {made.expires_at or 'never'}", "", "## Files to index", ""]
    lines += [f"- {f.relative} ({f.size} bytes, sha256 {f.sha256})" for f in made.files]
    lines += ["", "## Not indexed, and why", ""] + [f"- {e.relative}: {e.why}" for e in made.excluded]
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return made.model_copy(update={"listing_path": str(listing)})


def _document(data: bytes, member_uri: str, media_type: str) -> tuple[Document | None, str, tuple[str, ...]]:
    from g6.knowledge.knowledge_converter.converters import convert

    converted = convert(data, "text/plain" if media_type == "text/x-python" else media_type, uri=member_uri)
    if converted.document is None:
        return None, converted.backend, (converted.outcome,)
    return converted.document, converted.backend, tuple(converted.unread)


def _passages(document: Document) -> tuple[list[Passage], tuple[str, ...]]:
    from g6.knowledge.knowledge_chunker.chunker import ChunkError, chunk

    unit, size, overlap = CHUNKER.split(":")
    try:
        return chunk(document, unit=unit, size=int(size), overlap=int(overlap)), ()
    except ChunkError as exc:
        return [], (exc.code,)


def _lexical(store: ProjectStore, generation: int):
    from g6.knowledge.knowledge_indexer.index import SqliteIndex

    return SqliteIndex("lexical", path=store.index_path("sqlite", generation))


def _still_changed(store: ProjectStore, generation: int) -> set[tuple[str, str]]:
    return {(t.registration_id, t.uri) for t in store.tombstones() if t.reason == "changed" and t.generation >= generation}


def _build_generation(store: ProjectStore, registration: Registration, real: Path,
                      files: Sequence[FileEntry]) -> dict[str, Any]:
    """Stage the next generation: the previous one's index and membership for registrations still active, plus the
    new registration's files; publish it only when the index read back every listed passage."""
    previous = store.published()
    staged = store.stage({"sqlite": True})
    path = store.index_path("sqlite", staged.number)
    index = _lexical(store, staged.number)
    members: list[Member] = []
    try:
        if previous is not None:
            before = store.index_path("sqlite", previous.number)
            if before.exists():
                shutil.copyfile(before, path)
            active = {r.id for r in store.registrations() if r.state == "active"}
            denied = _still_changed(store, previous.number)
            dropped: list[str] = []
            for member in store.members(previous.number):
                if member.registration_id not in active:
                    dropped.extend(member.passage_ids)
                elif (member.registration_id, member.uri) in denied:
                    # the file was observed changed: it stays a member (the person accepted it) with no passages
                    # and no file hash, so the next refresh reads it again and nothing old comes back before then
                    dropped.extend(member.passage_ids)
                    members.append(member.model_copy(update={"generation": staged.number, "passage_ids": (),
                                                             "spans": (), "file_sha256": ""}))
                else:
                    members.append(member.model_copy(update={"generation": staged.number}))
            if dropped:
                index.remove(dropped)
        unread = 0
        for entry in files:
            uri = source_uri(registration.id, entry.relative)
            try:
                data = read_file(contained(real, entry.relative, refuse_links=True), MAX_FILE_BYTES)
            except (OSError, PathRefused):
                raise Refused(PREVIEW_CHANGED, "a file changed after the preview was accepted; nothing was "
                                               "indexed") from None
            if sha256_hex(data) != entry.sha256:
                raise Refused(PREVIEW_CHANGED, "a file changed after the preview was accepted; nothing was indexed")
            document, converter, notes = _document(data, uri, entry.media_type)
            passages, more = _passages(document) if document is not None else ([], ())
            for start in range(0, len(passages), BATCH):
                index.add(passages[start:start + BATCH])
            unread += bool(notes or more or not passages)
            members.append(Member(
                registration_id=registration.id, generation=staged.number, uri=uri, relative=entry.relative,
                file_sha256=entry.sha256, size=entry.size, document_sha256=document.sha256 if document else "",
                passage_ids=tuple(p.id for p in passages), spans=tuple((p.start, p.end) for p in passages),
                media_type=entry.media_type, converter=converter, chunker=CHUNKER, unread=(*notes, *more)))
        store.record(staged.number, members)
        checkpoint("commit:recorded")
        written = len({pid for m in members for pid in m.passage_ids})
        held = index.status()["passages"] if path.exists() else 0
        published = store.publish(staged.number, {"sqlite": {"written": written, "read_back": held}})
    except IndexRefused as exc:
        raise Refused(INDEX, f"the local index refused the write ({exc.code})") from None
    return {"generation": published.number, "passages": sum(len(m.passage_ids) for m in members
                                                           if m.registration_id == registration.id),
            "files": len(files), "unread": unread}


def commit(binding: Binding, accepted: Preview, attribution: str) -> dict[str, Any]:
    """Register and index exactly what ``accepted`` lists, after the person accepted it. The folder is walked again:
    a file list that differs from the accepted digest is ``G6_E_KNOWLEDGE_PREVIEW_CHANGED`` and changes nothing."""
    store = ProjectStore(binding)
    if not isinstance(accepted, Preview) or accepted.project != binding.project or accepted.sha256 != _digest(accepted):
        raise Refused(PREVIEW_CHANGED, "the preview is not one this project produced; nothing was indexed")
    if not isinstance(attribution, str) or not attribution.strip():
        raise Refused(INPUT, "acceptance carries the name the dialog returned")
    real = inside(binding, accepted.root)
    walked = walk(binding, real)
    again = _preview_of(binding, walked, nonce=accepted.nonce, expires_at=accepted.expires_at,
                        retention=accepted.retention)
    if again.sha256 != accepted.sha256:
        raise Refused(PREVIEW_CHANGED, "the folder's files differ from the preview that was accepted; nothing was "
                                       "indexed. Preview it again.")
    registration = store.register(accepted, attribution.strip())
    try:
        built = _build_generation(store, registration, real, accepted.files)
    except BaseException:
        try:
            store.revoke(registration.id)   # a registration that was never indexed does not stay active
        except Exception:  # noqa: BLE001 - the first error is the one to report
            pass
        raise
    return {"source": registration.id, "label": label_view(registration.label), "state": "active", **built,
            "excluded": excluded_counts(accepted.excluded), "expires_at": registration.expires_at,
            "retention": registration.retention, "authorisation_revision": registration.authorisation_revision}


def _granted(acc: Access, registration_id: str) -> bool:
    if acc.local:
        return True
    try:
        return cloud_consent.is_granted(consent_scope(registration_id), acc.destination)
    except (ValueError, ScopeDenied):
        return False


def _reread(binding: Binding, registration: Registration, member: Member) -> tuple[Document | None, bytes]:
    """The member's file read and converted again, when its bytes are the bytes that were indexed; else ``None``."""
    try:
        root = inside(binding, registration.root)
        data = read_file(contained(root, member.relative, refuse_links=True), MAX_FILE_BYTES)
    except (ScopeDenied, PathRefused, OSError):
        return None, b""
    if sha256_hex(data) != member.file_sha256:
        return None, data
    document, _, _ = _document(data, member.uri, member.media_type)
    return document, data


def _item(passage: Passage, member: Member, rank: int, contributions: tuple[Contribution, ...]) -> PassageItem:
    from g6.grounding.tripwire import instruction_signals

    return PassageItem(passage_id=passage.id, source=member.registration_id, path=label_view(member.relative),
                       start=passage.start, end=passage.end, view=view_of(passage.text, canonical_start=passage.start),
                       rank=rank, contributions=contributions,
                       checked=Checked(outcome="intact", reread_sha256=member.file_sha256, at=now()),
                       instruction_signals=instruction_signals(passage.text))


def _source(member: Member, passage: Passage) -> cloud_consent.Source:
    return cloud_consent.Source(consent_scope(member.registration_id), passage.id, sha256_hex(passage.text))


def _visible_denied(acc: Access, denied: Sequence[Denied]) -> list[Denied]:
    """The denials this destination may be told: for a non-local one, only those of sources granted to it."""
    return [d for d in denied if acc.local or (d.registration_id and _granted(acc, d.registration_id))]


def search(acc: Access, request: SearchRequest, *, clock: Clock = _utc) -> tuple[SearchResponse,
                                                                                tuple[cloud_consent.Source, ...]]:
    """Lexical search of the project's published generation; see the module docstring for the order of its steps."""
    store = ProjectStore(acc.binding)
    if request.sources is not None:
        narrowed = frozenset(request.sources)
        acc = Access(binding=acc.binding, operation=acc.operation, destination=acc.destination,
                     sources=narrowed if acc.sources is None else acc.sources & narrowed)
    generation = store.published()
    if generation is None:
        report = BackendReport(backend="sqlite", route="exact", state="unavailable",
                               reason="not_published: nothing is indexed for this project yet")
        return SearchResponse(request=request, generation=None, backends=(report,)), ()
    found = _lexical(store, generation.number).find(request.query, min(request.top_k * OVERSAMPLE, 4 * MAX_TOP_K))
    state = {"ok": "answered", "empty": "empty", "unavailable": "unavailable", "failed": "failed"}[found.outcome]
    report = BackendReport(backend="sqlite", route="exact", state=state, searched=found.searched,
                           reason="" if found.answered else str(found.evidence.get("code") or found.outcome))
    checkpoint("search:found")
    at = clock()
    members, denied = store.use(acc, [h.passage.id for h in found.hits], at=at, generation=generation.number)
    by_passage = {pid: m for m in members for pid in m.passage_ids}
    registrations = {r.id: r for r in store.registrations()}
    withheld: dict[str, dict[str, str]] = {}
    granted: dict[str, bool] = {}
    kept = []
    for hit in found.hits:
        member = by_passage.get(hit.passage.id)
        if member is None:
            continue
        if member.registration_id not in granted:
            granted[member.registration_id] = _granted(acc, member.registration_id)
        if not granted[member.registration_id]:
            withheld[member.registration_id] = {"source": member.registration_id, "provider": acc.destination}
            continue
        kept.append((hit, member))
    documents: dict[str, Document | None] = {}
    intact = []
    for hit, member in kept:
        if member.uri not in documents:
            documents[member.uri], _ = _reread(acc.binding, registrations[member.registration_id], member)
            if documents[member.uri] is None:
                store.changed(member.registration_id, member.uri)
        document = documents[member.uri]
        if document is None or passage_problems(hit.passage, document):
            if document is not None:
                store.changed(member.registration_id, member.uri)
                documents[member.uri] = None
            denied.append(Denied(passage_id=hit.passage.id, registration_id=member.registration_id, why="changed"))
            continue
        intact.append((hit, member))
    checkpoint("search:checked")
    _, late = store.use(acc, [h.passage.id for h, _ in intact], at=clock(), generation=generation.number)
    refused = {d.passage_id for d in late}
    denied.extend(late)
    items, sources = [], []
    for hit, member in intact:
        if hit.passage.id in refused or len(items) >= request.top_k:
            continue
        contribution = Contribution(backend="sqlite", route="exact", query=request.query, rank=hit.rank,
                                    score_scale=hit.score_scale, reason="lexical match",
                                    passage_id=hit.passage.id, generation=generation.number)
        items.append(_item(hit.passage, member, len(items) + 1, (contribution,)))
        sources.append(_source(member, hit.passage))
    shown = {i.source for i in items}
    response = SearchResponse(
        request=request, generation=generation.number, backends=(report,),
        authorisation_revisions={rid: registrations[rid].authorisation_revision for rid in sorted(shown)},
        payload={"kind": "passages", "items": [i.model_dump() for i in items]},
        withheld=tuple(withheld[k] for k in sorted(withheld)),
        denied=tuple(dict.fromkeys(_visible_denied(acc, denied))))
    return response, tuple(sources)


def fetch(acc: Access, passage_id: str, *, raw: bool = False, clock: Clock = _utc) -> tuple[dict[str, Any],
                                                                                          tuple[cloud_consent.Source, ...]]:
    """One passage by id, re-read and compared before it is returned: its redacted view, and with ``raw`` its exact
    text. For a destination the source is not granted to, the reply names the source and the provider and nothing
    else."""
    store = ProjectStore(acc.binding)
    members, denied = store.use(acc, [passage_id], at=clock())
    if not members:
        shown = _visible_denied(acc, denied)
        if not shown:   # a destination that may not be told why learns only that the id is not usable here
            why = "not_published" if denied and denied[0].why == "not_published" else "unknown"
            shown = [Denied(passage_id=passage_id, registration_id="", why=why)]
        return {"passage": None, "withheld": [], "denied": [d.model_dump() for d in shown]}, ()
    member = members[0]
    if not _granted(acc, member.registration_id):
        return {"passage": None, "denied": [],
                "withheld": [{"source": member.registration_id, "provider": acc.destination}]}, ()
    registration = next(r for r in store.registrations() if r.id == member.registration_id)
    start, end = member.spans[member.passage_ids.index(passage_id)]
    document, _ = _reread(acc.binding, registration, member)
    passage = None
    if document is not None and end <= len(document.text):
        candidate = document.passage(start, end)
        passage = candidate if candidate.id == passage_id else None
    if passage is None:
        store.changed(member.registration_id, member.uri)
        return {"passage": None, "withheld": [], "denied": [
            {"passage_id": passage_id, "registration_id": member.registration_id, "why": "changed"}]}, ()
    checkpoint("fetch:checked")
    _, late = store.use(acc, [passage_id], at=clock())
    if late:
        return {"passage": None, "withheld": [], "denied": [d.model_dump() for d in _visible_denied(acc, late)]}, ()
    item = _item(passage, member, 1, ()).model_dump()
    if raw:
        item["text"] = passage.text
    return {"passage": item, "withheld": [], "denied": []}, (_source(member, passage),)


def revoke(acc: Access, registration_id: str) -> dict[str, Any]:
    """Revoke a source registration: its passages are denied from this moment, in every process, and each sharing
    grant for it is revoked too. Text already delivered cannot be recalled."""
    store = ProjectStore(acc.binding)
    tombstone = store.revoke(registration_id)
    scope = consent_scope(registration_id)
    revoked = []
    try:
        for row in cloud_consent.grants():
            if row.get("source") == scope:
                cloud_consent.revoke(scope, row["provider"])
                revoked.append(row["provider"])
    except Exception:  # noqa: BLE001 - unreadable sharing state already denies every disclosure
        pass
    return {"source": registration_id, "state": "revoked", "deny_epoch": tombstone.epoch,
            "sharing_revoked_for": sorted(revoked)}


def sources(acc: Access) -> list[dict[str, Any]]:
    """Each source registration of the project: its id, state, expiry, generation and backend readiness; and, for the
    local destination or a provider the source is granted to, its label and its file, passage and unread counts."""
    store = ProjectStore(acc.binding)
    generation = store.published()
    members = store.members(generation.number) if generation is not None else []
    out = []
    for registration in store.registrations():
        if acc.sources is not None and registration.id not in acc.sources:
            continue
        own = [m for m in members if m.registration_id == registration.id]
        row: dict[str, Any] = {
            "source": registration.id, "state": registration.state, "expires_at": registration.expires_at,
            "retention": registration.retention, "generation": generation.number if generation and own else None,
            "backends": {name: entry["state"] for name, entry in (generation.backends if generation else {}).items()},
            "shared_with_this_client": (not acc.local) and _granted(acc, registration.id)}
        if acc.local or row["shared_with_this_client"]:
            row.update(label=label_view(registration.label), files=len(own),
                       passages=sum(len(m.passage_ids) for m in own),
                       unread=sum(1 for m in own if m.unread or not m.passage_ids))
        out.append(row)
    return out


__all__ = ["CHUNKER", "INDEX", "INPUT", "PREVIEW_CHANGED", "Preview", "checkpoint", "commit", "excluded_counts",
           "fetch", "preview", "revoke", "search", "sources"]
