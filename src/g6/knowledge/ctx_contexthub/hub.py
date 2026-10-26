# SPDX-License-Identifier: Apache-2.0
"""A documentation registry as a source: its ``registry.json`` and its documents kept, each document with when it was
read, its version and a time to live, and notes kept apart from them.

The registry format is context-hub's: a ``registry.json`` listing ``docs`` (an id, a name, a description, tags, and
for each language its versions, each with a ``path``) and ``skills`` (an id, a name, a description, tags and a
``path``); a document's text is ``<path>/DOC.md`` (``SKILL.md`` for a skill) under the registry's base. The person
names the registry: an ``http`` or ``https`` address, or a folder on this machine. There is no default host. A network
share or a device path (``\\\\host\\share``, ``//host/share``, ``\\\\?\\``, ``\\\\.\\``, ``\\??\\``) is
refused before the file system is touched: opening one connects to another host outside ``g6.core.http``.

:func:`sync` reads ``registry.json`` and each entry's document (each language's recommended version). An address is
fetched only through ``knowledge_fetcher``'s guarded fetch (``g6.core.http``: every redirect hop checked before it is
requested, no private or loopback address, a cap on the body, ``robots.txt``), invoked through the registry so this
package never imports it; a folder is read only inside itself. A path in the registry that would leave its base (an
absolute path, ``..``, an address) is refused. Each document is kept with its own ``retrieved_at`` (when it was
read), its version as the registry states it and a time to live (at most :data:`MAX_TTL_HOURS`); a document that
cannot be fetched again keeps its earlier copy and its earlier date, so it goes stale on its own clock however
recently the registry was read. The registry's own version and last-updated date are kept as it states them (``""``
when it states none). The characters kept are totalled before each document is kept: the document that would cross
:data:`MAX_KEPT` stops the reading, and nothing kept changes.

:func:`search` ranks the kept entries with ``g6.grounding``'s BM25 over their name, tags, description and kept
content. :func:`get` returns a kept document and, apart from it, the notes kept on its entry; a document past its
time to live is refused as stale. A note is a plainly labelled record (its text, when, and that a caller wrote it,
unchecked); it is never written into a document. All of it is untrusted text: the registry vouches only for itself.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from g6.core.storage import state_path
from g6.knowledge import _store
from g6.knowledge._types import Document, Source, instant, now, sha256_hex

NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*(?:/[A-Za-z0-9_][A-Za-z0-9._-]*)*")
#: A network share or a device path (two leading slashes of either kind, or the NT ``\??\`` prefix).
_NOT_LOCAL = re.compile(r"[\\/][\\/]|[\\/]\?\?[\\/]")
#: The most bytes of one file (registry.json or a document) read from a folder; a fetch has the fetcher's own cap.
MAX_FILE = 10 * 1024 * 1024
#: The most documents one sync reads, and the most characters kept for one registry.
MAX_DOCS = 200
MAX_KEPT = 50_000_000
#: The most notes kept on one entry, and the most characters in one.
MAX_NOTES = 200
MAX_NOTE = 4000
DEFAULT_TTL_HOURS = 24
#: The longest time to live a document may be given: a week (a library can release more than once a month, and the
#: time to live is the person's guess, not the publisher's).
MAX_TTL_HOURS = 24 * 7
#: Where a registry.json states its own version and its last update (top level, or in its ``bundle``).
_VERSION_KEYS = ("version",)
_UPDATED_KEYS = ("lastUpdated", "last_updated", "updated", "updatedAt", "updated_at", "generated", "generatedAt",
                 "generated_at")

NO_REGISTRY = "G6_E_KNOWLEDGE_NO_REGISTRY"
REGISTRY_UNREADABLE = "G6_E_KNOWLEDGE_REGISTRY_UNREADABLE"
NOT_SYNCED = "G6_E_KNOWLEDGE_REGISTRY_NOT_SYNCED"
NO_ENTRY = "G6_E_KNOWLEDGE_NO_ENTRY"
NOT_KEPT = "G6_E_KNOWLEDGE_DOC_NOT_KEPT"
STALE = "G6_E_KNOWLEDGE_DOC_STALE"
NEEDS_LANGUAGE = "G6_E_KNOWLEDGE_DOC_NEEDS_LANGUAGE"
HUB_INPUT = "G6_E_KNOWLEDGE_HUB_INPUT"
HUB_STORE = "G6_E_KNOWLEDGE_HUB_STORE"
TOO_LARGE = "G6_E_KNOWLEDGE_HUB_TOO_LARGE"


class HubError(Exception):
    """A registry that cannot be read, kept or used, with its stable code."""

    def __init__(self, code: str, reason: str, evidence: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {reason}")
        self.code, self.reason, self.evidence = code, reason, dict(evidence or {})


def clock() -> datetime:
    """This moment, in UTC (replaced in tests to move time on)."""
    return datetime.now(UTC)


# -- reading a registry's files ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Read:
    """One file read for a registry: its text as a document, or the outcome and code of the failure."""

    document: Document | None
    outcome: str = "ok"
    reason: str = ""
    code: str = ""


Reader = Callable[[str], Read]


def fetch_through_fetcher(url: str) -> Read:
    """``url`` fetched by ``knowledge_fetcher``'s ``fetch`` (source ``http``), through the component registry."""
    from g6.core.registry import get_registry

    result = get_registry().invoke("knowledge_fetcher", "fetch", {"source": "http", "url": url})
    if result.__class__.__name__ != "Success":
        return Read(None, "refused", str(result.failure())[:500], "G6_E_KNOWLEDGE_FETCH_REFUSED")
    reply = result.unwrap()
    outcome, code = str(reply.get("outcome") or "failed"), str(reply.get("code") or "")
    if reply.get("completion_state") == "blocked-escalated":
        return Read(None, outcome, str(reply.get("reason") or outcome)[:500], code)
    documents = reply.get("documents") or []
    if documents:
        return Read(Document.model_validate(documents[0]), "ok", code=code)
    if reply.get("kept"):  # a body that is not text or HTML: the fetcher kept it; a document here is UTF-8 text
        data = _store.kept(str(reply["kept"])) or b""
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return Read(None, "unreadable", f"{url} is {reply.get('media_type')} and not UTF-8 text", code)
        fetched = (_store.origins(str(reply["kept"])) or [{}])[-1]
        source = Source(uri=str(reply.get("url") or url), sha256=sha256_hex(data), provenance="fetched",
                        retrieved_at=fetched.get("retrieved_at") or now())
        return Read(Document(text=text, source=source, media_type=str(reply.get("media_type") or "text/plain")),
                    "ok", code=code)
    return Read(None, "empty", str(reply.get("reason") or "the server sent nothing"), code)


