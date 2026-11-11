"""The one text-key reader names the key it refuses through the key's metaclass.

``snapshot._text_key`` is "the one reader of a mapping's key ... a key's own ``__eq__`` and ``__hash__`` never run for
G6". Its refusal says ``_named(key)``, which reads ``type(key).__module__`` by ordinary lookup: a metaclass that
defines ``__module__`` as a property runs, for every foreign key (and every other foreign value) the boundary refuses.
The package reads a type's MRO and members through ``type``'s own descriptors for this reason (``codes._type_mro``).
"""
import pytest

from g6.cognitive_bridge import snapshot
from g6.cognitive_bridge.block import CognitiveBridgeBlock
from g6.cognitive_bridge.codes import INPUT


def _foreign(calls):
    class Meta(type):
        pass

    class Key(metaclass=Meta):
        pass

    Meta.__module__ = property(lambda cls: calls.append('__module__') or 'hooked')
    return Key()


def test_the_block_refuses_a_foreign_key_without_running_its_metaclass():
    calls = []
    answer = CognitiveBridgeBlock().infer({_foreign(calls): None}).unwrap()
    assert answer['code'] == INPUT
    assert calls == [], 'the key\'s metaclass ran while the boundary refused the key'


@pytest.mark.parametrize('make', [lambda key: {key: 1}, lambda key: [key]], ids=['a key', 'a value'])
def test_the_copier_refuses_a_foreign_object_without_running_its_metaclass(make):
    calls = []
    with pytest.raises(ValueError):
        snapshot.snapshot(make(_foreign(calls)))
    assert calls == [], 'the object\'s metaclass ran while the boundary refused it'
