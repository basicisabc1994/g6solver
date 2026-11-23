"""Repository check provenance never overrides a rejected outcome."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import semantic
from g6.harness import confirmed_checks, outcome_history, patch_review as pr, resolution
from g6.harness import verification_search as vs
from g6.harness.confirmed_expectation import ExpectationStandard, Question, confirm


@pytest.mark.parametrize("route", ["stated", "extra", "derived"])
@pytest.mark.parametrize("result", ["passed", "failed", "collection_error"])
def test_every_repository_route_lacks_authority_under_rejection(monkeypatch, route, result):
    root = Path.cwd()
    rel = "tests/harness/test_repository_rejection_design.py"
    node = rel + "::test_rejected_fixture"
    question = Question(requirement_id="R1", function="calc.add", args=[2, 3],
                        outcomes={"A": {"returns": 5}, "B": {"returns": -1}}, scope="this case",
                        cited="Repair addition.", baseline_sha256="b" * 64)
    standard = ExpectationStandard().with_expectation(confirm(
        question, "A", answered_by="person:Fixture", answered_at="2026-11-19T00:00:00Z"))
    change = SimpleNamespace(repo=root, root=root, base="HEAD", base_commit="b" * 40,
                             files=["calc.py"], sources=["calc.py"], submodule_changes=[],
                             submodules=[], unseen=[], outside_links=[])
    rt = pr.repo_tests
    for name, value in (
        ("checked_repo", lambda value: root), ("snapshot", lambda *a, **k: {}),
        ("change", lambda *a, **k: change), ("test_related", lambda *a, **k: []),
        ("related_texts", lambda *a, **k: ({}, {})), ("loadable_changes", lambda *a, **k: {}),
        ("ignored_inputs", lambda *a, **k: SimpleNamespace(loadable=[], other=[], complete=True,
                                                          named_like_modules=[])),
        ("derive_tests", lambda *a, **k: ([rel], 0) if route == "derived" else ([], 0)),
        ("diff_text", lambda *a, **k: ""), ("shadowed", lambda *a, **k: {}),
    ):
        monkeypatch.setattr(rt, name, value)
    for name in ("_import_roots", "_runner_changes", "_test_data", "_patch_imports_missing", "_at_base"):
        monkeypatch.setattr(pr, name, lambda *a, **k: [])
    monkeypatch.setattr(pr, "_check_shas", lambda *a, **k: {})
    monkeypatch.setattr(pr, "_run_again", lambda *a, **k: {})
    monkeypatch.setattr(pr, "_closing", lambda *a, **k: ({}, [], {}))
    monkeypatch.setattr(pr, "_gate", lambda *a, **k: {"receipts": [], "findings": []})
    monkeypatch.setattr(outcome_history, "retained_checks", lambda *a, **k: ([], {}, []))
    monkeypatch.setattr(outcome_history, "check_sources", lambda *a, **k: {node: "assert add(2, 3) == -1"})
    monkeypatch.setattr(vs, "bind_execution", lambda change, before, **k: before)
    monkeypatch.setattr(confirmed_checks, "run", lambda *a, **k: [{
        "expectation_id": standard.expectations[0].expectation_id, "result": "passed"}])
    monkeypatch.setattr(resolution, "build", lambda *a, **k: {})
    learned = []
    monkeypatch.setattr(outcome_history, "record_stated", lambda *a, **k: learned.append(True))
    monkeypatch.setattr(outcome_history, "read", lambda root: outcome_history.History())
    monkeypatch.setattr(outcome_history, "disclose", lambda reply, root: reply)

    def execute(*args, **kwargs):
        report = {"verdict": result, "baseline": "passed", "patched": result,
                "failed": int(result == "failed"), "passed": int(result == "passed"), "seconds": .01,
                "said": "", "output": "", "rootdir": "", "collected_ids": [node], "omitted_ids": [],
                "failed_ids": [node] if result == "failed" else [], "setup_errors": {},
                "collection_errors": [rel] if result == "collection_error" else [],
                "failure_messages": {node: "assert 5 == -1"} if result == "failed" else {},
                "test_outcomes": {node: {"setup": "passed", "call": result, "teardown": "passed"}},
                "loaded_outside": {}, "loaded_complete": True}
        return {rel: report} if kwargs.get("change") is not None else report

    monkeypatch.setattr(pr, "_execute", execute)
    with semantic.request(llm=None):
        reply = pr.review_patch(root, task="Repair addition.", confirmed=standard,
                                requirements=[{"id": "R1", "text": "Repair addition.",
                                               "checks": [node] if route == "stated" else []}],
                                tests=[node] if route == "extra" else [])
    assert reply["ok"], reply
    assessment = reply["assessment"]
    assert not assessment["completion"]["defects_reproduced"]["stated_and_repository_checks"]
    assert assessment["outcome"] == pr.INSUFFICIENT
    assert not learned
    assert all(req["status"] != pr.PASSED_ON_CHECKS for req in assessment["requirements"])
    assert any(reason["code"] == "rejection_unreadable" for reason in assessment["reasons"])
