"""Typed graph data contracts and content identities."""
import importlib
import importlib.util

import pytest
from pydantic import ValidationError

from g6.core.ledger import digest

D = digest("verdict-schema")


def records():
    assert importlib.util.find_spec("g6.distill.graph_records"), "graph records are missing"
    return importlib.import_module("g6.distill.graph_records")


def primitive(**changes):
    r = records()
    body = dict(kind="code", impl_ref="fixture.check", impl_version=digest("v1"),
                input_schema=D, output_schema=D, state_access="none", side_effect="read",
                pre=[], post=[], assumptions=[], scope=[], budget=r.CostVector(wall_s=5, cpu_s=5),
                stop=r.StopRule(timeout_s=5, max_iterations=None, abstain_on=[]),
                determinism="deterministic", contract_sha256=None, entry_id=None, teacher=None)
    return r.Primitive(**(body | changes))


def graph_for(primitives, *, combinator="vote", k=1, lowering="native", output_type="verdict"):
    r = records()
    nodes = [r.Node(node_id=f"n{i}", primitive_id=p.primitive_id, combinator=None, budget=p.budget)
             for i, p in enumerate(primitives)]
    edges = []
    if combinator:
        nodes.append(r.Node(node_id="root", primitive_id=None, combinator=combinator, k=k,
                            budget=r.CostVector()))
        edges = [r.Edge(src=n.node_id, src_port="out", dst="root", dst_port=str(i), type_sha256=D)
                 for i, n in enumerate(nodes[:-1])]
    budget = r.CostVector()
    for p in primitives:
        budget = budget + p.budget
    return r.ProgramGraph(output_type=output_type, nodes=nodes, edges=edges,
                          root="root" if combinator else "n0", budget=budget,
                          max_effect="read", lowering=lowering, parents=[])


def test_schema_roundtrip_extra_fields_and_content_identity():
    r = records()
    p = primitive()
    g = graph_for([p])
    for obj in (p, g):
        assert type(obj).model_validate_json(obj.model_dump_json()) == obj
        assert obj.content_id == getattr(obj, obj.identity_field)
        with pytest.raises(ValidationError):
            type(obj).model_validate(obj.model_dump() | {"surprise": True})
        with pytest.raises(ValidationError):
            type(obj).model_validate(obj.model_dump() | {obj.identity_field: digest("wrong")})
    assert primitive(impl_version=digest("v2")).primitive_id != p.primitive_id
    with pytest.raises(ValidationError):
        r.CostVector(cpu_s=-1)
    with pytest.raises(ValidationError):
        r.CostVector(llm_calls=True)
    with pytest.raises(ValidationError):
        r.CostVector(wall_s=float("nan"))


@pytest.mark.parametrize("fault", ["duplicate", "cycle", "missing", "disconnected", "threshold", "budget"])
def test_graph_admission_rejects_bad_structure(fault):
    r = records()
    body = graph_for([primitive()]).model_dump(exclude={"graph_id"})
    if fault == "duplicate":
        body["nodes"].append(body["nodes"][0])
    elif fault == "cycle":
        body["edges"].append(dict(src="root", src_port="out", dst="n0", dst_port="in", type_sha256=D))
    elif fault == "missing":
        body["edges"][0]["src"] = "absent"
    elif fault == "disconnected":
        body["edges"] = []
    elif fault == "threshold":
        body["nodes"][-1]["k"] = 2
    else:
        body["budget"]["wall_s"] = 0
    with pytest.raises(ValidationError):
        r.ProgramGraph(**body)


def test_node_requires_one_kind_and_bounded_loops():
    r = records()
    for body in [dict(primitive_id=None, combinator=None),
                 dict(primitive_id=D, combinator="seq"),
                 dict(primitive_id=None, combinator="loop")]:
        with pytest.raises(ValidationError):
            r.Node(node_id="x", budget=r.CostVector(), **body)


def test_feedback_external_label_requires_receipt():
    r = records()
    body = dict(run_id=D, internal={"note": "self reading"}, external_label=None, label_receipt=None,
                critical_nodes=[], failure_ref=None, kind="node_failed")
    f = r.FeedbackRecord(**body)
    assert r.FeedbackRecord.model_validate_json(f.model_dump_json()) == f
    with pytest.raises(ValidationError):
        r.FeedbackRecord(**(body | {"external_label": "positive"}))


def test_execution_rows_refuse_inconsistent_totals_and_duplicate_nodes():
    from tests.distill.test_graph import api, run
    a, r = api(), records()
    p = primitive()
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(
        output="concern", cost=r.CostVector(cpu_s=0.5))})
    body = rec.model_dump(exclude={"run_id"})
    with pytest.raises(ValidationError):
        r.ExecutionRecord(**(body | {"cost": r.CostVector()}))
    with pytest.raises(ValidationError):
        r.ExecutionRecord(**(body | {"runs": [*body["runs"], body["runs"][0]]}))


def test_attempt_rows_refuse_send_and_execution_cost_mismatches():
    from tests.distill.test_graph import api, run
    a, r = api(), records()
    p = primitive()
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(
        output="concern", cost=r.CostVector(cpu_s=0.5))})
    attempt = rec.attempts[0].model_dump(exclude={"attempt_id"})
    with pytest.raises(ValidationError, match="measured model sends"):
        r.AttemptRecord(**(attempt | {"model_sends": 1}))
    attempt["cost"] = r.CostVector(cpu_ns=1)
    with pytest.raises(ValidationError, match="attempt totals"):
        r.ExecutionRecord(**(rec.model_dump(exclude={"run_id"}) | {"attempts": [attempt]}))
