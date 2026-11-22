# SPDX-License-Identifier: Apache-2.0
"""Local operational outcomes are admitted by receipts, never agent claims."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from g6.core.ledger import ChainLedger, digest
from g6.cognitive_bridge import MethodVariant, ProblemSignature
from g6.harness import patch_review
from g6.workforce import receipt_store


def history():
    from g6.harness import outcome_history
    return outcome_history


def test_history_reader_exists_without_caller_trial_input():
    # Removing the protected reader must break this contract, even without filesystem fixtures.
    assert callable(history().read)


def test_cold_start_is_the_pinned_c5782df_reply(monkeypatch):
    from g6.cognitive_bridge.block import CognitiveBridgeBlock
    from g6.core.selector import select
    pin = json.loads((Path(__file__).parent / 'data/cold_c5782df.json').read_text(encoding='utf-8'))
    def wire(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
    assert wire(CognitiveBridgeBlock().infer(pin['rank_input']).value) == wire(pin['rank'])
    assert wire(select('review my Python patch', phase='verify', request_id='fixed', run_id='fixed')) == wire(pin['select'])
    forged = {**pin['rank_input'], 'trials': [{'resolved': True}]}
    assert CognitiveBridgeBlock().infer(forged).value.get('code') == 'G6_E_COGNITIVE_BRIDGE_INPUT'


def test_empty_history_keeps_cold_reply_but_history_controls_remain_discoverable():
    from g6.core.selector import select
    from g6.harness.outcome_history import History

    pin = json.loads((Path(__file__).parent / 'data/cold_c5782df.json').read_text(encoding='utf-8'))
    assert select('review my Python patch', phase='verify', request_id='fixed', run_id='fixed',
                  _history=History()) == pin['select']
    listed = select('inspect local learning history', phase='verify', _history=History())
    assert any(c['id'] == 'op:learning_history.list' for c in listed['candidates'])


@pytest.fixture
def store(tmp_path, monkeypatch):
    h = history()
    monkeypatch.setenv('G6_HOME', str(tmp_path))
    monkeypatch.setattr(h, 'repository_id', lambda root: 'a' * 64)
    return h


def receipt(repo='a' * 64, *, passed=False, source='source'):
    node = 'test_edge.py::test_edge'
    body = {'source_before': digest(source), 'source_after': digest(source),
            'repository_id': repo, 'check_digests': {'test_edge.py': digest('bytes')},
            'tests': {'requested': [node], 'collected': [node]},
            **phase_receipt(node, passed=passed),
            'isolation': {'backend': 'synthetic-unit-fixture'}}
    return receipt_store._record(body), body


def phase_receipt(node, *, passed=False):
    # ``readings``: the live reader's result, with its collection qualifier, a confined run binds
    # (``repo_tests._learning_binding``).
    return {'tests': {'requested': [node], 'collected': [node]},
            'readings': {node: {'result': 'passed' if passed else 'failed', 'collection': False}},
            'test_outcomes': {node: {'setup': 'passed', 'call': 'passed' if passed else 'failed',
                                     'teardown': 'passed'}},
            'result': {'verdict': 'passed' if passed else 'failed', 'total': 1, 'not_passed': 0,
                       'completed': passed, 'degraded': False, 'failed_ids': [] if passed else [node]}}


def row(repo='a' * 64, *, method='formal', passed=False):
    ident, body = receipt(repo, passed=passed)
    return {'schema': 'g6.outcome/1', 'repository_id': repo, 'receipt': ident,
            'artifact_digest': body['source_before'], 'source_after': body['source_after'],
            'classification': 'not_refuted' if passed else 'defect_detected',
            'origin': 'stated', 'node': 'test_edge.py::test_edge', 'modules': ['calc.py'],
            'method': method, 'check': {'file': 'test_edge.py', 'source': '', 'sha256': digest('bytes'),
                                     'test': 'test_edge', 'intent': 'preserve'},
            'finding': 'edge is kept'}


def append(h, value):
    path = h.store_path(value['repository_id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    ChainLedger(path).append(h.KIND, digest(value), value)


@pytest.mark.parametrize('tamper', ['missing_receipt', 'digest', 'repository', 'classification', 'source'])
def test_forged_rows_are_rejected(store, tamper):
    value = row(passed=tamper == 'classification')
    if tamper == 'missing_receipt':
        value['receipt'] = 'f' * 64
    elif tamper == 'digest':
        value['artifact_digest'] = 'f' * 64
    elif tamper == 'repository':
        value['repository_id'] = 'b' * 64
    elif tamper == 'classification':
        value['classification'] = 'defect_detected'
    else:
        value['check']['sha256'] = 'f' * 64
    path = store.store_path('a' * 64)
    path.parent.mkdir(parents=True, exist_ok=True)
    ChainLedger(path).append(store.KIND, digest(value), value)
    loaded = store.read(Path.cwd())
    assert loaded.rows == () and loaded.rejected == 1


def test_ranking_learns_only_from_admitted_receipts(store):
    good, nothing = row(method='review'), row(method='review', passed=True)
    good.update(engine='g6_review_patch', op='review')
    nothing.update(engine='g6_review_until_clean', op='review')
    append(store, good)
    append(store, nothing)
    context = ProblemSignature('software_engineering', 'bug_fix', 'patch', 1, 10)
    variants = [MethodVariant('g', 'review', 'pytest', 'g6_review_until_clean', 'review', 1, kind='reviewer'),
                MethodVariant('f', 'review', 'pytest', 'g6_review_patch', 'review', 1, kind='reviewer')]
    ranked = store.read(Path.cwd()).selector(context, variants).select(context, variants,
                                                required_methods=['review'], max_optional=1)
    assert ranked.ordered_variants[0]['variant_id'] == 'f'
    assert ranked.admitted_trials == 2


def test_admission_recomputes_classification_from_the_receipt(monkeypatch):
    h = history()
    body = {'repository_id': 'a' * 64, 'source_before': 'd' * 64, 'source_after': 'd' * 64,
            'check_digests': {'test_edge.py': 'b' * 64},
            **phase_receipt('test_edge.py::test_edge', passed=True)}
    monkeypatch.setattr(receipt_store, 'lookup', lambda ident, **kw: body if ident == 'owned' and kw[
                        'artifact_digest'] == body['source_before'] else None)
    value = {'schema': h.SCHEMA, 'repository_id': 'a' * 64, 'receipt': 'owned',
             'artifact_digest': 'd' * 64, 'source_after': 'd' * 64,
             'classification': 'defect_detected', 'node': 'test_edge.py::test_edge',
             'modules': [], 'finding': 'edge kept', 'method': 'review',
             'check': {'source': '', 'file': 'test_edge.py', 'sha256': 'b' * 64}}
    assert not h._admitted(value, 'a' * 64)
    value['classification'] = 'not_refuted'
    assert h._admitted(value, 'a' * 64)
    for field in ('artifact_digest', 'source_after', 'repository_id'):
        assert not h._admitted({**value, field: 'f' * 64}, 'a' * 64)


def test_rank_methods_accepts_only_g6_review_variants_for_outcome_history():
    from g6.cognitive_bridge.block import CognitiveBridgeBlock
    given = {'op': 'rank_methods', 'context': {'domain': 'software_engineering', 'uncertainty': 'bug_fix',
             'claim_kind': 'patch', 'risk': 1, 'budget': 10.0},
             'variants': [{'id': 'review', 'method': 'review', 'representation': 'pytest',
                'engine': 'g6_review_patch', 'op': 'review', 'kind': 'reviewer', 'estimated_cost': 1.0}],
             'required_methods': ['review']}
    got = CognitiveBridgeBlock().infer(given).value
    assert got.get('ordered_variants'), got
    given['variants'][0]['engine'] = 'agent-self-rating'
    assert CognitiveBridgeBlock().infer(given).value.get('code') == 'G6_E_COGNITIVE_BRIDGE_INPUT'


def test_stated_execution_records_a_defect_and_keeps_the_check(monkeypatch):
    from types import SimpleNamespace
    h = history()
    recorded = []
    source = 'from calc import add\n\ndef test_edge():\n    assert add(0, 0) == 0\n'
    import hashlib
    sha = hashlib.sha256(source.encode()).hexdigest()
    body = {'source_before': 'd' * 64, 'source_after': 'd' * 64,
            'repository_id': 'a' * 64, 'check_digests': {'test_edge.py': sha},
            **phase_receipt('test_edge.py::test_edge')}
    monkeypatch.setattr(receipt_store, 'lookup', lambda *a, **kw: body)
    monkeypatch.setattr(h, 'repository_id', lambda root: 'a' * 64)
    monkeypatch.setattr(h, '_record', lambda root, row: recorded.append(row))
    change = SimpleNamespace(repo=Path.cwd(), files=['calc.py'])
    h.record_stated(change, {'test_edge.py::test_edge': source},
                    [{'receipt': 'owned', 'provenance': 'stated'}], artifact='d' * 64)
    assert recorded[0]['classification'] == 'defect_detected'
    assert recorded[0]['check']['source'] == source and recorded[0]['origin'] == 'stated'


def test_invalid_select_never_reads_the_repository(monkeypatch):
    from g6.mcp import selector
    h = history()
    reads = []
    monkeypatch.setattr(h, 'read', lambda root: reads.append(root) or h.History())
    class Server:
        def tool(self):
            return lambda function: setattr(self, function.__name__, function) or function
    server = Server()
    selector.register(server)
    assert server.g6_select('')['ok'] is False
    assert reads == []


def test_k_reply_preserves_retained_activities():
    from g6.mcp import review_loop
    activity = {'origin': 'retained', 'check': 'old-edge', 'result': 'counterexample'}
    state = {'reviews': [{'learned': {'retained_checks': 1}, 'completion': {'activities': [activity]}}]}
    fields = review_loop._learning(state)
    assert fields['completion']['activities'] == [activity]
    assert fields['learned']['retained_checks'] == 1
    assert review_loop._learning({}) == {}


def test_retained_base_timeout_is_skipped_not_stale(monkeypatch):
    from types import SimpleNamespace
    from g6.harness import verification_search as vs
    clock, calls = [0.0], []
    monkeypatch.setattr(vs, '_clock', lambda: clock[0])
    def run(*args, **kwargs):
        calls.append(kwargs['at_base'])
        clock[0] = 20
        return {'verdict': 'timed_out', 'said': 'time limit', 'seconds': 20}
    monkeypatch.setattr(vs, '_run_check', run)
    one = vs.ProposedCheck(id='retained-edge', requirement_id='R1', intent='preserve',
                           file='test_edge.py', source='def test_edge():\n    assert True\n')
    done = vs.search(SimpleNamespace(repo=Path.cwd(), sources=[]), [one], {one.id: vs.placement(one)},
                     deadline=20, artifact='a' * 64,
                     origins={one.id: vs.Origin(label='retained', about={'origin': 'retained'},
                                               retained_check=one, retained_digest=digest(one.model_dump()))})
    assert done.activities[0]['result'] == 'skipped'
    assert 'time' in done.activities[0]['reason']
    assert calls == [True]


@pytest.mark.parametrize('patch_collects', [True, False])
def test_retained_check_the_base_cannot_collect_still_runs_on_the_patch(monkeypatch, patch_collects):
    from types import SimpleNamespace
    from g6.harness import verification_search as vs
    from tests.harness.efu2_cases import FAILED, report
    calls = []
    one = vs.ProposedCheck(id='retained-added', requirement_id='R1', intent='preserve', file='test_added.py',
                           source='from calc import added\n\ndef test_added():\n    assert added() == 1\n')
    node = vs._node(one, vs.placement(one))

    def run(*args, at_base, **kwargs):
        calls.append(at_base)
        if at_base or not patch_collects:   # the base, or a patch, without the API the check imports
            return {**report(FAILED), 'verdict': 'collection_error', 'test_outcomes': {}, 'collected_ids': [],
                    'failed_ids': [], 'completed': False,
                    'failure_messages': {node.partition('::')[0]: 'ImportError: cannot import name added'}}
        ran = report(FAILED, completed=False, failure_messages={node: 'AssertionError: added returned 0'})
        ran.update(test_outcomes={node: ran['test_outcomes'].popitem()[1]}, failed_ids=[node], collected_ids=[node])
        return ran
    monkeypatch.setattr(vs, '_run_check', run)
    monkeypatch.setattr(vs, '_loaded_elsewhere', lambda change, ran: '')
    done = vs.search(SimpleNamespace(repo=Path.cwd(), base_commit='b' * 40), [one], {one.id: vs.placement(one)},
                     deadline=vs._clock() + 60, artifact='a' * 64,
                     origins={one.id: vs.Origin(label='retained', about={'origin': 'retained'},
                                               retained_check=one, retained_digest=digest(one.model_dump()))})
    assert calls == [True, False]
    activity = done.activities[0]
    assert [run['on'] for run in activity['runs']] == ['base commit', 'patch']
    # The live classification of a failure the base cannot collect; collected on neither tree, it no longer applies.
    assert activity['result'] == ('fails_on_base_too' if patch_collects else 'stale'), activity


def test_observation_receipt_cannot_pass_a_stated_requirement():
    body = {'schema': 'g6.observation_receipt/1', 'result': {'verdict': 'passed', 'total': 1,
            'not_passed': 0, 'completed': True, 'degraded': False}}
    assert receipt_store.counts_as_pass(body) is False


def test_observation_receipts_resolve_only_for_learning(monkeypatch):
    from types import SimpleNamespace
    body = {'schema': 'g6.observation_receipt/1', 'source_before': 'a' * 64}
    ident = receipt_store.receipt_id(body)
    monkeypatch.setattr(receipt_store, 'store_path', lambda: SimpleNamespace(is_file=lambda: True))
    monkeypatch.setattr(receipt_store, 'ChainLedger', lambda path: SimpleNamespace(get=lambda *args: body))
    assert receipt_store.check(ident, artifact_digest='a' * 64) == ('unknown', None)
    assert receipt_store.lookup(ident, artifact_digest='a' * 64, _observations=True) == body


def test_observed_differences_need_two_bound_receipts(monkeypatch):
    h = history()
    import hashlib
    source = 'def test_observe():\n    pass\n'
    sha = hashlib.sha256(source.encode()).hexdigest()
    row = {'schema': h.SCHEMA, 'repository_id': 'a' * 64, 'receipt': 'patch', 'artifact_digest': 'b' * 64,
           'source_after': 'b' * 64, 'base_receipt': 'base', 'base_artifact_digest': 'c' * 64,
           'base_source_after': 'c' * 64, 'classification': 'counterexample', 'origin': 'template',
           'node': 'test_observe.py', 'modules': [], 'finding': 'preserve output', 'method': 'review',
           'check': {'file': 'test_observe.py', 'source': source, 'sha256': sha,
                     'observation': {'function': 'calc.add'}, 'refuted_by_difference': True}}
    def body(artifact, output):
        return {'schema': 'g6.observation_receipt/1', 'repository_id': 'a' * 64,
                'source_before': artifact, 'source_after': artifact, 'check_digests': {'test_observe.py': sha},
                'observation_digest': digest(row['check']['observation']),
                'tests': {'requested': ['test_observe.py']},
                'result': {'verdict': 'passed', 'completed': True, 'degraded': False,
                           'observations': [{'input': [], 'output': output}]}}
    receipts = {'patch': body('b' * 64, 2), 'base': body('c' * 64, 1)}
    monkeypatch.setattr(receipt_store, 'lookup', lambda ident, **kw: receipts.get(ident))
    assert h._admitted(row, 'a' * 64)
    receipts['patch']['result']['observations'] = receipts['base']['result']['observations']
    assert not h._admitted(row, 'a' * 64)


def test_stated_node_is_retained_when_source_exceeds_replay_limit(monkeypatch):
    from types import SimpleNamespace
    h = history()
    body = {'repository_id': 'a' * 64, 'source_before': 'd' * 64, 'source_after': 'd' * 64,
            'check_digests': {'test_large.py': 'b' * 64}, **phase_receipt('test_large.py')}
    recorded = []
    monkeypatch.setattr(h, 'repository_id', lambda root: 'a' * 64)
    monkeypatch.setattr(receipt_store, 'lookup', lambda *a, **kw: body)
    monkeypatch.setattr(h, '_record', lambda root, row: recorded.append(row) if h._admitted(row, 'a' * 64) else None)
    change = SimpleNamespace(repo=Path.cwd(), files=[])
    h.record_stated(change, {'test_large.py': ''}, [{'receipt': 'owned', 'provenance': 'stated'}], artifact='d' * 64)
    assert len(recorded) == 1 and recorded[0]['check']['sha256'] == 'b' * 64


def test_resolution_is_decided_and_deterministic():
    # Missing stated checks must produce an actionable entry, without running anything.
    from g6.harness import resolution
    assessment = {'requirements': [{'id': 'R1', 'text': 'Keep negative addition.', 'checks': [],
                                   'status': patch_review.NOT_ESTABLISHED}],
                  'completion': {'defects_reproduced': {'proposed_checks': []}}}
    got = resolution.build(assessment, repo='fixture', task='addition')
    assert got == resolution.build(assessment, repo='fixture', task='addition')
    assert len(got) == 1 and got[0]['requirement'] == 'R1'
    assert set(got[0]) >= {'untested', 'cheapest_check', 'agent_can_run_now', 'needs_person'}
    assert resolution.build({'requirements': [], 'completion': {}}, repo='fixture', task='addition') == []
