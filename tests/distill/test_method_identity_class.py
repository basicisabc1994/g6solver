# SPDX-License-Identifier: Apache-2.0
"""Inactive identity is invariant under every supported descriptive rewrite."""
from contextlib import nullcontext
from itertools import product
from types import SimpleNamespace

import pytest

from g6.core.ledger import ChainLedger, digest
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.library import Library
from tests.distill.test_records import bodies

BASE = "def solve(facts):\n return {'verdict': 'concern' if facts else 'no_concern', 'reason': 'same'}"


def rewritten(flags):
    source = BASE
    if flags[0]:
        source = source.replace("{'verdict': 'concern' if facts else 'no_concern', 'reason': 'same'}",
                                "{'reason': 'same', 'verdict': 'concern' if facts else 'no_concern'}")
    if flags[1]:
        source = source.replace('solve(facts)', 'solve(facts=None)')
    if flags[2]:
        source = source.replace(
            "):\n return", "):\n 'method documentation'\n return")
    if flags[3]:
        source = 'def identity(fn):\n return fn\n@identity\n' + source
    if flags[4]:
        source = source.replace("'concern'", "('con' + 'cern')")
    return source


def memory(monkeypatch, status):
    first = r.VerifierCandidate(**(bodies()[3][1] | {'body': BASE}))
    entry = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': first.candidate_id, 'status': status, 'authority': 'A0'}))
    rows = {r.VerifierCandidate: [first], r.LibraryEntry: [entry]}
    def for_model(model):
        return SimpleNamespace(list=lambda: rows[model], get=lambda ident: next(
            value for value in rows[model] if value.content_id == ident))
    monkeypatch.setattr(gate, 'Stores', lambda: SimpleNamespace(for_model=for_model))
    monkeypatch.setattr(Library, 'list', lambda self: [entry])
    monkeypatch.setattr(gate, '_writer', nullcontext)
    monkeypatch.setattr(gate, '_journal', ChainLedger)
    monkeypatch.setattr(gate, '_policy', lambda: None)
    monkeypatch.setattr(gate.stop, 'engaged', lambda: None)
    return first, entry


@pytest.mark.parametrize('status', ['withdrawn', 'held'])
@pytest.mark.parametrize('flags', list(product((False, True), repeat=5)))
def test_all_rewrite_combinations_stop_before_evaluation(monkeypatch, status, flags):
    first, _ = memory(monkeypatch, status)
    other = r.VerifierCandidate(**(bodies()[3][1] | {
        'body': rewritten(flags), 'concept_id': digest('another observation')}))
    assert gate._method_identity(first) == gate._method_identity(other)
    monkeypatch.setattr(gate, '_look_available', lambda *args: pytest.fail('inactive method spent a look'))
    monkeypatch.setattr(gate, '_load', lambda *args: pytest.fail('inactive method reached evaluation'))
    assert gate.decide(other, Library(), digest('registration')).verdict == 'block'


@pytest.mark.parametrize('source', [
    BASE.replace("'concern' if facts else 'no_concern'", "'no_concern' if facts else 'concern'"),
    "def solve(facts):\n return {'verdict': 'abstain', 'reason': 'same'}",
    "def change(fn):\n return lambda facts: {'verdict': 'abstain', 'reason': 'same'}\n@change\n" + BASE,
])
@pytest.mark.parametrize('status', ['withdrawn', 'held'])
def test_different_execution_reaches_admission_boundary(monkeypatch, status, source):
    first, _ = memory(monkeypatch, status)
    other = r.VerifierCandidate(**(bodies()[3][1] | {'body': source}))
    assert gate._method_identity(first) != gate._method_identity(other)
    reached = []
    monkeypatch.setattr(gate, '_look_available', lambda *args: None)
    def load(*args):
        reached.append(other.candidate_id)
        raise ValueError('test stops at admission custody')
    monkeypatch.setattr(gate, '_load', load)
    gate.decide(other, Library(), digest('registration'))
    assert reached == [other.candidate_id]


@pytest.mark.parametrize('left,right', [
    ("{'a': facts.pop(), 'b': facts.pop()}", "{'b': facts.pop(), 'a': facts.pop()}"),
    ("{'a': 1, 'a': 2}", "{'a': 2, 'a': 1}"),
])
def test_order_sensitive_dictionary_expressions_stay_distinct(left, right):
    def candidate(body):
        return r.VerifierCandidate(**(bodies()[3][1] | {'body': 'def solve(facts):\n return ' + body}))
    assert gate._method_identity(candidate(left)) != gate._method_identity(candidate(right))


@pytest.mark.parametrize('expression,literal', [
    ("'con' + 'cern'", "'concern'"), ('1 + 1', '2'), ('3 - 1', '2'), ('2 * 3', '6'),
    ('-(-2)', '2'), ('not False', 'True'), ('0x02', '2'),
])
def test_safe_literal_equivalence_inventory(expression, literal):
    def candidate(value):
        source = f"def solve(facts):\n return {{'verdict': 'concern' if facts == ({value}) else 'no_concern'}}"
        return r.VerifierCandidate(**(bodies()[3][1] | {'body': source}))
    assert gate._method_identity(candidate(expression)) == gate._method_identity(candidate(literal))
