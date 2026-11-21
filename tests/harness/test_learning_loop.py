# SPDX-License-Identifier: Apache-2.0
"""Two tasks use real assertions in process; synthetic runner evidence is not a confinement test."""
from __future__ import annotations

import hashlib
import sys
import types

import pytest

from g6.core.ledger import digest
from g6.core.llm import FakeLLM
from g6.harness import patch_review, verification_search as vs
from g6.workforce import receipt_store, repo_tests
from tests.harness.test_proposed_checks import _repo, result

pytestmark = pytest.mark.usefixtures('declared_mcp_grants')
BASE = 'def add(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b\n'
GOOD = BASE.replace('return a - b', 'return a + b')
BAD = GOOD.replace('return a * b', 'return a * b if a else 1')
EDGE = 'from calc import mul\n\ndef test_zero():\n    assert mul(0, 3) == 0\n'


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = _repo(tmp_path, {'calc.py': BASE, 'test_add.py':
                           'from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n'})
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    calls = []

    def executed(change, placed, source, node, *, at_base, timeout_s, **kwargs):
        calls.append((node, at_base))
        code = repo_tests._git(change.repo, 'show', f'{change.base_commit}:calc.py').decode() if at_base else (
            change.repo / 'calc.py').read_text(encoding='utf-8')
        module = types.ModuleType('calc')
        exec(compile(code, 'calc.py', 'exec'), module.__dict__)
        with monkeypatch.context() as local:
            local.setitem(sys.modules, 'calc', module)
            namespace = {}
            exec(compile(source, placed, 'exec'), namespace)
            try:
                for name, check in namespace.items():
                    if name.startswith('test_'):
                        check()
            except AssertionError:
                ran = result(failed=[node], messages={node: 'AssertionError: zero multiplication changed'})
            else:
                ran = result([node])
        sha = hashlib.sha256(source.encode()).hexdigest()
        ran['source_before'] = digest({'code': code, 'check': sha})
        # The live reading bound as a confined run binds it (``repo_tests._learning_binding``).
        body = {'source_before': ran['source_before'], 'source_after': ran['source_before'],
                'repository_id': receipt_store.repository_id(change.repo),
                'check_digests': {placed: sha}, 'tests': {'requested': [node], 'collected': [node]},
                'readings': repo_tests._learning_binding(ran, [node], repository='', check_digests={})['readings'],
                'isolation': ran['isolation'], 'result': {**ran, 'completed': ran['verdict'] == 'passed'}}
        ran['receipt'] = receipt_store._record(body)
        return ran

    def execute(root, ids, **kwargs):
        ran = result(ids)
        return ran

    monkeypatch.setattr(vs, '_run_check', executed)
    monkeypatch.setattr(patch_review, '_execute', execute)
    monkeypatch.setattr(patch_review, '_execute_at_base', lambda change, ids, **kwargs: result(failed=ids))
    return root, calls


def review(root, checks=None, requirements=None):
    return patch_review.review_patch(root, task='Fix addition and keep multiplication',
        requirements=requirements if requirements is not None else [
            {'id': 'R1', 'text': 'Add positive integers', 'checks': ['test_add.py::test_add']}],
        proposed_checks=checks)


def test_task_two_reuses_task_one_counterexample_without_caller_checks(world, monkeypatch, tmp_path):
    root, calls = world
    (root / 'calc.py').write_text(BAD, encoding='utf-8')
    llm = FakeLLM(lambda system, user: EDGE)
    source = llm.complete('Propose a regression check', 'Keep multiplication')
    proposed = [{'id': 'zero', 'requirement_id': 'R1', 'intent': 'preserve', 'file': 'test_zero.py',
                 'source': source}]
    first = review(root, proposed)
    assert first['assessment']['completion']['defects_reproduced']['proposed_checks'][0]['result'] == 'counterexample'
    assert first['learned']['admitted_trials'] == 1 and first['learned']['retained_checks'] == 1
    (root / 'calc.py').write_text(GOOD, encoding='utf-8')
    fixed = review(root)
    assert fixed['learned']['ran'] and not fixed['assessment']['completion']['defects_reproduced']['proposed_checks']
    (root / 'calc.py').write_text(BAD + '\ndef other():\n    return 2\n', encoding='utf-8')
    second = review(root)
    assert second['assessment']['outcome'] == 'insufficient_evidence'
    assert second['learned']['ran']
    activities = second['assessment']['completion']['activities']
    assert any(a.get('origin') == 'retained' and a['result'] == 'counterexample' for a in activities)
    assert second['assessment']['requirements'][0]['status'] != 'failed'
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'empty'))
    control = review(root)
    assert control['learned']['retained_checks'] == 0 and control['learned']['ran'] == []
    assert not control['assessment']['completion']['defects_reproduced']['proposed_checks']
    assert calls


