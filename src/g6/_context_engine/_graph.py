# SPDX-License-Identifier: Apache-2.0
"""The passage-linked graph, first half (design 12.3): containment and explicit static imports, each edge with a
canonical passage and witness offsets.

``_``-prefixed, so it never registers as a component. Nodes are scoped to one source registration: a document (a
file), a section (a Markdown heading) or a module (a Python file). Two files of the same name in two registrations,
or two projects, are two nodes; nothing is merged by name. Edges are ``contains`` (a document contains a section)
and ``imports`` (a module names another in an import statement). An import is a possible dependency, stated as
such: it says the name appears in an import statement, never that code runs or that behaviour depends on it. An
import built at run time (``importlib.import_module``, ``__import__``) is recorded as unknown.

Every edge names the passage its witness lies in and the witness's offsets and hash. :func:`traverse` re-reads each
edge's file and checks the witness is still there, checks every edge and every node on a path with
:meth:`ProjectStore.use` and the disclosure grant, and drops a whole path when any part of it may not be used: a
path never shows that a withheld or revoked node exists. Hop and path limits refuse; they do not truncate silently.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core import cloud_consent
from g6._context_engine import _engine as engine
from g6._context_engine._envelope import BackendReport, SearchRequest, SearchResponse
from g6._context_engine._registry import Dependent, ProjectStore, Refused, may_use
from g6._context_engine._scope import Access, consent_scope
from g6.knowledge._types import sha256_hex
from g6._context_engine._view import label_view

EXTRACTOR = "g6.knowledge._graph/1"
GRAPH = "G6_E_KNOWLEDGE_GRAPH"
RELATIONS = ("contains", "imports")
MAX_HOPS = 6
MAX_PATHS = 200
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: Literal["document", "section", "module", "symbol"]
    registration_id: str
    label: str


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    subject: str
    object: str
    relation: str
    direction: Literal["subject_to_object"] = "subject_to_object"
    passage_id: str
    witness: tuple[int, int]
    witness_sha256: str
    source_version: str
    extractor: str = EXTRACTOR
    status: Literal["asserted", "proposed"] = "asserted"
    certainty: Literal["witnessed", "possible"] = "witnessed"


class Path(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    edges: tuple[Edge, ...]
    endpoints: tuple[str, str]
    status: Literal["asserted", "proposed"]


def node_id(registration_id: str, kind: str, label: str) -> str:
    return "n-" + sha256_hex(f"{registration_id}\x00{kind}\x00{label}")[:32]


def _edge(subject: str, obj: str, relation: str, passage_id: str, witness: tuple[int, int], text: str,
          version: str, **extra: Any) -> Edge:
    body = f"{subject}\x00{obj}\x00{relation}\x00{passage_id}\x00{witness[0]}\x00{witness[1]}"
    return Edge(id="e-" + sha256_hex(body)[:32], subject=subject, object=obj, relation=relation,
                passage_id=passage_id, witness=witness, witness_sha256=sha256_hex(text[witness[0]:witness[1]]),
                source_version=version, **extra)


def _passage_at(member: Any, offset: int) -> str:
    return next((pid for pid, (start, end) in zip(member.passage_ids, member.spans, strict=True)
                 if start <= offset < end), "")


def _imports(text: str) -> tuple[list[tuple[str, int, int]], list[tuple[int, int]]]:
    """The modules a Python text names in import statements, each with the statement's offsets; and the offsets of
    calls that import at run time."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [], []
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))

    def span(node: ast.AST) -> tuple[int, int]:
        return (starts[node.lineno - 1] + node.col_offset,
                starts[(node.end_lineno or node.lineno) - 1] + (node.end_col_offset or 0))

    static, dynamic = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            static += [(alias.name, *span(node)) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            base = node.module or ""
            static += [(f"{base}.{alias.name}" if base else alias.name, *span(node)) for alias in node.names]
            if base:
                static.append((base, *span(node)))
        elif isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")
                or (isinstance(node.func, ast.Name) and node.func.id == "__import__")):
            dynamic.append(span(node))
    return static, dynamic


