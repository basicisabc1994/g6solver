# SPDX-License-Identifier: Apache-2.0
"""Inactive identity uses all recorded observations, before spending a look."""
from contextlib import nullcontext
from itertools import product
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from g6.core.ledger import ChainLedger, canonical, digest
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.held_out.case_gate import CaseRecord
from g6.distill.library import Library, public_entry
from g6.distillation.sandbox import Run, check_source
from tests.distill.test_records import bodies
from tests.distill.test_review2_repairs import CASE

BASE = "def solve(facts):\n return {'verdict': 'concern', 'reason': 'same'}"


def candidate(source=BASE):
    return r.VerifierCandidate(**(bodies()[3][1] | {'body': source}))


def case(unit='recorded'):
    return CaseRecord.model_validate(CASE | {'unit_id': unit, 'before_tests': CASE['before_tests'] + f'# {unit}\n'})


def memory(monkeypatch, tmp_path, status, *, source=BASE, recurrences=(), extra=None):
    first = candidate(source)
    frozen = case()
    entry = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': first.candidate_id, 'status': status, 'authority': 'A0',
        'regression_suite': [{'case_ref': frozen.case_id, 'verdict': 'concern'}],
        'held_recurrences': list(recurrences), **(extra or {})}))
    rows = {r.LibraryEntry: [entry], r.VerifierCandidate: [first], r.FailureRecord: []}

    def for_model(model):
        values = rows.get(model, [])
        return SimpleNamespace(list=lambda: values, get=lambda ident: next(
            value for value in values if value.content_id == ident))

    monkeypatch.setenv('G6_HOME', str(tmp_path))
    monkeypatch.setenv('G6_REQUIRE_ISOLATION', '1')
    monkeypatch.setattr(gate, 'Stores', lambda: SimpleNamespace(for_model=for_model))
    monkeypatch.setattr(Library, 'list', lambda self: rows[r.LibraryEntry])
    monkeypatch.setattr(gate, '_writer', nullcontext)
    monkeypatch.setattr(gate, '_journal', ChainLedger)
    monkeypatch.setattr(gate, '_policy', lambda: None)
    monkeypatch.setattr(gate.stop, 'engaged', lambda: None)
    archive = tmp_path / 'distill' / 'held_out' / 'regression_cases'
    archive.mkdir(parents=True)
    (archive / f'{frozen.case_id}.json').write_text(canonical(frozen.model_dump(mode='json')), encoding='utf-8')
    reached, runs = [], []

    def confined(source, values, **kwargs):
        runs.extend(values)
        problems = check_source(source)
        if problems:
            return [Run('refused', reason=problems[0]) for _ in values]
        namespace = {}
        exec(source, namespace)
        output = []
        for facts in values:
            try:
                output.append(Run('ok', value=namespace['solve'](facts)))
            except Exception as error:
                output.append(Run('error', reason=str(error)))
        return output

    monkeypatch.setattr(gate, 'run_batch', confined)
    monkeypatch.setattr(gate, '_look_available', lambda *args: None)

    def load(*args):
        reached.append(args[1].candidate_id)
        raise ValueError('test stops at registration boundary')

    monkeypatch.setattr(gate, '_load', load)
    return first, entry, frozen, rows, reached, runs


def rewrites():
    # Generate combinations, so presentation changes cannot be addressed one
    # spelling at a time. These fixtures all return concern for any input.
    forms = {
        'rename': BASE.replace('facts', 'data'),
        'lambda': "solve = lambda data: {'verdict': 'concern', 'reason': 'same'}",
        'decorator': '@(lambda fn: fn)\n' + BASE,
        'alias': BASE.replace('solve', 'other') + '\nsolve = other',
        'dead': BASE.replace(' return', ' if False:\n  return {}\n return'),
        'reorder': "def solve(facts):\n b = 'same'\n a = 'concern'\n return {'verdict': a, 'reason': b}",
        'wrapper': BASE.replace('solve', 'other') + '\ndef solve(data):\n return other(data)',
        # Imports remain forbidden in the confined runner. This equivalent
        # rewrite must stop as unknown, rather than become a different method.
        'wraps': 'import functools\n' + BASE.replace('solve', 'other') +
                 '\n@functools.wraps(other)\ndef solve(data):\n return other(data)',
    }
    for (name, source), comment, doc in product(forms.items(), (False, True), (False, True)):
        if comment:
            source = '# presentation\n' + source
        if doc:
            source = "'module documentation'\n" + source
        yield pytest.param(source, name == 'wraps', id=f'{name}-comment{int(comment)}-doc{int(doc)}')


