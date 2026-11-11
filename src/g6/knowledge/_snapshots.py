# SPDX-License-Identifier: Apache-2.0
"""Snapshots of what a search returned, for a later reconstruction (design 12.5 item 3, 12.6).

``_``-prefixed, so it never registers as a component. A snapshot is kept only for items of a registration whose
retention is ``"audit"``: the passage ids, the file and text hashes, the exact rendered view text, the grant revision,
the generation and the versions of the converter, the chunker, the index schema and G6. Hashes alone cannot
reconstruct a decision, so with ``"audit"`` the file's bytes at indexing time are retained too, content-addressed in
the project's own folder, where no index reads them: retained bytes are never searchable live content.

:func:`reconstruct` rebuilds the rendered excerpts from the retained bytes and compares each with what the snapshot
recorded. It answers ``unavailable``, with the reason, when no snapshot was kept, when the bytes were not retained
or were deleted, or when the snapshot's source was revoked, changed or deleted since (a snapshot is a stored
consumer: it registers a dependent and honours invalidation before use).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from g6.knowledge._envelope import SearchRequest, SearchResponse
from g6.knowledge._registry import Dependent, ProjectStore
from g6.knowledge._scope import Access
from g6.knowledge._types import sha256_hex
from g6.knowledge._view import view_of

INDEX_SCHEMA = "sqlite-fts5-v1"


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    request: SearchRequest
    generation: int
    items: tuple[dict[str, Any], ...]
    versions: dict[str, str]


def versions(store: ProjectStore, generation: int) -> dict[str, str]:
    import g6

    members = store.members(generation)
    return {"converter": ",".join(sorted({m.converter for m in members if m.converter})),
            "chunker": ",".join(sorted({m.chunker for m in members if m.chunker})),
            "index_schema": INDEX_SCHEMA, "g6": getattr(g6, "__version__", "")}


def write(store: ProjectStore, response: SearchResponse, members: Mapping[str, Any] | None = None) -> str:
    """Keep a snapshot of ``response``'s items whose registration retains audit copies; ``""`` when none does. The
    items are ones the search just returned, so each passed the use check of that call. A snapshot records what its
    rendering needs (the file's address, its media type and its converter), so that a later reconstruction does not
    depend on a membership record that publication may have pruned since."""
    if response.generation is None:
        return ""
    audited = {r.id: r for r in store.registrations() if r.retention == "audit"}
    kept = []
    for item in response.payload.get("items", []):
        registration = audited.get(item["source"])
        member = (members or {}).get(item["passage_id"])
        if registration is None or member is None:
            continue
        kept.append({"passage_id": item["passage_id"], "registration_id": item["source"], "path": item["path"],
                     "uri": member.uri, "media_type": member.media_type, "converter": member.converter,
                     "start": item["start"], "end": item["end"], "file_sha256": item["checked"]["reread_sha256"],
                     "view_text": item["view"]["text"], "view_sha256": sha256_hex(item["view"]["text"]),
                     "authorisation_revision": registration.authorisation_revision})
    if not kept:
        return ""
    body = {"request": response.request.model_dump(), "generation": response.generation, "items": kept,
            "versions": versions(store, response.generation)}
    ident = "snap-" + sha256_hex(json.dumps(body, sort_keys=True, default=str))[:32]
    made = Snapshot(id=ident, request=response.request, generation=response.generation, items=tuple(kept),
                    versions=body["versions"])
    store.keep_snapshot(ident, made.model_dump_json(), sorted({i["registration_id"] for i in kept}))
    for registration_id in sorted({i["registration_id"] for i in kept}):
        store.depend(Dependent(kind="snapshot", ident=ident, registration_id=registration_id,
                               passage_ids=tuple(i["passage_id"] for i in kept
                                                 if i["registration_id"] == registration_id),
                               generation=response.generation))
    return ident


def reconstruct(acc: Access, snapshot_id: str, *, clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """The snapshot's excerpts rebuilt from the retained bytes and compared with what was recorded, or
    ``unavailable`` with the reason. A snapshot is a use of its source: its registration must be usable at this
    time (not revoked, deleted or past its expiry)."""
    store = ProjectStore(acc.binding)

    def unavailable(reason: str) -> dict[str, Any]:
        return {"snapshot": snapshot_id, "state": "unavailable", "reason": reason, "items": []}

    raw = store.snapshot(snapshot_id) if snapshot_id else None
    if raw is None:
        return unavailable("no snapshot is kept under this id: none was written (retention was 'none'), or it was "
                           "deleted with its source")
    made = Snapshot.model_validate_json(raw)
    denied = store.invalidated("snapshot", made.id, at=(clock or (lambda: datetime.now(UTC)))())
    if denied:
        return unavailable("the snapshot's source was revoked, changed or deleted since: "
                           + ", ".join(sorted({d.why for d in denied})))
    if not acc.local:
        return unavailable("a snapshot is reconstructed for the person on this machine only")
    from g6.knowledge import _engine as engine

    items = []
    for item in made.items:
        data = store.retained(item["file_sha256"])
        if data is None:
            return unavailable("the bytes this snapshot was rendered from are not retained (they were never kept, or "
                               "were removed by a complete deletion)")
        document, _, _ = engine._document(data, item["uri"], item["media_type"])
        text = document.text[item["start"]:item["end"]] if document is not None else ""
        rendered = view_of(text, canonical_start=item["start"]).text
        items.append({"passage_id": item["passage_id"], "path": item["path"], "start": item["start"],
                      "end": item["end"], "view_text": rendered,
                      "matches_the_recorded_excerpt": sha256_hex(rendered) == item["view_sha256"]})
    return {"snapshot": made.id, "state": "reconstructed", "generation": made.generation, "versions": made.versions,
            "request": made.request.model_dump(), "items": items,
            "exact": all(i["matches_the_recorded_excerpt"] for i in items)}


__all__ = ["INDEX_SCHEMA", "Snapshot", "reconstruct", "versions", "write"]
