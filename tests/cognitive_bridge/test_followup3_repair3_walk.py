# SPDX-License-Identifier: Apache-2.0
"""Count planted visits at the boundary; shared references count on every reach."""
import sys
import pickle

import pytest

from g6.cognitive_bridge import CognitiveBridgeBlock, CognitiveInput, Probe, StaticProposer, codes, snapshot
from g6.cognitive_bridge.epistemic import assertions_sha256, findings_state
from g6.cognitive_bridge.adaptation import protected_paths


def plant(shape):
    value = 'the planted leaf for visit counting'
    if shape == 'wide':
        return [value] * 300
    if shape == 'cycle':
        value = []
        value.append(value)
        return value
    for _ in range(70 if shape == 'deep' else 40):
        value = [value] if shape == 'deep' else [value, value]
    return value


ENTRIES = {
    'plain': lambda x: snapshot.plain(x, where='plant'),
    'snapshot': snapshot.snapshot,
    'fresh': snapshot.fresh,
    'view': snapshot.view,
    'canonical': snapshot.canonical_json,
    'claims': codes.claims,
    'unclaimed': lambda x: codes.unclaimed(x, where='plant'),
    'settled': lambda x: codes.settled(None, payload=x),
    'refusal': lambda x: codes.refusal(codes.INPUT, 'plant', payload=x),
    'record': lambda x: Probe('p', 'q', 't', {'A': True}, .8, arguments={'plant': x}),
    'proposer': StaticProposer,
    'infer': lambda x: CognitiveBridgeBlock().infer({'op': 'ops', 'claims': x}),
    'screen': lambda x: CognitiveBridgeBlock().screen_raw({'op': 'ops', 'claims': x}),
    'typed_infer': lambda x: CognitiveBridgeBlock().infer(CognitiveInput.model_construct(claims=x)),
    'typed_screen': lambda x: CognitiveBridgeBlock().screen_raw(CognitiveInput.model_construct(claims=x)),
    'assertion_digest': assertions_sha256,
    'findings': lambda x: findings_state({'scoped_findings': x}),
    'protected_paths': protected_paths,
    'frozen_hash': lambda x: hash(codes.FrozenMap(payload=x)),
    'frozen_pickle': lambda x: pickle.dumps(codes.FrozenMap(payload=x)),
    'frozen_repr': lambda x: repr(codes.FrozenMap(payload=x)),
}


def _registry_input(value):
    from tests.cognitive_bridge.test_followup3_repair1_inventory import invoke
    return invoke('registry', {'op': 'ops', 'claims': value})


ENTRIES['registry'] = _registry_input


@pytest.mark.parametrize('entry', ENTRIES)
@pytest.mark.parametrize('shape', ['wide', 'deep', 'shared', 'cycle'])
def test_first_value_read_is_bounded(entry, shape, monkeypatch):
    monkeypatch.setattr(snapshot, 'MAX_ITEMS', 128)
    value = plant(shape)
    if entry == 'frozen_repr' and shape == 'shared':
        value = 'the planted rendering leaf'
        for _ in range(12):
            value = [value, value]
    planted = set()
    pending = [value]
    while pending:
        item = pending.pop()
        if id(item) in planted:
            continue
        planted.add(id(item))
        if type(item) is list:
            pending.extend(item)
    visits = 0
    readers = {codes._read_as.__code__, snapshot._has_record.__code__, snapshot._not_json.__code__,
               snapshot.record_data.__code__}

    class TooManyVisits(BaseException):
        pass

    def count(frame, event, arg):
        nonlocal visits
        if event == 'call' and frame.f_code in readers and id(frame.f_locals.get('value')) in planted:
            visits += 1
            if visits > 512:
                raise TooManyVisits('the value was read before the visit budget')

    old = sys.getprofile()
    stopped = False
    unexpected = ''
    sys.setprofile(count)
    try:
        try:
            out = ENTRIES[entry](value)
        except TooManyVisits:
            # Do not pass the raw shared graph to pytest's traceback renderer.
            stopped = True
        except codes.NotPlainData as exc:
            assert exc.code == codes.INPUT
        except ValueError:
            assert entry in ('claims', 'unclaimed')
        except TypeError:
            unexpected = 'unregistered type refusal'
        else:
            if entry in ('infer', 'screen', 'typed_infer', 'typed_screen'):
                assert out.value['code'] == codes.INPUT
            elif entry == 'registry':
                assert out['code'] == codes.INPUT
            elif entry == 'view':
                assert codes.claims(out)
            else:
                assert entry in ('claims', 'findings') and out
    finally:
        sys.setprofile(old)
    assert not stopped, f'{entry}/{shape}: the value was read before the visit budget'
    assert not unexpected, f'{entry}/{shape}: {unexpected}'


def test_one_walker_counts_repeated_references_before_reading_them(monkeypatch):
    monkeypatch.setattr(snapshot, 'MAX_ITEMS', 8)
    seen = []
    original = codes._read_as

    def read(value):
        seen.append(value)
        return original(value)

    monkeypatch.setattr(snapshot, '_read_as', read)
    with pytest.raises(codes.NotPlainData, match='size'):
        snapshot.plain([[0]] * 10, where='plant')
    assert len(seen) <= 8


