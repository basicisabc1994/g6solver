# SPDX-License-Identifier: Apache-2.0
"""The scoped onboarding dispatcher (design 12.3, 12.5 item 2): which search routes a project has, whether each is
ready, and the one place that opens the managed Elasticsearch backend.

``_``-prefixed, so it never registers as a component. The local indexer's refusal of network backends is not relaxed:
``knowledge_indexer`` still opens only local backends, and ``elastic_managed`` is opened here and nowhere else, for
one project and one published generation, filtered to the source registrations the access may use.

A route is ready for a generation only when that generation's record says the backend read back what was written
(``answered``). A server that is installed and reachable but holds nothing for the generation is not searchable: the
route says why, and a search over it reports ``unavailable``, never ``empty``. ``sqlite`` is the only required
backend of a generation; Elasticsearch is always optional.

Counts and aggregations are structured answers, not passages: a typed, closed set of fields, a query built here, and
``unsupported`` when the project has no managed Elasticsearch generation. A count some shards did not answer is
refused.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.knowledge._index import Index
from g6._context_engine._registry import ProjectStore, Refused
from g6._context_engine._scope import Access, Operation

UNSUPPORTED = "G6_E_KNOWLEDGE_UNSUPPORTED"
RouteName = Literal["exact", "structure", "meaning", "relations"]
AGGREGATE_FIELDS = ("registration_id", "document_sha256")


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: RouteName
    backend: str
    operations: frozenset[Operation]


ROUTES: tuple[Route, ...] = (
    Route(name="exact", backend="sqlite", operations=frozenset({"search", "fetch", "context", "plan"})),
    Route(name="structure", backend="elastic_managed", operations=frozenset({"search", "count", "context", "plan"})),
    Route(name="meaning", backend="colbert_persistent", operations=frozenset({"search", "context", "plan"})),
    Route(name="relations", backend="graph", operations=frozenset({"traverse", "plan"})),
)


def route(name: str) -> Route | None:
    return next((r for r in ROUTES if r.name == name), None)


def routes(store: ProjectStore) -> list[tuple[Route, str]]:
    """Each route with why it is not ready for the published generation (``""``: ready)."""
    generation = store.published()
    out = []
    for one in ROUTES:
        if generation is None:
            out.append((one, "not_published: nothing is indexed for this project yet"))
            continue
        if one.backend == "graph":   # derived on this machine from the indexed files: ready once it is built
            built = store.graph(generation.number)[0] is not None
            out.append((one, "" if built else f"no graph is built for generation {generation.number}"))
            continue
        entry = generation.backends.get(one.backend)
        if entry is None:
            out.append((one, f"the {one.backend} backend is not selected for this project"))
        elif not answered(generation, one.backend):
            out.append((one, f"the {one.backend} backend holds no complete copy of any source of generation "
                             f"{generation.number} ({entry.get('state', 'unavailable')})"))
        else:
            out.append((one, ""))
    return out


OUT_OF_SCOPE = "no source in your scope has a complete copy in this backend"


def routes_for(store: ProjectStore, readable: Sequence[str], *, local: bool) -> list[tuple[Route, str]]:
    """:func:`routes` as one caller may be told it. The person's own (local) access sees the project's routes as
    they are. Any other caller's routes are worked out from the sources in its scope and nothing else: an optional
    backend is ready for it only when it holds a complete copy of a source the caller may read, and otherwise the
    reason is one fixed sentence, whatever any other source's copy is like."""
    found = routes(store)
    if local:
        return found
    generation = store.published()
    out = []
    for one, why in found:
        entry = generation.backends.get(one.backend) if generation is not None else None
        optional = entry is None or not entry.get("required")
        if one.backend != "graph" and optional and (why or not set(answered(generation, one.backend)) & set(readable)):
            why = OUT_OF_SCOPE
        out.append((one, why))
    return out


def answered(generation: Any, backend: str) -> list[str]:
    """The registrations whose passages ``backend`` read back in full for ``generation``; for the required local
    backend, every registration (``["*"]``)."""
    entry = generation.backends.get(backend) or {}
    if entry.get("required"):
        return ["*"] if entry.get("state") == "answered" else []
    return sorted(rid for rid, one in (entry.get("registrations") or {}).items() if one.get("state") == "answered")


def usable_registrations(store: ProjectStore, acc: Access, *, at: datetime) -> list[str]:
    """The source registrations ``acc`` may read at ``at``: the engine's scope (usable by the registry's one
    predicate, inside the caller's narrowing and, for a destination other than local, granted to that provider)."""
    from g6._context_engine import _engine as engine

    return engine.scope_of(store, acc, at)[0]


def open_route(store: ProjectStore, one: Route, generation: int, *, registrations: list[str] | None = None,
               caller: Any = None) -> Index:
    """The index behind ``one`` for ``generation``. The only place the managed Elasticsearch backend is opened."""
    if one.backend == "sqlite":
        from g6._context_engine._lexical import ScopedLexical

        from g6._context_engine import _engine as engine

        stale = {uri for _, uri in engine._still_changed(store, generation)}   # denied files take no place in the hits
        return ScopedLexical(store.index_path("sqlite", generation),
                             registrations if registrations is not None else [], excluded=sorted(stale))
    if one.backend == "elastic_managed":
        from g6.knowledge.ctx_elastic.managed import ManagedElasticIndex

        return ManagedElasticIndex(store.binding.project, generation, registrations=registrations, caller=caller)
    if one.backend == "colbert_persistent":
        from g6.knowledge.ctx_colbert.persistent import open_for

        return open_for(store, generation, registrations=registrations)
    raise Refused(UNSUPPORTED, f"the {one.name} route has no passage index")


