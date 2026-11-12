"""Review refufu2r4, finding 3: the counter's inventory names two facts; it does not read what the stop reader reads.

``test_every_change_this_process_makes_to_what_admission_reads_unlocked_moves_the_counter`` is the commit's inventory for
the stale-read class. It looks for stores to an attribute named ``cannot_record`` and for calls ending in ``refused.add``.
What admission reads outside the lock is whatever ``ShadowLoop._halted``, ``stopped_by`` and the store reader read, and
the test never reads those. So a third fact of the folder, read by the stop reader and stored with no lock and no move of
the counter (the very class the counter was added for), passes it.

Each case hands the inventory a changed copy of the loop's source text. Nothing is imported from the copy or run."""
from __future__ import annotations

import inspect

import pytest

from g6.metaharness import loop as loop_module
from tests.metaharness.refufu2r3 import test_stop_admission as builder

INVENTORY = builder.test_every_change_this_process_makes_to_what_admission_reads_unlocked_moves_the_counter


def _changed(source: str, *edits: tuple[str, str]) -> str:
    for old, new in edits:
        assert source.count(old) == 1, f"the fixture's edit no longer finds its one place: {old!r}"
        source = source.replace(old, new)
    return source


def _run_inventory_on(monkeypatch, source: str) -> None:
    original = inspect.getsource
    monkeypatch.setattr(inspect, "getsource", lambda thing: source if thing is loop_module else original(thing))
    try:
        INVENTORY()
    finally:
        monkeypatch.undo()


def test_control_the_inventory_passes_on_the_code_as_it_is(monkeypatch):
    _run_inventory_on(monkeypatch, inspect.getsource(loop_module))


def test_control_the_inventory_fails_when_a_known_store_stops_moving_the_counter(monkeypatch):
    source = _changed(inspect.getsource(loop_module),
                      ("                here.cannot_record = why\n                _RECORD.moved += 1\n            try:",
                       "                here.cannot_record = why\n            try:"))
    with pytest.raises(AssertionError):
        _run_inventory_on(monkeypatch, source)


def test_the_inventory_fails_for_a_third_folder_fact_the_stop_reader_reads_and_nothing_counts(monkeypatch):
    source = _changed(
        inspect.getsource(loop_module),
        # the folder gains a fact this process alone knows
        ('    cannot_record: str = ""\n    lost_cycles: int = 0\n',
         '    cannot_record: str = ""\n    held: str = ""\n    lost_cycles: int = 0\n'),
        # which every admission's stop reader reads, outside the lock
        ("        return self.cannot_record or stop_standing(self.root, self.log.started())",
         "        return self.cannot_record or self._here.held or stop_standing(self.root, self.log.started())"),
        # and which is stored with no lock and no move of the counter
        ("        self._here.lost_cycles = count\n",
         "        self._here.lost_cycles = count\n        self._here.held = 'its log lost a cycle' if count else ''\n"))
    compile(source, "loop_with_a_third_fact.py", "exec")       # the changed copy is still a module that compiles
    with pytest.raises(AssertionError):
        # An admission that read the stop before ``held`` was stored sends on that read: nothing makes it read again.
        _run_inventory_on(monkeypatch, source)
