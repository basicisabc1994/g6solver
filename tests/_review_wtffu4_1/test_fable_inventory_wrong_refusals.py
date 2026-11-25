"""Limit: plain read-only uses the inventory now refuses (37a893ed). Each was admitted by the parent's inventory
(1827b22e), none writes anything (the controls run them), and the three completion files do not have these shapes
today (their inventory test passes): this fails closed, and costs a false refusal of a later, honest edit.

* a data key that is spelled like a writer. "Refuse ... constant raw writer/import keys" (lines 452-456) refuses every
  subscript and every ``.get`` whose constant key is in ``WRITERS`` or is ``'os'`` or ``'__import__'``, on any receiver:
  ``counts['open']``, ``modes.get('write')``, ``row['os']``, ``plan['move']``, ``entry['link']``. A state or report
  field named ``open``, ``copy``, ``move``, ``link``, ``write``, ``remove``, ``flush`` or ``os`` can no longer be read;
* a text ``replace`` on a subscript whose key names an import. ``text_replace`` (lines 405-408) leaves out a receiver
  with any imported name anywhere inside it, the key included: ``rows[re.escape(k)].replace('-', ' ')`` and
  ``json.loads(body)['k'].replace('-', ' ')`` are ``str.replace`` on a string;
* every gate read in a function of a module whose last statement raises. ``_GateFlow.final`` is the state after the
  module's last statement, and a ``raise`` there leaves none (``or set()``, line 154): with
  ``raise SystemExit(main())`` as a module's last line, each function's deferred ``store.`` read is refused, though
  the import above them has run before any of them is called.
"""
import types

import pytest

from g6.harness import _completion_storage as gate
from tests._review_wtffu3_4.rev4_inventory import GATE, inventory_at
from tests.harness.test_completion_writer_allowlist import unguarded_calls

KEYS = {
    'subscript-open': "counts = {'open': 2, 'closed': 5}\nshown = counts['open']\n",
    'get-write': "modes = {'write': 'w'}\nshown = modes.get('write')\n",
    'subscript-os': "row = {'os': 'nt'}\nshown = row['os']\n",
    'subscript-move': "plan = {'move': 1}\nshown = plan['move']\n",
    'get-link': "entry = {'link': 'https://example.invalid'}\nshown = entry.get('link', '')\n",
}
TEXT = {
    'key-from-an-imported-call': "import re\nrows = {re.escape('k'): 'a-b'}\nshown = rows[re.escape('k')].replace('-', ' ')\n",
    'subscript-of-an-imported-calls-result': "import json\nshown = json.loads('{\"k\": \"a-b\"}')['k'].replace('-', ' ')\n",
}
RAISE = {
    'module-ends-with-a-raise': GATE + 'def present():\n    return store.task_present(p)\nraise SystemExit(present())\n',
}
EVERY = {**KEYS, **TEXT, **RAISE}


def _runs(source, folder, monkeypatch):
    """What the source does when Python runs it: every gate call is recorded and touches nothing, so a changed or
    missing ``p`` afterwards was a raw write. A module that ends by raising SystemExit is run to that end."""
    folder = folder / 'scratch'
    folder.mkdir()
    victim = folder / 'victim.txt'
    victim.write_text('kept')
    asked = []
    with monkeypatch.context() as recording:
        for name in ('unlink', 'replace', 'write_text', 'write_bytes', 'task_present', 'path'):
            recording.setattr(gate, name, lambda *args, _name=name, **kwargs: asked.append(_name))
        module = types.ModuleType('inventoried_source')
        module.__dict__.update(p=victim, q=folder / 'moved.txt', flag=False)
        try:
            exec(compile(source, 'inventoried_source', 'exec'), module.__dict__)
        except SystemExit:
            pass
    raw = not victim.exists() or victim.read_text() != 'kept' or len(list(folder.iterdir())) != 1
    return types.SimpleNamespace(raw_write=raw, asked=asked, shown=module.__dict__.get('shown'))


@pytest.mark.parametrize('form', list(KEYS))
def test_a_data_key_spelled_like_a_writer_can_be_read(form):
    assert not unguarded_calls(KEYS[form]), f'refused: a plain read of a mapping, {sorted(unguarded_calls(KEYS[form]))}'


@pytest.mark.parametrize('form', list(TEXT))
def test_a_text_replace_on_a_subscript_keyed_by_an_import_is_admitted(form):
    assert not unguarded_calls(TEXT[form]), f'refused: str.replace on a string, {sorted(unguarded_calls(TEXT[form]))}'


@pytest.mark.parametrize('form', list(RAISE))
def test_a_gate_read_in_a_function_of_a_module_that_ends_with_a_raise_is_admitted(form):
    assert not unguarded_calls(RAISE[form]), f'refused: a read-only gate call, {sorted(unguarded_calls(RAISE[form]))}'


@pytest.mark.parametrize('form', list(EVERY))
def test_control_the_source_writes_nothing(form, tmp_path, monkeypatch):
    ran = _runs(EVERY[form], tmp_path, monkeypatch)
    assert not ran.raw_write and ran.asked == (['task_present'] if form in RAISE else [])
    if form in TEXT:
        assert ran.shown == 'a b'


@pytest.mark.parametrize('form', list(EVERY))
def test_control_the_parents_inventory_admitted_it(form):
    assert not inventory_at('1827b22e')(EVERY[form])


@pytest.mark.parametrize('body', [
    "make = values.get('unlink')\n", "make = values['__import__']\n", 'import os\n[os][0].replace(p, q)\n',
])
def test_control_the_forms_the_rules_were_written_for_are_refused(body):
    assert unguarded_calls(body)


def test_control_the_same_module_ending_in_a_guarded_raise_is_admitted():
    source = GATE + 'def present():\n    return store.task_present(p)\nif flag:\n    raise SystemExit(present())\n'
    assert not unguarded_calls(source)
