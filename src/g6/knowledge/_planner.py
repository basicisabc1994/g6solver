# SPDX-License-Identifier: Apache-2.0
"""The search planner (design 12.4): choose routes by fixed priorities, track coverage against evidence needs
declared before the search, and record the decision.

``_``-prefixed, so it never registers as a component. Nothing here is learned. :func:`features_of` reads a query
with regular expressions only and says ``None`` for what it cannot tell; :func:`plan` orders the routes by a fixed
rule in which every feature changes the order or what is admissible; :func:`run` executes inside limits fixed
before the run and inside the access's scope (every read goes through the engine's own checks, so the planner
cannot widen a grant), and returns a :class:`DecisionEvent`.

Coverage is tracked, never claimed: a need is ``found`` when a returned passage holds its words, ``conflicting``
when a passage that holds them is under a caution, ``missing`` when the routes answered and no returned passage
holds them, and ``unknown`` when the budget ran out, a route was refused, a source was withdrawn during the run or
a source is withheld from the caller's provider.
A round that brings no new passage stops the branch; that is not a statement that the sources hold nothing more.
Model reformulation is off by default (``model_calls: 0``); when a caller is given and the limits allow it, the
model is asked through ``consent_callers.guard`` under the sources already retrieved, so a model those sources are
not shared with is refused, and its reply is read into typed search fields only: it cannot name a source, a filter
or a limit.

The decision log (``search_decisions.jsonl`` in the project's folder) keeps hashes and counts: no query text, no
passage text and no passage ids. A log of chosen actions alone is not training data for a router.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core import cloud_consent
from g6.core.consent_callers import guard
from g6.knowledge import _engine as engine
from g6.knowledge._envelope import MAX_TOP_K, PassageItem, SearchRequest, SearchResponse
from g6.knowledge._fuse import tokens_of
from g6.knowledge._registry import ProjectStore
from g6.knowledge._relations import Limits
from g6.knowledge._scope import Access
from g6.knowledge._types import now, sha256_hex

LOG = "search_decisions.jsonl"
DEFAULT_LIMITS = Limits()   # frozen: no model call, six searches
PASSAGE_ROUTES = ("exact", "meaning", "structure")   # the default order: by route name
EXPECTED = {"exact": "passages that hold the query's words", "structure": "the same from the managed backend",
            "meaning": "passages close in meaning (the rung that ranked is reported)",
            "relations": "witnessed containment and import paths from a file the query names"}
COVERAGE = ("found: a returned passage holds the need's words. missing: the routes answered and no returned passage "
            "holds them; that does not say the sources hold nothing more. unknown: the run could not tell.")
REFORMULATE = ("You are given a search query and the evidence needs it has not met. Propose one broader or more "
               "exact query. Reply with JSON only: an object with the key query (text) and optionally routes (a list "
               "from exact, meaning, structure) and top_k (a whole number).")
_IDENTIFIER = re.compile(r"`[^`]+`|\b[A-Za-z]+_[A-Za-z0-9_]+\b|\b[a-z]+[A-Z][A-Za-z0-9]*\b|\b[A-Z][a-z0-9]+[A-Z]\w*\b|\b\w+\(\)")
_PATH_OR_ERROR = re.compile(r"[\w.-]+/[\w./-]+|\b[\w-]+\.(?:py|md|txt|rst|html?|json|toml|ya?ml)\b|\b\w*(?:Error|Exception)\b|Traceback")
_RELATION = re.compile(r"\b(?:imports?|imported by|depends? on|dependenc(?:y|ies)|contains?|which (?:files|modules)|what uses)\b", re.I)
_PATH = re.compile(r"[\w.-]+/[\w./-]+\.\w+|\b[\w-]+\.(?:py|md)\b")
_WORD = re.compile(r"\w+")


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Need(_Record):
    id: str
    text: str
    state: Literal["found", "conflicting", "missing", "unknown"] = "unknown"
    witnesses: tuple[str, ...] = ()


class Features(_Record):
    has_identifier: bool | None = None
    has_path_or_error: bool | None = None
    is_relation_question: bool | None = None
    is_conceptual: bool | None = None


class DecisionEvent(_Record):
    needs: tuple[Need, ...]
    features: Features
    available: tuple[str, ...]
    grants: dict[str, int]
    generation: int | None
    admissible: tuple[str, ...]
    rule: str
    chosen: tuple[str, ...]
    alternatives: tuple[dict[str, str], ...]
    expected: dict[str, str]
    limits: Limits
    actual_rung: dict[str, str]
    contributions: int
    costs: dict[str, Any]
    stop_reason: Literal["needs_met", "no_new_passage", "budget", "refused", "unavailable"]


def features_of(query: str) -> Features:
    """What the query's shape says, by regular expressions only. What a query is too short to tell is ``None``
    (unknown), never a guess."""
    words = _WORD.findall(query or "")
    if not words:
        return Features()
    identifier, path_or_error = bool(_IDENTIFIER.search(query)), bool(_PATH_OR_ERROR.search(query))
    if len(words) < 3:
        return Features(has_identifier=identifier, has_path_or_error=path_or_error)
    relation = bool(_RELATION.search(query))
    return Features(has_identifier=identifier, has_path_or_error=path_or_error, is_relation_question=relation,
                    is_conceptual=not (identifier or path_or_error or relation))


def admissible_routes(features: Features) -> tuple[str, ...]:
    """The routes the features admit, ready or not. A path or an error text is matched literally, so the meaning
    route is not admissible for it; the relations route is admissible only for a relation question."""
    passages = tuple(r for r in PASSAGE_ROUTES if not (r == "meaning" and features.has_path_or_error))
    return (*passages, *(("relations",) if features.is_relation_question else ()))


def plan(features: Features, routes: Sequence[tuple[Any, str]]) -> tuple[tuple[str, ...], str]:
    """The routes to use, in order, and the name of the rule that ordered them. Fixed priorities: a relation
    question starts with witnessed edges; an identifier, a path or an error goes to exact search first, then the
    managed backend; a conceptual query tries the meaning route first; what is left follows by route name. A route
    that is not ready is never chosen: it stays unavailable."""
    ready = {route.name for route, why in routes if not why}
    admissible = admissible_routes(features)
    order: list[str] = []
    rule = "default"
    if features.is_relation_question:
        order.append("relations")
        rule = "relation-first"
    if features.has_identifier or features.has_path_or_error:
        order += ["exact", "structure"]
        rule = rule if rule != "default" else "exact-first"
    if features.is_conceptual:
        order.append("meaning")
        rule = rule if rule != "default" else "meaning-first"
    order += sorted(name for name in admissible if name not in order)
    return tuple(name for name in order if name in admissible and name in ready), rule


def _covered(need: str, items: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    words = {w.casefold() for w in _WORD.findall(need) if len(w) > 3}
    if not words:
        return ()
    return tuple(i["passage_id"] for i in items if words <= {w.casefold() for w in _WORD.findall(i["view"]["text"])})


def _typed(reply: Any) -> dict[str, Any] | None:
    """A model's reply read into typed search fields only: a query string, known route names, a result count.
    Everything else it wrote is dropped: it names no source, no filter and no limit, and no part of it is used as a
    query document."""
    try:
        text = str(reply)
        proposal = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        return None
    if not isinstance(proposal, dict) or not isinstance(proposal.get("query"), str) or not proposal["query"].strip():
        return None
    fields: dict[str, Any] = {"query": proposal["query"][:400]}
    if isinstance(proposal.get("routes"), list):
        named = tuple(r for r in PASSAGE_ROUTES if r in proposal["routes"])
        if named:
            fields["routes"] = named
    if type(proposal.get("top_k")) is int:
        fields["top_k"] = min(max(proposal["top_k"], 1), MAX_TOP_K)
    return fields


def _counted(items: Sequence[dict[str, Any]]) -> int:
    """The contributions that count as agreement: a lexical fallback already counted with the lexical backend, and a
    model's lead, are not counted again."""
    return sum(1 for i in items for c in i["contributions"]
               if "counted once" not in c["reason"] and "no evidence weight" not in c["reason"])


