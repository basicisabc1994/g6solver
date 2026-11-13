"""Controls for review 3: probes that pass on this commit, so a broken instrument cannot stand in for a finding."""
import array
import sys

import pytest

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAssuranceOrchestrator, EvidenceArtifact, Hypothesis, LLMProposer, MetacognitiveMethodSelector,
    MethodRouter, MethodVariant, Observation, ProblemSignature, ToolSpec, check_beliefs, extract_tree, snapshot,
)
from g6.cognitive_bridge.block import CognitiveBridgeBlock
from g6.cognitive_bridge.codes import INPUT
from tests.cognitive_bridge.followup4_repair3_support import (
    NoModel, compile_inputs, count_reaches, observe_walk, second_writer, watch_encodings, watch_hashes,
)


def _claim(name):
    return ClaimIR(name, 's', ('formal',), {'formal': {'formula': 'A'}})


# finding 1: the instrument, and the caps without a second writer
def test_twenty_claims_are_planned_and_twenty_one_refused_when_nothing_writes():
    claims = [_claim(f'c{i}') for i in range(20)]
    with second_writer(claims, claims[0], []) as writer:
        plan = MethodRouter().plan(claims)
    assert writer['done'] and len(plan.claims) == 20 and len(plan.steps) == 20
    with pytest.raises(ValueError, match='20 typed claims'):
        MethodRouter().plan(claims + [_claim('one-more')])


def test_one_hundred_and_one_variants_are_refused_when_nothing_writes():
    variants = [MethodVariant(f'v{i}', 'formal', 'SMT', 'formal', 'check_validity', 1) for i in range(101)]
    with pytest.raises(ValueError, match='at most 100'):
        MetacognitiveMethodSelector().select(ProblemSignature('review', 'logic', 'validity', 1, 1000), variants,
                                             required_methods=['formal'])


# finding 2: the encoding probe sees an admitted model, and the block plans it
def test_the_block_plans_a_model_under_the_cap_and_the_probe_sees_its_encodings(monkeypatch):
    formula = 'A' * 240_000
    encoded = watch_encodings(monkeypatch, formula)
    answer = CognitiveBridgeBlock().infer({'op': 'plan_methods', 'claims': [
        {'id': 'c', 'statement': 's', 'evidence_needs': ['formal'], 'models': {'formal': {'formula': formula}}}]}).unwrap()
    assert answer['completion_state'] == 'qualified-draft' and len(answer['steps']) == 1
    assert encoded, 'the probe saw no encoding of a model the block took'


def test_the_router_itself_refuses_an_oversized_model_before_any_encoding(monkeypatch):
    # repair 2's own claim for the record path, which holds: the cap is read where a ClaimIR is detached
    formula = 'A' * 250_001
    claim = ClaimIR('c', 's', ('formal',), {'formal': {'formula': formula}})
    encoded = watch_encodings(monkeypatch, formula)
    with pytest.raises(ValueError, match='250 kB'):
        MethodRouter().plan([claim])
    assert encoded == []


# finding 3: a whole record is copied, and a missing field is named where no declared check comes first
def test_a_whole_claim_is_copied_and_a_fieldless_undeclared_record_is_refused_by_the_boundary():
    assert snapshot.snapshot(_claim('c')) == _claim('c')
    with pytest.raises(ValueError, match='missing record field'):
        snapshot.snapshot(object.__new__(ProblemSignature))


# finding 4: the hash probe sees a hash the bridge is meant to take; JSON models compile
def test_the_hash_probe_sees_an_artifact_digest_and_json_models_compile(monkeypatch):
    content = b'report bytes'
    hashed = watch_hashes(monkeypatch, content)
    artifact = EvidenceArtifact('a', content, 'report', 'producer', 'ref', 'claim')
    hashed.clear()
    assert len(artifact.digest) == 64 and hashed == ['hashed']
    compiled = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector()).compile(**compile_inputs())
    assert compiled.completion_state == 'qualified-draft'