def read_folder(root: Path) -> Reader:
    """A reader of files inside ``root`` only: a path that resolves outside it, a link included, is refused."""
    base = root.resolve()

    def read(relative: str) -> Read:
        target = (base / relative).resolve()
        if target != base and base not in target.parents:
            return Read(None, "refused", f"{relative} leaves the folder {base}", HUB_INPUT)
        try:
            size = target.stat().st_size
            if size > MAX_FILE:
                return Read(None, "too_large", f"{target} is {size} bytes, over {MAX_FILE}", TOO_LARGE)
            data = target.read_bytes()
        except FileNotFoundError:
            return Read(None, "not_found", f"{target} does not exist", NOT_KEPT)
        except OSError as exc:
            return Read(None, "unreadable", f"{target} cannot be read: {type(exc).__name__}", NOT_KEPT)
        text = data.decode("utf-8", errors="replace")
        source = Source(uri=target.as_uri(), sha256=sha256_hex(data), retrieved_at=now(), provenance="caller_supplied")
        return Read(Document(text=text, source=source, media_type="text/markdown" if relative.endswith(".md")
                             else "application/json"))
    return read


def locate(registry: str) -> tuple[str, Reader]:
    """Where a registry is (its base address, or its folder) and the reader of its files; :class:`HubError` for a
    location that is neither an ``http(s)`` address nor a folder on this machine."""
    registry = registry.strip()
    if not registry:
        raise HubError(NO_REGISTRY, "no registry was named: G6 has no default host; name the address or the folder "
                                    "of the registry you trust")
    if _NOT_LOCAL.match(registry):
        raise HubError(HUB_INPUT, f"{registry[:80]} is a network share or a device path, not a folder on this machine: "
                                  "opening it would connect to another host outside G6's checked web access, so it "
                                  "is never opened; copy the registry to a local folder, or name its http address")
    parts = urlsplit(registry)
    if parts.scheme in ("http", "https"):
        base = registry[: -len("/registry.json")] if registry.endswith("/registry.json") else registry.rstrip("/")
        return base, lambda relative: fetch_through_fetcher(f"{base}/{relative}")
    if parts.scheme and len(parts.scheme) > 1:
        raise HubError(HUB_INPUT, f"{parts.scheme}: is not a registry location: give an http or https address, or a "
                                  "folder")
    folder = Path(registry)
    if not folder.is_dir():
        raise HubError(NO_REGISTRY, f"{registry} is neither an http or https address nor a folder on this machine")
    return str(folder.resolve()), read_folder(folder)


