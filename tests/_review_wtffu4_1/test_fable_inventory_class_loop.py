"""Finding 4 (minor, a regression of 37a893ed): a class body's load that comes before the class's own, later import of a raw
writer is lent the module's gate allowance, though a loop runs the import first.

37a893ed's rule for class bodies (``_BindingScope.lookup``, the ``self.kind != 'class'`` branch, lines 279-284):
"Class loads without an established import fall through to the globals". Whether the class's import is established
is decided by line order (``origin``: ``imported <= node.lineno``, line 266). Python decides it by what has run: a class
body is executed top to bottom, but a ``for`` or ``while`` in it runs its body again, and from the second turn the
name is the class's own binding. So in

    from g6.harness import _completion_storage as store
    class Run:
        for turn in range(2):
            store.unlink(p)          # turn 0: the gate's; turn 1: os.unlink
            import os as store

the inventory reads line 4 as the module's gate on every turn and admits it, and the second turn deletes a file with
``os.unlink`` (the control below runs it). The parent's inventory (1827b22e) refused each of these: there one name
with a class-level binding that is not the gate's was refused throughout the class.

The file's own comment says "Execution order, callbacks and loop back edges give no allowance" (line 242). None of
the three completion files has this shape, so nothing shipped writes raw; the inventory, which is what would catch
the edit that does, no longer catches it.
"""
import pytest

from tests._review_wtffu3_4.rev4_inventory import GATE, inventory_at, runs
from tests.harness.test_completion_writer_allowlist import unguarded_calls

SOURCES = {
    'for-loop': GATE + 'class Run:\n    for turn in range(2):\n        store.unlink(p)\n        import os as store\n',
    'while-loop': GATE + 'class Run:\n    turn = 0\n    while turn < 2:\n        store.unlink(p)\n'
                         '        import os as store\n        turn += 1\n',
    'loop-with-the-import-in-a-finally': GATE + 'class Run:\n    for turn in range(2):\n        try:\n'
                                                '            store.unlink(p)\n        finally:\n'
                                                '            import os as store\n',
}


@pytest.mark.parametrize('form', list(SOURCES))
def test_inventory_refuses_a_class_load_its_own_later_import_rebinds_in_a_loop(form):
    assert unguarded_calls(SOURCES[form]), 'admitted: on the loop\'s second turn this call is os.unlink'


@pytest.mark.parametrize('form', list(SOURCES))
def test_control_the_source_deletes_a_file_with_os_unlink(form, tmp_path, monkeypatch):
    ran = runs(SOURCES[form], tmp_path, monkeypatch)
    assert ran.raw_write and ran.asked == ['unlink'], 'the first turn asks the gate; the second deletes the file raw'


@pytest.mark.parametrize('form', list(SOURCES))
def test_control_the_parents_inventory_refused_the_same_source(form):
    assert inventory_at('1827b22e')(SOURCES[form])


def test_control_the_same_loop_in_a_function_is_refused():
    source = GATE + 'def run():\n    for turn in range(2):\n        store.unlink(p)\n        import os as store\n'
    assert unguarded_calls(source)


def test_control_a_class_load_after_its_own_raw_import_is_refused():
    assert unguarded_calls(GATE + 'class Run:\n    import os as store\n    store.unlink(p)\n')


def test_control_a_class_that_only_reads_the_modules_gate_is_admitted(tmp_path, monkeypatch):
    source = GATE + 'class Run:\n    for turn in range(2):\n        store.unlink(p)\n'
    assert not unguarded_calls(source)
    ran = runs(source, tmp_path, monkeypatch)
    assert ran.asked == ['unlink', 'unlink'] and not ran.raw_write
