# SPDX-License-Identifier: Apache-2.0
"""The Context Engine's paths (design 12.9 steps 1 and 2): preview a folder, commit it after the person accepted,
search it, assemble a budgeted context, fetch one passage, refresh, revoke, delete, list the sources.

``_``-prefixed, so it never registers as a component. Everything here runs on this machine: the index is a local
SQLite file per project and generation, and nothing is sent to a model.

**Scope is decided before retrieval.** For every search the engine first works out, from the registry and the grant
ledger alone, which source registrations the access may read at this time: usable (not revoked, not deleted, not
past its expiry by the clock), inside the caller's narrowing, and, for a destination other than ``"local"``, granted
to that provider through the client's dialog (``g6.core.cloud_consent``). Only those are searched. So nothing a
destination is told can vary with what a source outside its scope holds: ``withheld`` lists the usable sources not
shared with it, the same for every query, and no count, state or rank comes from them. No result is written to a
file for a destination that was refused it.

A search then runs in a fixed order: (1) the published generation, or ``not_published``; (2) the scope; (3) each
route's search inside the scope, on that generation; (4) :meth:`ProjectStore.use` on every candidate, with the clock;
(5) each candidate's file is read again, converted and compared with the hit, and a mismatch writes a ``changed``
tombstone and drops it; (6) ``use`` is called again, and once more immediately before the return; (7) the routes are
fused by passage id and items carry redacted views only. "Checked" on an item means exactly step 5: the passage was
re-read and compared in the call that returned it.

Removal is a checked step. Every file that holds a source's bytes or names (an index file, an audit copy, a preview
listing) is removed through ``ProjectStore.remove_file``, which says whether the file is gone and keeps a durable
list of the ones that are not, retried by every later deletion, stage and publish. A deletion reports removal only
when that list is empty.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from g6.core import cloud_consent
from g6.core.storage import PathRefused, contained, state_path
from g6._context_engine._envelope import (MAX_TOP_K, BackendReport, Checked, Contribution, Denied, PassageItem,
                                    SearchRequest, SearchResponse)
from g6.knowledge._index import IndexRefused
from g6._context_engine._lexical import ScopedLexical
from g6._context_engine._registry import (Dependent, Member, ProjectStore, Refused, Registration, is_kept, may_use,
                                    source_uri)
from g6._context_engine._scope import Access, Binding, ScopeDenied, bound, consent_scope, inside
from g6.knowledge._types import Document, Passage, now, sha256_hex
from g6._context_engine._view import label_view, view_of
from g6._context_engine._walk import (MAX_FILE_BYTES, Excluded, FileEntry, Walked, is_placeholder, read_file, shown_name,
                                walk)

PREVIEW_CHANGED = "G6_E_KNOWLEDGE_PREVIEW_CHANGED"
INPUT = "G6_E_KNOWLEDGE_INPUT"
INDEX = "G6_E_KNOWLEDGE_INDEX"
PACK_UNKNOWN = "G6_E_KNOWLEDGE_PACK"
DELETION_PENDING = "G6_E_KNOWLEDGE_DELETION_PENDING"
CHUNKER = "words:200:20"
#: Passages added to the index in one write.
BATCH = 500
#: A route is asked for this many candidates per wanted result, since the re-read may drop some.
OVERSAMPLE = 4
#: How many times one search asks its routes again after it observed changed files among the candidates.
ROUNDS = 4
#: Routes a search uses when the request names none: the ones that are ready, in this order.
DEFAULT_ROUTES = ("exact", "structure", "meaning")
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
    #: Every backend the source is written to. The person accepts this list with the files: no later argument adds one.
    backends: tuple[str, ...] = ("sqlite",)
    destinations: tuple[str, ...] = ("local",)
    effects: tuple[str, ...] = ("the listed files are indexed on this machine", "nothing is sent to a model")
    retention: str = "none"
    expires_at: str = ""
    nonce: str
    note: str = ""
    sha256: str = ""
    listing_path: str = ""


#: The optional backends a preview may list beside the local index, each with the copy it means, in the words the
#: listing and the dialog use.
OPTIONAL_BACKENDS = {
    "elastic_managed": "after indexing, a job copies every passage of these files to the Elasticsearch server G6 is "
                       "set up to use (elastic_managed)",
}


def copies_sentence(backends: Sequence[str]) -> str:
    """Where the accepted files' text is written, in one sentence: the same words in the listing and the dialog."""
    extra = [OPTIONAL_BACKENDS[b] for b in backends if b in OPTIONAL_BACKENDS]
    if not extra:
        return "Indexed on this machine only; nothing is sent to a model."
    return f"Indexed on this machine; and {'; and '.join(extra)}. Nothing is sent to a model."


