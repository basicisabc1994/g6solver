"""Authority reduction through the real gate and recurrence operations."""
from types import SimpleNamespace

import pytest

from g6.distill import acceptance_gate as g
from g6.distill.records import LibraryEntry
from g6.distill.rectify_gate import Rectifier
from .test_records import bodies


def replacement_entry():
    return LibraryEntry(**(bodies()[4][1] | {"family": "g6_self", "authority": "A2",
        "status": "admitted", "replacement": "R", "replacement_scope": [],
        "teacher": "c" * 64, "noninferiority": "d" * 64}))


def test_recurrence_revokes_replacement_metadata():
    entry = replacement_entry()
    stored = [entry]
    store = SimpleNamespace(list=lambda: stored, get=lambda ident: entry, put=stored.append)
    rectifier = Rectifier(SimpleNamespace(for_model=lambda cls: store))
    receipt = SimpleNamespace(pinning=SimpleNamespace(test_ref="library:" + entry.entry_id), rect_id="e" * 64)
    rectifier._demote(receipt, [SimpleNamespace(observed_at=entry.changed_at)])
    changed = stored[-1]
    assert changed.authority == "A0" and changed.status == "demoted"
    assert changed.replacement == "none"
    assert changed.replacement_scope is changed.teacher is changed.noninferiority is None


@pytest.mark.parametrize("authority", ["A0", "A1"])
def test_lowering_from_a2_clears_all_replacement_fields(monkeypatch, authority):
    stored = []
    monkeypatch.setattr(g, "Stores", lambda: SimpleNamespace(for_model=lambda cls:
        SimpleNamespace(put=stored.append)))
    changed = g._change(replacement_entry(), status="demoted", authority=authority, reason="recurrence")
    assert stored == [changed]
    assert changed.replacement == "none"
    assert changed.replacement_scope is changed.teacher is changed.noninferiority is None


def test_pinning_requires_current_replacement_authority(monkeypatch):
    entry = replacement_entry()
    store = SimpleNamespace(list=lambda: [entry], get=lambda ident: entry)
    rectifier = Rectifier(SimpleNamespace(for_model=lambda cls: store))
    rect = SimpleNamespace(pinning=SimpleNamespace(test_ref="library:" + entry.entry_id), removal="class removed")
    monkeypatch.setattr(g, "_replacement_authorized", lambda entry: False)
    with pytest.raises(ValueError, match="authority"):
        rectifier._pinning(rect)
