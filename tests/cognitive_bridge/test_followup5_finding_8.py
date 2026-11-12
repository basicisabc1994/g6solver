"""The one field reader leaves as AttributeError for a record that has no attribute mapping (a regression of repair 3).

Repair 3 reads a record's fields from its instance attribute mapping, never by ``getattr``; its ruling says a record
whose field is answered otherwise "is refused as missing a field". A record type that declares ``__slots__`` for its
fields (``snapshot.record`` is public in its module) has no ``__dict__``: ``_attributes`` raises AttributeError from
inside the record's own making, so no instance can be made at all. On b683e40 it was made, copied and digested.
"""
from g6.cognitive_bridge import snapshot
from g6.cognitive_bridge.codes import NotPlainData


def _slotted():
    @snapshot.record
    class Slotted:
        __slots__ = ('name', 'weight')
        name: str
        weight: float

    return Slotted


def test_a_record_type_with_slots_is_made_and_copied():
    kind = _slotted()
    try:
        made = kind('a', 1.0)
        raised = None
    except Exception as exc:
        made, raised = None, exc
    assert raised is None, f'making a slotted record left with {type(raised).__name__}: {raised}'
    assert snapshot.snapshot(made, where='a record') == made and len(snapshot.digest(made)) == 64


def test_a_record_with_no_attribute_mapping_is_at_least_the_boundarys_refusal():
    kind = _slotted()
    try:
        kind('a', 1.0)
        raised = None
    except Exception as exc:
        raised = exc
    assert raised is None or type(raised) is NotPlainData, (
        f'the field reader left with {type(raised).__name__}: {raised}')
