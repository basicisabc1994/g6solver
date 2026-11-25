"""Finding 2 (important): the lexical inventory lends the gate's allowance to a name Python resolves somewhere else.

Repair 3 (d636f5ad) replaced the reaching-bindings pass with ``_BindingScope``: a name with one import in a scope is
the gate "throughout its scope", from that import's line on. Python does not resolve names that way in two places:

* a class body. A name the class binds is looked up in the class's own namespace and then in the module's globals
  (and then the builtins), so until the class's import has run, the load is the module's global. An import in a
  branch not taken, or later in a loop's body, never runs before the load. The same lookup makes a nested class
  blind to its enclosing class's import: it reads the module's global, always;
* the module body. A name the module binds only in a branch falls back to the builtin of that name.

Each source below is admitted by ``unguarded_calls`` (an empty result), and each really deletes a file with
``os.unlink`` while the gate is never asked. Repair 2's inventory (d56384c3) refused the three forms marked
``regression``; the nested class is admitted by both.
"""
import pytest

from tests._review_wtffu3_4.rev4_inventory import GATE, inventory_at, runs
from tests.harness.test_completion_writer_allowlist import unguarded_calls

RAW = 'import os as store\n'
SOURCES = {
    # form: (source, repair 2's inventory refused it)
    'class-import-in-a-branch-not-taken': (
        RAW + 'class Run:\n    if flag:\n        from g6.harness import _completion_storage as store\n    store.unlink(p)\n',
        True),
    'class-import-later-in-a-loop-body': (
        RAW + 'class Run:\n    for turn in range(1):\n        if turn:\n'
              '            from g6.harness import _completion_storage as store\n        store.unlink(p)\n',
        True),
    'module-import-in-a-branch-falls-to-the-builtin': (
        "import os\nif flag:\n    from g6.harness._completion_storage import path as vars\n"
        "make = vars(os)['unlink']\nmake(p)\n",
        True),
    'nested-class-reads-the-global-not-the-outer-class': (
        RAW + 'class Outer:\n    from g6.harness import _completion_storage as store\n'
              '    class Inner:\n        store.unlink(p)\n',
        False),
}


@pytest.mark.parametrize('form', list(SOURCES))
def test_control_the_source_deletes_a_file_and_never_asks_the_gate(form, tmp_path, monkeypatch):
    ran = runs(SOURCES[form][0], tmp_path, monkeypatch)
    assert ran.raw_write and ran.asked == []


@pytest.mark.parametrize('form', list(SOURCES))
def test_inventory_refuses_a_gate_name_python_resolves_elsewhere(form):
    found = unguarded_calls(SOURCES[form][0])
    assert found, 'the inventory admits a source that deletes a file with os.unlink'


@pytest.mark.parametrize('form', [form for form, (_, refused) in SOURCES.items() if refused])
def test_control_repair_2s_inventory_refused_the_same_source(form):
    assert inventory_at('d56384c3')(SOURCES[form][0])


def test_control_two_bindings_in_one_scope_are_refused():
    assert unguarded_calls(RAW + GATE + 'store.unlink(p)\n')


def test_control_a_class_that_imports_the_gate_before_its_load_asks_the_gate(tmp_path, monkeypatch):
    source = RAW + 'class Run:\n    from g6.harness import _completion_storage as store\n    store.unlink(p)\n'
    ran = runs(source, tmp_path, monkeypatch)
    assert not unguarded_calls(source)
    assert ran.asked == ['unlink'] and not ran.raw_write