@pytest.mark.parametrize('entry', ['infer', 'screen_raw'])
def test_outer_mapping_protocol_is_bounded_before_reading_items(entry, monkeypatch):
    monkeypatch.setattr(snapshot, 'MAX_ITEMS', 128)
    reads = []

    class Outer:
        def keys(self):
            return (str(index) for index in range(1000))

        def __getitem__(self, key):
            reads.append(key)
            return 0

    out = getattr(CognitiveBridgeBlock(), entry)(Outer())
    assert out.value['code'] == codes.INPUT
    assert len(reads) <= 128, 'outer mapping copied before the visit budget'


def _value_paths():
    from tests.cognitive_bridge.test_publication_inventory import PLACES
    return {(name, where): call for name, places in PLACES.items()
            for where, call in places.items() if where[0] == 'value'}


@pytest.mark.parametrize('path', list(_value_paths()))
@pytest.mark.parametrize('shape', ['wide', 'deep', 'shared', 'cycle'])
def test_caller_and_collaborator_value_paths_spend_the_visit_budget(path, shape, monkeypatch):
    # The fixture inventory is discovered from public callables and includes
    # model, predictor, learner, tool, registry, executor and assessment paths.
    from g6.core import door
    monkeypatch.setattr(door, 'allowed_sensitive', lambda: frozenset())
    monkeypatch.setattr(snapshot, 'MAX_ITEMS', 4096)
    value = plant(shape) if shape != 'wide' else ['planted wide leaf'] * 5000
    if shape == 'shared':
        # Fixture assertions may render an argument before they raise. Keep
        # that rendering finite while exceeding this probe's visit limit.
        value = 'planted callback leaf'
        for _ in range(14):
            value = [value, value]
    planted, pending = set(), [value]
    while pending:
        item = pending.pop()
        if id(item) in planted:
            continue
        planted.add(id(item))
        if type(item) is list:
            pending.extend(item)
    readers = {codes._read_as.__code__, snapshot._has_record.__code__, snapshot._not_json.__code__}
    visits = 0

    class Unbounded(BaseException):
        pass

    def count(frame, event, arg):
        nonlocal visits
        if event == 'call' and frame.f_code in readers and id(frame.f_locals.get('value')) in planted:
            visits += 1
            if visits > 4 * snapshot.MAX_ITEMS:
                raise Unbounded

    stopped = False
    old = sys.getprofile()
    try:
        sys.setprofile(count)
        try:
            _value_paths()[path](value, monkeypatch)
        except (TypeError, ValueError):
            pass  # a kind or size refusal before traversal is also bounded
        except Unbounded:
            stopped = True
    finally:
        sys.setprofile(old)
    assert not stopped, f'{path}/{shape}: planted data scanned before the visit budget'


def test_composed_tree_is_bounded_before_a_consumer_follows_nodes(monkeypatch):
    from g6.cognitive_bridge import ExplicitTree, TreeNode
    root = TreeNode(value=1)
    for _ in range(300):
        root = TreeNode(feature=0, threshold=0., left=root, right=TreeNode(value=0))
    tree = ExplicitTree(('x',), root, 'regression', 'tree_transcription')
    planted = set()
    node = tree.root
    while node is not None:
        planted.add(id(node))
        node = node.left
    reads = 0

    class Unbounded(BaseException):
        pass

    def read(self, name):
        nonlocal reads
        if id(self) in planted and name == 'feature':
            reads += 1
            if reads > 128:
                raise Unbounded
        return object.__getattribute__(self, name)

    monkeypatch.setattr(TreeNode, '__getattribute__', read)
    stopped = refused = False
    try:
        tree.explain_one([-1])
    except codes.NotPlainData as exc:
        refused = exc.code == codes.INPUT
    except Unbounded:
        stopped = True
    assert not stopped, 'consumer followed raw tree nodes before the boundary'
    assert refused, 'composed records must be bounded where a consumer traverses them'


@pytest.mark.parametrize('path', ['direct', 'screen', 'registry'])
def test_native_outer_dict_copy_keeps_the_registry_protocol(path):
    class Outer(dict):
        def keys(self):
            raise RuntimeError('native dict unpacking does not call this hook')

        def __getitem__(self, key):
            raise RuntimeError('native dict unpacking does not call this hook')

    request = Outer(op='help')
    if path == 'registry':
        from g6.core.registry import ComponentRef, ComponentRegistry
        registry = ComponentRegistry()
        registry._refs = {'cognitive_bridge': ComponentRef('cognitive_bridge', 'g6.cognitive_bridge', 'builtin')}
        response = registry.invoke('cognitive_bridge', 'help', request).unwrap()
    else:
        block = CognitiveBridgeBlock()
        early = block.screen_raw(request) if path == 'screen' else None
        response = (early if early else block.infer(request)).value
    assert response.get('op') == 'help', 'bounded copying changed native outer mapping semantics'