def _record(store: ProjectStore, public: dict[str, Any]) -> None:
    """Append the decision to the project's log: hashes and counts only."""
    store.folder.mkdir(parents=True, exist_ok=True)
    with (store.folder / LOG).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(public, sort_keys=True) + "\n")


def run(acc: Access, query: str, needs: Sequence[str], *, limits: Limits = DEFAULT_LIMITS, top_k: int = 8,
        reformulate: Any = None, clock: Callable[[], datetime] = engine._utc,
        ) -> tuple[SearchResponse, DecisionEvent, tuple[cloud_consent.Source, ...]]:
    """Plan and run a search for ``query`` against the evidence ``needs`` declared now, inside ``limits`` fixed
    before the run. ``reformulate`` is a model caller, used only when ``limits.model_calls`` allows; see the module
    docstring."""
    from g6.knowledge import _dispatch, _graph

    started = time.monotonic()
    store = ProjectStore(acc.binding)
    features = features_of(query)
    if features.is_relation_question:
        _graph.ensure(store)   # derived on this machine from the indexed files; what a traversal returns is scoped
    generation = store.published()
    readable = engine.scope_of(store, acc, clock())[0] if generation is not None else []   # scope first
    routes = _dispatch.routes_for(store, readable, local=acc.local)   # which routes are ready, inside that scope
    chosen, rule = plan(features, routes)
    reasons = {route.name: why for route, why in routes}
    admissible = admissible_routes(features)
    declared = [Need(id=f"need-{n + 1}", text=str(text)[:400]) for n, text in enumerate(needs)]
    grants = {r.id: r.authorisation_revision for r in store.registrations() if r.id in readable}
    alternatives = tuple({"route": name, "why": "unavailable: " + (reasons.get(name) or "not a route of this project")}
                         for name in admissible if name not in chosen)
    passage_routes = tuple(r for r in chosen if r in PASSAGE_ROUTES)
    top_k = min(max(int(top_k), 1), MAX_TOP_K, max(limits.candidates, 1))
    response = SearchResponse(request=SearchRequest(query=query, top_k=top_k, routes=passage_routes or None),
                              generation=generation.number if generation else None)
    sources: dict[str, cloud_consent.Source] = {}
    path_sources: tuple[cloud_consent.Source, ...] = ()
    items: dict[str, dict[str, Any]] = {}
    searches = model_calls = 0
    stop = "unavailable"
    asked: dict[str, Any] = {"query": query, "top_k": top_k, "routes": passage_routes}
    paths: list[dict[str, Any]] = []
    if "relations" in chosen and _PATH.search(query):
        if limits.searches < 1:
            stop = "budget"
        else:
            found, path_sources = _graph.traverse(acc, _PATH.search(query).group(0), "imports", clock=clock)
            searches += 1
            paths = found.payload["paths"]
            stop = "no_new_passage"
    while passage_routes:
        if searches + len(asked["routes"]) > limits.searches or time.monotonic() - started > limits.seconds:
            stop = "budget"
            break
        response, refs = engine.search(acc, SearchRequest(**asked), clock=clock)
        searches += len(asked["routes"])
        fresh = [i for i in response.payload["items"] if i["passage_id"] not in items]
        for item, ref in zip(response.payload["items"], refs, strict=True):
            items.setdefault(item["passage_id"], item)
            sources.setdefault(item["passage_id"], ref)
        if response.payload.get("refused"):
            stop = "refused"
            break
        if declared and all(_covered(n.text, list(items.values())) for n in declared):
            stop = "needs_met"
            break
        if not fresh or reformulate is None:
            stop = "no_new_passage"
            break
        if model_calls >= limits.model_calls:
            stop = "no_new_passage" if limits.model_calls == 0 else "budget"
            break
        open_needs = [n.text for n in declared if not _covered(n.text, list(items.values()))]
        try:   # the model's question depends on what was retrieved, so it is sent under those sources' consent
            with cloud_consent.request_sources(tuple(sources.values())):
                reply = guard(reformulate).complete(REFORMULATE, json.dumps({"query": query, "unmet_needs": open_needs}),
                                                    max_tokens=400)
        except Exception as exc:  # noqa: BLE001 - the consent refusal is told apart by its code; anything else is raised on
            if cloud_consent.REQUIRED not in str(exc):
                raise
            stop = "refused"
            break
        model_calls += 1
        proposal = _typed(reply)
        if proposal is None:
            stop = "no_new_passage"
            break
        asked = {"query": proposal["query"], "top_k": min(proposal.get("top_k", top_k), top_k),
                 "routes": tuple(r for r in proposal.get("routes", passage_routes) if r in passage_routes) or passage_routes}
    # the last check before anything is returned: a revoke or an expiry that arrived during the run is honoured
    late = store.use(acc, list(items), at=clock(), generation=response.generation)[1] if items else []
    for gone in late:
        items.pop(gone.passage_id, None)
        sources.pop(gone.passage_id, None)
    if late:
        stop = "refused"   # a source was withdrawn during the run: what is left says nothing about the needs it met
    kept: list[dict[str, Any]] = []
    spent = 0
    for item in items.values():   # the token limit refuses the rest; it is counted, not hidden
        cost = tokens_of(PassageItem(**item))
        if spent + cost > limits.tokens:
            stop = "budget"
            break
        kept.append(item)
        spent += cost
    shown = {i["passage_id"] for i in kept}
    cautioned = {p for c in response.payload.get("cautions", []) for p in c["passages"]}
    judged = []
    for need in declared:
        witnesses = _covered(need.text, kept)
        if not witnesses:
            state = "unknown" if stop in ("budget", "unavailable", "refused") or response.withheld else "missing"
        else:
            state = "conflicting" if set(witnesses) & cautioned else "found"
        judged.append(need.model_copy(update={"state": state, "witnesses": witnesses}))
    if stop == "needs_met" and any(n.state != "found" for n in judged):
        stop = "no_new_passage"
    payload = {"kind": "passages", "items": kept, "omitted": len(items) - len(kept), "coverage": COVERAGE,
               **{k: [g for g in response.payload[k] if all(p in shown for p in (g if k == "groups" else g["passages"]))]
                  for k in ("groups", "cautions") if k in response.payload}}
    if paths:
        payload["paths"] = paths
    if response.payload.get("refused"):
        payload["refused"] = response.payload["refused"]
    response = response.model_copy(update={"payload": payload, "authorisation_revisions": {
        r: v for r, v in grants.items() if r in {i["source"] for i in kept} | {e["source"] for p in paths for e in p["edges"]}}})
    event = DecisionEvent(
        needs=tuple(judged), features=features, available=tuple(name for name, why in reasons.items() if not why),
        grants=grants, generation=response.generation, admissible=admissible, rule=rule, chosen=chosen,
        alternatives=alternatives, expected={name: EXPECTED[name] for name in chosen}, limits=limits,
        actual_rung={b.route: b.rung or b.backend for b in response.backends if b.state in ("answered", "partial")},
        contributions=_counted(kept),
        costs={"searches": searches, "model_calls": model_calls, "seconds": round(time.monotonic() - started, 3),
               "tokens": spent},
        stop_reason=stop)
    _record(store, {"at": now(), "query_sha256": sha256_hex(query), "features": features.model_dump(),
                    "needs": [{"id": n.id, "text_sha256": sha256_hex(n.text), "state": n.state,
                               "witnesses": len(n.witnesses)} for n in judged],
                    "available": list(event.available), "grants": grants, "generation": event.generation,
                    "admissible": list(admissible), "rule": rule, "chosen": list(chosen),
                    "alternatives": list(alternatives), "expected": event.expected, "limits": limits.model_dump(),
                    "actual_rung": event.actual_rung, "contributions": event.contributions, "costs": event.costs,
                    "stop_reason": stop})
    return response, event, (*(sources[i["passage_id"]] for i in kept), *(path_sources if paths else ()))


__all__ = ["COVERAGE", "EXPECTED", "LOG", "PASSAGE_ROUTES", "DecisionEvent", "Features", "Limits", "Need",
           "admissible_routes", "features_of", "plan", "run"]
