# SPDX-License-Identifier: Apache-2.0
"""Publication, record conversion and collaborator inventories, derived from code."""
from __future__ import annotations

import ast
from collections import Counter
import dataclasses
import inspect
import json
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6.cognitive_bridge as bridge
from g6.cognitive_bridge import codes
from g6.cognitive_bridge.snapshot import NotPlainData, canonical_json

PACKAGE = Path(codes.__file__).parent

# Construction is the input boundary: records are composed here and checked in
# full when published by an action. Capability factories retain injected code.
CAPABILITIES = {
    'BayesianUpdater', 'CognitiveAgent', 'CognitiveBridgeBlock', 'InformationGainSelector',
    'LLMProposer', 'LLMIdeaGenerator', 'MLAcquisitionSelector', 'StaticProposer', 'MethodRouter',
    'MixedMethodExecutor', 'LLMClaimCompiler', 'MetacognitiveMethodSelector', 'MetacognitivePlanner',
    'InProcessAssuranceEvaluator', 'LLMMetacognitiveCritic', 'CompositionalFailureModel',
    'AdaptationPromotionGate', 'CognitiveAssuranceOrchestrator', 'CrossMethodConflictGate',
}


def test_every_exported_callable_is_classified():
    from g6.cognitive_bridge.snapshot import _record_type
    seen = set()
    for name in bridge.__all__:
        value = getattr(bridge, name)
        if not callable(value):
            continue
        if inspect.isfunction(value):
            seen.add(name)
        elif _record_type(value):
            assert dataclasses.is_dataclass(value) and '__post_init__' in vars(value)
        elif name == 'CognitiveInput':
            assert value.model_config['strict'] is True
        else:
            assert name in CAPABILITIES, f'unclassified public callable: {name}'
    assert seen == {name for name, fn in public_calls() if '.' not in name}
    assert CAPABILITIES <= set(bridge.__all__)


def public_calls():
    """No annotation exemptions: records, mixed tuples, leaves and properties count."""
    for name in bridge.__all__:
        value = getattr(bridge, name)
        if inspect.isfunction(value):
            yield name, value
        elif inspect.isclass(value):
            for attr, member in vars(value).items():
                if attr.startswith('_'):
                    continue
                member = member.fget if isinstance(member, property) else member
                member = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
                if inspect.isfunction(member) and member.__module__.startswith('g6.cognitive_bridge'):
                    yield f'{name}.{attr}', member
        else:
            assert not callable(value), f'unclassified public callable: {name}'


def test_every_public_return_has_one_publication_step():
    # A publication is explicit on every return path, even a record or scalar.
    bad = []
    for name, fn in public_calls():
        node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
        nested = {id(n) for child in ast.walk(node) if isinstance(child, (ast.FunctionDef, ast.Lambda))
                  and child is not node for n in ast.walk(child)}
        for ret in (n for n in ast.walk(node) if isinstance(n, ast.Return) and id(n) not in nested):
            if ret.value is None or isinstance(ret.value, ast.Constant) and ret.value.value is None:
                continue  # a lifecycle hook has no data answer
            if not (isinstance(ret.value, ast.Call) and isinstance(ret.value.func, ast.Name)
                    and ret.value.func.id == 'publish'):
                bad.append((name, ast.unparse(ret)))
    assert not bad, bad


def test_every_recursive_serializer_is_the_bounded_encoder():
    places = []
    for path in PACKAGE.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call):
                callee = ast.unparse(node.func)
                if callee in ('asdict', 'dataclasses.asdict', 'json.dumps'):
                    places.append((path.name, callee))
    assert places == [('snapshot.py', 'json.dumps')], places