@pytest.mark.parametrize('status', ['withdrawn', 'held'])
@pytest.mark.parametrize('source,unknown', list(rewrites()))
def test_generated_equivalent_rewrites_stop_before_load(monkeypatch, tmp_path, status, source, unknown):
    _, _, _, _, reached, _ = memory(monkeypatch, tmp_path, status)
    if 'solve = lambda' in source or 'solve = other' in source:
        with pytest.raises(ValidationError, match=r'predicate needs solve\(facts\)'):
            candidate(source)
        assert reached == []
        return
    answer = gate.decide(candidate(source), Library(), digest('registration'))
    assert reached == []
    assert answer.verdict in {'block', 'refuse'}
    if unknown:
        assert 'G6_E_DISTILL_IDENTITY_UNKNOWN' in ' '.join(answer.reasons)
    else:
        assert status in ' '.join(answer.reasons)
        assert 'unknown' not in ' '.join(answer.reasons)


@pytest.mark.parametrize('status', ['withdrawn', 'held'])
@pytest.mark.parametrize('source,changed', [
    (BASE, BASE.replace("'concern'", "'no_concern'")),
    ("def solve(facts='concern'):\n if isinstance(facts, dict):\n  return solve()\n return {'verdict': facts}",
     "def solve(facts='no_concern'):\n if isinstance(facts, dict):\n  return solve()\n return {'verdict': facts}"),
    ("def solve(facts):\n return {'verdict': 'concern' if facts else 'no_concern'}",
     "def solve(facts):\n return {'verdict': 'no_concern' if facts else 'concern'}"),
], ids=['constant', 'recursive-default', 'branch'])
def test_changed_recorded_verdict_reaches_normal_gate(monkeypatch, tmp_path, status, source, changed):
    _, _, _, _, reached, _ = memory(monkeypatch, tmp_path, status, source=source)
    other = candidate(changed)
    gate.decide(other, Library(), digest('registration'))
    assert reached == [other.candidate_id]


@pytest.mark.parametrize('status', ['withdrawn', 'held'])
@pytest.mark.parametrize('outcome,code', [
    (Run('refused', reason='G6_E_ISOLATION_UNAVAILABLE: host confinement missing'), 'G6_E_ISOLATION_UNAVAILABLE'),
    (Run('error', reason='private stderr'), 'G6_E_SYNTH_EXECUTION'),
    (Run('timeout', reason='private stderr'), 'G6_E_SYNTH_EXECUTION'),
    (Run('refused', reason='imports are not allowed'), 'G6_E_SYNTH_SOURCE'),
])
def test_unknown_recorded_run_preserves_code_without_details(monkeypatch, tmp_path, status, outcome, code):
    _, _, _, _, reached, _ = memory(monkeypatch, tmp_path, status)
    monkeypatch.setattr(gate, 'run_batch', lambda *args, **kwargs: [outcome])
    answer = gate.decide(candidate(BASE.replace('facts', 'data')), Library(), digest('registration'))
    reasons = ' '.join(answer.reasons)
    assert reached == []
    assert 'G6_E_DISTILL_IDENTITY_UNKNOWN' in reasons and code in reasons
    assert 'private stderr' not in reasons


