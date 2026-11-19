"""Reproducible shadow selection and maintenance accounting."""
import importlib
import importlib.util

import pytest

from g6.core.ledger import digest
from g6.core.llm import FakeLLM


def api():
    assert importlib.util.find_spec("g6.distill.shadow_audit"), "P4 shadow audit is missing"
    return importlib.import_module("g6.distill.shadow_audit")


def test_shadow_sample_is_five_percent_and_seeded():
    a = api()
    ids = [digest(str(i)) for i in range(10000)]
    first = [i for i in ids if a.shadow_selected(i, seed=41)]
    assert first == [i for i in ids if a.shadow_selected(i, seed=41)]
    assert first != [i for i in ids if a.shadow_selected(i, seed=42)]
    assert 0.04 <= len(first) / len(ids) <= 0.06
    assert a.SHADOW_RATE == 0.05
    for i in ids[:100]:
        assert a.shadow_selected(i, seed=41) == (int(digest(["shadow-audit.v1", 41, i]), 16) * 20 < 2**256)


def test_shadow_disagreements_and_calls_are_maintenance_only():
    a = api()
    from g6.distill.cost_ledger import CostLedger
    ledger = CostLedger()
    chosen, record = selected_record()
    audit = a.ShadowAudit(seed=7, ledger=ledger, teacher_graph=record.graph_id, teacher_node="n0")
    caller = FakeLLM(default="no_concern")
    def teacher(*, caller):
        caller.complete("", "")
        return record
    assert audit.run(chosen, program_verdict="concern", admitted_replacement=False,
                     in_scope=True, teacher=teacher) is None
    assert audit.run(chosen, program_verdict="abstain", admitted_replacement=True,
                     in_scope=True, teacher=teacher) is None
    assert audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                     in_scope=False, teacher=teacher) is None
    row = audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                    in_scope=True, teacher=teacher, caller=caller)
    assert row.disagrees and row.program_verdict == "concern" and row.teacher_verdict == "no_concern"
    assert audit.disagreements == [row]
    assert ledger.total("maintenance").llm_calls == 1
    assert ledger.total("acquisition").llm_calls == 0


def test_failed_shadow_call_is_still_costed():
    a = api()
    from g6.distill.cost_ledger import CostLedger
    ledger = CostLedger()
    chosen, record = selected_record()
    audit = a.ShadowAudit(seed=7, ledger=ledger, teacher_graph=record.graph_id, teacher_node="n0")
    def fail(system, user):
        raise RuntimeError("teacher failed")
    caller = FakeLLM(responder=fail)
    with pytest.raises(RuntimeError):
        audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                  in_scope=True, teacher=lambda caller: caller.complete("", ""), caller=caller)
    assert ledger.attempts[0].outcome == "failed"
    assert ledger.total("maintenance").llm_calls == 1 and not audit.records


@pytest.mark.parametrize("field,value", [("seed", 8), ("rate", 1.0), ("teacher_graph", digest("changed")),
                                       ("teacher_node", "other")])
def test_registered_shadow_settings_cannot_be_reassigned(field, value):
    from g6.distill.cost_ledger import CostLedger
    audit = api().ShadowAudit(seed=7, ledger=CostLedger())
    with pytest.raises(AttributeError):
        setattr(audit, field, value)


def test_shadow_record_for_another_unit_is_refused_and_costed():
    from g6.distill.cost_ledger import CostLedger
    from .test_distill_operator import case, trace
    c, _ = case(ident="another-unit")
    record, _ = trace(c)
    ledger = CostLedger()
    audit = api().ShadowAudit(seed=7, ledger=ledger, teacher_graph=record.graph_id, teacher_node="n0")
    chosen = next(digest(str(i)) for i in range(1000) if api().shadow_selected(digest(str(i)), seed=7))
    with pytest.raises(ValueError, match="unit|case"):
        audit.run(chosen, program_verdict="concern", admitted_replacement=True,
            in_scope=True, teacher=lambda **_: record)
    assert not audit.records
    assert ledger.attempts[0].outcome == "failed"