def held_sentence(held: Sequence[dict[str, Any]]) -> str:
    """What a deletion leaves in a server G6 wrote to, in the words the dialog and the receipt use; ``""`` when
    G6's records show no such copy."""
    if not held:
        return ""
    where = "; ".join(
        f"{'Elasticsearch' if one['backend'] == 'elastic_managed' else one['backend']} "
        f"(index {one['index'] or 'unknown'}, {one['records_sent']} records sent"
        f"{', possibly more' if one.get('records_possibly_sent') else ''})" for one in held)
    return (f"This deletion does not remove the copy of its passages, with their text, that G6 wrote to {where}: G6 "
            "stops reading it, and the copy stays in that server until it is removed there. ")


def _digest(preview: Preview) -> str:
    body = preview.model_dump(exclude={"sha256", "listing_path"})
    return sha256_hex(json.dumps(body, sort_keys=True, separators=(",", ":")))


def _label(binding: Binding, real: Path) -> str:
    if binding.start is not None and (real == binding.start or binding.start in real.parents):
        return real.relative_to(binding.start).as_posix() or "."
    root = next(r for r in binding.roots if real == r or r in real.parents)
    return (Path(root.name) / real.relative_to(root)).as_posix()


def _preview_of(binding: Binding, walked: Walked, *, nonce: str, expires_at: str, retention: str,
                backends: Sequence[str] = ("sqlite",)) -> Preview:
    real = Path(walked.root)
    listed = ("sqlite", *sorted(b for b in dict.fromkeys(backends) if b != "sqlite"))
    extra = tuple(OPTIONAL_BACKENDS[b] for b in listed[1:])
    made = Preview(project=binding.project, root=walked.root, label=_label(binding, real), files=walked.files,
                   excluded=walked.excluded, retention=retention, expires_at=expires_at, nonce=nonce, note=walked.note,
                   backends=listed, destinations=("local", *listed[1:]),
                   effects=("the listed files are indexed on this machine", *extra, "nothing is sent to a model"))
    return made.model_copy(update={"sha256": _digest(made)})


