"""A raw attribute mapping is looked up before the one text-key reader has read its keys by native storage.

Root C of repair 2: raw keys pass one reader (``snapshot._text_key``) before any lookup, so a key's own ``__eq__``
never runs for G6. Two attribute mappings still reach a lookup first:

- an input model's field mapping that is a ``dict`` subclass: ``_text_keys`` asks the mapping's own ``__len__`` and
  ``__iter__`` (the caller's code, which now runs) and so reads the keys the mapping chooses to show, then the schema's
  names are looked up in its native storage (review 2's finding 2, with its own assertion, still open);
- a record's attribute mapping: the walker and the declared checks read fields by ``getattr``, a keyed lookup in the
  instance's ``__dict__``, which no reader has read (the commit says this is not checked).
"""
import pytest

from g6.cognitive_bridge import ClaimIR, Hypothesis, MethodRouter, check_beliefs, snapshot
from g6.cognitive_bridge.block import CognitiveBridgeBlock, CognitiveInput
from g6.cognitive_bridge.codes import INPUT


def _key(name, comparisons):
    class Key:
        def __hash__(self):
            return hash(name)

        def __eq__(self, other):
            comparisons.append(other)
            return False

    return Key()


def test_a_model_whose_field_mapping_is_a_dict_subclass_is_refused_without_running_its_code():
    comparisons, hooks = [], []

    class Shown(dict):
        def __len__(self):
            hooks.append('__len__')
            return 0

        def __iter__(self):
            hooks.append('__iter__')
            return iter(())

    model = CognitiveInput()
    object.__setattr__(model, '__dict__', Shown({_key('probes', comparisons): None}))
    answer = CognitiveBridgeBlock().infer(model).unwrap()
    assert answer['code'] == INPUT
    assert comparisons == [], 'a schema name was looked up among keys the text-key reader had not read'
    assert hooks == [], f'the field mapping\'s own {hooks} ran while the block admitted its input'


@pytest.mark.parametrize('entry', ['check_beliefs', 'snapshot', 'router'])
def test_a_record_whose_attribute_mapping_holds_a_foreign_key_is_refused_uncompared(entry):
    comparisons = []
    raised = None
    if entry == 'router':
        record = ClaimIR('c', 's', ('formal',), {'formal': {'formula': 'A'}})
        fields = dict(record.__dict__)
        del fields['id']
        object.__setattr__(record, '__dict__', {_key('id', comparisons): 'c', **fields})

        def call():
            return MethodRouter().plan([record])
    else:
        record = Hypothesis('a', 's', 0.5)
        object.__setattr__(record, '__dict__', {_key('id', comparisons): 'a', 'statement': 's', 'prior': 0.5})

        def call():
            if entry == 'snapshot':
                return snapshot.snapshot(record)
            return check_beliefs([record, Hypothesis('b', 's', 0.5)])
    try:
        call()
    except Exception as exc:
        raised = exc
    assert comparisons == [], 'a field was looked up in an attribute mapping whose keys no reader had read'
    assert isinstance(raised, ValueError), f'not the boundary\'s refusal: {raised!r}'
