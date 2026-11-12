"""Controls for review 4: probes that pass on this commit, so a broken instrument cannot stand in for a finding."""
import array
import ctypes
import threading

import pytest

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAssuranceOrchestrator, CognitiveInput, Hypothesis, MetacognitiveMethodSelector, MethodPlan,
    MethodRouter, MixedMethodExecutor, check_beliefs, evaluated_trace_rows, snapshot, xai,
    ACQUISITION_FEATURES, UTILITY_TARGET,
)
from g6.cognitive_bridge.block import ClaimInput, CognitiveBridgeBlock
from g6.cognitive_bridge.codes import NotPlainData
from tests.cognitive_bridge.followup5_support import (
    FakeRegistry, claim, count_reaches, peak_of, predictive_inputs, raw_claim, watch_encodings,
    writer_before_the_take,
)


# finding 1: the memory probe sees a copy, and a small refusal stays small
def test_the_memory_probe_sees_the_copy_of_an_admitted_list():
    value = [None] * 50_000
    taken, peak = peak_of(lambda: snapshot.snapshot(value, where='a list'))
    assert len(taken) == 50_000 and peak > 400_000


def test_a_list_just_past_the_budget_is_refused():
    with pytest.raises(NotPlainData, match='size exceeds'):
        snapshot.snapshot([None] * 100_001, where='a list')


# finding 2: values well inside the budget are planned, compiled and taken
def test_five_claims_each_with_a_model_under_the_cap_are_planned():
    formula = 'x' * 240_000
    claims = [ClaimIR(f'c{i}', 's', ('formal',), {'formal': {'formula': formula}}) for i in range(5)]
    assert len(MethodRouter().plan(claims).steps) == 5


def test_a_small_compilation_is_taken_by_execute_and_its_step_reaches_the_door():
    registry = FakeRegistry()
    orchestrator = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=registry)
    compiled = orchestrator.compile(**predictive_inputs(1000))
    answer = orchestrator.execute(compiled, approved=True)
    assert 'cannot be taken' not in answer.get('reason', '')
    assert answer['code'] != 'G6_E_COGNITIVE_EXECUTION_NOT_APPROVED'


def test_a_model_past_the_cap_is_still_refused_by_the_router():
    with pytest.raises(ValueError, match='250 kB'):
        MethodRouter().plan([claim('c', {'formal': {'formula': 'A' * 250_001}})])


# finding 3: the reach probe counts, and a model inside the budget is planned by the block
def test_the_block_plans_a_model_inside_the_budget_and_the_probe_counts_its_reaches(monkeypatch):
    raw = {'op': 'plan_methods', 'claims': [raw_claim('c', {'formal': {'formula': 'A', 'premises': ['A'] * 9000}})]}
    reaches = count_reaches(monkeypatch)
    answer = CognitiveBridgeBlock().infer(raw).unwrap()
    assert answer['completion_state'] == 'qualified-draft' and len(answer['steps']) == 1
    assert reaches[0] > 9000


def test_the_block_refuses_a_model_past_the_budget_on_its_own_within_the_visit_budget(monkeypatch):
    # one model past the budget by itself: the first walk of it refuses, so the count is the budget's
    raw = {'op': 'plan_methods', 'claims': [raw_claim('c', {'formal': {'formula': 'A', 'premises': ['A'] * 120_000}})]}
    reaches = count_reaches(monkeypatch)
    answer = CognitiveBridgeBlock().infer(raw).unwrap()
    assert 'size exceeds' in answer['reason'] and reaches[0] <= 3 * snapshot.MAX_ITEMS


def test_compile_refuses_an_aliased_model_mapping_within_the_visit_budget(monkeypatch):
    # review 3's finding 5, which repair 3 closed at this entry
    inputs = predictive_inputs(10)
    model = {'formula': 'A', 'premises': ['A'] * 5000}
    inputs['variant_models'] = {f'v{i}': model for i in range(200)}
    reaches = count_reaches(monkeypatch)
    with pytest.raises(ValueError, match='size exceeds'):
        CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector()).compile(**inputs)
    assert reaches[0] <= 3 * snapshot.MAX_ITEMS


# finding 4: an ordinary foreign type is refused by name, and a metaclass does not answer for it
def test_an_ordinary_foreign_key_is_refused_by_its_name():
    class Key:
        pass

    with pytest.raises(NotPlainData, match='Key key is not text'):
        snapshot.snapshot({Key(): 1}, where='a value')


def test_a_metaclass_does_not_name_a_refused_key():
    ran = []

    class Meta(type):
        def __getattribute__(cls, name):
            ran.append(name)
            return type.__getattribute__(cls, name)

    class Key(metaclass=Meta):
        pass

    value = {Key(): 1}
    assert Key.__qualname__.endswith('Key') and ran == ['__qualname__'], 'the probe sees a lookup the metaclass answers'
    ran.clear()
    with pytest.raises(NotPlainData):
        snapshot.snapshot(value, where='a value')
    assert ran == []