def test_no_confinement_is_unknown(monkeypatch, tmp_path):
    _, _, _, _, reached, runs = memory(monkeypatch, tmp_path, 'withdrawn')
    monkeypatch.delenv('G6_REQUIRE_ISOLATION')
    answer = gate.decide(candidate(BASE.replace('facts', 'data')), Library(), digest('registration'))
    assert reached == [] and runs == []
    assert 'G6_E_ISOLATION_UNAVAILABLE' in ' '.join(answer.reasons)


def test_difference_does_not_hide_later_crash(monkeypatch, tmp_path):
    _, entry, _, rows, reached, _ = memory(monkeypatch, tmp_path, 'withdrawn')
    other = case('second')
    path = tmp_path / 'distill' / 'held_out' / 'regression_cases' / f'{other.case_id}.json'
    path.write_text(canonical(other.model_dump(mode='json')), encoding='utf-8')
    rows[r.LibraryEntry] = [r.LibraryEntry(**(entry.model_dump(exclude={'entry_id'}) | {
        'regression_suite': [*entry.regression_suite, r.RegressionCase(case_ref=other.case_id, verdict='concern')]}))]
    results = iter([Run('ok', value={'verdict': 'no_concern'})] * 3 + [Run('error', reason='crash')])
    monkeypatch.setattr(gate, 'run_batch', lambda *args, **kwargs: [next(results)])
    answer = gate.decide(candidate(BASE.replace('facts', 'data')), Library(), digest('registration'))
    assert reached == []
    assert 'G6_E_DISTILL_IDENTITY_UNKNOWN' in ' '.join(answer.reasons)


def test_hold_recurrence_verdict_can_distinguish_methods(monkeypatch, tmp_path):
    failure_case = case('recurrence')
    failure = r.FailureRecord(**(bodies()[0][1] | {
        'case_ref': failure_case.case_id, 'provenance': {'case': failure_case.model_dump(mode='json')}}))
    _, _, _, rows, reached, _ = memory(monkeypatch, tmp_path, 'held', recurrences=[failure.record_id])
    rows[r.FailureRecord].append(failure)
    calls = []

    def facts(before, after):
        calls.append((before, after))
        return {'recurrence': '# recurrence' in before['tests/test_case.py']}

    monkeypatch.setattr(gate, '_extract_facts', facts)
    other = candidate("def solve(facts):\n return {'verdict': 'no_concern' if facts['recurrence'] else 'concern'}")
    gate.decide(other, Library(), digest('registration'))
    assert reached == [other.candidate_id]
    assert len(calls) >= 6


@pytest.mark.parametrize('status', ['withdrawn', 'held'])
def test_missing_recorded_body_is_unknown(monkeypatch, tmp_path, status):
    _, _, frozen, _, reached, _ = memory(monkeypatch, tmp_path, status)
    (tmp_path / 'distill' / 'held_out' / 'regression_cases' / f'{frozen.case_id}.json').unlink()
    answer = gate.decide(candidate(BASE.replace('facts', 'data')), Library(), digest('registration'))
    assert reached == []
    assert 'G6_E_DISTILL_IDENTITY_UNKNOWN' in ' '.join(answer.reasons)


def test_other_family_does_not_spend_recorded_runs(monkeypatch, tmp_path):
    _, entry, _, rows, reached, runs = memory(monkeypatch, tmp_path, 'withdrawn')
    rows[r.LibraryEntry] = [r.LibraryEntry(**(entry.model_dump(exclude={'entry_id'}) | {'family': 'other'}))]
    other = candidate(BASE.replace('facts', 'data'))
    gate.decide(other, Library(), digest('registration'))
    assert reached == [other.candidate_id] and runs == []


def test_only_current_inactive_rows_block_after_restore(monkeypatch, tmp_path):
    first, entry, _, rows, reached, _ = memory(monkeypatch, tmp_path, 'withdrawn')
    restored = r.LibraryEntry(**(entry.model_dump(exclude={'entry_id'}) | {
        'version': 2, 'status': 'admitted', 'authority': 'A2'}))
    rows[r.LibraryEntry] = [entry, restored]
    monkeypatch.setattr(Library, 'list', lambda self: [restored])
    assert not gate._withdrawn_match(first)
    gate.decide(first, Library(), digest('registration'))
    assert reached == [first.candidate_id]