# finding 5: the reach counter counts, and distinct small models stay far inside the budget
def test_distinct_small_models_compile_within_the_visit_budget(monkeypatch):
    inputs = compile_inputs()
    inputs['variant_models'] = {'v': {'formula': 'A', 'premises': ['A']}, **{f'w{i}': {'formula': 'A'} for i in range(50)}}
    reaches = count_reaches(monkeypatch)
    CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector()).compile(**inputs)
    assert 0 < reaches[0] <= snapshot.MAX_ITEMS


# finding 6: review 2's finding 2 is closed for a raw dict, and a whole record is taken
def test_a_foreign_key_in_a_raw_dict_is_refused_uncompared():
    comparisons = []

    class Key:
        def __hash__(self):
            return hash('probes')

        def __eq__(self, other):
            comparisons.append(other)
            return False

    answer = CognitiveBridgeBlock().infer({Key(): None}).unwrap()
    assert answer['code'] == INPUT and comparisons == []
    assert check_beliefs([Hypothesis('a', 's', 0.5), Hypothesis('b', 's', 0.5)]) == {'a': 0.5, 'b': 0.5}


# finding 7: review 2's finding 1 is closed, and a subclass with no hook is converted
def test_an_array_subclass_with_no_hook_is_converted_and_an_instance_tolist_is_ignored():
    ran = []

    class Plain(array.array):
        pass

    shadowed = Plain('b', [3])
    shadowed.tolist = lambda: ran.append('instance tolist') or [0] * 100_001

    class Estimator:
        tree_ = None
        n_features_in_ = shadowed

    with pytest.raises(ValueError, match='not a number'):
        extract_tree(Estimator())
    assert ran == []


@pytest.mark.skipif(sys.version_info < (3, 12), reason='__release_buffer__ is Python 3.12 (PEP 688)')
def test_python_calls_release_buffer_on_an_array_subclass():
    ran = []

    class Hooked(array.array):
        def __release_buffer__(self, view):
            ran.append('ran')

    memoryview(Hooked('b', [1])).release()
    assert ran == ['ran']


# finding 8: a foreign key of an ordinary class is refused by name
def test_a_foreign_key_of_an_ordinary_class_is_named_in_its_refusal():
    class Key:
        pass

    with pytest.raises(ValueError, match='Key key is not text'):
        snapshot.snapshot({Key(): 1})


# finding 9: a list of 33 hypotheses is refused before it is copied (repair 2's claim, which holds)
def test_a_list_of_thirty_three_hypotheses_is_refused_before_it_is_copied():
    hypotheses = [Hypothesis(f'h{i}', 's', 1 / 33) for i in range(33)]
    proposer = LLMProposer(NoModel({'probes': []}), {'read': ToolSpec('read', lambda q, a: Observation(True, 'r'))})
    with observe_walk(lambda raw: raw is hypotheses) as copied:
        with pytest.raises(ValueError, match='32 competing hypotheses'):
            proposer.propose('objective', hypotheses, [])
    assert copied == []


def test_the_walk_probe_sees_a_mapping_of_two_hypotheses_detached():
    hypotheses = {f'h{i}': Hypothesis(f'h{i}', 's', 0.5) for i in range(2)}
    with observe_walk(lambda raw: raw is hypotheses) as copied:
        snapshot.snapshot(hypotheses)
    assert copied == ['walk']


# finding 10: an ordinary numeric array is admitted and converted within the budget
def test_an_ordinary_numpy_array_of_names_is_converted():
    numpy = pytest.importorskip('numpy')

    class Estimator:
        tree_ = None
        n_features_in_ = numpy.int64(2)
        feature_names_in_ = numpy.array(['a', 'a'], dtype=object)

    with pytest.raises(ValueError, match='feature names must be unique'):
        extract_tree(Estimator())  # read as two names, then refused by the tree's own rule


def test_a_memoryview_of_bytes_is_converted():
    from g6.cognitive_bridge import xai
    assert xai._native(memoryview(b'abc'), where='a buffer') == [97, 98, 99]
