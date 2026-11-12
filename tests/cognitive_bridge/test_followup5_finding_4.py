"""The boundary's name of a refused type still runs the caller's code, and can leave as AttributeError.

Repair 3 reads a type's module and qualified name through ``type``'s own descriptors, so a metaclass no longer
answers (review 3's finding 8). What those descriptors hand back is whatever the class's namespace holds: any object
as ``__module__``, a ``str`` subclass as ``__qualname__``. ``_named`` then formats it in an f-string, which calls its
``__format__``. A class with no ``__module__`` at all (made by ``type()`` where the globals have no ``__name__``)
makes the descriptor raise AttributeError, which leaves the copier in place of the refusal.
"""
import pytest

from g6.cognitive_bridge import snapshot
from g6.cognitive_bridge.codes import NotPlainData

RAN = []


class _Module:
    def __format__(self, spec):
        RAN.append('__module__.__format__')
        return 'elsewhere'

    def __str__(self):
        RAN.append('__module__.__str__')
        return 'elsewhere'


class _ForeignModule:
    __module__ = _Module()


class _Name(str):
    def __format__(self, spec):
        RAN.append('__qualname__.__format__')
        return 'Other'


_ForeignName = type('_ForeignName', (), {'__qualname__': _Name('_ForeignName')})

_namespace: dict = {}
exec("Unplaced = type('Unplaced', (), {})", _namespace)  # no __name__ in these globals: the class has no __module__
_Unplaced = _namespace['Unplaced']


@pytest.mark.parametrize('kind', [_ForeignModule, _ForeignName], ids=['module', 'qualname'])
@pytest.mark.parametrize('place', ['key', 'value'])
def test_naming_a_refused_type_runs_none_of_its_code(kind, place):
    RAN.clear()
    value = {kind(): 1} if place == 'key' else [kind()]
    with pytest.raises(NotPlainData):
        snapshot.snapshot(value, where='a value')
    assert RAN == [], f'the refusal ran the caller\'s {RAN[0]}'


@pytest.mark.parametrize('place', ['key', 'value'])
def test_a_type_with_no_module_is_refused_by_the_boundary(place):
    value = {_Unplaced(): 1} if place == 'key' else [_Unplaced()]
    try:
        snapshot.snapshot(value, where='a value')
        raised = None
    except Exception as exc:
        raised = exc
    assert type(raised) is NotPlainData, f'the copier left with {type(raised).__name__}: {raised}'
