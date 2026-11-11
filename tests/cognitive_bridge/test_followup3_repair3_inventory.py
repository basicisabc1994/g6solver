# SPDX-License-Identifier: Apache-2.0
"""Every child loop, recursion and native serializer is classified from source."""
import ast
import sys
from pathlib import Path

import pytest

from g6.cognitive_bridge import Probe, StaticProposer, codes, snapshot
from tests.cognitive_bridge.test_depth_inventory import _functions


GATES = {'snapshot', 'plain', 'fresh', 'returned', '_walk', '_plain', 'text', 'number',
         '_admit_json', '_prompt_value',
         'only_fields', 'checked_report', 'checked_selection', '_arguments', '_native', '_estimated', 'canonical_json',
         'view', '_answered'}

# Every route checked below is named. A boundary call somewhere in a new
# function is insufficient evidence that its child loops consume that result.
CHECKED_ROUTES = set('''
adaptation.py:detect_failure_modes
adaptation.py:CompositionalFailureModel.map
adaptation.py:protected_paths
adaptation.py:AdaptationPromotionGate.__init__
adaptation.py:AdaptationPromotionGate.assess
assurance.py:InProcessAssuranceEvaluator.review_scope
assurance.py:InProcessAssuranceEvaluator.evaluate
block.py:CognitiveBridgeBlock._refused
block.py:CognitiveBridgeBlock.infer
codes.py:FrozenMap.__hash__
codes.py:FrozenMap.__reduce__
codes.py:FrozenMap.__repr__
codes.py:only_fields
codes.py:refusal
codes.py:settled
critique.py:LLMMetacognitiveCritic.propose
engine.py:check_beliefs
engine.py:expected_information_gain
engine.py:InformationGainSelector.rank
engine.py:BayesianUpdater.update
engine.py:StaticProposer.propose
engine.py:LLMProposer.propose
engine.py:MLAcquisitionSelector.rank
engine.py:_credited
engine.py:CognitiveAgent._observe
engine.py:CognitiveAgent.run
engine.py:LLMIdeaGenerator.generate
epistemic.py:assertions_sha256
epistemic.py:findings_state
epistemic.py:CrossMethodConflictGate.reconcile
metacognition.py:MetacognitiveMethodSelector.__init__
metacognition.py:MetacognitiveMethodSelector._score
metacognition.py:MetacognitiveMethodSelector.select
metacognition.py:MetacognitivePlanner.plan
mixed_methods.py:MethodRouter.plan
mixed_methods.py:MixedMethodExecutor._invoke
mixed_methods.py:MixedMethodExecutor._run
mixed_methods.py:MixedMethodExecutor.execute
mixed_methods.py:LLMClaimCompiler.draft
orchestration.py:CognitiveAssuranceOrchestrator.compile
orchestration.py:CognitiveAssuranceOrchestrator.execute
orchestration.py:CognitiveAssuranceOrchestrator.assess
quantitative.py:evaluated_trace_rows
quantitative.py:sklearn_payload
snapshot.py:canonical_json
snapshot.py:record_data
snapshot.py:plain
snapshot.py:_field
snapshot.py:record.__post_init__
snapshot.py:json_reply
xai.py:extract_tree
xai.py:distill_blackbox
'''.split())

