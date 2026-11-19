# SPDX-License-Identifier: Apache-2.0
"""Typed program graphs and execution data; construction confers no authority."""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_serializer, model_validator

from .records import (Count, FeaturePredicate, Finite, Interval, Model, Positive, Prediction,
                      Proposer, Record, Sha256, Split)

Kind = Literal["code", "template", "predicate", "probe", "llm", "solver", "g6_component", "subgraph"]
Effect = Literal["read", "local_write", "external", "irreversible"]
Terminal = Literal["ok", "fail", "timeout", "abstain", "refused"]
Nonnegative = Annotated[Finite, Field(ge=0)]


class CostVector(Model):
    llm_calls: Count = 0
    llm_tokens_in: Count = 0
    llm_tokens_out: Count = 0
    cpu_s: Nonnegative = 0
    wall_s: Nonnegative = 0
    solver_calls: Count = 0
    probe_runs: Count = 0
    sandbox_runs: Count = 0

    @classmethod
    def zero(cls) -> CostVector:
        return cls()

    def __add__(self, other: CostVector) -> CostVector:
        return CostVector(**{name: getattr(self, name) + getattr(other, name) for name in type(self).model_fields})

    def within(self, limit: CostVector) -> bool:
        return all(getattr(self, name) <= getattr(limit, name) for name in type(self).model_fields)


class StopRule(Model):
    timeout_s: Positive
    max_iterations: Count | None = None
    abstain_on: list[Terminal] = Field(default_factory=list)


class Primitive(Record):
    identity_field = "primitive_id"
    primitive_id: Sha256 | None = None
    kind: Kind
    impl_ref: Annotated[str, Field(min_length=1)]
    impl_version: Sha256
    input_schema: Sha256
    output_schema: Sha256
    state_access: Literal["none", "read", "local_write"]
    side_effect: Effect
    pre: list[FeaturePredicate]
    post: list[str]
    assumptions: list[str]
    scope: list[FeaturePredicate]
    budget: CostVector
    stop: StopRule
    determinism: Literal["deterministic", "seeded", "stochastic"]
    contract_sha256: Sha256 | None
    entry_id: Sha256 | None
    teacher: Sha256 | None


class Node(Model):
    node_id: Annotated[str, Field(min_length=1)]
    primitive_id: Sha256 | None
    combinator: Literal["seq", "par", "branch", "cascade", "vote", "guard", "loop", "cegis"] | None
    k: Count | None = None
    guard: list[FeaturePredicate] = Field(default_factory=list)
    loop_bound: Count | None = None
    budget: CostVector

    @model_validator(mode="after")
    def shape(self) -> Self:
        if (self.primitive_id is None) == (self.combinator is None):
            raise ValueError("node requires exactly one primitive or combinator")
        if self.combinator in {"loop", "cegis"} and not self.loop_bound:
            raise ValueError("iteration combinator requires a positive loop_bound")
        if self.combinator not in {"loop", "cegis"} and self.loop_bound is not None:
            raise ValueError("loop_bound only applies to iteration combinators")
        if self.combinator != "vote" and self.k is not None:
            raise ValueError("k only applies to vote")
        return self


class Edge(Model):
    src: str
    src_port: Annotated[str, Field(min_length=1)]
    dst: str
    dst_port: Annotated[str, Field(min_length=1)]
    type_sha256: Sha256


class ProgramGraph(Record):
    identity_field = "graph_id"
    graph_id: Sha256 | None = None
    output_type: Literal["verdict", "value", "patch", "plan"]
    nodes: list[Node]
    edges: list[Edge]
    root: str
    budget: CostVector
    max_effect: Effect
    lowering: Literal["composition", "method_plan", "compose", "procedure", "native"]
    parents: list[Sha256]

    @model_validator(mode="after")
    def admissible(self) -> Self:
        nodes = {n.node_id: n for n in self.nodes}
        if len(nodes) != len(self.nodes) or self.root not in nodes:
            raise ValueError("node ids must be unique and include root")
        inputs = {n: [] for n in nodes}
        ports = set()
        for e in self.edges:
            if e.src not in nodes or e.dst not in nodes or (e.dst, e.dst_port) in ports:
                raise ValueError("edge endpoint missing or input port repeated")
            ports.add((e.dst, e.dst_port))
            inputs[e.dst].append(e.src)
        # Iteration is encapsulated inside bounded nodes, never a back edge.
        visiting, done = set(), set()
        def visit(n):
            if n in visiting:
                raise ValueError("graph must be acyclic")
            if n in done:
                return
            visiting.add(n)
            for child in inputs[n]:
                visit(child)
            visiting.remove(n)
            done.add(n)
        visit(self.root)
        if len(done) != len(nodes):
            raise ValueError("every node must contribute to root")
        total = CostVector()
        for n in self.nodes:
            total = total + n.budget
            if n.combinator and not inputs[n.node_id]:
                raise ValueError("combinator requires children")
            if n.combinator == "vote" and (n.k is None or not 1 <= n.k <= len(inputs[n.node_id])):
                raise ValueError("vote threshold out of range")
            if n.combinator == "guard" and len(inputs[n.node_id]) != 1:
                raise ValueError("guard requires one child")
        if not total.within(self.budget):
            raise ValueError("node budgets exceed graph budget")
        return self


