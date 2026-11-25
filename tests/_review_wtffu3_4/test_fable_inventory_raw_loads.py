"""Findings 3, 4 and 5 (minor): raw writer loads the inventory still admits. Repair 3's message says every raw writer
load outside the gate's named operations and the explicit text allowances is refused. These are admitted by
``unguarded_calls`` at HEAD (and were at d56384c3: none is new), and each really writes with the gate never asked.

* a star import. ``from os import *`` rebinds every name ``os`` exports, ``unlink`` and ``replace`` among them, and
  ``_Declarations`` records it as a binding of the name ``*`` only: no conflict is seen with the gate's ``unlink``
  imported above it;
* a writer fetched by its name from a mapping. ``.get`` is admitted on any receiver that is not an imported name
  (``VALUES``), so ``__dict__.get('unlink')`` and ``__globals__.get('os')`` are ``getattr`` and ``__import__`` under
  another spelling, and the builtins' own mapping gives ``__import__`` itself;
* ``os.replace`` behind the text allowance. A receiver named ``text`` or ``data``, or any subscript, may call
  ``replace`` with two arguments, whatever the same pass knows the name was imported as.
"""
import pytest

from tests._review_wtffu3_4.rev4_inventory import runs
from tests.harness.test_completion_writer_allowlist import unguarded_calls

STAR = {
    'gate-name-then-star': 'from g6.harness._completion_storage import unlink\nfrom os import *\nunlink(p)\n',
    'gate-names-then-star-replace': 'from g6.harness._completion_storage import replace\nfrom os import *\nreplace(p, q)\n',
}
LOOKUPS = {
    'class-mapping': "make = p.__class__.__base__.__dict__.get('unlink')\nmake(p)\n",
    'function-globals': "import os\ndef own():\n    pass\nmake = own.__globals__.get('os').__dict__.get('unlink')\nmake(p)\n",
    'builtins-mapping-import': "make = sorted.__self__.__dict__.get('__import__')\n"
                               "own = make('os').__dict__.get('unlink')\nown(p)\n",
}
TEXT = {
    'module-aliased-text': 'import os as text\ntext.replace(p, q)\n',
    'module-aliased-data': 'import os as data\ndata.replace(p, q)\n',
    'module-in-a-subscript': 'import os\n[os][0].replace(p, q)\n',
}
EVERY = {**STAR, **LOOKUPS, **TEXT}


@pytest.mark.parametrize('form', list(EVERY))
def test_control_the_source_writes_and_never_asks_the_gate(form, tmp_path, monkeypatch):
    ran = runs(EVERY[form], tmp_path, monkeypatch)
    assert ran.raw_write and ran.asked == []


@pytest.mark.parametrize('form', list(STAR))
def test_inventory_refuses_a_gate_name_a_star_import_rebinds(form):
    assert unguarded_calls(STAR[form]), 'admitted: the call is os\'s, the star import having rebound the name'


@pytest.mark.parametrize('form', list(LOOKUPS))
def test_inventory_refuses_a_writer_fetched_by_name_from_a_mapping(form):
    assert unguarded_calls(LOOKUPS[form]), 'admitted: a raw writer loaded by its name through .get'


@pytest.mark.parametrize('form', list(TEXT))
def test_inventory_refuses_os_replace_behind_the_text_allowance(form):
    assert unguarded_calls(TEXT[form]), 'admitted: os.replace, read as str.replace'


@pytest.mark.parametrize('body', [
    'from g6.harness._completion_storage import unlink\nfrom os import unlink\nunlink(p)\n',
    "getattr(p, 'unlink')()\n",
    "__import__('os').unlink(p)\n",
    'import os as fs\nfs.replace(p, q)\n',
])
def test_control_the_spelled_out_forms_are_refused(body):
    assert unguarded_calls(body)


def test_control_a_star_import_before_the_gate_import_leaves_the_gate_bound(tmp_path, monkeypatch):
    source = 'from os import *\nfrom g6.harness._completion_storage import unlink\nunlink(p)\n'
    ran = runs(source, tmp_path, monkeypatch)
    assert ran.asked == ['unlink'] and not ran.raw_write


@pytest.mark.parametrize('body', ["shown = text.replace('a', 'b')\n", "shown = parts[0].replace('a', 'b')\n"])
def test_control_text_replace_is_still_admitted(body):
    assert not unguarded_calls(body)