def excluded_counts(excluded: Sequence[Excluded]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in excluded:
        out[item.why] = out.get(item.why, 0) + 1
    return dict(sorted(out.items()))


def preview(binding: Binding, path: str, *, expires_days: int = 0, retention: str = "none",
            backends: Sequence[str] = (), clock: Clock = _utc) -> Preview:
    """Walk the folder ``path`` names and say exactly what would be indexed. Nothing is registered or indexed; the
    full list is written to a file under ``$G6_HOME/knowledge/previews/`` for the person to open. The listing names
    its project and folder, so a later complete deletion of that folder removes every listing of it."""
    bound(binding)
    if retention not in ("none", "audit"):
        raise Refused(INPUT, "retention is 'none' or 'audit'")
    if not isinstance(expires_days, int) or isinstance(expires_days, bool) or not 0 <= expires_days <= 3650:
        raise Refused(INPUT, "expires_days is a whole number of days from 0 (no expiry) to 3650")
    if isinstance(backends, str) or any(not isinstance(b, str) or b not in OPTIONAL_BACKENDS for b in backends):
        raise Refused(INPUT, f"backends names optional copies from: {', '.join(OPTIONAL_BACKENDS)}")
    walked = walk(binding, path)   # the walker applies the name rule first, then resolves the path inside a root
    expires_at = (clock().astimezone(UTC) + timedelta(days=expires_days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if expires_days else ""
    made = _preview_of(binding, walked, nonce=secrets.token_hex(16), expires_at=expires_at, retention=retention,
                       backends=("sqlite", *backends))
    listing = state_path("knowledge", "previews", f"{made.sha256}.md")
    listing.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Preview {made.sha256}", "", f"Project: {made.project}", f"Folder: {shown_name(made.root)}",
             f"Files to index: {len(made.files)} ({sum(f.size for f in made.files)} bytes)",
             f"Not indexed: {len(made.excluded)}",
             f"Ignored files decided by: {walked.ignored_by}" + (f" ({walked.note})" if walked.note else ""),
             f"Retention: {made.retention}", f"Expires: {made.expires_at or 'never'}",
             f"Written to: {', '.join(made.backends)}. {copies_sentence(made.backends)}", "", "## Files to index", ""]
    lines += [f"- {f.relative} ({f.size} bytes, sha256 {f.sha256})" for f in made.files]
    lines += ["", "## Not indexed, and why", ""] + [f"- {e.relative}: {e.why}" for e in made.excluded]
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return made.model_copy(update={"listing_path": str(listing)})


def discard_preview(shown: Preview, store: ProjectStore | None = None) -> bool:
    """Remove the listing of a preview whose dialog ended without acceptance or whose commit was refused; whether it
    is gone. With ``store`` it goes through the checked removal: a listing that cannot be removed now is kept on the
    project's durable list and removed by a later operation."""
    if store is not None and shown.listing_path:
        return store.remove_file(Path(shown.listing_path))
    try:
        Path(shown.listing_path).unlink()
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return True


def _listings_of(project: str, root: str) -> list[Path]:
    """Every preview listing that names this project and this folder."""
    folder = state_path("knowledge", "previews")
    found = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            head = path.read_text(encoding="utf-8", errors="replace").split("\n")[:6]   # a folder name may hold a line separator
        except OSError:
            found.append(path)   # a listing that cannot be read cannot be shown to name another folder
            continue
        if f"Project: {project}" in head and f"Folder: {shown_name(root)}" in head:
            found.append(path)
    return found


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


def _lexical(store: ProjectStore, generation: int) -> ScopedLexical:
    """A generation's lexical index, unscoped: for writing and for maintenance. A search goes through the
    dispatcher, which gives it a scope."""
    return ScopedLexical(store.index_path("sqlite", generation))


def _still_changed(store: ProjectStore, generation: int) -> set[tuple[str, str]]:
    return {(t.registration_id, t.uri) for t in store.tombstones() if t.reason == "changed" and t.generation >= generation}


def _carry(store: ProjectStore, previous: Any, staged: int, index: Any) -> list[Member]:
    """Start the staged generation from the previous one: a copy of its index file and its membership for the
    registrations that are kept. A file observed changed stays a member (the person accepted it) with no passages
    and no file hash, so the next refresh reads it again and nothing old comes back before then. Every row of the
    copy that the carried membership does not list is taken out, whatever an interrupted earlier step left there."""
    members: list[Member] = []
    if previous is None:
        return members
    before = store.index_path("sqlite", previous.number)
    if before.exists():
        shutil.copyfile(before, store.index_path("sqlite", staged))
    kept = {r.id for r in store.registrations() if is_kept(r)}
    denied = _still_changed(store, previous.number)
    for member in store.members(previous.number):
        if member.registration_id not in kept:
            continue
        if (member.registration_id, member.uri) in denied:
            members.append(member.model_copy(update={"generation": staged, "passage_ids": (), "spans": (),
                                                     "file_sha256": ""}))
        else:
            members.append(member.model_copy(update={"generation": staged}))
    listed = {pid for m in members for pid in m.passage_ids}
    extra = sorted(index.held_ids() - listed)
    if extra:
        index.remove(extra)
    return members


def _publish_staged(store: ProjectStore, staged: int, members: Sequence[Member], index: Any, *,
                    after: int | None = None) -> Any:
    store.record(staged, members)
    checkpoint("commit:recorded")
    written = len({pid for m in members for pid in m.passage_ids})
    held = index.status()["passages"] if store.index_path("sqlite", staged).exists() else 0
    return store.publish(staged, {"sqlite": {"written": written, "read_back": held}}, after=after)


def _purge(path: Path) -> None:
    """Rewrite an index file so that no freed page or full-text segment still holds a removed passage's bytes."""
    import sqlite3

    if not path.exists():
        return
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("INSERT INTO passages_fts (passages_fts) VALUES ('rebuild')")
        conn.execute("VACUUM")
    except sqlite3.Error:
        raise Refused(INDEX, "the index could not be rewritten after the deletion; run the deletion again") from None
    finally:
        conn.close()


def _build_generation(store: ProjectStore, registration: Registration, real: Path,
                      files: Sequence[FileEntry]) -> dict[str, Any]:
    """Stage the next generation: the previous one's index and membership for the registrations that are kept, plus
    the new registration's files; publish it only when the index read back every listed passage. When it is not
    published, the staged generation is abandoned and what it wrote is removed (the index file and, for an audited
    source, the audit copies), each through the checked removal."""
    previous = store.published()
    staged = store.stage({"sqlite": True})
    index = _lexical(store, staged.number)
    try:
        members = _carry(store, previous, staged.number, index)
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
            if registration.retention == "audit":
                store.retain(data, registration.id)   # an audit copy, kept apart from every index
            members.append(Member(
                registration_id=registration.id, generation=staged.number, uri=uri, relative=entry.relative,
                file_sha256=entry.sha256, size=entry.size, document_sha256=document.sha256 if document else "",
                passage_ids=tuple(p.id for p in passages), spans=tuple((p.start, p.end) for p in passages),
                media_type=entry.media_type, converter=converter, chunker=CHUNKER, unread=(*notes, *more)))
        published = _publish_staged(store, staged.number, members, index)
    except BaseException as exc:
        if isinstance(exc, Exception):   # a stop of the process leaves the staging generation for the next stage
            store.abandon(staged.number)
            store.forget_retained(registration.id)
        if isinstance(exc, IndexRefused):
            raise Refused(INDEX, f"the local index refused the write ({exc.code})") from None
        raise
    return {"generation": published.number, "passages": sum(len(m.passage_ids) for m in members
                                                           if m.registration_id == registration.id),
            "files": len(files), "unread": unread}


def commit(binding: Binding, accepted: Preview, attribution: str) -> dict[str, Any]:
    """Register and index exactly what ``accepted`` lists, after the person accepted it. The folder is walked again:
    a file list that differs from the accepted digest is ``G6_E_KNOWLEDGE_PREVIEW_CHANGED`` and changes nothing. A
    commit that is refused, or fails, leaves no registration in use, no staged index, no audit copy and no listing."""
    store = ProjectStore(binding)
    if not isinstance(accepted, Preview) or accepted.project != binding.project or accepted.sha256 != _digest(accepted):
        raise Refused(PREVIEW_CHANGED, "the preview is not one this project produced; nothing was indexed")
    if not isinstance(attribution, str) or not attribution.strip():
        raise Refused(INPUT, "acceptance carries the name the dialog returned")
    try:
        real = inside(binding, accepted.root)
        walked = walk(binding, real)
        again = _preview_of(binding, walked, nonce=accepted.nonce, expires_at=accepted.expires_at,
                            retention=accepted.retention, backends=accepted.backends)
        if again.sha256 != accepted.sha256:
            raise Refused(PREVIEW_CHANGED, "the folder's files differ from the preview that was accepted; nothing "
                                           "was indexed. Preview it again.")
        registration = store.register(accepted, attribution.strip())
    except (Refused, ScopeDenied):
        if not any(r.preview_sha256 == accepted.sha256 for r in store.registrations()):
            discard_preview(accepted, store)
        raise
    try:
        built = _build_generation(store, registration, real, accepted.files)
    except BaseException as exc:
        try:
            store.revoke(registration.id)   # a registration that was never indexed does not stay in use
            if isinstance(exc, Exception):
                discard_preview(accepted, store)
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


def shared_with(acc: Access, registration_id: str) -> bool:
    """Whether the access's destination may be told about this source: it is local, or the source is granted to it."""
    return _granted(acc, registration_id)


def scope_of(store: ProjectStore, acc: Access, at: datetime) -> tuple[list[str], list[str], list[Denied]]:
    """The access's scope at ``at``, from the registry and the grant ledger alone, before anything is retrieved: the
    registrations it may read; the usable ones withheld from its destination; and, for the registrations inside its
    narrowing that have expired, a denial the destination may be told (it is local, or the source is shared with
    it)."""
    readable, withheld, denied = [], [], []
    for registration in store.registrations():
        if acc.sources is not None and registration.id not in acc.sources:
            continue
        why = may_use(registration, at)
        if why == "expired" and _granted(acc, registration.id):
            denied.append(Denied(passage_id="", registration_id=registration.id, why="expired"))
        if why:
            continue
        (readable if _granted(acc, registration.id) else withheld).append(registration.id)
    return readable, withheld, denied


def _reread(binding: Binding, registration: Registration, member: Member) -> tuple[Document | None, bytes]:
    """The member's file read and converted again, when its bytes are the bytes that were indexed; else ``None``. A
    file that became a cloud placeholder is not opened."""
    try:
        root = inside(binding, registration.root)
        path = contained(root, member.relative, refuse_links=True)
        if is_placeholder(os.lstat(path)):
            return None, b""
        data = read_file(path, MAX_FILE_BYTES)
    except (ScopeDenied, PathRefused, OSError):
        return None, b""
    if sha256_hex(data) != member.file_sha256:
        return None, data
    document, _, _ = _document(data, member.uri, member.media_type)
    return document, data


def reread_passage(binding: Binding, registration: Registration, member: Member, passage_id: str,
                   documents: dict[str, Document | None]) -> Passage | None:
    """The one re-read and compare. The passage ``passage_id`` cut again from its file as the file is now, when the
    file's bytes are the indexed ones and the passage at the member's recorded offsets has that id; else ``None``.
    ``documents`` holds the files already read in this call. Every passage G6 returns (search, context, fetch, a
    saved pack) is built from what this returns and never from a backend's record, so "checked: intact" is said only
    of text the file holds."""
    if member.uri not in documents:
        documents[member.uri] = _reread(binding, registration, member)[0]
    document = documents[member.uri]
    if document is None or passage_id not in member.passage_ids:
        return None
    start, end = member.spans[member.passage_ids.index(passage_id)]
    if end > len(document.text):
        return None
    passage = document.passage(start, end)
    return passage if passage.id == passage_id else None


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


def _route_hits(store: ProjectStore, request: SearchRequest, generation: Any, readable: Sequence[str],
                left_out: list[str] | None = None, *, local: bool = True) -> list[tuple[BackendReport, list, int]]:
    """Each requested route's report and hits on the pinned generation, inside ``readable`` only. A route that is
    not ready is reported ``unavailable`` with why and is not asked; a route name G6 does not know is
    ``unsupported``."""
    from g6._context_engine import _dispatch

    ready = {r.name: (r, why) for r, why in _dispatch.routes_for(store, readable, local=local)}
    wanted = request.routes if request.routes is not None else tuple(
        name for name in DEFAULT_ROUTES if name in ready and not ready[name][1])
    limit = min(request.top_k * OVERSAMPLE, 4 * MAX_TOP_K)
    out = []
    for name in dict.fromkeys(wanted):
        if name not in ready or "search" not in ready[name][0].operations:
            out.append((BackendReport(backend="", route=label_view(str(name))[:40], state="unsupported",
                                      reason="no such search route"), [], generation.number))
            continue
        route, why = ready[name]
        if why:
            out.append((BackendReport(backend=route.backend, route=name, state="unavailable", reason=why), [],
                        generation.number))
            continue
        held = _dispatch.answered(generation, route.backend)
        scoped = list(readable) if held == ["*"] else [r for r in readable if r in held]
        found = _dispatch.open_route(store, route, generation.number, registrations=scoped).find(request.query, limit)
        state = {"ok": "answered", "empty": "empty", "unavailable": "unavailable", "failed": "failed"}[found.outcome]
        if found.evidence.get("partial"):
            state = "partial"
        out.append((BackendReport(backend=route.backend, route=name, state=state, searched=found.searched,
                                  rung=str(found.evidence.get("rung", "")),
                                  reason="" if found.answered else str(found.evidence.get("code") or found.outcome)),
                    list(found.hits), generation.number))
        if left_out is not None:
            left_out.extend(str(pid) for pid in found.evidence.get("left_out_changed") or ())
    return out


def _fused(acc: Access, request: SearchRequest, *, clock: Clock, budget_tokens: int | None = None):
    """The steps every passage search takes (the module docstring), over one or several routes; returns the store,
    the narrowed access, the generation, the fusion, each item's consent source and what was denied or withheld."""
    from g6._context_engine._fuse import fuse

    store = ProjectStore(acc.binding)
    if request.routes is not None and not request.routes:
        raise Refused(INPUT, "routes names at least one search route, or is left out for every ready route: a "
                             "search that asks no backend is not an answer")
    if request.sources is not None:
        narrowed = frozenset(request.sources)
        acc = Access(binding=acc.binding, operation=acc.operation, destination=acc.destination,
                     sources=narrowed if acc.sources is None else acc.sources & narrowed)
    generation = store.published()
    if generation is None:
        return store, acc, None, None, {}, {}
    at = clock()
    readable, withheld, denied = scope_of(store, acc, at)   # before anything is retrieved
    registrations = {r.id: r for r in store.registrations()}
    documents: dict[str, Document | None] = {}
    for attempt in range(ROUNDS):
        left_out: list[str] = []   # matches in files already observed changed: denied, and told as denied
        results = _route_hits(store, request, generation, readable, left_out, local=acc.local)
        if attempt == 0:
            checkpoint("search:found")
        candidates = {h.passage.id: h for _, hits, _ in results for h in hits}
        members, refused = store.use(acc, [*candidates, *left_out], at=at, generation=generation.number)
        denied.extend(refused)
        by_passage = {pid: m for m in members for pid in m.passage_ids}
        usable: dict[str, Member] = {}
        read_again: dict[str, Passage] = {}
        observed = False
        for pid in candidates:
            member = by_passage.get(pid)
            if member is None or member.registration_id not in readable:
                continue
            passage = reread_passage(acc.binding, registrations[member.registration_id], member, pid, documents)
            if passage is None:
                observed = True
                store.changed(member.registration_id, member.uri)
                documents[member.uri] = None
                denied.append(Denied(passage_id=pid, registration_id=member.registration_id, why="changed"))
                continue
            usable[pid] = member
            read_again[pid] = passage
        # a file observed changed is tombstoned, and the lexical query leaves tombstoned files out: asking again
        # fills the window with usable passages, so denied ones never keep an intact match out of the results
        if len(usable) >= request.top_k or not observed:
            break
    # a backend's record points at a passage and is never the evidence: every route's hit of an id is compared with
    # the file's text, and a record that differs is not used (the file itself is intact, so it is not denied)
    compared = []
    for report, hits, number in results:
        kept = [h for h in hits if h.passage.id not in read_again or h.passage.text == read_again[h.passage.id].text]
        if len(kept) != len(hits):
            report = report.model_copy(update={"state": "partial", "reason": (
                f"{len(hits) - len(kept)} record(s) of this backend did not match their files and were not used")})
        compared.append((report, kept, number))
    results = compared
    checkpoint("search:checked")
    _, late = store.use(acc, list(usable), at=clock(), generation=generation.number)
    denied.extend(late)
    for gone in late:
        usable.pop(gone.passage_id, None)
    sources: dict[str, cloud_consent.Source] = {}

    def render(hit: Any, rank: int, contributions: tuple[Contribution, ...]) -> PassageItem | None:
        member, passage = usable.get(hit.passage.id), read_again.get(hit.passage.id)
        if member is None or passage is None:
            return None
        sources[passage.id] = _source(member, passage)
        return _item(passage, member, rank, contributions)   # from the file as re-read, never from the hit

    fused = fuse(results, top_k=request.top_k, budget_tokens=budget_tokens, render=render, query=request.query)
    shown = {i.source for i in fused.items}
    meta = {"withheld": tuple({"source": rid, "provider": acc.destination} for rid in sorted(withheld)),
            "denied": list(dict.fromkeys(_visible_denied(acc, denied))),
            "revisions": {rid: registrations[rid].authorisation_revision for rid in sorted(shown)},
            "members": usable}
    return store, acc, generation, fused, sources, meta



def search(acc: Access, request: SearchRequest, *, clock: Clock = _utc) -> tuple[SearchResponse,
                                                                                tuple[cloud_consent.Source, ...]]:
    """Search the project's published generation over the requested routes (by default the ready ones), inside the
    access's scope, and fuse the results by passage id; see the module docstring for the order of its steps."""
    store, acc, generation, fused, sources, meta = _fused(acc, request, clock=clock)
    if generation is None:
        report = BackendReport(backend="sqlite", route="exact", state="unavailable",
                               reason="not_published: nothing is indexed for this project yet")
        return SearchResponse(request=request, generation=None, backends=(report,)), ()
    # the last check, immediately before the reply is built: a revoke or an expiry that arrived meanwhile is honoured
    _, late = store.use(acc, [i.passage_id for i in fused.items], at=clock(), generation=generation.number)
    gone = {d.passage_id for d in late}
    items = [i for i in fused.items if i.passage_id not in gone]
    shown = {i.passage_id for i in items}
    payload: dict[str, Any] = {"kind": "passages", "items": [i.model_dump() for i in items]}
    if fused.groups or fused.cautions:
        payload.update(groups=[list(g) for g in fused.groups if all(p in shown for p in g)],
                       cautions=[c for c in fused.cautions if all(p in shown for p in c["passages"])])
    if fused.refused:
        payload["refused"] = fused.refused
    denied = tuple(dict.fromkeys([*meta["denied"], *_visible_denied(acc, late)]))
    response = SearchResponse(request=request, generation=generation.number, backends=fused.backends,
                              authorisation_revisions={r: v for r, v in meta["revisions"].items()
                                                       if r in {i.source for i in items}},
                              payload=payload, withheld=meta["withheld"], denied=denied)
    from g6._context_engine import _snapshots

    kept_as = _snapshots.write(store, response, meta["members"])   # only for sources that retain audit copies
    if kept_as:
        response = response.model_copy(update={"snapshot": kept_as})
    return response, tuple(sources[i.passage_id] for i in items)


def context(acc: Access, task: str, *, budget_tokens: int = 4000, routes: Sequence[str] | None = None,
            clock: Clock = _utc) -> tuple[dict[str, Any], tuple[cloud_consent.Source, ...]]:
    """A budgeted context for ``task``: the fused passages that fit ``budget_tokens``, every rendered field,
    provenance and JSON overhead counted. The pack is saved (views only) and registered as a stored consumer, so
    :func:`pack` denies it once a source in it may no longer be used."""
    request = SearchRequest(operation="context", query=task, top_k=MAX_TOP_K,
                            routes=tuple(routes) if routes is not None else None)
    store, acc, generation, fused, sources, meta = _fused(acc, request, clock=clock, budget_tokens=budget_tokens)
    if generation is None:
        return {"pack": "", "generation": None, "items": [], "tokens": 0, "omitted": 0, "refused": "",
                "backends": [{"backend": "sqlite", "route": "exact", "state": "unavailable",
                              "reason": "not_published: nothing is indexed for this project yet"}],
                "withheld": [], "denied": []}, ()
    _, late = store.use(acc, [i.passage_id for i in fused.items], at=clock(), generation=generation.number)
    gone = {d.passage_id for d in late}
    items = [i for i in fused.items if i.passage_id not in gone]
    shown = {i.passage_id for i in items}
    from g6._context_engine._fuse import tokens_of

    body = {"task_sha256": sha256_hex(task), "generation": generation.number, "budget_tokens": budget_tokens,
            "items": [i.model_dump(mode="json") for i in items], "tokens": sum(tokens_of(i) for i in items),
            "omitted": fused.omitted + len(fused.items) - len(items), "refused": fused.refused,
            "backends": [b.model_dump() for b in fused.backends],
            "groups": [list(g) for g in fused.groups if all(p in shown for p in g)],
            "cautions": [c for c in fused.cautions if all(p in shown for p in c["passages"])]}
    ident = "pack-" + sha256_hex(json.dumps(body, sort_keys=True, default=str))[:32] if items else ""
    if ident:
        folder = store.folder / "packs"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{ident}.json").write_text(json.dumps({**body, "destination": acc.destination}, sort_keys=True),
                                              encoding="utf-8")
        for registration_id in sorted({i.source for i in items}):
            store.depend(Dependent(kind="pack", ident=ident, registration_id=registration_id,
                                   passage_ids=tuple(i.passage_id for i in items if i.source == registration_id),
                                   generation=generation.number))
    denied = list(dict.fromkeys([*meta["denied"], *_visible_denied(acc, late)]))
    return ({"pack": ident, **body, "withheld": list(meta["withheld"]), "denied": [d.model_dump() for d in denied]},
            tuple(sources[i.passage_id] for i in items))


def pack(acc: Access, pack_id: str, *, clock: Clock = _utc) -> tuple[dict[str, Any], tuple[cloud_consent.Source, ...]]:
    """A saved context, checked again at this use: denied when a source in it may no longer be used at this time
    (revoked, deleted, expired), was changed, or is not granted to this destination; a saved pack never bypasses the
    check. A subagent's context is built from this call, never from the file."""
    store = ProjectStore(acc.binding)
    path = store.folder / "packs" / f"{pack_id}.json"
    if not isinstance(pack_id, str) or not pack_id.startswith("pack-") or not pack_id[5:].isalnum() or not path.is_file():
        raise Refused(PACK_UNKNOWN, "no such saved context in this project")
    saved = json.loads(path.read_text(encoding="utf-8"))
    at = clock()
    invalid = store.invalidated("pack", pack_id, at=at)
    ids = [i["passage_id"] for i in saved["items"]]
    members, denied = store.use(acc, ids, at=at)
    by_passage = {pid: m for m in members for pid in m.passage_ids}
    ungranted = sorted({m.registration_id for m in members if not _granted(acc, m.registration_id)})
    reasons = {d.why for d in (*invalid, *denied)} | ({"not_shared"} if ungranted else set())
    if not reasons and not set(ids) <= set(by_passage):   # a pack holds passages of a file, not necessarily all of them
        reasons.add("unknown")
    read_again: list[tuple[dict[str, Any], Member, Passage]] = []
    if not reasons:   # every item is read again from its file at this use: a saved view is never what is returned
        registrations = {r.id: r for r in store.registrations()}
        documents: dict[str, Document | None] = {}
        for kept in saved["items"]:
            member = by_passage[kept["passage_id"]]
            passage = reread_passage(acc.binding, registrations[member.registration_id], member, kept["passage_id"],
                                     documents)
            if passage is None:
                store.changed(member.registration_id, member.uri)
                reasons.add("changed")
                break
            read_again.append((kept, member, passage))
    if not reasons:   # a revoke or an expiry that arrived while the files were read
        reasons |= {d.why for d in store.use(acc, ids, at=clock())[1]}
    if reasons:
        return {"pack": pack_id, "state": "denied", "why": sorted(reasons), "items": [],
                "withheld": [{"source": r, "provider": acc.destination} for r in ungranted] if not acc.local else []}, ()
    items = [_item(passage, member, kept["rank"], tuple(Contribution(**c) for c in kept["contributions"]))
             .model_dump(mode="json") for kept, member, passage in read_again]
    return {"pack": pack_id, "state": "usable", "generation": saved["generation"], "items": items,
            "tokens": saved["tokens"], "cautions": saved["cautions"], "groups": saved["groups"]}, tuple(
                _source(member, passage) for _, member, passage in read_again)


def fetch(acc: Access, passage_id: str, *, raw: bool = False, clock: Clock = _utc) -> tuple[dict[str, Any],
                                                                                          tuple[cloud_consent.Source, ...]]:
    """One passage by id, re-read and compared before it is returned: its redacted view, and with ``raw`` its exact
    text. Scope first: the id is looked up only among the sources the access may read at this time, so an id of a
    source that is not shared with the destination is answered exactly as an id that does not exist (a passage id
    can be worked out from a guessed path and guessed bytes, and the reply must not confirm the guess)."""
    store = ProjectStore(acc.binding)
    at = clock()
    readable = scope_of(store, acc, at)[0]   # before the id is looked up
    members, denied = store.use(acc, [passage_id], at=at)
    members = [m for m in members if m.registration_id in readable]
    if not members:
        shown = [d for d in _visible_denied(acc, denied) if d.registration_id in readable or acc.local]
        if not shown:   # a destination that may not be told why learns only that the id is not usable here
            why = "not_published" if store.published() is None else "unknown"
            shown = [Denied(passage_id=passage_id, registration_id="", why=why)]
        return {"passage": None, "withheld": [], "denied": [d.model_dump() for d in shown]}, ()
    member = members[0]
    registration = next(r for r in store.registrations() if r.id == member.registration_id)
    passage = reread_passage(acc.binding, registration, member, passage_id, {})
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


def _revoke_sharing(registration_id: str) -> list[str]:
    scope = consent_scope(registration_id)
    revoked = []
    try:
        for row in cloud_consent.grants():
            if row.get("source") == scope:
                cloud_consent.revoke(scope, row["provider"])
                revoked.append(row["provider"])
    except Exception:  # noqa: BLE001 - unreadable sharing state already denies every disclosure
        pass
    return sorted(revoked)


def revoke(acc: Access, registration_id: str) -> dict[str, Any]:
    """Revoke a source registration: its passages are denied from this moment, in every process, and each sharing
    grant for it is revoked too. Text already delivered cannot be recalled."""
    store = ProjectStore(acc.binding)
    tombstone = store.revoke(registration_id)
    return {"source": registration_id, "state": "revoked", "deny_epoch": tombstone.epoch,
            "sharing_revoked_for": _revoke_sharing(registration_id)}


def refresh(acc: Access, registration_id: str | None = None, *, clock: Clock = _utc) -> dict[str, Any]:
    """Re-read the accepted files and publish what changed as a new generation (``g6._context_engine._refresh``)."""
    from g6._context_engine import _refresh

    return _refresh.refresh(acc, registration_id, clock=clock)


def delete(acc: Access, registration_id: str, *, complete: bool = False, attribution: str = "") -> dict[str, Any]:
    """Delete a source after the person accepted the deletion dialog. It is denied at once (a tombstone first), every
    sharing grant for it is revoked, and the index is rebuilt without it. With ``complete``, after that publication
    the audit copies, the snapshots, the membership records and every preview listing of its folder go too, every
    older index file is removed and the new one is rewritten so that no freed page holds its text; only the
    registration's id and its tombstones stay, so it cannot return.

    Each step can be run again: a deletion interrupted anywhere is completed by running it again. A file that could
    not be removed (another program has it open) is kept on a durable list and the deletion is refused with
    ``G6_E_KNOWLEDGE_DELETION_PENDING`` rather than reported done: the source stays denied, and the next run removes
    what is left."""
    store = ProjectStore(acc.binding)
    if not isinstance(attribution, str) or not attribution.strip():
        raise Refused(INPUT, "a deletion carries the name the deletion dialog returned")
    receipt = store.delete(registration_id, complete=bool(complete))
    _revoke_sharing(registration_id)
    root, preview_sha256 = receipt.pop("root"), receipt.pop("preview_sha256")
    previous = store.published()
    published = previous
    if previous is not None:
        staged = store.stage({"sqlite": True})
        index = _lexical(store, staged.number)
        try:
            published = _publish_staged(store, staged.number, _carry(store, previous, staged.number, index), index,
                                        after=previous.number)
        except IndexRefused as exc:
            store.abandon(staged.number)
            raise Refused(INDEX, f"the index could not be rebuilt without the source ({exc.code}); it stays denied. "
                                 "Run the deletion again.") from None
        except Refused:
            store.abandon(staged.number)   # another publication happened meanwhile: the source stays denied
            raise
    if complete:
        listings = [state_path("knowledge", "previews", f"{preview_sha256}.md"), *(_listings_of(acc.binding.project, root) if root else [])]
        store.pend(listings)
        store.erase(registration_id)
        for generation in store.generations():   # only generations older than this deletion's own publication
            if published is None or generation.number < published.number:
                store._drop_index_files(generation.number)
        if published is not None:
            _purge(store.index_path("sqlite", published.number))
        for path in listings:
            store.remove_file(path)
        pending = store.retry_removals()
        if pending:
            raise Refused(DELETION_PENDING, f"the source is denied and out of search, but {len(pending)} file(s) or "
                                            "step(s) that hold its bytes could not be removed yet (another program "
                                            "has a file open); run the deletion again")
        receipt["audit_copies"] = "removed"
    from g6._context_engine import _jobs

    still_held = _jobs.held(store, registration_id)   # read from the job records, which a deletion keeps
    if still_held:
        receipt["held_elsewhere"] = still_held
    return {**receipt, "note": "The source is removed from G6's search on this machine. " + held_sentence(still_held)
                               + "Text already delivered to a client or a model cannot be recalled."}


def sources(acc: Access, *, clock: Clock = _utc) -> list[dict[str, Any]]:
    """Each source registration of the project: its id, state, expiry and whether it may be used at this time (the
    person's own settings); and, for the local destination or a provider the source is granted to, everything that
    is computed from what the source holds: its generation, its backends' readiness and, while it may be used, its
    label and its file, passage and unread counts. A row of a source that is not shared with the destination holds
    nothing that varies with the source's files."""
    store = ProjectStore(acc.binding)
    at = clock()
    generation = store.published()
    members = store.members(generation.number) if generation is not None else []
    out = []
    for registration in store.registrations():
        if acc.sources is not None and registration.id not in acc.sources:
            continue
        own = [m for m in members if m.registration_id == registration.id]
        why = may_use(registration, at)
        row: dict[str, Any] = {
            "source": registration.id, "state": registration.state, "expires_at": registration.expires_at,
            "usable": not why, "why_not": why, "retention": registration.retention,
            "shared_with_this_client": (not acc.local) and _granted(acc, registration.id)}
        if acc.local or row["shared_with_this_client"]:   # computed from the source: only inside the caller's scope
            row.update(generation=generation.number if generation and own else None,
                       backends={name: entry["state"] for name, entry in (generation.backends if generation else {}).items()})
        if acc.local or (row["shared_with_this_client"] and not why):
            row.update(label=label_view(registration.label), files=len(own),
                       passages=sum(len(m.passage_ids) for m in own),
                       unread=sum(1 for m in own if m.unread or not m.passage_ids))
        out.append(row)
    return out


__all__ = ["CHUNKER", "DEFAULT_ROUTES", "DELETION_PENDING", "INDEX", "INPUT", "PACK_UNKNOWN", "PREVIEW_CHANGED",
           "Preview", "checkpoint", "commit", "context", "delete", "discard_preview", "excluded_counts", "fetch",
           "pack", "preview", "refresh", "revoke", "scope_of", "search", "shared_with", "sources"]