# These routines consume an owned schema, a parser's bounded text, or only a
# detached result passed by a listed caller. No exemption is inferred by name.
OWNED = {
    'block.py:_admit_model_input': 'actual model MRO selects the owned schema before direct field validation',
    'block.py:_admit_input': 'owned fixed model topology, native lengths and numeric metadata bounds before detachment',
    'mixed_methods.py:_admit_claims': 'native 1..20 count before exact ClaimIR length admission; no conversion',
    'engine.py:LLMProposer._admit_reply': 'native parsed count admitted before per-item length and encoded-size checks',
    'engine.py:LLMIdeaGenerator._admit_reply': 'native parsed count admitted before field length checks',
    'critique.py:LLMMetacognitiveCritic._admit_reply': 'native parsed count before owned field metadata width checks',
    'snapshot.py:_admit_array': 'native buffer shape dimensions, before tolist; storage pinned through conversion',
    'snapshot.py:_admit_fields': 'owned record schema; byte counts and field lengths before copy',
    'snapshot.py:_encoded_size.string': 'exact escaped-size counter over detached text; charge stops at cap',
    'snapshot.py:_encoded_size.visit': 'detached depth/item-admitted projection from _admit_json; output counted before dumps',
    'snapshot.py:_admit_json': 'view detaches before validation; exact ASCII size admitted before any encoded output',
    'snapshot.py:generated': 'count preflight and root visit before tuple consumes the lazy items',
    'snapshot.py:generated.items': 'shared item/size budget checked before make(index), then the one walker',
    'codes.py:_state_fields': 'G6 door/envelope schema, created in this function',
    'codes.py:_is_type': 'owned tuple of allowed types; comparisons use identity',
    'codes.py:_has_member': 'actual class MRO and namespaces, executable schema',
    'codes.py:claims': '_bounded_claims detaches the root; recursive calls share its scope',
    'codes.py:_claims_nothing': 'result_faults supplies a detached result, or _read_card supplies the card',
    'codes.py:_no_reserved': 'refusal and settled detach their fields before this key check',
    'snapshot.py:_not_json': 'plain or canonical_json supplies the detached projection',
    'snapshot.py:_same': 'plain supplies the detached input and native JSON round trip',
    'snapshot.py:_frozen': 'plain supplies detached JSON data',
    'snapshot.py:_leaf_kind': 'owned annotation table',
    'snapshot.py:_protocol_kind': 'actual class MRO of an annotation',
    'snapshot.py:_kind': 'owned record annotations',
    'snapshot.py:_kinds': 'owned dataclass schema and annotations',
    'snapshot.py:_has_record': '_taken receives only the detached snapshot/fresh root',
    'snapshot.py:view': '_bounded_view detaches the root; recursion shares its scope',
    'snapshot.py:_unique': 'native JSON parser over text bounded by json_reply',
    'snapshot.py:issue': 'set() creates an empty owned issuance index; digest walks the issued value',
    'adaptation.py:judged_files': 'fixed tuple of bridge/G6 judge functions',
    'adaptation.py:_declared': 'owned ADAPTABLE paths and judge identities',
    'metacognition.py:MetacognitiveMethodSelector.select.search': 'selector-owned pools over detached variants',
    'xai.py:extract_tree.visit': 'indices into native arrays already detached by _native',
    'xai.py:_distinct_names': 'snapshot or _native supplies detached names',
    'engine.py:_beliefs': 'check_beliefs or a snapshotted action supplies the belief map',
    'engine.py:_validate_probe': 'caller check_beliefs/action has snapshotted probe and beliefs',
    'engine.py:_entropy': 'validated, detached probability iterable',
    'engine.py:_proposed': 'returned or StaticProposer supplies the detached proposal',
    'engine.py:_scores': 'returned supplies detached predictor scores',
    'engine.py:_posterior': 'returned supplies a detached posterior and checked beliefs',
    'engine.py:_selection': 'returned supplies the detached selection and checked ranking',
    'assurance.py:_evidence': 'evaluate snapshots evidence before this private checker is called',
    'metacognition.py:_eligible': 'select/checked_selection receive detached variants and context',
    'metacognition.py:checked_selection': 'returned supplies the detached selection; planners snapshot the comparison data',
    'orchestration.py:_plan_digest': 'record boundary or compile supplies the detached plan',
    'orchestration.py:compilation_gaps': 'record boundary or compile/execute supplies detached records',
    'orchestration.py:_claims_digest': 'compile/assess supplies detached claims',
    'orchestration.py:CognitiveAssuranceOrchestrator._every_step_ran': 'assess supplies the detached run and plan',
    'orchestration.py:CognitiveAssuranceOrchestrator._executed': 'assess supplies the detached run; parser reads this maker\'s stored bytes',
    'snapshot.py:_renewed': '_taken receives only the detached snapshot/fresh root',
    'snapshot.py:_taken': 'snapshot/fresh detach the entire root before calling this helper',
    'xai.py:_numbers': '_native/returned supply detached score arrays before this private leaf reader',
    'xai.py:_predictions': 'returned supplies a detached prediction array',
    'block.py:_as_given': 'all outer dict/model/protocol paths enter _walk before any enumeration',
}


