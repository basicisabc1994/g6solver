"""All patch readings constrain support; standard publication shares the answer lock."""
import ast
from contextlib import contextmanager
from pathlib import Path

import pytest

from g6.core.ledger import digest
from g6.harness import confirmed_checks, requirement_evidence
from g6.harness import question_store as qs
from g6.harness.confirmed_expectation import ExpectationStandard
from g6.mcp import behaviour

from .helpers import Dialog, REQUIREMENTS, TASK, ask, candidate, repo, result, standard, stated


@pytest.mark.parametrize("reading", [None, {}, {"result": "unfamiliar"}, {"result": "error"},
                                    {"result": "passed", "collection": True, "verdict": "collection_error"}])
def test_partial_or_unrecognised_patch_readings_cannot_aggregate_to_support(reading):
    aggregate, _detail = confirmed_checks.patch_result([{"result": "passed"}, reading])
    assert aggregate == confirmed_checks.NOT_RUN


def test_a_genuine_failure_still_decides_when_another_run_is_unavailable():
    assert confirmed_checks.patch_result([{"result": "failed", "detail": "case failed"}, None])[0] == "failed"


def test_adapter_counts_no_pass_from_a_partial_confirmed_result():
    held = standard()
    evidence = requirement_evidence.confirmed_evidence(held, [{
        "bound": True, "killed": True, "result": "not_run", "detail": "partial run",
        "runs": {"patch": {"result": "passed"}, "base": {"result": "failed"},
                 "again": {"result": "passed"}}}], artifact="a" * 64, base_commit="base", environment={})
    assert not evidence["expectations"][0]["checked"]


@pytest.mark.parametrize("damage", ["supersede", "unreadable"])
def test_standard_is_read_again_at_the_issuer_boundary(tmp_path, monkeypatch, damage):
    from g6.harness import patch_review
    from g6.harness import verification_search as vs

    root = repo(tmp_path)
    assert ask(root, dialog=Dialog({"choice": "A", "name": "Maintainer", "further": "one_more"}))["status"] == "confirmed"
    stated(monkeypatch)
    monkeypatch.setattr(vs, "_run_check", lambda change, placed, source, node, *, at_base, **k:
                        result(node + "::test_example", "failed" if at_base else "passed"))
    original = ExpectationStandard.facts
    changed = False

    def last_moment(self, mission):
        nonlocal changed
        if not changed:
            changed = True
            if damage == "supersede":
                assert ask(root, data=[candidate(scope="replacement confirmation of this case")],
                           dialog=Dialog({"choice": "B", "name": "Maintainer"}))["status"] == "confirmed"
            else:
                qs.TaskQuestions(qs.task_identity(root, TASK)).path.write_bytes(b"unreadable")
        return original(self, mission)

    monkeypatch.setattr(ExpectationStandard, "facts", last_moment)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    assert reply["ok"] is False and "assessment" not in reply
    assert reply["warning_card"]["code"] in ("G6_E_QUESTION_STANDARD_SUPERSEDED", qs.UNREADABLE)


def test_restart_after_answer_append_before_head_rejects_the_old_standard(tmp_path, monkeypatch):
    from g6.harness import standard_publication as publication

    root = repo(tmp_path)
    assert ask(root, dialog=Dialog({"choice": "A", "name": "Maintainer", "further": "one_more"}))["status"] == "confirmed"
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    previous = store.state().standard()
    original = qs.TaskQuestions._append

    def append_then_stop(self, ledger, kind, ident, body):
        if kind == qs.ANSWER:
            ledger.append(kind, ident, {**body, "task": self.identity})
            raise RuntimeError("interrupted before head update")
        return original(self, ledger, kind, ident, body)

    with monkeypatch.context() as local:
        local.setattr(qs.TaskQuestions, "_append", append_then_stop)
        with pytest.raises(RuntimeError, match="before head update"):
            ask(root, data=[candidate(scope="replacement confirmation of this case")],
                dialog=Dialog({"choice": "B", "name": "Maintainer"}))
    with pytest.raises(publication.StandardSuperseded):
        with store.counting(previous):
            pytest.fail("the old standard reached publication after restart")
    assert store.state().standard().expectations[0].option == "B"


def test_issuer_call_is_inside_the_current_standard_lock(tmp_path, monkeypatch):
    from g6.harness import standard_publication as publication

    root = repo(tmp_path)
    assert ask(root)["status"] == "confirmed"
    store = qs.TaskQuestions(qs.task_identity(root, TASK))
    held = store.state().standard()
    locking, observed = [], []
    original_lock, original_issue = qs._locked, requirement_evidence.issue_records

    @contextmanager
    def watch(path):
        with original_lock(path):
            locking.append(path)
            try:
                yield
            finally:
                locking.pop()

    def issue(*args):
        observed.append(tuple(locking))
        return original_issue(*args)

    monkeypatch.setattr(qs, "_locked", watch)
    monkeypatch.setattr(requirement_evidence, "issue_records", issue)
    with publication.observing(store.counting):
        requirement_evidence.confirmed_evidence(held, [{"bound": False, "result": "not_run", "runs": {}}],
                                                artifact=digest({}), base_commit="base", environment={})
    assert observed == [(store.path,)]
    assert locking == []


def test_publication_context_does_not_leak_after_an_interruption():
    from g6.harness import standard_publication as publication

    calls = []

    @contextmanager
    def current(held):
        calls.append(held)
        yield

    with pytest.raises(RuntimeError, match="interruption"):
        with publication.observing(current):
            with publication.counting("first"):
                raise RuntimeError("interruption")
    with publication.counting("second"):
        pass
    assert calls == ["first"]


def test_inventory_every_confirmed_publication_and_run_is_guarded():
    root = Path(__file__).resolve().parents[2] / "src" / "g6"
    tree = ast.parse((root / "harness/requirement_evidence.py").read_text(encoding="utf-8"))
    confirmed = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "confirmed_evidence")
    issuers = [n for n in ast.walk(confirmed) if isinstance(n, ast.Call) and ast.unparse(n.func) == "issue_records"]
    guarded = [n for guard in ast.walk(confirmed) if isinstance(guard, ast.With)
               and any(ast.unparse(item.context_expr) == "standard_publication.counting(standard)"
                       for item in guard.items)
               for n in ast.walk(guard) if n in issuers]
    assert len(issuers) == 1 and guarded == issuers
    run = ast.parse((root / "harness/confirmed_checks.py").read_text(encoding="utf-8"))
    method = next(n for n in ast.walk(run) if isinstance(n, ast.FunctionDef) and n.name == "run")
    aggregators = [n for n in ast.walk(method) if isinstance(n, ast.Call)
                   and ast.unparse(n.func) == "patch_result"]
    assert len(aggregators) == 1 and ast.unparse(aggregators[0].args[0]) == "on_the_patch"
    store = ast.parse((root / "harness/question_store.py").read_text(encoding="utf-8"))
    counting = next(n for n in ast.walk(store) if isinstance(n, ast.FunctionDef) and n.name == "counting")
    assert "with _locked(self.path):" in ast.unparse(counting)
    assert "self._open()" in ast.unparse(counting) and "self._read(" in ast.unparse(counting)
