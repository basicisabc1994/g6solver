"""Limit: a read-only gate use the lexical inventory now refuses.

``_BindingScope.lookup`` gives a name its import's origin only from the import's line on, in every scope that reads
the module's binding. A function defined above a module-level import runs after it, so its loads are the gate's:
Python binds the name when the function is called, not where it is written. Repair 2's inventory (d56384c3)
admitted this; HEAD refuses it. The three completion files do not have this shape today (their inventory test
passes), so this fails closed and costs only a false refusal of a later edit.
"""
from tests._review_wtffu3_4.rev4_inventory import GATE, inventory_at, runs
from tests.harness.test_completion_writer_allowlist import unguarded_calls

USE = 'def present():\n    return store.task_present(p)\n'
LATE = USE + GATE + 'present()\n'
EARLY = GATE + USE + 'present()\n'


def test_a_read_in_a_function_above_the_gate_import_is_admitted():
    assert not unguarded_calls(LATE), 'refused: a read-only gate call, in a function that runs after the import'


def test_control_the_source_only_asks_the_gate(tmp_path, monkeypatch):
    ran = runs(LATE, tmp_path, monkeypatch)
    assert ran.asked == ['task_present'] and not ran.raw_write


def test_control_the_same_read_below_the_import_is_admitted():
    assert not unguarded_calls(EARLY)


def test_control_repair_2s_inventory_admitted_it():
    assert not inventory_at('d56384c3')(LATE)


def test_control_a_module_level_load_above_the_import_is_refused():
    assert unguarded_calls("store.write_text(p, 'gated')\n" + GATE)