def test_retained_pass_cannot_pass_requirement_and_cap_lists_exclusions(world):
    from g6.harness import outcome_history as h
    root, _ = world
    (root / 'calc.py').write_text(BAD, encoding='utf-8')
    checks = [{'id': f'zero{i}', 'requirement_id': 'R1', 'intent': 'preserve', 'file': f'test_zero{i}.py',
               'source': EDGE + f'\n# {i}\n'} for i in range(7)]
    review(root, checks)
    (root / 'calc.py').write_text(GOOD, encoding='utf-8')
    got = review(root, requirements=[{'id': 'R2', 'text': 'Keep zero multiplication', 'checks': []}])
    assert got['assessment']['requirements'][0]['status'] == 'not_established'
    acts = [a for a in got['assessment']['completion']['activities'] if a.get('origin') == 'retained']
    assert len([a for a in acts if a['result'] != 'skipped']) == 5
    assert len([a for a in acts if a['result'] == 'skipped' and 'limit' in a['reason']]) == 2
    assert len(h.read(root).retained) == 7


def test_stale_retained_check_is_kept_and_not_run(world):
    root, calls = world
    (root / 'calc.py').write_text(BAD, encoding='utf-8')
    review(root, [{'id': 'zero', 'requirement_id': 'R1', 'intent': 'preserve', 'file': 'test_zero.py', 'source': EDGE}])
    (root / 'calc.py').unlink()
    calls.clear()
    got = review(root, requirements=[])
    assert got['learned']['stale_checks'] == 1 and not got['learned']['ran']
    assert not calls
    assert any(a.get('result') == 'stale' for a in got['assessment']['completion']['activities'])


def test_k_reproduction_requires_base_pass(world):
    from g6.harness import completion_loop as loop, outcome_history as h
    root, _ = world
    probes = root.parent / 'probes'
    probes.mkdir()
    (probes / 'test_zero.py').write_text(EDGE, encoding='utf-8')
    (root / 'calc.py').write_text(BAD, encoding='utf-8')
    change = repo_tests.change(root, 'HEAD')
    loop.run_set(change, probes, at_base=False, deadline=loop._clock() + 60)
    # A patch run alone must never write a K-loop reproduction.
    assert h.read(root).rows == ()
    loop.record_reproductions(change, probes, deadline=loop._clock() + 60)
    assert len(h.read(root).retained) == 1
    # Both trees fail: no new reproduction is recorded.
    (probes / 'test_zero.py').write_text(EDGE.replace('== 0', '== 42'), encoding='utf-8')
    before = len(h.read(root).rows)
    loop.record_reproductions(change, probes, deadline=loop._clock() + 60)
    assert len(h.read(root).rows) == before


def test_mcp_select_uses_host_repo_history(world, monkeypatch):
    from g6.mcp import selector
    root, _ = world
    (root / 'calc.py').write_text(BAD, encoding='utf-8')
    review(root, [{'id': 'zero', 'requirement_id': 'R1', 'intent': 'preserve', 'file': 'test_zero.py', 'source': EDGE}])
    class Server:
        def tool(self):
            return lambda function: setattr(self, function.__name__, function) or function
    server = Server()
    selector.register(server)
    monkeypatch.chdir(root)
    got = server.g6_select('review multiplication', phase='verify')
    assert got['learned']['retained_checks'] == 1
    assert any('history(' in by for c in got['candidates'] for by in c['ranked_by'])


def test_resolution_exact_calls_match_registered_schema():
    from g6.harness import resolution
    from g6.skills.corpus import _tools
    missing = {'id': 'R1', 'text': 'Return the sum.', 'status': 'not_established', 'checks': []}
    got = resolution.build({'requirements': [missing], 'completion': {}}, repo='fixture', task='addition')
    schemas = {e['call']['tool']: e['schema'] for e in _tools()}
    call = got[0]['cheapest_check']
    assert set(call['arguments']) <= set(schemas[call['tool']]['properties'])
    assert set(schemas[call['tool']].get('required', [])) <= set(call['arguments'])
    assert got[0]['needs']['model'] is True