def walk_sites():
    for module, name, fn, _ in _functions():
        nested = {id(n) for inner in ast.walk(fn) if isinstance(inner, (ast.FunctionDef, ast.Lambda))
                  and inner is not fn for n in ast.walk(inner)}
        nodes = [n for n in ast.walk(fn) if id(n) not in nested]
        loops = [n for n in nodes if isinstance(n, (ast.For, ast.While, ast.comprehension))]
        recursive = [n for n in nodes if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == fn.name]
        native = [n for n in nodes if isinstance(n, ast.Call)
                  and ast.unparse(n.func) in ('json.dumps', 'json.loads', 'dataclasses.asdict', 'asdict', 'dict.__repr__',
                      'list', 'dict', 'tuple', 'set', 'frozenset', 'sorted', 'sum', 'any', 'all', 'map', 'filter',
                      'zip', 'enumerate')]
        native += [n for n in nodes if isinstance(n, ast.Dict) and None in n.keys]
        if loops or recursive or native:
            yield module+':'+name, fn, loops, native


def classify(name, fn, loops, native):
    if name == 'snapshot.py:_walk':
        return 'the single raw-value visitor, with one shared budget'
    if name in OWNED:
        return OWNED[name]
    # A record's own methods and checks read only what record's pre-hook walker
    # detached. Discover record owners from their decorators, not a type list.
    module, _, owner = name.partition(':')
    source = ast.parse(Path(codes.__file__).with_name(module).read_text(encoding='utf-8'))
    records = {n.name for n in source.body if isinstance(n, ast.ClassDef)
               and any(ast.unparse(d) == 'record' for d in n.decorator_list)}
    if owner.split('.')[0] in records:
        return 'record data detached before the record hook; public record assignments are frozen'
    if name not in CHECKED_ROUTES:
        raise AssertionError(f'unclassified traversal: {name}')
    gates = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in GATES]
    # A gate wrapping a comprehension runs after that comprehension, so it
    # cannot admit its input. A preceding gate can supply the checked values.
    if gates and all(any(g.lineno < (loop.iter.lineno if isinstance(loop, ast.comprehension) else loop.lineno)
                          for g in gates) for loop in loops):
        return 'uses a preceding boundary read; entry-path probes challenge its inputs'
    if not loops and native and gates:
        return 'native encoder/parser receives the preceding boundary result'
    raise AssertionError(f'unclassified traversal: {name}')


def test_every_child_walk_has_a_route_and_unknown_walks_fail():
    listed = {name: classify(name, fn, loops, native) for name, fn, loops, native in walk_sites()}
    assert 'snapshot.py:_walk' in listed
    assert 'epistemic.py:assertions_sha256' in listed
    assert 'adaptation.py:protected_paths' in listed
    with pytest.raises(AssertionError, match='unclassified'):
        fn = ast.parse('def added(value):\n for child in value:\n  print(child)').body[0]
        classify('engine.py:added', fn, [fn.body[0]], [])
    with pytest.raises(AssertionError, match='unclassified'):
        fn = ast.parse('def added(value):\n snapshot(None)\n for child in value:\n  print(child)').body[0]
        classify('engine.py:added', fn, [fn.body[1]], [])
    assert CHECKED_ROUTES <= set(listed), 'remove or reclassify a route when its traversal changes'


def test_preflight_is_before_the_first_call_on_a_value():
    fn = next(fn for name, fn, _, _ in walk_sites() if name == 'snapshot.py:_walk')
    calls_on_value = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                      and any(isinstance(a, ast.Name) and a.id == 'value' for a in n.args)]
    guard = next(n for n in ast.walk(fn) if isinstance(n, ast.If)
                 and 'budget[0] >= MAX_ITEMS' == ast.unparse(n.test))
    assert all(n.lineno > guard.lineno for n in calls_on_value)
    recursive = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == '_walk']
    assert recursive and all(any(k.arg == 'budget' and ast.unparse(k.value) == 'budget'
                                for k in n.keywords) for n in recursive)


def test_secondary_walks_never_receive_the_original_containers():
    raw = {'k': [[1], [2]]}
    identities = {id(raw), id(raw['k']), *(id(x) for x in raw['k'])}
    names = {'_not_json', '_has_record', '_same', '_frozen', '_taken', '_renewed'}
    seen = set()

    def count(frame, event, arg):
        if event == 'call' and frame.f_code.co_name in names and frame.f_globals.get('__name__') == snapshot.__name__:
            seen.add(frame.f_code.co_name)
            assert id(frame.f_locals.get('value')) not in identities, frame.f_code.co_name

    old = sys.getprofile()
    try:
        sys.setprofile(count)
        snapshot.plain(raw, where='plant')
        StaticProposer(raw)
        Probe('p', 'q', 't', {'A': True}, .8, arguments=raw)
    finally:
        sys.setprofile(old)
    assert {'_not_json', '_same', '_has_record', '_frozen', '_taken'} <= seen
