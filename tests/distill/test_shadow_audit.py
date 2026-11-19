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
    audit = a.ShadowAudit(seed=7, ledger=ledger)
    chosen = next(digest(str(i)) for i in range(1000) if a.shadow_selected(digest(str(i)), seed=7))
    caller = FakeLLM(default="no_concern")
    def teacher():
        return caller.complete("", "")
    assert audit.run(chosen, program_verdict="concern", admitted_replacement=False,
                     in_scope=True, teacher=teacher) is None
    assert audit.run(chosen, program_verdict="abstain", admitted_replacement=True,
                     in_scope=True, teacher=teacher) is None
    assert audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                     in_scope=False, teacher=teacher) is None
    row = audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                    in_scope=True, teacher=teacher)
    assert row.disagrees and row.program_verdict == "concern" and row.teacher_verdict == "no_concern"
    assert audit.disagreements == [row]
    assert ledger.total("maintenance").llm_calls == 1
    assert ledger.total("acquisition").llm_calls == 0


def test_failed_shadow_call_is_still_costed():
    a = api()
    from g6.distill.cost_ledger import CostLedger
    ledger = CostLedger()
    audit = a.ShadowAudit(seed=3, ledger=ledger)
    chosen = next(digest(str(i)) for i in range(1000) if a.shadow_selected(digest(str(i)), seed=3))
    def fail(system, user):
        raise RuntimeError("teacher failed")
    caller = FakeLLM(responder=fail)
    with pytest.raises(RuntimeError):
        audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                  in_scope=True, teacher=lambda: caller.complete("", ""))
    assert ledger.attempts[0].outcome == "failed"
    assert ledger.total("maintenance").llm_calls == 1 and not audit.records


@pytest.mark.parametrize("field,value", [("seed", 8), ("rate", 1.0), ("teacher_graph", digest("changed"))])
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
    audit = api().ShadowAudit(seed=7, ledger=ledger, teacher_graph=record.graph_id)
    chosen = next(digest(str(i)) for i in range(1000) if api().shadow_selected(digest(str(i)), seed=7))
    with pytest.raises(ValueError, match="unit|case"):
        audit.run(chosen, program_verdict="concern", admitted_replacement=True,
            in_scope=True, teacher=lambda: record)
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
    audit = api().ShadowAudit(seed=7, ledger=ledger, **options)
    caller = FakeLLM(default="concern")

    def teacher():
        caller.complete("", "")
        return record

    if binding == "matching":
        row = audit.run(chosen, program_verdict="concern", admitted_replacement=True,
            in_scope=True, teacher=teacher)
        assert row is not None and not row.disagrees
    else:
        with pytest.raises(ValueError, match="graph|bound"):
            audit.run(chosen, program_verdict="concern", admitted_replacement=True,
                in_scope=True, teacher=teacher)
        assert not audit.records
    assert ledger.total("maintenance").wall_s > 0
    assert ledger.total("maintenance").llm_calls == 1