# -- the registry's entries --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Target:
    """One document of an entry: its language (``""`` for none), the version the registry names, the versions it
    lists, and the path of its file under the registry's base."""

    language: str
    version: str
    path: str
    versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Entry:
    id: str
    type: str
    name: str
    description: str
    tags: tuple[str, ...]
    targets: tuple[Target, ...]

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "name": self.name, "description": self.description,
                "tags": list(self.tags), "languages": sorted({t.language for t in self.targets if t.language})}


def _safe_path(path: Any) -> str:
    text = str(path or "").strip().strip("/")
    if not _PATH.fullmatch(text) or ".." in text.split("/") or len(text) > 512:
        raise ValueError(f"{str(path)[:80]!r} is not a path inside the registry")
    return text


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def parse_registry(text: str) -> tuple[list[Entry], list[str]]:
    """The entries of a ``registry.json`` and, for each one left out, why; :class:`HubError` when it is not a
    registry at all."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise HubError(REGISTRY_UNREADABLE, f"registry.json is not JSON: {exc}") from None
    if not isinstance(data, dict) or not any(isinstance(data.get(k), list) for k in ("docs", "skills")):
        raise HubError(REGISTRY_UNREADABLE, "registry.json lists neither docs nor skills")
    entries: list[Entry] = []
    left_out: list[str] = []
    seen: set[str] = set()
    for kind, items in (("doc", data.get("docs") or []), ("skill", data.get("skills") or [])):
        for raw in items if isinstance(items, list) else []:
            ident = _text(raw.get("id") or raw.get("name"), 200) if isinstance(raw, dict) else ""
            try:
                if not ident:
                    raise ValueError("it has no id")
                if ident in seen:
                    raise ValueError("its id is listed twice")
                targets: list[Target] = []
                if kind == "skill" or (raw.get("path") and not any(isinstance(lang, dict)
                                                                   for lang in raw.get("languages") or [])):
                    targets.append(Target("", _text(raw.get("version"), 80), _safe_path(raw.get("path"))))
                for lang in raw.get("languages") or []:
                    if not isinstance(lang, dict):
                        continue
                    versions = [v for v in lang.get("versions") or [] if isinstance(v, dict)]
                    chosen = next((v for v in versions if v.get("version") == lang.get("recommendedVersion")),
                                  versions[0] if versions else None)
                    if chosen is None:
                        continue
                    targets.append(Target(_text(lang.get("language"), 40).lower(), _text(chosen.get("version"), 80),
                                          _safe_path(chosen.get("path")),
                                          tuple(_text(v.get("version"), 80) for v in versions)))
                if not targets:
                    raise ValueError("it names no document")
            except (ValueError, AttributeError) as exc:
                left_out.append(f"{kind} {ident or '(no id)'}: {exc}")
                continue
            seen.add(ident)
            tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
            entries.append(Entry(ident, kind, _text(raw.get("name"), 300), _text(raw.get("description"), 2000),
                                 tuple(_text(t, 60).lower() for t in tags[:50] if _text(t, 60)), tuple(targets)))
    return entries, left_out


def registry_stated(text: str) -> dict[str, str]:
    """The version and last-updated date a ``registry.json`` states for itself (``""`` for what it does not)."""
    try:
        data = json.loads(text)
    except ValueError:
        return {"registry_version": "", "registry_updated": ""}
    bundle = data.get("bundle") if isinstance(data, dict) and isinstance(data.get("bundle"), dict) else {}

    def first(keys: tuple[str, ...]) -> str:
        for where in (data if isinstance(data, dict) else {}, bundle):
            for key in keys:
                if isinstance(where.get(key), (str, int, float)) and _text(where[key], 80):
                    return _text(where[key], 80)
        return ""

    return {"registry_version": first(_VERSION_KEYS), "registry_updated": first(_UPDATED_KEYS)}


# -- what is kept ------------------------------------------------------------------------------------------------------

def check_name(name: str) -> str:
    if not NAME.fullmatch(name or ""):
        raise HubError(HUB_INPUT, f"{name!r} is not a registry name (lower-case letters, digits, '-' and '_')")
    return name


def _kept_path(name: str, suffix: str = "") -> Path:
    return state_path("knowledge", "registries", f"{check_name(name)}{suffix}.json")


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".partial-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=1)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def load(name: str) -> dict[str, Any]:
    """The kept registry ``name``; :class:`HubError` when it was never synced or cannot be read."""
    path = _kept_path(name)
    if not path.is_file():
        raise HubError(NOT_SYNCED, f"no registry named {name!r} is kept: sync it first (a sensitive op)")
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(kept, dict) or not isinstance(kept.get("entries"), list):
            raise ValueError("not a kept registry")
        for doc in (kept.get("docs") or {}).values():
            Document.model_validate(doc["document"])  # a document whose text no longer has its hash does not load
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HubError(HUB_STORE, f"the kept registry {name!r} cannot be read ({type(exc).__name__}); sync it again")\
            from None
    return kept


def _key(entry: str, language: str) -> str:
    return f"{entry}\x1f{language}"


@dataclass
class Synced:
    """What a sync did: entries listed, documents read now, documents that failed (and kept their earlier copy, if
    any), entries left out and why."""

    name: str
    location: str
    entries: int
    read: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)
    registry_source: dict[str, Any] = field(default_factory=dict)
    registry_version: str = ""
    registry_updated: str = ""


def sync(name: str, registry: str, *, ttl_hours: int = DEFAULT_TTL_HOURS, content: bool = True,
         max_docs: int = MAX_DOCS, reader: Reader | None = None) -> Synced:
    """Read the registry at ``registry`` and its documents, and keep them as ``name``; see the module docstring.
    :class:`HubError` when ``registry.json`` cannot be read (nothing kept changes then)."""
    check_name(name)
    if not 1 <= ttl_hours <= MAX_TTL_HOURS:
        raise HubError(HUB_INPUT, f"ttl_hours is 1 to {MAX_TTL_HOURS} (a week): sync again to keep a copy longer")
    location, read = locate(registry)
    read = reader or read
    got = read("registry.json")
    if got.document is None:
        raise HubError(REGISTRY_UNREADABLE, f"registry.json at {location} could not be read ({got.outcome}): "
                                            f"{got.reason}", {"outcome": got.outcome, "code": got.code})
    entries, left_out = parse_registry(got.document.text)
    stated = registry_stated(got.document.text)
    try:
        earlier = load(name)
    except HubError:
        earlier = {}
    docs: dict[str, Any] = dict(earlier.get("docs") or {}) if earlier.get("location") == location else {}
    out = Synced(name, location, len(entries), left_out=left_out, registry_source=got.document.source.model_dump(),
                 **stated)
    ttl = timedelta(hours=ttl_hours)
    wanted = [(e, t) for e in entries for t in e.targets][:max_docs] if content else []
    held = {_key(e.id, t.language) for e in entries for t in e.targets}
    docs = {k: v for k, v in docs.items() if k in held}  # an entry the registry no longer lists is not kept
    total = sum(len(d["document"]["text"]) for d in docs.values())  # kept, before each new document is
    for entry, target in wanted:
        file = f"{target.path}/{'SKILL.md' if entry.type == 'skill' else 'DOC.md'}"
        found = read(file)
        key = _key(entry.id, target.language)
        if found.document is None or not found.document.text.strip():
            out.failed.append({"entry": entry.id, "language": target.language, "file": file,
                               "outcome": found.outcome if found.document is None else "empty",
                               "reason": found.reason, "kept_earlier_copy": "yes" if key in docs else "no"})
            continue
        total += len(found.document.text) - (len(docs[key]["document"]["text"]) if key in docs else 0)
        if total > MAX_KEPT:
            raise HubError(TOO_LARGE, f"keeping {file} would bring the registry's documents to {total} characters, "
                                      f"over {MAX_KEPT}: reading stopped there and nothing kept changed; keep fewer "
                                      "(max_docs) or a smaller registry", {"stopped_at": file, "characters": total})
        retrieved = instant(found.document.source.retrieved_at) or clock()
        docs[key] = {"entry": entry.id, "language": target.language, "version": target.version,
                     "versions": list(target.versions), "file": file,
                     "retrieved_at": found.document.source.retrieved_at, "ttl_hours": ttl_hours,
                     "stale_after": (retrieved + ttl).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                     "document": found.document.model_dump()}
        out.read.append(f"{entry.id}{'/' + target.language if target.language else ''}")
    if sum(len(d["document"]["text"]) for d in docs.values()) > MAX_KEPT:
        raise HubError(TOO_LARGE, f"the registry's documents are over {MAX_KEPT} characters; keep fewer (max_docs) "
                                  "or a smaller registry")
    _write(_kept_path(name), {"name": name, "location": location, "registry": got.document.model_dump(),
                              "retrieved_at": got.document.source.retrieved_at, **stated, "left_out": left_out,
                              "entries": [{"id": e.id, "type": e.type, "name": e.name, "description": e.description,
                                           "tags": list(e.tags),
                                           "targets": [t.__dict__ | {"versions": list(t.versions)} for t in e.targets]}
                                          for e in entries],
                              "docs": docs})
    return out


def _entries(kept: dict[str, Any]) -> list[Entry]:
    return [Entry(e["id"], e["type"], e["name"], e["description"], tuple(e["tags"]),
                  tuple(Target(t["language"], t["version"], t["path"], tuple(t.get("versions") or ()))
                        for t in e["targets"])) for e in kept["entries"]]


def freshness(doc: dict[str, Any], at: datetime | None = None) -> tuple[bool, str]:
    """Whether a kept document is past its time to live (and so stale), and its age in words."""
    moment = at or clock()
    retrieved, after = instant(str(doc.get("retrieved_at", ""))), instant(str(doc.get("stale_after", "")))
    if retrieved is None or after is None:
        return True, "its date cannot be read"
    hours = (moment - retrieved).total_seconds() / 3600
    return moment >= after, f"read {hours:.1f} hours ago, time to live {doc.get('ttl_hours')} hours"


def search(name: str, query: str, *, tags: Sequence[str] = (), language: str = "", top_k: int = 10) -> \
        list[dict[str, Any]]:
    """The kept entries of ``name`` that match ``query``, best first, by BM25 over name, tags, description and
    content; each with its kept documents' dates and whether each is stale."""
    from g6.grounding.retrieval import TextRetriever

    kept = load(name)
    wanted = {t.lower() for t in tags}
    entries = [e for e in _entries(kept) if wanted <= set(e.tags)
               and (not language or language.lower() in {t.language for t in e.targets})]
    docs = kept.get("docs") or {}
    texts = []
    for e in entries:
        content = " ".join(d["document"]["text"][:20_000] for k, d in docs.items() if d["entry"] == e.id)
        texts.append((e.id, f"{e.name}\n{' '.join(e.tags)}\n{e.description}\n{content}"))
    found = TextRetriever(texts).search(query, top_k=top_k) if query.strip() else []
    by_id = {e.id: e for e in entries}
    out = []
    for hit in found:
        entry = by_id[hit.source]
        kept_docs = []
        for d in (d for d in docs.values() if d["entry"] == entry.id):
            stale, age = freshness(d)
            kept_docs.append({"language": d["language"], "version": d["version"], "retrieved_at": d["retrieved_at"],
                              "stale": stale, "age": age, "source": d["document"]["source"]})
        out.append({**entry.describe(), "score": hit.score, "score_scale": "bm25", "documents": kept_docs})
    return out