def build(store: ProjectStore, generation: int, *, clock: Callable[[], datetime] = engine._utc) -> dict[str, Any]:
    """Extract and keep the graph of ``generation``: containment from Markdown headings, imports from Python import
    statements. Returns counts and what it could not read. Only a source that may be used at this time is read (the
    registry's one predicate, with the time); it is asked again before every file and once more before the graph is
    kept, and a source that stopped being usable meanwhile leaves no node and no edge."""
    registrations = {r.id: r for r in store.registrations() if may_use(r, clock()) == ""}
    nodes: dict[str, Node] = {}
    edges: dict[str, Edge] = {}
    unknown = unread = 0
    stopped: set[str] = set()
    members = [m for m in store.members(generation) if m.registration_id in registrations and m.passage_ids]
    modules = {(m.registration_id, m.relative[:-3].replace("/", ".")): m for m in members if m.relative.endswith(".py")}
    for member in members:
        if member.registration_id in stopped:
            continue
        if may_use(store.registration(member.registration_id), clock()):   # a stop stops: asked before every file
            stopped.add(member.registration_id)
            continue
        document, _ = engine._reread(store.binding, registrations[member.registration_id], member)
        if document is None:
            unread += 1
            continue
        text = document.text
        kind = "module" if member.relative.endswith(".py") else "document"
        here = Node(id=node_id(member.registration_id, kind, member.relative), kind=kind,
                    registration_id=member.registration_id, label=member.relative)
        nodes[here.id] = here
        if kind == "document":
            for found in _HEADING.finditer(text):
                passage = _passage_at(member, found.start())
                if not passage:
                    continue
                label = f"{member.relative}#{found.group(2)}"
                section = Node(id=node_id(member.registration_id, "section", f"{label}@{found.start()}"),
                               kind="section", registration_id=member.registration_id, label=label)
                nodes[section.id] = section
                made = _edge(here.id, section.id, "contains", passage, (found.start(), found.end()), text,
                             member.file_sha256)
                edges[made.id] = made
            continue
        static, dynamic = _imports(text)
        for name, start, end in static:
            target = next((modules[(member.registration_id, name[:cut])] for cut in
                           sorted({len(name), *(i for i, ch in enumerate(name) if ch == ".")}, reverse=True)
                           if (member.registration_id, name[:cut]) in modules), None)
            passage = _passage_at(member, start)
            if target is None or not passage or target.uri == member.uri:
                continue
            other = Node(id=node_id(target.registration_id, "module", target.relative), kind="module",
                         registration_id=target.registration_id, label=target.relative)
            nodes[other.id] = other
            made = _edge(here.id, other.id, "imports", passage, (start, end), text, member.file_sha256,
                         certainty="possible")
            edges[made.id] = made
        for start, end in dynamic:
            passage = _passage_at(member, start)
            if passage:
                unknown += 1
                target = Node(id=node_id(member.registration_id, "module", f"{member.relative}:dynamic@{start}"),
                              kind="module", registration_id=member.registration_id,
                              label="(an import built at run time: unknown)")
                nodes[target.id] = target
                made = _edge(here.id, target.id, "imports_unknown", passage, (start, end), text, member.file_sha256,
                             certainty="possible")
                edges[made.id] = made
    stopped |= {rid for rid in registrations if may_use(store.registration(rid), clock())}   # and before it is kept
    nodes = {key: node for key, node in nodes.items() if node.registration_id not in stopped}
    edges = {key: edge for key, edge in edges.items() if edge.subject in nodes and edge.object in nodes}
    store.put_graph(generation, [n.model_dump_json() for n in nodes.values()],
                    [(e.id, e.subject, e.object, e.relation, e.model_dump_json()) for e in edges.values()])
    for registration_id in sorted({n.registration_id for n in nodes.values()}):
        store.depend(Dependent(kind="graph_edge", ident=f"graph-g{generation}", registration_id=registration_id,
                               passage_ids=tuple(sorted({e.passage_id for e in edges.values()
                                                         if nodes[e.subject].registration_id == registration_id}))[:2000],
                               generation=generation))
    return {"generation": generation, "nodes": len(nodes), "edges": len(edges), "dynamic_imports_unknown": unknown,
            "files_not_read": unread, "extractor": EXTRACTOR}


def ensure(store: ProjectStore) -> None:
    """Build the graph of the published generation when none is kept for it (after a new generation, or a
    deletion). The graph is derived data on this machine; what a traversal returns is decided by its own scope."""
    generation = store.published()
    if generation is not None and store.graph(generation.number)[0] is None:
        build(store, generation.number)


def _witness_holds(acc: Access, store: ProjectStore, edge: Edge, members: dict[str, Any], registrations: dict,
                   documents: dict) -> bool:
    member = members.get(edge.passage_id)
    if member is None:
        return False
    if member.uri not in documents:
        documents[member.uri], _ = engine._reread(acc.binding, registrations[member.registration_id], member)
        if documents[member.uri] is None:
            store.changed(member.registration_id, member.uri)
    document = documents[member.uri]
    return document is not None and sha256_hex(document.text[edge.witness[0]:edge.witness[1]]) == edge.witness_sha256