@pytest.mark.parametrize('source,other', [
    (BASE, BASE.replace('facts', 'facts=None')),
    (BASE, '@(lambda fn: fn)\n' + BASE),
    (BASE, BASE.replace("'concern'", "('con' + 'cern')")),
    ("def solve(facts):\n return {'verdict': 'concern', 'reason': 'same'}",
     "def solve(facts):\n return {'reason': 'same', 'verdict': 'concern'}"),
])
def test_executable_syntax_is_never_erased(source, other):
    assert gate._method_identity(candidate(source)) != gate._method_identity(candidate(other))


# test_only_nonexecuting_syntax_is_canonical (docstrings leave identity unchanged) was superseded by review 3's
# finding 3, since a predicate can read __doc__ (lead ruling, e58c51e0); the control below pins what still holds.
def test_comments_blank_lines_and_formatting_alone_are_canonical():
    other = "# comment\n\ndef solve( facts ):\n    return { 'verdict': 'concern', 'reason': 'same' }\n\n"
    assert gate._method_identity(candidate()) == gate._method_identity(candidate(other))


def test_inactive_case_field_preserves_old_record_addresses():
    old = bodies()[4][1]
    entry = r.LibraryEntry(**old)
    assert entry.content_id == digest(old)
    updated = r.LibraryEntry(**(old | {'inactive_cases': [dict(case_ref=digest('counterexample'), verdict='abstain')]}))
    assert updated.inactive_cases[0].verdict == 'abstain'
    assert updated.content_id != entry.content_id
    assert 'inactive_cases' not in public_entry(updated)


def test_inventory_cannot_lose_a_rewrite_family():
    # The generated property must keep each independently reported escape class.
    ids = {param.id.split('-comment')[0] for param in rewrites()}
    assert ids == {'rename', 'lambda', 'decorator', 'alias', 'dead', 'reorder', 'wrapper', 'wraps'}


def test_withdrawal_counterexample_overrides_the_old_expectation(monkeypatch, tmp_path):
    frozen = case()
    _, _, _, _, reached, _ = memory(monkeypatch, tmp_path, 'withdrawn', extra={
        'inactive_cases': [dict(case_ref=frozen.case_id, verdict='no_concern')]})
    other = candidate(BASE.replace("'concern'", "'no_concern'"))
    answer = gate.decide(other, Library(), digest('registration'))
    assert reached == []
    assert 'person-authorized restore' in ' '.join(answer.reasons)


def test_recheck_retains_observed_withdrawal_verdict(monkeypatch, tmp_path):
    _, entry, frozen, rows, _, _ = memory(monkeypatch, tmp_path, 'withdrawn')
    second = case('second drift')
    active = r.LibraryEntry(**(entry.model_dump(exclude={'entry_id'}) | {
        'status': 'probation', 'authority': 'A1', 'regression_suite': [*entry.regression_suite,
            r.RegressionCase(case_ref=second.case_id, verdict='concern')]}))
    rows[r.LibraryEntry] = [active]
    monkeypatch.setattr(gate, '_snapshot', lambda *args: active)
    monkeypatch.setattr(gate, '_health', lambda *args: None)
    monkeypatch.setattr(gate, '_load', lambda *args: gate.EvaluationSet(
        digest('registration'), digest('split'), tuple(gate.Observation(c, 'positive', 'acceptance')
            for c in (frozen, second)), (), (), ('a', 'b')))
    monkeypatch.setattr(gate, 'run_batch', lambda *args, **kwargs: [Run('ok', value={'verdict': 'no_concern'})])
    writes = []
    original = gate.Stores().for_model

    def for_model(model):
        store = original(model)
        return SimpleNamespace(get=store.get, list=store.list, put=writes.append)

    monkeypatch.setattr(gate, 'Stores', lambda: SimpleNamespace(for_model=for_model))
    monkeypatch.setattr(gate, '_cascade', lambda *args: None)
    answer = gate.recheck(active.entry_id, Library(), digest('registration'))
    assert answer.verdict == 'withdraw'
    assert writes[-1].inactive_cases == [r.RegressionCase(case_ref=c.case_id, verdict='no_concern')
                                       for c in (frozen, second)]
    assert writes[-1].regression_suite == active.regression_suite