def notes_for(name: str, entry: str) -> list[dict[str, Any]]:
    path = _kept_path(name, ".notes")
    if not path.is_file():
        return []
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HubError(HUB_STORE, f"the notes kept for {name!r} cannot be read") from None
    return [n for n in found if isinstance(n, dict) and n.get("entry") == entry]


def get(name: str, entry: str, *, language: str = "", version: str = "") -> tuple[dict[str, Any], bool, str]:
    """A kept document of ``entry`` and whether it is stale, with its age; :class:`HubError` when the entry is not
    listed, its language is needed, or the document asked for is not kept."""
    kept = load(name)
    found = next((e for e in _entries(kept) if e.id == entry), None)
    if found is None:
        raise HubError(NO_ENTRY, f"{name!r} lists no entry {entry!r}")
    languages = sorted({t.language for t in found.targets})
    if not language and len(languages) > 1:
        raise HubError(NEEDS_LANGUAGE, f"{entry} is kept in {', '.join(languages)}: name the language")
    language = (language or languages[0]).lower()
    doc = (kept.get("docs") or {}).get(_key(entry, language))
    if doc is None:
        raise HubError(NOT_KEPT, f"no document of {entry} ({language or 'no language'}) is kept: it failed when the "
                                 "registry was synced, or the sync kept no content")
    if version and version != doc["version"]:
        raise HubError(NOT_KEPT, f"{entry} is kept at version {doc['version']!r} (the registry's recommended one), "
                                 f"not {version!r}; the registry lists {', '.join(doc['versions']) or 'no others'}")
    stale, age = freshness(doc)
    return doc, stale, age


