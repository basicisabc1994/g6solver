"""Findings 5 and 6 (minor): raw writer loads the inventory still admits. None is new (the parent's inventory,
1827b22e, admits each), and none of the three completion files has these shapes. Each source below is admitted by
``unguarded_calls`` (an empty result) and each really deletes or moves a file with the gate never asked.

Finding 5, ``os.replace`` behind the text allowance, through a plain alias. 37a893ed's one predicate
(``text_replace``, lines 401-411) leaves out a receiver that names an import: a name in the flat import map, or one
whose lexical binding has an origin. A name that is assigned the module (``text = os``), given it as a default
or a loop target, or a list that holds it (``mods = [os]`` then ``mods[0]``), has neither: its binding's origin is
None, which the predicate reads as "not imported". Review 4's three forms (``import os as text``, ``[os][0]``) are
refused now; these are the same call one assignment away.

Finding 6, ``getattr``, ``__import__`` and ``exec`` as values. The inventory refuses a call spelled ``getattr(...)``
with any but three constant names, and calls spelled ``__import__``, ``exec`` or ``eval`` (lines 463-471). The
builtins themselves, as a value assigned or passed (``make = getattr``, ``map(getattr, ...)``, ``make = exec``), are no
call and are not looked at, and their result is then called under any name the inventory admits by name alone:
the reviewed callback names (``make``, ``own``, ...), a builtin's name, a function the module defines. So every form
the inventory names as refused is admitted one assignment away.
"""
import pytest

from tests._review_wtffu3_4.rev4_inventory import inventory_at, runs
from tests.harness.test_completion_writer_allowlist import unguarded_calls

TEXT = {
    'text-assigned-the-module': 'import os\ntext = os\ntext.replace(p, q)\n',
    'data-assigned-the-module': 'import os\ndata = os\ndata.replace(p, q)\n',
    'a-named-list-holding-the-module': 'import os\nmods = [os]\nmods[0].replace(p, q)\n',
    'text-as-a-default': 'import os\ndef go(text=os):\n    text.replace(p, q)\ngo()\n',
    'text-as-a-loop-target': 'import os\nfor text in [os]:\n    text.replace(p, q)\n',
}
VALUES = {
    'getattr-under-a-callback-name': 'import os\nmake = getattr\nown = make(os, "unlink")\nown(p)\n',
    'getattr-passed-to-map': 'import os\nmake = next(map(getattr, [os], ["unlink"]))\nmake(p)\n',
    'import-under-a-callback-name': 'make = __import__\nown = next(map(getattr, [make("os")], ["unlink"]))\nown(p)\n',
    'exec-under-a-callback-name': 'make = exec\nmake("import os; os.unlink(p)")\n',
    'the-result-under-a-builtins-name': 'import os\nfor sorted in map(getattr, [os], ["unlink"]):\n    sorted(p)\n',
    'the-result-under-a-defined-functions-name': 'import os\ndef settle():\n    pass\n'
                                                 'for settle in map(getattr, [os], ["unlink"]):\n    settle(p)\n',
}
EVERY = {**TEXT, **VALUES}


@pytest.mark.parametrize('form', list(TEXT))
def test_inventory_refuses_os_replace_behind_a_plain_alias_of_the_module(form):
    assert unguarded_calls(TEXT[form]), 'admitted: os.replace, read as str.replace'


@pytest.mark.parametrize('form', list(VALUES))
def test_inventory_refuses_a_writer_fetched_by_getattr_import_or_exec_as_a_value(form):
    assert unguarded_calls(VALUES[form]), 'admitted: a raw writer loaded by name, through a builtin used as a value'


@pytest.mark.parametrize('form', list(EVERY))
def test_control_the_source_writes_and_never_asks_the_gate(form, tmp_path, monkeypatch):
    ran = runs(EVERY[form], tmp_path, monkeypatch)
    assert ran.raw_write and ran.asked == []


@pytest.mark.parametrize('form', list(EVERY))
def test_control_none_is_new_the_parents_inventory_admitted_it_too(form):
    assert not inventory_at('1827b22e')(EVERY[form])


@pytest.mark.parametrize('body', [
    'import os as text\ntext.replace(p, q)\n',
    'import os\n[os][0].replace(p, q)\n',
    'import os\ngetattr(os, "unlink")(p)\n',
    'import os\nown = getattr(os, "unlink")\nown(p)\n',
    '__import__("os").unlink(p)\n',
    'exec("import os; os.unlink(p)")\n',
    'import os\nmake = os.unlink\nmake(p)\n',
])
def test_control_the_spelled_out_forms_are_refused(body):
    assert unguarded_calls(body)


@pytest.mark.parametrize('body', ["shown = text.replace('a', 'b')\n", "shown = parts[0].replace('a', 'b')\n",
                                  "shown = getattr(caller, 'model', '')\n"])
def test_control_the_plain_uses_are_still_admitted(body):
    assert not unguarded_calls(body)
