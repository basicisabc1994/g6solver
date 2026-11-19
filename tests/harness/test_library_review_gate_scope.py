"""Serving must abstain outside the registered replacement scope."""
from types import SimpleNamespace

import pytest

from g6.distill import acceptance_gate as g
from g6.distill.records import FeaturePredicate
from g6.harness import library_review_gate as serving
from tests.distill import test_acceptance_repair as repair


@pytest.mark.parametrize("authority,status", [("A1", "probation"), ("A2", "admitted")])
def test_excluded_assertion_removal_abstains_before_run(monkeypatch, authority, status):
    _, candidate, frozen, _ = repair.sample.__wrapped__()
    entry = SimpleNamespace(entry_id="e" * 64, candidate_id=candidate.candidate_id,
        family="test_weakening", authority=authority, status=status, replacement="R",
        replacement_scope=[FeaturePredicate(feature="assertions", op="eq", value=0)],
        evidence=SimpleNamespace(precision=SimpleNamespace(lo95=.99), n_pos=100, n_neg=100))
    monkeypatch.setattr(serving.Library, "list", lambda self: [entry])
    monkeypatch.setattr(serving, "Stores", lambda: SimpleNamespace(for_model=lambda cls:
        SimpleNamespace(get=lambda ident: candidate)))
    monkeypatch.setattr(g, "_run", lambda *args: pytest.fail("excluded replacement executed"))
    case = frozen.acceptance[0].case
    result = serving.check({"tests/test_case.py": case.before_tests}, {"tests/test_case.py": case.after_tests})
    assert len(result) == 1 and result[0]["verdict"] == "abstain"


def test_unconfirmed_replacement_abstains_before_run(monkeypatch):
    _, candidate, frozen, _ = repair.sample.__wrapped__()
    entry = SimpleNamespace(entry_id="e" * 64, candidate_id=candidate.candidate_id,
        family="test_weakening", authority="A1", status="probation", replacement="R",
        replacement_scope=[], evidence=SimpleNamespace(precision=SimpleNamespace(lo95=.99), n_pos=100, n_neg=100))
    monkeypatch.setattr(serving.Library, "list", lambda self: [entry])
    monkeypatch.setattr(serving, "Stores", lambda: SimpleNamespace(for_model=lambda cls:
        SimpleNamespace(get=lambda ident: candidate)))
    monkeypatch.setattr(g, "_replacement_authorized", lambda entry: False)
    monkeypatch.setattr(g, "_run", lambda *args: "concern")
    case = frozen.acceptance[0].case
    result = serving.check({"tests/test_case.py": case.before_tests}, {"tests/test_case.py": case.after_tests})
    assert result[0]["verdict"] == "abstain"