class NodeRun(Model):
    node_id: str
    input_sha256: Sha256
    output_sha256: Sha256
    output: JsonValue
    terminal: Terminal
    cost: CostVector
    trace_ref: Sha256 | None
    replayable: bool

    @model_validator(mode="after")
    def output_digest(self) -> Self:
        from g6.core.ledger import digest
        if digest(self.output) != self.output_sha256:
            raise ValueError("output digest differs from output")
        if self.terminal in {"fail", "timeout", "refused"} and self.output is not None:
            raise ValueError("unsuccessful run cannot carry an output")
        return self


class AttemptRecord(Record):
    identity_field = "attempt_id"
    attempt_id: Sha256 | None = None
    what: str
    cost: CostVector
    model_sends: Count
    send_ids: list[Sha256] = Field(default_factory=list)
    execution_ids: list[Sha256] = Field(default_factory=list)
    outcome: Literal["proposed", "screened", "concept_refused", "no_candidate", "refused_unbound_label",
                     "failed", "budget_exhausted", "unmetered_send"]
    update_id: Sha256 | None
    started_at: str
    ended_at: str

    @model_serializer(mode="wrap")
    def resource_references(self, serialize):
        # Empty references retain the canonical body of older graph attempts.
        body = serialize(self)
        for name in ("send_ids", "execution_ids"):
            if not body.get(name):
                body.pop(name, None)
        return body

    @model_validator(mode="after")
    def sends_match(self) -> Self:
        if self.model_sends != self.cost.llm_calls:
            raise ValueError("attempt cost differs from measured model sends")
        return self


class ExecutionRecord(Record):
    identity_field = "run_id"
    run_id: Sha256 | None = None
    graph_id: Sha256
    unit_ref: Sha256
    split: Split
    request_id: str
    g6_commit: str
    model_ids: dict[str, str]
    seed: int
    runs: list[NodeRun]
    attempts: list[AttemptRecord] = Field(default_factory=list)
    root_output: JsonValue
    terminal: Terminal
    cost: CostVector
    completion_state: Literal["verified", "qualified-draft", "blocked-escalated"]
    started_at: str
    ended_at: str

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if not self.runs or len({r.node_id for r in self.runs}) != len(self.runs):
            raise ValueError("execution needs unique node runs")
        total = CostVector()
        for run in self.runs:
            total = total + run.cost
        if total != self.cost:
            raise ValueError("execution cost differs from node run totals")
        if self.attempts:
            measured = CostVector()
            for attempt in self.attempts:
                measured = measured + attempt.cost
            if measured != self.cost:
                raise ValueError("execution cost differs from attempt totals")
        if self.terminal in {"fail", "timeout", "refused"} and self.root_output is not None:
            raise ValueError("unsuccessful execution cannot carry a root output")
        return self


class FeedbackRecord(Record):
    identity_field = "feedback_id"
    feedback_id: Sha256 | None = None
    run_id: Sha256
    internal: dict[str, JsonValue]
    external_label: Literal["positive", "negative", "excluded"] | None
    label_receipt: Sha256 | None
    critical_nodes: list[str]
    failure_ref: Sha256 | None
    kind: Literal["missed_defect", "false_alarm", "recurring_llm_step", "budget_exhausted", "node_failed", "conflict"]

    @model_validator(mode="after")
    def receipt(self) -> Self:
        if (self.external_label is None) != (self.label_receipt is None):
            raise ValueError("external label and receipt must be supplied together")
        return self


class DistillationDetail(Model):
    teacher: Sha256
    mode: Literal["replace", "add"]
    scope: list[FeaturePredicate]
    traces_used: list[Sha256]
    imitative_cases: Count
    corrective_cases: Count
    coverage_dev: Finite
    calls_saved_per_task_dev: Finite


class ProposedUpdate(Record):
    identity_field = "update_id"
    update_id: Sha256 | None = None
    op: Literal["distill", "insert", "delete", "recombine", "rescope", "prompt", "route", "subgraph", "extract"]
    base_graph: Sha256
    new_graph: Sha256
    candidate_id: Sha256 | None
    distillation: DistillationDetail | None
    mechanism: str
    falsifier: str
    prediction: Prediction
    acquisition: CostVector
    dev_screen: dict[str, JsonValue]
    proposer: Proposer


class SavingsEstimate(Model):
    coverage: Interval
    calls_saved_per_task: Finite
    delta_cost: CostVector
    validation: CostVector
    maintenance_per_task: CostVector
    break_even_tasks: Finite | None


class AdmissionDecision(Record):
    identity_field = "decision_id"
    decision_id: Sha256 | None = None
    update_id: Sha256
    outcome: Literal["admit_probation", "admit_scoped", "refuse", "inconclusive", "block"]
    reasons: list[str]
    report_sha256: Sha256
    policy_sha256: Sha256
    split_sha256: Sha256
    registration_sha256: Sha256
    reuse_index: Count
    p_value: Finite
    minimum_detectable_gain: Finite
    concern_authority: Literal["A0", "A1", "A2"]
    replacement: Literal["none", "R"]
    replacement_scope: list[FeaturePredicate]
    noninferiority: dict[str, Interval] | None
    savings: SavingsEstimate | None
    gate_version: str
    decided_at: str
