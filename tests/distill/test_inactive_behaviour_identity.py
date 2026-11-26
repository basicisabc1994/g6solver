"""Adversarial inactive-method identity regressions."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from g6.core.ledger import ChainLedger, digest
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.library import Library
from g6.distillation.sandbox import check_source
from g6.distillation.sandbox import Run
from tests.distill.test_records import bodies

BASE = "def solve(facts):\n return {'verdict': 'concern' if facts else 'no_concern'}"


def candidate(source):
    return r.VerifierCandidate(**(bodies()[3][1] | {'body': source}))


def install(monkeypatch, first, status):
    entry = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': first.candidate_id, 'status': status, 'authority': 'A0'}))
    rows = {r.VerifierCandidate: [first], r.LibraryEntry: [entry]}
    def for_model(model):
        return SimpleNamespace(list=lambda: rows[model], get=lambda ident: next(
            row for row in rows[model] if row.content_id == ident))
    monkeypatch.setattr(gate, 'Stores', lambda: SimpleNamespace(for_model=for_model))
    monkeypatch.setattr(Library, 'list', lambda self: [entry])
    monkeypatch.setattr(gate, '_writer', nullcontext)
    monkeypatch.setattr(gate, '_journal', ChainLedger)
    monkeypatch.setattr(gate, '_policy', lambda: None)
    monkeypatch.setattr(gate.stop, 'engaged', lambda: None)
    # The revised gate needs the recorded case and confined-run result before
    # registration loading. Keep the seven reviewer assertions unchanged.
    monkeypatch.setenv('G6_REQUIRE_ISOLATION', '1')
    monkeypatch.setattr(gate, '_recorded_case', lambda ident: SimpleNamespace(
        case_id=ident, before_tests='assert 1', after_tests='pass'), raising=False)
    monkeypatch.setattr(gate, 'run_batch', lambda source, values, **kwargs: [
        Run('ok', value=run(source, facts)) for facts in values])


def run(source, facts):
    assert check_source(source) == []
    namespace = {}
    exec(source, namespace)
    return namespace['solve'](facts)


@pytest.mark.parametrize('status', ['withdrawn', 'held'])
@pytest.mark.parametrize('rewrite', [
    "def solve(data):\n return {'verdict': 'concern' if data else 'no_concern'}",
    "def solve(facts):\n return {'verdict': 'concern' if facts else 'no_concern'}\n"
    "solve = lambda facts: {'verdict': 'concern' if facts else 'no_concern'}",
    "@(lambda fn: fn)\ndef solve(facts):\n"
    " return {'verdict': 'concern' if facts else 'no_concern'}",
], ids=['rename', 'lambda', 'decorator'])
def test_inactive_equivalent_rewrite_never_reaches_evaluation(monkeypatch, status, rewrite):
    assert all(run(BASE, facts) == run(rewrite, facts) for facts in ({}, {'x': 1}))
    first, other = candidate(BASE), candidate(rewrite)
    install(monkeypatch, first, status)
    reached = []
    monkeypatch.setattr(gate, '_look_available', lambda *args: None)
    def load(*args):
        reached.append(other.candidate_id)
        raise ValueError('test stops before evaluation work')
    monkeypatch.setattr(gate, '_load', load)
    gate.decide(other, Library(), digest('registration'))
    assert reached == [], f'{status} equivalent predicate reached _load'


def test_behavior_changing_recursive_default_is_not_blocked(monkeypatch):
    source = ("def solve(facts='concern'):\n"
              " if isinstance(facts, dict):\n  return solve()\n"
              " return {'verdict': facts}")
    other_source = source.replace("facts='concern'", "facts='no_concern'")
    assert run(source, {}) == {'verdict': 'concern'}
    assert run(other_source, {}) == {'verdict': 'no_concern'}
    first, other = candidate(source), candidate(other_source)
    install(monkeypatch, first, 'withdrawn')
    reached = []
    monkeypatch.setattr(gate, '_look_available', lambda *args: None)
    def load(*args):
        reached.append(other.candidate_id)
        raise ValueError('test stops before evaluation work')
    monkeypatch.setattr(gate, '_load', load)
    answer = gate.decide(other, Library(), digest('registration'))
    assert reached == [other.candidate_id], answer.reasons