@pytest.mark.parametrize("binding", ["matching", "different", "missing"])
def test_shadow_execution_requires_the_bound_teacher_graph(binding):
    from g6.distill.cost_ledger import CostLedger
    from g6.distill.distill_operator import DistillationCase
    from g6.distill.records import FailureRecord
    from .test_distill_operator import case, trace
    chosen = next(digest(str(i)) for i in range(1000) if api().shadow_selected(digest(str(i)), seed=7))
    c, _ = case()
    c = DistillationCase(**(c.__dict__ | {"failure": FailureRecord(**(
        c.failure.model_dump(exclude={"record_id"}) | {"case_ref": chosen}))}))
    record, _ = trace(c)
    ledger = CostLedger()
    options = {} if binding == "missing" else {
        "teacher_graph": record.graph_id if binding == "matching" else digest("other-graph")}
    audit = api().ShadowAudit(seed=7, ledger=ledger, teacher_node="n0", **options)
    caller = FakeLLM(default="concern")

    def teacher(*, caller):
        caller.complete("", "")
        return record

    if binding == "matching":
        row = audit.run(chosen, program_verdict="concern", admitted_replacement=True,
            in_scope=True, teacher=teacher, caller=caller)
        assert row is not None and not row.disagrees
    else:
        with pytest.raises(ValueError, match="graph|bound"):
            audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                in_scope=True, teacher=teacher, caller=caller)
        assert not audit.records
    assert ledger.total("maintenance").wall_s > 0
    assert ledger.total("maintenance").llm_calls == (0 if binding == "missing" else 1)


def selected_record():
    from g6.distill.graph_records import ExecutionRecord
    from .test_distill_operator import case, trace
    chosen = next(digest(str(i)) for i in range(1000) if api().shadow_selected(digest(str(i)), seed=7))
    c, _ = case()
    record, _ = trace(c, verdict="no_concern")
    record = ExecutionRecord(**(record.model_dump(exclude={"run_id"}) | {"unit_ref": chosen}))
    return chosen, record


def test_threaded_shadow_send_is_costed_at_injected_boundary():
    from threading import Thread
    from g6.distill.cost_ledger import CostLedger
    chosen, record = selected_record()
    ledger = CostLedger()
    audit = api().ShadowAudit(seed=7, ledger=ledger, teacher_graph=record.graph_id, teacher_node="n0")
    def teacher(*, caller):
        worker = Thread(target=lambda: caller.complete("teacher", "input"))
        worker.start()
        worker.join()
        return record
    row = audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                    in_scope=True, teacher=teacher, caller=FakeLLM())
    assert row.disagrees
    assert ledger.total("maintenance").llm_calls == 1
    assert ledger.total("maintenance").llm_tokens_in > 0


def test_shadow_rejects_verdict_only_result_from_other_execution():
    from g6.distill.cost_ledger import CostLedger
    from .test_distill_operator import case, trace
    chosen, bound = selected_record()
    other, _ = case(ident="other")
    record, _ = trace(other)
    audit = api().ShadowAudit(seed=7, ledger=CostLedger(), teacher_graph=bound.graph_id, teacher_node="n0")
    with pytest.raises(ValueError, match="execution|record"):
        audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                  in_scope=True, teacher=lambda **_: record.root_output, caller=FakeLLM())
    assert not audit.records


def test_shadow_compares_registered_node_rather_than_vote_root():
    from g6.distill.cost_ledger import CostLedger
    from g6.distill.graph import execute
    from .test_distill_operator import teacher_graph
    from .test_graph_records import graph_for
    chosen, _ = selected_record()
    _, p, config = teacher_graph()
    graph = graph_for([p, p], combinator="vote", k=1)
    caller = FakeLLM(default="no_concern")
    def teacher(*, caller):
        result = execute(graph, {}, primitives={p.primitive_id: p}, unit_ref=chosen, split="dev",
            request_id="shadow-vote", g6_commit="fixture", caller=caller,
            llm_configs={p.primitive_id: config}, cache=None)
        assert result.root_output == "concern"
        assert result.runs[0].node_id == "n0" and result.runs[0].output == "no_concern"
        return result
    replies = iter(["no_concern", "concern"])
    caller = FakeLLM(responder=lambda *_: next(replies))
    audit = api().ShadowAudit(seed=7, ledger=CostLedger(), teacher_graph=graph.graph_id, teacher_node="n0")
    row = audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                   in_scope=True, teacher=teacher, caller=caller)
    assert row.disagrees and row.teacher_verdict == "no_concern"


@pytest.mark.parametrize("fault", ["case", "graph", "node", "output_digest"])
def test_shadow_revalidates_execution_and_teacher_node(fault):
    from g6.distill.cost_ledger import CostLedger
    from g6.distill.graph_records import ExecutionRecord
    chosen, record = selected_record()
    graph_id = record.graph_id
    if fault in {"case", "graph"}:
        record = ExecutionRecord(**(record.model_dump(exclude={"run_id"}) | {
            "unit_ref" if fault == "case" else "graph_id": digest("different")}))
    elif fault == "output_digest":
        record = record.model_copy(deep=True)
        object.__setattr__(record.runs[0], "output", "concern")
    ledger = CostLedger()
    audit = api().ShadowAudit(seed=7, ledger=ledger, teacher_graph=graph_id,
                             teacher_node="missing" if fault == "node" else "n0")
    with pytest.raises(ValueError):
        audit.run(chosen, program_verdict="concern", admitted_replacement=True, in_scope=True,
                  teacher=lambda **_: record)
    assert not audit.records
    assert ledger.attempts[0].outcome == "failed"