# finding 5: the same entries take a list of the right kind, and refuse a mapping past the cap
def test_lists_of_records_and_a_scored_history_are_taken():
    assert len(MethodRouter().plan([claim('a'), claim('b')]).steps) == 2
    assert check_beliefs([Hypothesis('a', 's', 0.5), Hypothesis('b', 's', 0.5)]) == {'a': 0.5, 'b': 0.5}
    row = {**{name: 1.0 for name in (*ACQUISITION_FEATURES, UTILITY_TARGET)}, 'evaluator_ref': 'e'}
    assert len(evaluated_trace_rows([row])) == 1


def test_a_mapping_of_twenty_one_claims_is_refused_by_the_tally():
    with pytest.raises(ValueError, match='20 typed claims'):
        snapshot.snapshot({str(i): claim(str(i)) for i in range(21)}, where='claims')


# finding 6: the formats the rule does name
def test_native_scalar_arrays_convert_and_a_struct_view_is_refused():
    assert xai._native(array.array('d', [1.5, 2.5]), where='an array') == [1.5, 2.5]
    assert xai._native(array.array('q', [1, 2]), where='an array') == [1, 2]
    class Pair(ctypes.Structure):
        _fields_ = [('a', ctypes.c_double), ('b', ctypes.c_double)]

    with pytest.raises(NotPlainData, match='native scalar'):
        xai._native(memoryview((Pair * 2)()), where='an array')


def test_numpy_arrays_of_one_scalar_convert_and_a_structured_one_is_refused():
    numpy = pytest.importorskip('numpy')
    assert xai._native(numpy.arange(3.0), where='an array') == [0.0, 1.0, 2.0]
    assert xai._native(numpy.arange(3), where='an array') == [0, 1, 2]
    assert xai._native(numpy.float64(1.5), where='a scalar') == 1.5
    assert xai._native(numpy.array(['a', 'bc']), where='names') == ['a', 'bc']
    assert xai._native(numpy.array(['a', 'b'], dtype=object), where='names') == ['a', 'b']
    with pytest.raises(NotPlainData, match='native scalar'):
        xai._native(numpy.zeros(3, dtype=[('a', 'f8'), ('b', 'f8')]), where='an array')


# finding 7: the encoding probe and the writer, and the cap when the model is over it from the start
def test_a_model_over_the_cap_from_the_start_is_refused_by_the_block_before_any_encoding(monkeypatch):
    over = 'B' * 250_001
    raw = {'op': 'plan_methods', 'claims': [raw_claim('c', {'formal': {'formula': over}})]}
    encoded = watch_encodings(monkeypatch, over)
    answer = CognitiveBridgeBlock().infer(raw).unwrap()
    assert '250 kB' in answer['reason'] and encoded == []


def test_the_encoding_probe_sees_a_model_the_block_took_and_the_writer_runs(monkeypatch):
    formula = 'A' * 1000
    raw = {'op': 'plan_methods', 'claims': [raw_claim('c', {'formal': {'formula': formula}})]}
    encoded = watch_encodings(monkeypatch, formula)
    with writer_before_the_take(raw, lambda: None) as writer:
        answer = CognitiveBridgeBlock().infer(raw).unwrap()
    assert writer['done'] and encoded and answer['completion_state'] == 'qualified-draft'


# places the author named as not exercised
def test_a_real_second_thread_never_gets_more_than_twenty_claims_planned():
    extra = claim('late')
    for _ in range(20):
        claims = [claim(f'c{i}') for i in range(20)]
        stop = threading.Event()

        def write():
            while not stop.is_set():
                claims.append(extra)
                claims.pop()

        thread = threading.Thread(target=write)
        thread.start()
        try:
            try:
                planned = len(MethodRouter().plan(claims).claims)
            except ValueError:
                planned = 0
        finally:
            stop.set()
            thread.join()
        assert planned <= 20


def test_a_typed_input_and_a_plan_of_twenty_claims_are_read_from_their_attribute_mappings():
    typed = CognitiveInput(op='plan_methods', claims=[ClaimInput(**raw_claim('c'))])
    assert CognitiveBridgeBlock().infer(typed).unwrap()['completion_state'] == 'qualified-draft'
    plan = MethodRouter().plan([claim(f'c{i}') for i in range(20)])
    assert type(snapshot.snapshot(plan, where='plan')) is MethodPlan
    answer = MixedMethodExecutor(FakeRegistry()).execute(plan, approved=True)
    assert answer['code'] != 'G6_E_COGNITIVE_PLAN_TAMPERED'


def test_a_record_with_a_missing_field_is_the_boundarys_refusal_and_the_executor_answers():
    plan = MethodRouter().plan([claim('c')])
    del plan.claims[0].__dict__['statement']
    with pytest.raises(NotPlainData, match='missing record field statement'):
        snapshot.snapshot(plan, where='plan')
    answer = MixedMethodExecutor(FakeRegistry()).execute(plan, approved=True)
    assert answer['code'] == 'G6_E_COGNITIVE_PLAN_TAMPERED'
