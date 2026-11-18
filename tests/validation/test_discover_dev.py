# SPDX-License-Identifier: Apache-2.0
"""Seen development-data replay reporting, with a fake review; no confinement claim."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def dev(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'validation/worked_examples/discover_dev.py'
    spec = importlib.util.spec_from_file_location('discover_dev', path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def record(**kw):
    return {'patch_id': 'recorded-round-1', 'repo': 'prepared-repository', 'base': 'base',
            'task': 'Keep calc.scale', 'requirements': [], 'artifact_digest': 'a' * 64,
            'patch_file': 'archived.patch', 'patch_sha256': 'b' * 64, **kw}


def finding():
    return {'check': 'discover-one', 'result': 'counterexample', 'reproduction': {
        'generated_by': 'G6 automatic discovery', 'one_line': 'calc.scale(-1): -2 -> 2',
        'differences': [{'input': json.dumps({'args': [-1], 'kwargs': {}}, sort_keys=True),
                         'base': '-2', 'patch': '2'}]}}


def test_replay_enables_discovery_and_binds_prepared_snapshot(dev):
    calls = []
    def review(repo, **kw):
        calls.append((repo, kw))
        return {'ok': True, 'assessment': {'outcome': 'insufficient_evidence', 'completion': {
            'defects_reproduced': {'proposed_checks': [finding()]}, 'activities': []}}}
    result = dev.replay([record()], reviewer=review)
    assert 'already seen' in result['label'] and 'development' in result['label']
    [row] = result['patches']
    assert row['reproduced_counterexamples'][0]['maintainer_match'] is None
    assert row['alarm_on_maintainer_passing_patch'] is None
    assert calls[0][1]['search']['discovery'] is True
    assert calls[0][1]['expected_artifact_digest'] == 'a' * 64


def test_exact_recorded_maintainer_outcomes_can_match_a_reproduction(dev):
    facts = [{'test': 'tests/test_calc.py::test_negative', 'function': 'calc.scale',
              'input': {'args': [-1], 'kwargs': {}}, 'expected': -2, 'actual': 2,
              'evidence': 'recorded maintainer failure log, call case negative'}]
    one = finding()
    one['reproduction']['function'] = 'calc.scale'
    assert dev.maintainer_match(one, facts) == (True, ['tests/test_calc.py::test_negative'])
    facts[0]['actual'] = 3
    assert dev.maintainer_match(one, facts) == (False, [])
    assert dev.maintainer_match(one, []) == (None, [])


def test_exact_match_preserves_json_types(dev):
    one = finding()
    one['reproduction']['function'] = 'calc.scale'
    one['reproduction']['differences'][0]['patch'] = 'true'
    facts = [{'test': 'test_x', 'function': 'calc.scale', 'input': {'args': [-1], 'kwargs': {}},
              'expected': -2, 'actual': 1, 'evidence': 'recorded failure'}]
    assert dev.maintainer_match(one, facts) == (False, [])


def test_passing_maintainer_patch_with_counterexample_is_an_alarm(dev):
    def review(*a, **kw):
        return {'ok': True, 'assessment': {'completion': {'defects_reproduced': {
            'proposed_checks': [finding()]}}}}
    row = dev.replay([record(maintainer_passed=True)], reviewer=review)['patches'][0]
    assert row['alarm_on_maintainer_passing_patch'] is True


def test_refused_review_is_unknown_never_zero_detections(dev):
    row = dev.replay([record()], reviewer=lambda *a, **kw: {'ok': False, 'error': 'no isolation'})['patches'][0]
    assert row['status'] == 'not_run'
    assert row['reproduced_counterexamples'] is None
    assert 'no isolation' in row['reason']


def test_manifest_refuses_executable_fields_before_review(dev):
    calls = []
    with pytest.raises(ValueError):
        dev.replay([record(source='print(1)')], reviewer=lambda *a, **kw: calls.append(kw))
    assert not calls
