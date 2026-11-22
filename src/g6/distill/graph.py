# SPDX-License-Identifier: Apache-2.0
"""Bounded graph execution with injected callers and kernels, plus structural credit.

Kernels are trusted caller bindings, never dynamically imported from graph data.
They must honour timeout_s and provide complete non-time resource accounting.
Late synchronous returns are discarded; this module cannot interrupt arbitrary Python.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from pydantic import JsonValue, TypeAdapter

from g6.core.ledger import canonical, digest
from g6.core.llm import LLMError
from g6.core.model_calls import counting

from .execution import ExecutionRefused, Observation, run_candidate
from .graph_lowering import LoweringRefused, lower, validated
from .graph_records import AttemptRecord, CostVector, ExecutionRecord, Model, NodeRun, Terminal

VERDICTS = ("concern", "abstain", "no_concern")
ERRORS = ("refused", "timeout", "fail")


class LLMConfig(Model):
    model_id: str
    system: str
    max_tokens: int = 2048
    sampling: dict[str, JsonValue] = {}

    @property
    def content_id(self):
        return digest(self.model_dump(mode="json"))


@dataclass(frozen=True)
class KernelResult:
    output: JsonValue = None
    terminal: Terminal = "ok"
    cost: CostVector = field(default_factory=CostVector)
    trace: JsonValue = None

    def __post_init__(self):
        TypeAdapter(Terminal).validate_python(self.terminal)
        TypeAdapter(JsonValue).validate_python(self.output)
        TypeAdapter(JsonValue).validate_python(self.trace)
        if not isinstance(self.cost, CostVector):
            raise TypeError("kernel cost must be CostVector")
        CostVector.model_validate_json(self.cost.model_dump_json())


@dataclass
class _Attempt:
    cost: CostVector = field(default_factory=CostVector)
    outcome: str = "screened"
    record: AttemptRecord | None = None


@contextmanager
def attempt_meter(what, *, update_id=None):
    """Retain measured time and noted sends on success, refusal and exception."""
    meter = _Attempt()
    started_at = datetime.now(timezone.utc).isoformat()
    wall_start, cpu_start = time.monotonic(), time.process_time()
    with counting() as sends:
        try:
            yield meter
        except BaseException:
            meter.outcome = "failed"
            raise
        finally:
            measured = meter.cost.model_dump()
            measured["llm_calls"] = sends()
            measured["wall_s"] = max(0, time.monotonic() - wall_start)
            measured["cpu_s"] = max(0, time.process_time() - cpu_start)
            meter.record = AttemptRecord(what=what, cost=CostVector(**measured), model_sends=sends(),
                                         outcome=meter.outcome, update_id=update_id,
                                         started_at=started_at, ended_at=datetime.now(timezone.utc).isoformat())


class ReplayCache:
    """Process-local replay data; callers own the lifetime of each snapshot."""
    def __init__(self):
        self._rows = {}

    def __len__(self):
        return len(self._rows)

    def get(self, key):
        raw = self._rows.get(key)
        row = NodeRun.model_validate_json(raw) if raw is not None else None
        return row if row is not None and row.cost.complete and row.terminal == "ok" and row.replayable else None

    def put(self, key, row):
        if row.terminal == "ok" and row.replayable and row.cost.complete:
            self._rows[key] = row.model_dump_json()


def _json_input(value):
    if isinstance(value, Observation):
        return dict(before=dict(value.before), after=dict(value.after), facts=value.facts,
                    language=value.language, framework=value.framework)
    return TypeAdapter(JsonValue).validate_python(value)


def _matches(predicates, facts):
    for p in predicates:
        if p.feature not in facts:
            return False
        value, other = facts[p.feature], p.value
        try:
            if not {"eq": lambda: value == other, "ne": lambda: value != other,
                    "gt": lambda: value > other, "ge": lambda: value >= other,
                    "lt": lambda: value < other, "le": lambda: value <= other,
                    "contains": lambda: other in value}[p.op]():
                return False
        except (TypeError, ValueError):
            return False
    return True


def _combine(node, values):
    if node.combinator == "cascade":
        return next((v for v in values if v != "abstain"), "abstain")
    if node.combinator == "vote":
        concerns, abstains = values.count("concern"), values.count("abstain")
        return "concern" if concerns >= node.k else "abstain" if concerns + abstains >= node.k else "no_concern"
    if node.combinator == "guard":
        return values[0]
    if node.combinator == "seq":
        return values[-1]
    if node.combinator == "par":
        return values
    raise LoweringRefused("combinator has no native execution adapter")


def _llm(p, inputs, *, caller, config, timeout_s):
    if (caller is None or config is None or config.max_tokens < 1 or
            p.impl_ref != f"model:{config.model_id}" or p.impl_version != config.content_id):
        raise LoweringRefused("caller and matching model/prompt/sampling binding required")
    user = canonical(inputs)
    # UTF-8 bytes reserve text input capacity; they are not observed tokens.
    tokens_in = len(config.system.encode("utf-8")) + len(user.encode("utf-8"))
    if (not p.budget.complete or p.budget.llm_calls < 1 or tokens_in > p.budget.llm_tokens_in
            or config.max_tokens > p.budget.llm_tokens_out):
        raise LoweringRefused("LLM token or call reservation is insufficient")
    started, attempts = time.monotonic(), 0
    def before_call():
        nonlocal attempts
        if attempts or time.monotonic() - started >= timeout_s:
            raise LoweringRefused("extra or late LLM send refused")
        attempts += 1
    result = KernelResult()
    try:
        with counting(before_call=before_call) as sends:
            reply = caller.complete(config.system, user, max_tokens=config.max_tokens)
        if isinstance(reply, KernelResult):
            # Only an injected trusted adapter can supply actual non-time use.
            result = reply
            if result.terminal in ERRORS:
                return replace(result, output=None)
            reply = result.output
        attempts = sends()
        if attempts != 1:
            raise LoweringRefused("caller must report its send through the call counter")
        output = reply if reply in VERDICTS else json.loads(reply)
        result = KernelResult(output=output, terminal=result.terminal, cost=result.cost,
                              trace={"model_id": config.model_id, "reply": reply, "config": config.content_id})
    except (LoweringRefused, ExecutionRefused):
        result = replace(result, output=None, terminal="refused")
    except TimeoutError:
        result = replace(result, output=None, terminal="timeout")
    except LLMError:
        result = replace(result, output=None, terminal="fail")
    except Exception:
        result = replace(result, output=None, terminal="fail")
    return result


def execute(graph, x, *, primitives, unit_ref, split, request_id, g6_commit, seed=0,
            caller=None, llm_configs=None, kernels=None, resolve=None, entries=None,
            budget=None, cache=None, store=None):
    """Execute one unit; a refusal, failure or timeout never supplies a verdict.

    Edges flow from producers to consumers; edge order is child order. Only
    started nodes have rows. Cascade and Guard deliberately leave unused nodes out.
    All non-read effects and state writes are outside this executor's authority.
    """
    from .records import Split
    if split not in ("training", "dev", "monitor", "field"):
        raise ExecutionRefused("G6_E_GRAPH_SPLIT", f"split {split!r} is refused by the graph executor")
    TypeAdapter(Split).validate_python(split)
    started_at = datetime.now(timezone.utc).isoformat()
    data = _json_input(x)
    facts = x.facts if isinstance(x, Observation) else data if isinstance(data, dict) else {}
    runs, fingerprints, attempts = {}, {}, []
    spent = CostVector.zero()
    llm_configs, kernels = llm_configs or {}, kernels or {}
    cache = cache if cache is not None else ReplayCache()
    limit = budget if budget is not None else graph.budget
    admission_error = False
    try:
        g, ps = validated(graph, primitives)
        if not limit.complete:
            raise LoweringRefused("runtime resource limits contain unknown dimensions")
        if any(p.side_effect != "read" or p.state_access == "local_write" for p in ps.values()):
            raise LoweringRefused("native executor permits read effects only")
        if any(n.combinator in {"branch", "loop", "cegis"} for n in g.nodes):
            raise LoweringRefused("bounded iteration/branch kernel adapter is not available")
        if g.lowering == "composition":
            lower(g, ps, entries=entries)
        elif g.lowering != "native":
            raise LoweringRefused("execution target is deferred until after M1")
    except LoweringRefused:
        g, ps, admission_error = graph, {}, True
    nodes = {n.node_id: n for n in g.nodes}
    incoming = {n.node_id: [e for e in g.edges if e.dst == n.node_id] for n in g.nodes}

    def row(n, inp, result, *, replayable=False):
        output = result.output if result.terminal not in ERRORS else None
        terminal = result.terminal
        if g.output_type == "verdict" and terminal == "abstain":
            output = "abstain"
        if g.output_type == "verdict" and terminal == "ok":
            if output not in VERDICTS:
                terminal, output = "fail", None
            elif output == "abstain":
                terminal = "abstain"
        trace_ref = None
        if result.trace is not None and store is not None and hasattr(store, "put_trace"):
            trace_ref = store.put_trace(result.trace)
            if trace_ref != digest(result.trace):
                raise ValueError("trace store returned a different content id")
        value = NodeRun(node_id=n.node_id, input_sha256=digest(inp), output_sha256=digest(output),
                        output=output, terminal=terminal, cost=result.cost,
                        trace_ref=trace_ref,
                        replayable=replayable and terminal == "ok")
        runs[n.node_id] = value
        return value

    def visit(key):
        nonlocal spent
        if key in runs:
            return runs[key]
        n, edges = nodes[key], incoming[key]
        if admission_error:
            for child in edges:
                visit(child.src)
            return row(n, data, KernelResult(terminal="refused", cost=CostVector.zero()))
        if n.combinator == "guard" and not _matches(n.guard, facts):
            fingerprints[key] = digest([n.model_dump(mode="json"), data])
            return row(n, data, KernelResult(output="abstain", terminal="abstain", cost=CostVector.zero()))
        children = []
        for e in edges:
            child = visit(e.src)
            children.append(child)
            if child.terminal in ERRORS:
                break
            if n.combinator == "cascade" and child.output != "abstain":
                break
            if n.combinator == "vote":
                concerns = sum(c.output == "concern" for c in children)
                abstains = sum(c.output == "abstain" for c in children)
                remaining = len(edges) - len(children)
                if (concerns >= n.k or concerns + abstains + remaining < n.k
                        or concerns + remaining < n.k <= concerns + abstains):
                    break
        failure = next((c for c in children if c.terminal in ERRORS), None)
        inp = {"unit": data, "ports": {e.dst_port: c.output for e, c in zip(edges, children)}}
        fingerprints[key] = digest([n.model_dump(mode="json"), inp,
                                    [fingerprints.get(c.node_id) for c in children]])
        if failure:
            return row(n, inp, KernelResult(terminal=failure.terminal, cost=CostVector.zero()))
        if n.combinator:
            if not (spent + n.budget).within(limit):
                return row(n, inp, KernelResult(terminal="refused", cost=CostVector.zero()))
            try:
                out = _combine(n, [c.output for c in children])
                return row(n, inp, KernelResult(output=out, cost=CostVector.zero()))
            except Exception:
                return row(n, inp, KernelResult(terminal="fail", cost=CostVector.zero()))
        p = ps[n.primitive_id]
        if not _matches(p.pre + p.scope, facts):
            return row(n, inp, KernelResult(output="abstain", terminal="abstain", cost=CostVector.zero()))
        if not spent.within(limit):
            return row(n, inp, KernelResult(terminal="refused", cost=CostVector.zero()))
        config = llm_configs.get(p.primitive_id)
        cache_key = digest([p.model_dump(mode="json"), fingerprints[key], seed,
                            config.model_dump(mode="json") if config is not None else None])
        reusable = (p.determinism in {"deterministic", "seeded"} or p.kind == "llm") and p.state_access == "none"
        cached = cache.get(cache_key) if reusable else None
        if cached is not None:
            return row(n, inp, KernelResult(output=cached.output, terminal=cached.terminal,
                       cost=CostVector.zero(), trace={"replay": cache_key}), replayable=True)
        if not (spent + n.budget).within(limit):
            return row(n, inp, KernelResult(terminal="refused", cost=CostVector.zero()))
        with attempt_meter(n.node_id) as meter:
            try:
                timeout_s = min(p.stop.timeout_s, n.budget.wall_s, limit.wall_s - spent.wall_s)
                if timeout_s <= 0:
                    raise LoweringRefused("no remaining wall time")
                if p.kind == "llm":
                    result = _llm(p, inp, caller=caller, config=config, timeout_s=timeout_s)
                    if g.output_type == "verdict" and isinstance(result.output, dict):
                        result = replace(result, output=result.output.get("verdict"))
                elif p.entry_id is not None:
                    if resolve is None or p.entry_id is None or not isinstance(x, Observation):
                        raise LoweringRefused("checking kernel requires entry resolver and Observation")
                    candidate = resolve(p.entry_id)
                    if candidate.contract.content_id != p.contract_sha256:
                        raise LoweringRefused("resolved candidate contract differs")
                    if entries is not None and p.entry_id in entries:
                        if candidate.candidate_id != entries[p.entry_id].candidate_id:
                            raise LoweringRefused("resolved candidate differs from admitted entry")
                    out = run_candidate(candidate, [x], resolve)[0]
                    result = KernelResult(output=out)
                elif p.kind in {"code", "solver", "g6_component"} and p.impl_ref in kernels:
                    result = kernels[p.impl_ref](p, inp, seed=seed, timeout_s=timeout_s)
                    if not isinstance(result, KernelResult):
                        raise ValueError("kernel must return KernelResult")
                else:
                    raise LoweringRefused("no confined or trusted kernel binding for primitive")
            except (LoweringRefused, ExecutionRefused):
                result = KernelResult(terminal="refused")
            except TimeoutError:
                result = KernelResult(terminal="timeout")
            except Exception:
                result = KernelResult(terminal="fail")
            meter.cost = result.cost
            if result.terminal in ERRORS:
                meter.outcome = "failed"
        cost = meter.record.cost
        spent = spent + cost
        comparisons = (cost.comparison(p.budget), cost.comparison(n.budget), spent.comparison(limit))
        if "exceeded" in comparisons:
            result = replace(result, output=None, terminal="refused")
            meter.outcome = "budget_exhausted"
        elif cost.wall_s > p.stop.timeout_s:
            result = replace(result, output=None, terminal="timeout")
            meter.outcome = "failed"
        elif "unknown" in comparisons:
            if result.terminal not in ERRORS:
                result = replace(result, output=None, terminal="refused")
            meter.outcome = "failed"
        result = replace(result, cost=cost)
        value = row(n, inp, result, replayable=reusable)
        if value.terminal in ERRORS and meter.outcome == "screened":
            meter.outcome = "failed"
        attempts.append(AttemptRecord(**(meter.record.model_dump(exclude={"attempt_id"}) |
                                         {"outcome": meter.outcome})))
        cache.put(cache_key, value)
        return value

    with counting() as sends:
        root = visit(g.root)
    if spent.llm_calls != sends():
        raise ExecutionRefused("G6_E_GRAPH_METER", "booked model calls differ from measured sends")
    rec = ExecutionRecord(graph_id=g.graph_id, unit_ref=unit_ref, split=split,
                          request_id=request_id, g6_commit=g6_commit,
                          model_ids={key: config.model_id for key, config in llm_configs.items()}, seed=seed,
                          runs=list(runs.values()), attempts=attempts,
                          root_output=root.output, terminal=root.terminal, cost=spent,
                          completion_state="blocked-escalated" if root.terminal in ERRORS else "qualified-draft",
                          started_at=started_at, ended_at=datetime.now(timezone.utc).isoformat())
    if store is not None:
        store.put(rec)
    return rec


def _combine_masks(node, values, all_bits):
    """Combine all single-leaf substitutions together as concern/abstain bit sets."""
    if node.combinator == "cascade":
        concerns, pending = 0, all_bits
        for concern, abstain in values:
            concerns |= pending & concern
            pending &= abstain
        return concerns, pending
    def at_least(masks):
        counts = [all_bits] + [0] * node.k
        for i, mask in enumerate(masks):
            for j in range(min(node.k, i + 1), 0, -1):
                counts[j] |= counts[j - 1] & mask
        return counts[node.k]
    concerns = at_least([c for c, a in values])
    possible = at_least([c | a for c, a in values])
    return concerns, possible & ~concerns


def structural_blame(graph, leaf_verdicts):
    """A0 single-leaf sensitivity for recorded Vote/Cascade trees or DAGs.

    Every leaf verdict must be supplied, including any skipped by lazy execution.
    Missing counterfactual inputs are refused rather than guessed.
    """
    from .graph_records import ProgramGraph
    g = ProgramGraph.model_validate_json(graph.model_dump_json())
    nodes = {n.node_id: n for n in g.nodes}
    leaves = [n.node_id for n in g.nodes if n.primitive_id]
    if (g.output_type != "verdict" or set(leaf_verdicts) != set(leaves)
            or any(v not in VERDICTS for v in leaf_verdicts.values())
            or any(n.combinator not in {None, "vote", "cascade"} for n in g.nodes)
            or any(e.dst in leaves for e in g.edges)):
        raise LoweringRefused("structural credit requires complete verdict leaves and Vote/Cascade nodes")
    # Bit zero is the observed run. Three bits per leaf encode its substitutions.
    all_bits = (1 << (1 + 3 * len(leaves))) - 1
    memo = {}
    for i, leaf in enumerate(leaves):
        start = 1 + 3 * i
        other = all_bits & ~(7 << start)
        value = leaf_verdicts[leaf]
        memo[leaf] = ((other if value == "concern" else 0) | (1 << start),
                      (other if value == "abstain" else 0) | (1 << (start + 1)))
    def visit(key):
        if key not in memo:
            memo[key] = _combine_masks(nodes[key], [visit(e.src) for e in g.edges if e.dst == key], all_bits)
        return memo[key]
    concern, abstain = visit(g.root)
    same = concern if concern & 1 else abstain if abstain & 1 else all_bits & ~(concern | abstain)
    return [leaf for i, leaf in enumerate(leaves) if (same >> (1 + 3 * i)) & 7 != 7]