def add_note(name: str, entry: str, text: str) -> dict[str, Any]:
    """Keep a note on ``entry``, apart from its documents: a record of what a caller wrote, when."""
    kept = load(name)
    if not any(e["id"] == entry for e in kept["entries"]):
        raise HubError(NO_ENTRY, f"{name!r} lists no entry {entry!r}")
    text = text.strip()
    if not text or len(text) > MAX_NOTE:
        raise HubError(HUB_INPUT, f"a note is 1 to {MAX_NOTE} characters")
    path = _kept_path(name, ".notes")
    notes = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    if sum(1 for n in notes if n.get("entry") == entry) >= MAX_NOTES:
        raise HubError(TOO_LARGE, f"{entry} already has {MAX_NOTES} notes")
    note = {"kind": "note", "entry": entry, "text": text, "written_at": now(),
            "written_by": "a caller of ctx_contexthub (not checked)", "sha256": sha256_hex(text)}
    _write(path, [*notes, note])
    return note


def registries() -> list[dict[str, Any]]:
    """Each kept registry: where it came from, when it was read, how many entries and documents, how many stale."""
    folder = state_path("knowledge", "registries")
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.name.endswith(".notes.json") or not NAME.fullmatch(path.stem):
            continue
        try:
            kept = load(path.stem)
        except HubError as exc:
            out.append({"name": path.stem, "problem": exc.reason})
            continue
        docs = list((kept.get("docs") or {}).values())
        out.append({"name": path.stem, "location": kept["location"], "retrieved_at": kept["retrieved_at"],
                    "registry_version": str(kept.get("registry_version", "")),
                    "registry_updated": str(kept.get("registry_updated", "")),
                    "entries": len(kept["entries"]), "documents": len(docs),
                    "stale": sum(1 for d in docs if freshness(d)[0])})
    return out


__all__ = ["DEFAULT_TTL_HOURS", "HUB_INPUT", "HUB_STORE", "MAX_DOCS", "MAX_TTL_HOURS", "NEEDS_LANGUAGE", "NOT_KEPT", "NOT_SYNCED",
           "NO_ENTRY", "NO_REGISTRY", "REGISTRY_UNREADABLE", "STALE", "TOO_LARGE", "Entry", "HubError", "Read",
           "Synced", "Target", "add_note", "check_name", "clock", "fetch_through_fetcher", "freshness", "get", "load",
           "locate", "notes_for", "parse_registry", "read_folder", "registries", "registry_stated", "search", "sync"]
