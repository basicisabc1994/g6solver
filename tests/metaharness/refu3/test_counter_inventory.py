"""The counter's inventory reads what the readers read (review refufu2r4, finding 3).

Each case hands the inventory a changed copy of the loop's source text, in which something admission reads outside its lock
is changed with no move of the counter. Nothing is imported from a copy or run."""
from __future__ import annotations

import inspect

import pytest

from g6.metaharness import loop as loop_module
from tests.metaharness.refufu2r3 import test_stop_admission as builder

INVENTORY = builder.test_every_change_this_process_makes_to_what_admission_reads_unlocked_moves_the_counter


def _changed(*edits: tuple[str, str]) -> str:
    source = inspect.getsource(loop_module)
    for old, new in edits:
        assert source.count(old) == 1, f"the fixture's edit no longer finds its one place: {old!r}"
        source = source.replace(old, new)
    compile(source, "a_changed_loop.py", "exec")           # the changed copy is still a module that compiles
    return source


def _run_inventory_on(monkeypatch, source: str) -> None:
    original = inspect.getsource
    monkeypatch.setattr(inspect, "getsource", lambda thing: source if thing is loop_module else original(thing))
    try:
        INVENTORY()
    finally:
        monkeypatch.undo()


def test_the_inventory_passes_on_the_code_as_it_is(monkeypatch):
    _run_inventory_on(monkeypatch, inspect.getsource(loop_module))


STOP_READ = "        return self.cannot_record or stop_standing(self.root, self.log.started())"

CASES = {
    # the stop on the record that was found, stored with the lock and no move of the counter
    "the keeper's stop moves no counter": [
        ("                    here.cannot_record = why\n                    _RECORD.moved += 1\n        finally:",
         "                    here.cannot_record = why\n        finally:")],
    # the same store with no lock at all
    "the setter's stop is stored outside the lock": [
        ("            with _LOCK:               # an admission that read the folder's stop before this reads it again\n"
         "                here.cannot_record = why\n                _RECORD.moved += 1\n",
         "            here.cannot_record = why\n")],
    # a fact of the folder's record the stop reader starts to read, whose stores were never counted
    "the stop reader reads the count of lost cycles": [
        (STOP_READ, STOP_READ.replace("self.cannot_record or", "self.cannot_record or str(self._here.lost_cycles or '') or"))],
    # the folder's refused requests changed in bulk, outside any pending refusal write
    "the folder's set is changed outside a refusal write": [
        ("        self.requests.unknown |= held\n",
         "        self.requests.unknown |= held\n        self._here.refused.update(refused)\n")],
    # something of the handle itself that the stop reader starts to read, and any method may set
    "the stop reader reads a new attribute of the handle": [
        (STOP_READ, STOP_READ.replace("self.cannot_record or", "self.cannot_record or self.waiting or")),
        ("        self.evaluating = True\n\n    def _keep_refusal",
         "        self.evaluating = True\n        self.waiting = 'an evaluation'\n\n    def _keep_refusal")],
    # an attribute a reader reads that is given once, set again elsewhere
    "the handle's folder is set outside its construction": [
        ("        self.evaluating = True\n\n    def _keep_refusal",
         "        self.evaluating = True\n        self.root = self.root.parent\n\n    def _keep_refusal")],
    # a reader that reads something new of the process: a call the inventory has not classified
    "the store reader calls something unclassified": [
        ("        refused, held = _kinds(refusal_rows(self.root))\n        return refused | held | self._here.refused",
         "        refused, held = _kinds(refusal_rows(self.root))\n"
         "        return refused | held | set(_RECORD.out) | self._here.refused")],
    # a reader handed to the meter that is not the one the inventory followed
    "the meter is handed another store reader": [
        ("self._barred, self._keep_refusal, self._on_record)",
         "self._barred, self._keep_refusal, lambda: self._on_record() | {str(self._here.lost_cycles)})")],
    # the protected step no longer compares the counter
    "admission stops comparing the counter": [
        ("                    if _RECORD.moved != seen:  # a refusal was kept or a folder stopped here since the reads above began\n"
         "                        seen = _RECORD.moved\n                        continue\n", "")],
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_inventory_fails_for_each_change_admission_could_not_see(monkeypatch, case):
    with pytest.raises(AssertionError):
        _run_inventory_on(monkeypatch, _changed(*CASES[case]))