def traverse(acc: Access, start: str, relation: str, *, max_hops: int = 3, max_paths: int = 50,
             clock: Callable[[], datetime] = engine._utc) -> tuple[SearchResponse, tuple[cloud_consent.Source, ...]]:
    """The paths of ``relation`` edges from the node ``start`` names (a file's path inside its source, or a node
    id), as a path payload; see the module docstring for what is checked on every edge and node."""
    if relation not in RELATIONS or type(max_hops) is not int or type(max_paths) is not int \
            or not 1 <= max_hops <= MAX_HOPS or not 1 <= max_paths <= MAX_PATHS:
        raise Refused(GRAPH, f"a traversal follows one of {', '.join(RELATIONS)}, for 1 to {MAX_HOPS} hops and at "
                             f"most {MAX_PATHS} paths")
    store = ProjectStore(acc.binding)
    request = SearchRequest(operation="traverse", query=str(start)[:4000], top_k=1)
    generation = store.published()
    report = BackendReport(backend="graph", route="relations", state="unavailable",
                           reason="not_published: nothing is indexed for this project yet")
    if generation is None:
        return SearchResponse(request=request, generation=None, backends=(report,), payload={"kind": "path", "paths": []}), ()
    raw_nodes, raw_edges = store.graph(generation.number)
    if raw_nodes is None:
        report = report.model_copy(update={"reason": f"no graph is built for generation {generation.number}"})
        return SearchResponse(request=request, generation=generation.number, backends=(report,),
                              payload={"kind": "path", "paths": []}), ()
    nodes = {n.id: n for n in (Node.model_validate_json(raw) for raw in raw_nodes)}
    edges = [Edge.model_validate_json(raw) for raw in raw_edges]
    registrations = {r.id: r for r in store.registrations()}
    at = clock()
    members_list, _ = store.use(acc, [e.passage_id for e in edges], at=at, generation=generation.number)
    members = {pid: m for m in members_list for pid in m.passage_ids}
    readable = set(engine.scope_of(store, acc, at)[0])   # scope first: only these registrations' nodes exist here

    def allowed(node: Node) -> bool:
        return node.registration_id in readable

    documents: dict[str, Any] = {}
    usable = [e for e in edges if e.relation == relation and e.passage_id in members
              and allowed(nodes[e.subject]) and allowed(nodes[e.object])
              and _witness_holds(acc, store, e, members, registrations, documents)]
    out_of: dict[str, list[Edge]] = {}
    for edge in sorted(usable, key=lambda e: e.id):
        out_of.setdefault(edge.subject, []).append(edge)
    starts = sorted(n.id for n in nodes.values() if allowed(n) and (n.id == start or n.label == start))
    paths: list[Path] = []
    refused = ""
    frontier = [(node, ()) for node in starts]
    for _ in range(max_hops):
        grown = []
        for node, taken in frontier:
            for edge in out_of.get(node, []):
                if any(edge.object in (e.subject, e.object) for e in taken) or edge.object == node:
                    continue   # a path does not visit a node twice
                step = (*taken, edge)
                paths.append(Path(edges=step, endpoints=(step[0].subject, edge.object), status="asserted"))
                grown.append((edge.object, step))
        frontier = grown
        if len(paths) > max_paths:
            refused = (f"[{GRAPH}] more than {max_paths} paths: narrow the start or lower max_hops; nothing is "
                       "returned rather than an arbitrary part")
            break
    if not refused and frontier and any(out_of.get(node) for node, _ in frontier):
        truncated = True
    else:
        truncated = False
    _, late = store.use(acc, sorted({e.passage_id for p in paths for e in p.edges}), at=clock(),
                        generation=generation.number)
    gone = {d.passage_id for d in late}
    kept = [] if refused else [p for p in paths if not any(e.passage_id in gone for e in p.edges)]
    sources, shown = {}, []
    for path in kept:
        steps = []
        for edge in path.edges:
            member = members[edge.passage_id]
            sources[edge.id] = cloud_consent.Source(consent_scope(member.registration_id), edge.passage_id,
                                                    edge.witness_sha256)
            steps.append({"subject": label_view(nodes[edge.subject].label), "object": label_view(nodes[edge.object].label),
                          "subject_kind": nodes[edge.subject].kind, "object_kind": nodes[edge.object].kind,
                          "relation": edge.relation, "direction": edge.direction, "passage_id": edge.passage_id,
                          "source": member.registration_id, "witness": list(edge.witness), "status": edge.status,
                          "certainty": edge.certainty,
                          "means": ("a possible dependency: the name appears in an import statement; nothing here "
                                    "says the code runs or that behaviour depends on it") if edge.relation == "imports"
                          else "the heading is in this file"})
        shown.append({"endpoints": [label_view(nodes[path.endpoints[0]].label), label_view(nodes[path.endpoints[1]].label)],
                      "status": path.status, "hops": len(steps), "edges": steps})
    payload: dict[str, Any] = {"kind": "path", "paths": shown, "start_found": bool(starts), "max_hops": max_hops,
                               "more_beyond_max_hops": truncated}
    if refused:
        payload["refused"] = refused
    state = "answered" if shown else "empty"
    report = BackendReport(backend="graph", route="relations", state="refused" if refused else state,
                           reason=refused[:200] if refused else "", searched=len(usable))
    return (SearchResponse(request=request, generation=generation.number, backends=(report,), payload=payload,
                           authorisation_revisions={rid: registrations[rid].authorisation_revision
                                                    for rid in sorted({s["source"] for p in shown for s in p["edges"]})}),
            tuple(sources.values()))


__all__ = ["EXTRACTOR", "GRAPH", "MAX_HOPS", "MAX_PATHS", "RELATIONS", "Edge", "Node", "Path", "build", "ensure", "node_id",
           "traverse"]
