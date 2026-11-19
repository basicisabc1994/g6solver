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
