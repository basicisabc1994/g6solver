# SPDX-License-Identifier: Apache-2.0
"""The managed Elasticsearch record and its reader (design 12.3, "Managed Elasticsearch").

Beside the generic cluster search in ``elastic.py``, which keeps its own cluster-document identity, this module
defines the one index G6 manages for a project (``g6-knowledge-v1-<project>``), its fixed mapping and the canonical
passage record kept in it. :func:`encode` writes a passage with its source, hashes, offsets, source registration and
generation; :func:`decode` rebuilds the canonical :class:`~g6.knowledge._types.Passage` from a hit and checks it: the
document id is the passage id, the text has the recorded hash, and the passage id is the hash of its source, its
text's document and its offsets. A record that fails is ``G6_E_KNOWLEDGE_INDEX_FAILED``: it is never served and never
given a cluster-document identity.

:class:`ManagedElasticIndex` reads that index only. Its query is built here from typed parts: one ``multi_match`` on
``text`` whose query is the caller's words as a string value, and term filters on the generation and the source
registrations. No caller text is parsed as JSON, and no caller names the index. It never writes (``writer.py`` does),
never starts a server, and :meth:`ManagedElasticIndex.ready` costs at most two seconds.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from g6.core import outside
from g6.knowledge._index import FAILED, READ_ONLY, Added, Hit, IndexRefused, Removed, Retrieves, Searched
from g6.knowledge._types import Passage, Source, sha256_hex
from g6.knowledge.ctx_elastic import elastic

INDEX_PREFIX = "g6-knowledge-v1-"
SCHEMA = "g6.knowledge.passage.v1"
BACKEND_NAME = "elastic_managed"
READY_SECONDS = 2.0
MAPPING: dict[str, Any] = {"dynamic": "strict", "properties": {
    "passage_id": {"type": "keyword"}, "registration_id": {"type": "keyword"}, "generation": {"type": "integer"},
    "source": {"type": "object", "enabled": False}, "document_sha256": {"type": "keyword"},
    "start": {"type": "integer"}, "end": {"type": "integer"}, "text": {"type": "text"},
    "text_sha256": {"type": "keyword"}, "schema": {"type": "keyword"}}}
MAPPING_SHA256 = hashlib.sha256(json.dumps(MAPPING, sort_keys=True).encode("utf-8")).hexdigest()


def index_name(project: str) -> str:
    """The only index this module ever names for ``project``."""
    return INDEX_PREFIX + hashlib.sha256(project.encode("utf-8")).hexdigest()[:16]


def encode(passage: Passage, registration_id: str, generation: int) -> dict[str, Any]:
    """The canonical record of ``passage``; its document id is the passage id."""
    return {"passage_id": passage.id, "registration_id": registration_id, "generation": int(generation),
            "source": passage.source.model_dump(), "document_sha256": passage.document_sha256,
            "start": passage.start, "end": passage.end, "text": passage.text,
            "text_sha256": sha256_hex(passage.text), "schema": SCHEMA}


def decode(hit: Mapping[str, Any]) -> tuple[Passage, str, int]:
    """The canonical passage, its registration and its generation from one hit (``_id`` and ``_source``), checked;
    :class:`IndexRefused` (``G6_E_KNOWLEDGE_INDEX_FAILED``) for a record that does not check out."""
    record = hit.get("_source") if isinstance(hit, Mapping) else None
    try:
        if not isinstance(record, Mapping) or record.get("schema") != SCHEMA:
            raise ValueError("it is not a G6 passage record of this schema")
        if hit.get("_id") != record["passage_id"]:
            raise ValueError("its document id is not its passage id")
        if sha256_hex(record["text"]) != record["text_sha256"]:
            raise ValueError("its text does not have the recorded hash")
        passage = Passage(id=record["passage_id"], source=Source(**record["source"]),
                          document_sha256=record["document_sha256"], start=record["start"], end=record["end"],
                          text=record["text"])
        registration, generation = record["registration_id"], record["generation"]
        if not isinstance(registration, str) or type(generation) is not int:
            raise ValueError("its registration or generation is missing")
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise IndexRefused(FAILED, f"a managed record does not check out ({type(exc).__name__}): it is not served",
                           {"reason": str(exc)[:200]}) from None
    return passage, registration, generation


def query_body(query: str, generation: int, registrations: Sequence[str] | None, top_k: int) -> dict[str, Any]:
    """The one query the managed reader sends: the caller's words as a value, the filters as typed terms."""
    filters: list[dict[str, Any]] = [{"term": {"generation": int(generation)}}, {"term": {"schema": SCHEMA}}]
    if registrations is not None:
        filters.append({"terms": {"registration_id": sorted(str(r) for r in registrations)}})
    return {"size": int(top_k), "track_total_hits": True,
            "query": {"bool": {"must": [{"multi_match": {"query": str(query), "fields": ["text"]}}],
                               "filter": filters}}}