def _structured(state: str, **fields: Any) -> dict[str, Any]:
    return {"kind": "structured", "state": state, **fields}


def _managed(store: ProjectStore) -> tuple[Any, str]:
    ready = dict((r.name, why) for r, why in routes(store))
    return store.published(), ready.get("structure", "")


def _filters(generation: int, registrations: list[str], extra: Mapping[str, str | int | bool] | None) -> list[dict]:
    from g6.knowledge.ctx_elastic.managed import SCHEMA

    out: list[dict] = [{"term": {"generation": generation}}, {"term": {"schema": SCHEMA}},
                       {"terms": {"registration_id": sorted(registrations)}}]
    for field, value in sorted((extra or {}).items()):
        if field not in ("registration_id", "document_sha256") or isinstance(value, float) \
                or not isinstance(value, (str, int, bool)):
            raise Refused(UNSUPPORTED, "a count filters only on registration_id or document_sha256, by an exact value")
        out.append({"term": {field: value}})
    return out


def _query(query: str, filters: list[dict]) -> dict[str, Any]:
    must = [{"multi_match": {"query": str(query), "fields": ["text"]}}] if str(query).strip() else [{"match_all": {}}]
    return {"bool": {"must": must, "filter": filters}}


def count(acc: Access, *, query: str = "", filters: Mapping[str, str | int | bool] | None = None,
          caller: Any = None, clock: Any = None) -> dict[str, Any]:
    """How many managed passages match, as a structured answer; ``unsupported`` when the project has no managed
    Elasticsearch generation; ``refused`` when some shards did not answer."""
    from g6.knowledge.ctx_elastic import elastic
    from g6.knowledge.ctx_elastic.managed import index_name

    store = ProjectStore(acc.binding)
    generation, why = _managed(store)
    if generation is None or why:
        return _structured("unsupported", reason=why or "not_published", backend="elastic_managed",
                           note="counts need the managed Elasticsearch backend; the local lexical index answers "
                                "passage searches only")
    held = answered(generation, "elastic_managed")
    registrations = [r for r in usable_registrations(store, acc, at=(clock or (lambda: datetime.now(UTC)))()) if r in held]
    if not registrations:
        return _structured("answered", answer={"count": 0}, backend="elastic_managed", generation=generation.number,
                           sources_counted=[])
    body = {"query": _query(query, _filters(generation.number, registrations, filters))}
    try:
        reply = elastic._call("POST", f"/{index_name(store.binding.project)}/_count", body, caller)
    except elastic.ElasticError as exc:
        return _structured("unavailable", reason=exc.code, backend="elastic_managed")
    shards = reply.get("_shards") if isinstance(reply, dict) else None
    if not isinstance(shards, dict) or shards.get("failed") or shards.get("successful") != shards.get("total") \
            or type(reply.get("count")) is not int:
        return _structured("refused", reason=elastic.COUNT_PARTIAL, backend="elastic_managed",
                           note="some shards did not answer; a partial count is not an answer")
    return _structured("answered", answer={"count": reply["count"]}, backend="elastic_managed",
                       generation=generation.number, sources_counted=registrations)


def aggregate(acc: Access, field: Literal["registration_id", "document_sha256"],
              kind: Literal["terms", "value_count"], *, query: str = "", size: int = 20,
              caller: Any = None, clock: Any = None) -> dict[str, Any]:
    """A typed aggregation over the managed passages, built here from a closed field list: an answer, never
    passages."""
    from g6.knowledge.ctx_elastic import elastic
    from g6.knowledge.ctx_elastic.managed import index_name

    if field not in AGGREGATE_FIELDS or kind not in ("terms", "value_count"):
        raise Refused(UNSUPPORTED, "an aggregation is terms or value_count over registration_id or document_sha256")
    store = ProjectStore(acc.binding)
    generation, why = _managed(store)
    if generation is None or why:
        return _structured("unsupported", reason=why or "not_published", backend="elastic_managed")
    held = answered(generation, "elastic_managed")
    registrations = [r for r in usable_registrations(store, acc, at=(clock or (lambda: datetime.now(UTC)))()) if r in held]
    if not registrations:
        return _structured("answered", answer={"buckets": [], "value": 0}, backend="elastic_managed",
                           generation=generation.number, sources_counted=[])
    shape = {"terms": {"field": field, "size": min(max(int(size), 1), 100)}} if kind == "terms" \
        else {"value_count": {"field": field}}
    body = {"size": 0, "query": _query(query, _filters(generation.number, registrations, None)), "aggs": {"a": shape}}
    try:
        reply = elastic._call("POST", f"/{index_name(store.binding.project)}/_search", body, caller)
        shards, found = reply["_shards"], reply["aggregations"]["a"]
    except elastic.ElasticError as exc:
        return _structured("unavailable", reason=exc.code, backend="elastic_managed")
    except (KeyError, TypeError):
        return _structured("failed", reason="the server's reply is not an aggregation", backend="elastic_managed")
    if shards.get("failed") or shards.get("successful") != shards.get("total"):
        return _structured("refused", reason=elastic.COUNT_PARTIAL, backend="elastic_managed")
    answer = ({"buckets": [{"key": b["key"], "count": b["doc_count"]} for b in found.get("buckets", [])]}
              if kind == "terms" else {"value": found.get("value")})
    return _structured("answered", answer=answer, backend="elastic_managed", generation=generation.number,
                       sources_counted=registrations)


__all__ = ["AGGREGATE_FIELDS", "OUT_OF_SCOPE", "ROUTES", "UNSUPPORTED", "Route", "aggregate", "answered", "count",
           "open_route", "route", "routes", "routes_for", "usable_registrations"]