def test_review3_mismatched_graph_keeps_cpu_probe_sandbox_costs():
    from g6.distill.cost_ledger import CostLedger
    from g6.distill.graph import KernelResult, execute
    from g6.distill.graph_records import CostVector, ExecutionRecord
    from .test_graph_records import graph_for, primitive
    chosen, original = selected_record()
    cost = CostVector(cpu_s=7, probe_runs=3, sandbox_runs=2)
    p = primitive(budget=CostVector(cpu_s=10, wall_s=5, probe_runs=3, sandbox_runs=2))
    def teacher(**_):
        result = execute(graph_for([p]), {}, primitives={p.primitive_id: p}, unit_ref=chosen, split="dev",
            request_id="mismatch", g6_commit="fixture", kernels={p.impl_ref: lambda *a, **kw:
                KernelResult(output="concern", cost=cost)})
        return ExecutionRecord(**(result.model_dump(exclude={"run_id"}) | {"graph_id": digest("other graph")}))
    ledger = CostLedger()
    audit = api().ShadowAudit(seed=7, ledger=ledger, teacher_graph=original.graph_id, teacher_node="n0")
    with pytest.raises(ValueError, match="graph"):
        audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                  in_scope=True, teacher=teacher)
    total = ledger.total("maintenance")
    assert total.cpu_s >= 7 and total.probe_runs == 3 and total.sandbox_runs == 2


def resource_teacher_graph():
    from g6.distill.graph_records import CostVector
    from .test_distill_operator import teacher_graph
    from .test_graph_records import graph_for, primitive
    _, llm, config = teacher_graph()
    probe = primitive(budget=CostVector(probe_runs=3, wall_s=5, cpu_s=5))
    return graph_for([probe, llm], combinator="seq", k=None), probe, llm, config


def test_review4_runner_exception_keeps_three_probes_and_one_maintenance_send():
    from g6.distill.cost_ledger import CostLedger
    from g6.distill.graph import KernelResult, execute
    from g6.distill.graph_records import CostVector
    chosen, _ = selected_record()
    graph, probe, llm, config = resource_teacher_graph()
    ledger = CostLedger()
    audit = api().ShadowAudit(seed=7, ledger=ledger, teacher_graph=graph.graph_id, teacher_node="n1")
    def teacher(*, caller):
        execute(graph, {}, primitives={p.primitive_id: p for p in (probe, llm)},
            unit_ref=chosen, split="dev", request_id="raise-after", g6_commit="fixture", caller=caller,
            llm_configs={llm.primitive_id: config}, kernels={probe.impl_ref: lambda *a, **kw:
                KernelResult(output="concern", cost=CostVector(probe_runs=3))})
        raise RuntimeError("runner failed after graph")
    with pytest.raises(RuntimeError, match="after graph"):
        audit.run(chosen, program_verdict="concern", admitted_replacement=True,
            in_scope=True, teacher=teacher, caller=FakeLLM(default="concern"))
    total = ledger.total("maintenance")
    assert total.probe_runs == 3 and total.llm_calls == 1
    assert ledger.total("acquisition").llm_calls == 0


def test_review4_early_acquisition_booking_refused_and_horizon_stays_100_calls():
    from g6.distill.cost_ledger import CostLedger
    from g6.distill.graph import execute
    from .test_distill_operator import teacher_graph
    chosen, _ = selected_record()
    graph, p, config = teacher_graph()
    ledger = CostLedger()
    audit = api().ShadowAudit(seed=7, ledger=ledger, teacher_graph=graph.graph_id, teacher_node="n0")
    def teacher(*, caller):
        rec = execute(graph, {}, primitives={p.primitive_id: p}, unit_ref=chosen, split="dev",
            request_id="early", g6_commit="fixture", caller=caller, llm_configs={p.primitive_id: config})
        with pytest.raises(ValueError, match="category|ownership"):
            ledger.book_attempt(rec.attempts[0])
        return rec
    audit.run(chosen, program_verdict="concern", admitted_replacement=True,
        in_scope=True, teacher=teacher, caller=FakeLLM(default="concern"))
    assert ledger.total("maintenance").llm_calls == 1
    assert ledger.total("acquisition").llm_calls == 0
    assert ledger.profile(deployment_tasks=10).total().llm_calls == 100
