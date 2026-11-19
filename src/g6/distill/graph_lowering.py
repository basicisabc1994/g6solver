# SPDX-License-Identifier: Apache-2.0
"""Verdict-graph lowering to the existing Composition interface."""
from __future__ import annotations

from .graph_records import Primitive, ProgramGraph
from .records import Composition, LibraryEntry


class LoweringRefused(ValueError):
    pass


def validated(graph, primitives):
    """Reparse content identities and check the single-schema primitive ports."""
    g = ProgramGraph.model_validate_json(graph.model_dump_json())
    ps = {}
    for n in g.nodes:
        if n.primitive_id is None:
            continue
        if n.primitive_id not in primitives:
            raise LoweringRefused("missing primitive")
        p = Primitive.model_validate_json(primitives[n.primitive_id].model_dump_json())
        if p.primitive_id != n.primitive_id:
            raise LoweringRefused("primitive identity differs from node")
        effects = ("read", "local_write", "external", "irreversible")
        if effects.index(p.side_effect) > effects.index(g.max_effect):
            raise LoweringRefused("primitive effect exceeds graph allowance")
        if not p.budget.within(n.budget):
            raise LoweringRefused("primitive bounds exceed node reservation")
        ps[n.primitive_id] = p
    nodes = {n.node_id: n for n in g.nodes}
    for e in g.edges:
        src, dst = nodes[e.src], nodes[e.dst]
        if src.primitive_id and ps[src.primitive_id].output_schema != e.type_sha256:
            raise LoweringRefused("edge output type mismatch")
        if dst.primitive_id and ps[dst.primitive_id].input_schema != e.type_sha256:
            raise LoweringRefused("edge input type mismatch")
    # Pure combinators preserve the port type; implicit conversions are refused.
    for n in g.nodes:
        if n.combinator:
            types = {e.type_sha256 for e in g.edges if n.node_id in (e.src, e.dst)}
            if len(types) > 1:
                raise LoweringRefused("combinator port type mismatch")
    return g, ps


def lower(graph, primitives, *, entries=None):
    """Lower verdict graphs to Composition; broad adapters are deferred in v3.1."""
    g, ps = validated(graph, primitives)
    deferred = {"method_plan": "MethodPlan", "compose": "compose", "procedure": "ProcedureSpec"}
    if g.lowering in deferred:
        raise NotImplementedError(f"{deferred[g.lowering]} lowering is deferred until after M1")
    if g.lowering != "composition":
        raise LoweringRefused("only Composition is a lowering target; native graphs execute directly")
    nodes = {n.node_id: n for n in g.nodes}
    root = nodes[g.root]
    if g.lowering == "composition":
        if g.output_type != "verdict" or root.combinator not in {"vote", "cascade"}:
            raise LoweringRefused("composition needs a verdict Vote or Cascade root")
        children = [nodes[e.src] for e in g.edges if e.dst == root.node_id]
        if len(children) + 1 != len(nodes) or any(n.combinator for n in children):
            raise LoweringRefused("composition requires direct entry leaves")
        ids = []
        for n in children:
            p = ps[n.primitive_id]
            if p.scope or p.pre or p.state_access != "none" or p.side_effect != "read":
                raise LoweringRefused("composition cannot carry extra scope, preconditions or effects")
            if p.kind in {"llm", "probe"} or not p.entry_id or not entries or p.entry_id not in entries:
                raise LoweringRefused("composition leaf requires an admitted entry")
            entry = LibraryEntry.model_validate_json(entries[p.entry_id].model_dump_json())
            if (entry.entry_id != p.entry_id or entry.status != "admitted"
                    or entry.contract_sha256 != p.contract_sha256):
                raise LoweringRefused("entry identity, status or contract differs")
            ids.append(p.entry_id)
        if root.combinator == "cascade":
            return Composition(op="sequence", entries=ids)
        if root.k == 1:
            return Composition(op="any", entries=ids)
        if root.k == len(ids):
            return Composition(op="all", entries=ids)
        return Composition(op="k_of_n", entries=ids, k=root.k)
    raise LoweringRefused("unsupported lowering")