class ManagedElasticIndex(Retrieves):
    """The read side of G6's managed index for one project and generation; see the module docstring."""

    backend = BACKEND_NAME
    score_scale = elastic.SCALE

    def __init__(self, project: str, generation: int, *, registrations: Sequence[str] | None = None,
                 caller: elastic.Caller | None = None) -> None:
        self.index = elastic.check_index(index_name(project))
        self.generation = int(generation)
        self.registrations = None if registrations is None else tuple(registrations)
        self._caller = caller

    def find(self, query: str, top_k: int = 10) -> Searched:
        said = {"backend": self.backend, "query": query, "score_scale": self.score_scale}
        if not str(query).strip():
            return Searched(**said, outcome="failed", reason="the query has no word to search for")
        if self.registrations is not None and not self.registrations:
            return Searched(**said, outcome="empty", searched=0, reason="no source registration is in scope")
        body = query_body(query, self.generation, self.registrations, top_k)
        try:
            reply = elastic._call("POST", f"/{self.index}/_search", body, self._caller)
        except elastic.ElasticError as exc:
            down = exc.code in (elastic.UNREACHABLE, elastic.TIMED_OUT, elastic.NOT_FOUND)
            return Searched(**said, outcome="unavailable" if down else "failed", reason=exc.code,
                            evidence={"code": exc.code})
        shards = reply.get("_shards") if isinstance(reply, dict) else None
        if not isinstance(shards, dict) or shards.get("failed") or shards.get("successful") != shards.get("total"):
            return Searched(**said, outcome="failed", reason="some shards did not answer: a partial result is not "
                            "served as the result", evidence={"partial": True, "code": elastic.COUNT_PARTIAL})
        try:
            raw = reply["hits"]["hits"]
            hits = []
            for rank, one in enumerate(raw, start=1):
                passage, registration, generation = decode(one)
                if generation != self.generation or (self.registrations is not None
                                                     and registration not in self.registrations):
                    raise IndexRefused(FAILED, "the server returned a record outside the filters G6 sent")
                hits.append(Hit(passage=passage, score=float(one.get("_score") or 0.0), score_scale=self.score_scale,
                                rank=rank))
            total = reply["hits"].get("total", {}).get("value")
        except IndexRefused as exc:
            return Searched(**said, outcome="failed", reason=exc.reason, evidence={"code": exc.code})
        except (KeyError, TypeError, ValueError, AttributeError):
            return Searched(**said, outcome="failed", reason="the server's reply is not a search result")
        if not hits:
            return Searched(**said, outcome="empty", total=total, reason="no managed passage holds the query's words")
        return Searched(**said, outcome="ok", hits=hits, total=total)

    def ready(self) -> str:
        """``""`` when the server answers one loopback request within two seconds; otherwise why not. It starts
        nothing."""
        send = self._caller or outside.call_service
        try:
            send(elastic.ELASTICSEARCH, "GET", "/", headers={"Accept": "application/json"}, timeout=READY_SECONDS,
                 max_bytes=64 * 1024)
        except outside.ServiceError as exc:
            return f"Elasticsearch did not answer within {READY_SECONDS:g} seconds ({exc.code})"
        return ""

    def add(self, passages: Sequence[Passage]) -> Added:
        raise IndexRefused(READ_ONLY, "the managed reader does not write; the scoped dispatcher's writer does")

    def remove(self, ids: Sequence[str]) -> Removed:
        raise IndexRefused(READ_ONLY, "the managed reader does not write; the scoped dispatcher's writer does")

    def status(self) -> dict[str, Any]:
        return {"backend": self.backend, "index": self.index, "generation": self.generation,
                "score_scale": self.score_scale, "ready": self.ready() or "ready"}


__all__ = ["BACKEND_NAME", "INDEX_PREFIX", "MAPPING", "MAPPING_SHA256", "READY_SECONDS", "SCHEMA",
           "ManagedElasticIndex", "decode", "encode", "index_name", "query_body"]