def test_restore_exemption_never_releases_another_hold(monkeypatch, tmp_path):
    first, entry, _, rows, reached, _ = memory(monkeypatch, tmp_path, 'withdrawn')
    held_candidate = candidate(BASE.replace('facts', 'data'))
    held = r.LibraryEntry(**(entry.model_dump(exclude={'entry_id'}) | {
        'candidate_id': held_candidate.candidate_id, 'status': 'held', 'authority': 'A0'}))
    rows[r.VerifierCandidate].append(held_candidate)
    rows[r.LibraryEntry].append(held)
    with pytest.raises(ValueError, match='hold release'):
        gate._check_inactive(first, Library(), restoring=entry.entry_id)
    assert reached == []


def test_exact_fast_path_needs_no_case_body_or_execution(monkeypatch, tmp_path):
    first, _, frozen, _, reached, runs = memory(monkeypatch, tmp_path, 'held')
    (tmp_path / 'distill' / 'held_out' / 'regression_cases' / f'{frozen.case_id}.json').unlink()
    answer = gate.decide(first, Library(), digest('registration'))
    assert 'hold release' in ' '.join(answer.reasons)
    assert reached == [] and runs == []


def test_legacy_withdrawal_drift_without_verdict_is_unknown(monkeypatch, tmp_path):
    _, _, _, _, reached, _ = memory(monkeypatch, tmp_path, 'withdrawn', extra={
        'reason': 'own regression suite no longer reproduces'})
    answer = gate.decide(candidate(BASE.replace('facts', 'data')), Library(), digest('registration'))
    assert reached == []
    assert 'G6_E_DISTILL_IDENTITY_UNKNOWN' in ' '.join(answer.reasons)


@pytest.mark.parametrize('operation', ['recheck', 'ablate', 'promote'])
@pytest.mark.parametrize('status', ['withdrawn', 'held'])
def test_maintenance_loads_also_require_inactive_check(monkeypatch, tmp_path, operation, status):
    _, inactive, _, rows, reached, _ = memory(monkeypatch, tmp_path, status)
    other = candidate(BASE.replace('facts', 'data'))
    active = r.LibraryEntry(**(inactive.model_dump(exclude={'entry_id'}) | {
        'candidate_id': other.candidate_id, 'status': 'probation', 'authority': 'A1'}))
    rows[r.VerifierCandidate].append(other)
    rows[r.LibraryEntry].append(active)
    monkeypatch.setattr(gate, '_snapshot', lambda *args: active)
    monkeypatch.setattr(gate, '_health', lambda *args: None)
    monkeypatch.setattr(gate, '_a2_bar', lambda *args: None)
    answer = getattr(gate, operation)(active.entry_id, Library(), digest('registration'))
    assert reached == []
    assert status in ' '.join(answer.reasons)


@pytest.mark.parametrize('outputs', [[], [Run('ok', value={'verdict': 'invalid'})],
                                    [Run('ok', value='bad output')]])
def test_invalid_recorded_run_is_unknown(monkeypatch, tmp_path, outputs):
    _, _, _, _, reached, _ = memory(monkeypatch, tmp_path, 'withdrawn')
    monkeypatch.setattr(gate, 'run_batch', lambda *args, **kwargs: outputs)
    answer = gate.decide(candidate(BASE.replace('facts', 'data')), Library(), digest('registration'))
    assert reached == []
    assert 'G6_E_SYNTH_EXECUTION' in ' '.join(answer.reasons)