def test_collaborator_reads_are_classified_before_use():
    # Reuse the exhaustive path finder, without copying its list of known places.
    from tests.cognitive_bridge import test_read_inventory as reads
    reads.test_the_reads_are_listed_from_the_code()
    reads.test_no_read_is_left_unclassified()
    tree = ast.parse((PACKAGE / 'xai.py').read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_feature_names')
    assert not any(isinstance(n, ast.BoolOp) and any(isinstance(c, ast.Call)
                   and isinstance(c.func, ast.Name) and c.func.id == '_native' for c in ast.walk(n))
                   for n in ast.walk(fn)), 'metadata must be read by kind before fallback'


@pytest.mark.parametrize('names', [False, 0, None, '', True, 1, 'x', {}, b'x', {1}])
def test_present_malformed_names_have_the_registered_code_and_path(names):
    estimator = SimpleNamespace(n_features_in_=1, feature_names_in_=names,
                                tree_=SimpleNamespace(children_left=[-1], children_right=[-1], value=[[[1.0]]]))
    with pytest.raises(NotPlainData) as caught:
        bridge.extract_tree(estimator)
    assert codes.INPUT in str(caught.value)
    assert 'feature_names_in_' in str(caught.value)


@pytest.mark.parametrize('depth', [0, 1, codes.DEPTH, codes.DEPTH + 1, 1200])
def test_encoder_bounds_before_serialization(depth):
    value = 0
    for _ in range(depth):
        value = [value]
    if depth <= codes.DEPTH:
        assert canonical_json(value)
    else:
        with pytest.raises(NotPlainData, match='nested deeper'):
            canonical_json(value)


def test_bounded_encoder_limits_size():
    with pytest.raises(NotPlainData, match='size'):
        canonical_json(['x' * 1024] * 5000)


@pytest.mark.parametrize('edges', [codes.DEPTH - 2, codes.DEPTH - 1])
def test_composed_record_at_and_past_the_publication_limit(edges):
    root = bridge.TreeNode(value=0)
    for _ in range(edges):
        root = bridge.TreeNode(feature=0, threshold=0.5, left=root)
    tree = bridge.ExplicitTree(('x',), root, 'regression', 'tree_transcription')
    if edges == codes.DEPTH - 2:
        assert tree.to_dict()['features'] == ['x']
    else:
        with pytest.raises(NotPlainData, match='nested deeper'):
            tree.to_dict()


def test_digest_keeps_its_bytes_at_the_depth_limit():
    from g6.cognitive_bridge.snapshot import digest
    from hashlib import sha256
    value = 0
    for _ in range(codes.DEPTH):
        value = [value]
    old_bytes = json.dumps({'record': '', 'value': value}, sort_keys=True,
                          separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
    assert digest(value) == sha256(old_bytes).hexdigest()


@pytest.mark.parametrize('value', [{}, {'verified': True}, [], [0], ['x', 'x']])
def test_a_static_proposal_validates_records_and_their_probe_rules(value):
    hypotheses = [bridge.Hypothesis('a', 'first', .5), bridge.Hypothesis('b', 'second', .5)]
    with pytest.raises((TypeError, ValueError)):
        bridge.StaticProposer([value]).propose('test', hypotheses, [])


@pytest.mark.parametrize('names', [[], ['x', 'x'], [0], [None]])
def test_present_malformed_name_arrays_are_not_defaulted(names):
    estimator = SimpleNamespace(n_features_in_=1, feature_names_in_=names,
                                tree_=SimpleNamespace(children_left=[-1], children_right=[-1], value=[[[1.0]]]))
    with pytest.raises(NotPlainData) as caught:
        bridge.extract_tree(estimator)
    assert 'feature_names_in_' in str(caught.value)


def test_only_absent_metadata_gets_generated_names():
    estimator = SimpleNamespace(n_features_in_=1,
                                tree_=SimpleNamespace(children_left=[-1], children_right=[-1], value=[[[1.0]]]))
    assert bridge.extract_tree(estimator).features == ('x0',)


def test_a_subclass_cannot_run_truth_testing_before_the_boundary():
    class Names(list):
        def __bool__(self):
            pytest.fail('collaborator value used before the boundary')
    estimator = SimpleNamespace(n_features_in_=1, feature_names_in_=Names(), tree_=SimpleNamespace())
    with pytest.raises(NotPlainData):
        bridge.extract_tree(estimator)


@pytest.mark.parametrize('field,value', [('question', ''), ('cost', -1), ('assumed_match_probability', .2),
                                       ('positive_if', {'a': True})])
def test_static_proposals_apply_the_shared_probe_rule(field, value):
    probe = dataclasses.replace(bridge.Probe('p', 'question', 'tool', {'a': True, 'b': False}, .8),
                                **{field: value})
    hypotheses = [bridge.Hypothesis('a', 'first', .5), bridge.Hypothesis('b', 'second', .5)]
    with pytest.raises(ValueError):
        bridge.StaticProposer([probe]).propose('test', hypotheses, [])


def test_encoder_checks_depth_before_calling_the_recursive_serializer(monkeypatch):
    from g6.cognitive_bridge import snapshot
    calls = []
    original = snapshot.json.dumps

    def capture(value, **kwargs):
        calls.append(value)
        return original(value, **kwargs)

    value = 0
    for _ in range(codes.DEPTH + 1):
        value = [value]
    monkeypatch.setattr(snapshot.json, 'dumps', capture)
    with pytest.raises(NotPlainData):
        canonical_json(value)
    assert calls == []


def conversion_sites():
    """Each record projection, encoder call and delegation to a public record reader."""
    found = Counter()

    def walk(node, module, prefix=''):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = f'{prefix}.{node.name}' if prefix else node.name
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name in ('view', 'record_data', 'canonical_json', 'dataclasses.fields') or (
                    isinstance(node.func, ast.Attribute) and node.func.attr in ('to_dict', 'manifest')):
                callee = node.func.attr if isinstance(node.func, ast.Attribute) else name
                found[(module, prefix, callee)] += 1
        for child in ast.iter_child_nodes(node):
            walk(child, module, prefix)

    for path in PACKAGE.glob('*.py'):
        walk(ast.parse(path.read_text(encoding='utf-8')), path.name)
    return found


def test_record_conversion_inventory_has_no_unclassified_path():
    assert conversion_sites() == Counter(RECORD_CONVERSIONS)
    # Projection and encoding are bounded in the boundary module. Every other
    # record method is checked by the publication inventory above.
    for (module, fn, callee), count in RECORD_CONVERSIONS.items():
        if callee in ('view', 'fields'):
            assert module == 'snapshot.py', (module, fn, callee)


# Inventory from the component source; a new site must be classified explicitly.
RECORD_CONVERSIONS = {
    ('adaptation.py', 'CompositionalFailureModel.map', 'record_data'): 2,
    ('assurance.py', 'CandidateDossier.from_payload', 'canonical_json'): 1,
    ('assurance.py', 'CandidateDossier.intact', 'canonical_json'): 1,
    ('assurance.py', 'DomainStandard.digest', 'canonical_json'): 1,
    ('assurance.py', 'DomainStandard.digest', 'manifest'): 1,
    ('assurance.py', 'InProcessAssuranceEvaluator.evaluate', 'manifest'): 3,
    ('assurance.py', 'InProcessAssuranceEvaluator.review_scope', 'canonical_json'): 1,
    ('assurance.py', '_evidence', 'manifest'): 2,
    ('assurance.py', 'evidence_sha256', 'canonical_json'): 1,
    ('block.py', 'CognitiveBridgeBlock.infer', 'to_dict'): 2,
    ('block.py', '_unchanged', 'canonical_json'): 2,
    ('critique.py', 'CritiqueReport.to_dict', 'record_data'): 1,
    ('critique.py', 'LLMMetacognitiveCritic.propose', 'canonical_json'): 1,
    ('engine.py', 'LLMIdeaGenerator.generate', 'canonical_json'): 1,
    ('engine.py', 'LLMProposer.propose', 'canonical_json'): 1,
    ('engine.py', '_ToolDoor.invoke', 'record_data'): 1,
    ('engine.py', '_digest', 'canonical_json'): 1,
    ('epistemic.py', 'assertions_sha256', 'canonical_json'): 2,
    ('epistemic.py', 'assertions_sha256', 'record_data'): 1,
    ('metacognition.py', 'MetacognitiveMethodSelector.select', 'canonical_json'): 1,
    ('metacognition.py', 'MetacognitiveMethodSelector.select', 'record_data'): 1,
    ('metacognition.py', 'MetacognitivePlanner.plan', 'to_dict'): 5,
    ('metacognition.py', 'MethodVariant.definition_digest', 'canonical_json'): 1,
    ('mixed_methods.py', 'LLMClaimCompiler.draft', 'canonical_json'): 1,
    ('mixed_methods.py', 'MethodPlan.to_dict', 'record_data'): 2,
    ('mixed_methods.py', 'MixedMethodExecutor.execute', 'canonical_json'): 1,
    ('mixed_methods.py', 'step_digest', 'canonical_json'): 1,
    ('mixed_methods.py', 'step_digest', 'record_data'): 1,
    ('orchestration.py', 'CognitiveAssuranceOrchestrator._executed', 'canonical_json'): 1,
    ('orchestration.py', 'CognitiveAssuranceOrchestrator.assess', 'record_data'): 1,
    ('orchestration.py', 'CognitiveAssuranceOrchestrator.assess', 'to_dict'): 2,
    ('orchestration.py', 'CognitiveAssuranceOrchestrator.compile', 'canonical_json'): 1,
    ('orchestration.py', 'CognitiveAssuranceOrchestrator.execute', 'canonical_json'): 1,
    ('orchestration.py', 'CognitiveAssuranceOrchestrator.execution_artifact', 'canonical_json'): 1,
    ('orchestration.py', 'CompiledResearch.changed_since_compiled', 'canonical_json'): 1,
    ('orchestration.py', 'CompiledResearch.to_dict', 'to_dict'): 2,
    ('orchestration.py', '_claims_digest', 'canonical_json'): 1,
    ('orchestration.py', '_plan_digest', 'canonical_json'): 1,
    ('orchestration.py', '_plan_digest', 'record_data'): 1,
    ('snapshot.py', '_record_fields', 'fields'): 1,
    ('snapshot.py', '_bounded_view', 'view'): 1,
    ('snapshot.py', '_kinds', 'fields'): 2,
    ('snapshot.py', '_admit_fields', 'fields'): 1,
    ('snapshot.py', '_renewed', 'fields'): 1,
    ('snapshot.py', '_admit_json', 'view'): 1,
    ('snapshot.py', 'digest', 'canonical_json'): 2,
    ('snapshot.py', 'plain', 'canonical_json'): 1,
    ('snapshot.py', 'publish', 'record_data'): 1,
    ('snapshot.py', 'record.__post_init__', 'fields'): 1,
    ('snapshot.py', 'record_data', 'canonical_json'): 1,
    ('snapshot.py', 'returned', 'view'): 2,
    ('snapshot.py', 'view', 'fields'): 1,
    ('snapshot.py', 'view', 'view'): 3,
    ('xai.py', 'ExplicitTree.to_dict', 'record_data'): 1,
    ('xai.py', 'distill_blackbox', 'canonical_json'): 2,
}
