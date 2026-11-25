"""Finding 3: the writer inventory reads a name by the module's imports, never by what the name is bound to.

``unguarded_calls`` builds one map of imported names for the whole module (``from`` imports
applied last, whatever the source order) and admits ``<name>.<operation>`` when the map says the
name is the completion gate. A name bound again to a path, a parameter, a loop target or ``os``
keeps the gate's allowance, so a raw filesystem write passes as a gated one. Each source below is
also run on the real disk, to show the write it holds really happens outside the gate.
"""
import pytest

from tests.harness.test_completion_writer_allowlist import unguarded_calls

GATE = 'from g6.harness import _completion_storage as store\n'
SOURCES = {
    # controls: the same writes under names the inventory already refuses
    'control-plain-path': "p.write_text('outside the completion gate')\n",
    'control-os-alias-alone': "import os as fs\nfs.replace(spare, p)\n",
    'control-path-through-the-gate': GATE + "store.path(p).write_text('outside the completion gate')\n",
    # the gate's name, bound to something else
    'rebound-by-assignment': GATE + "store = p\nstore.write_text('outside the completion gate')\n",
    'shadowed-by-a-parameter': GATE + ("def publish(store):\n    store.write_text('outside the completion gate')\n"
                                       "publish(p)\n"),
    'rebound-by-a-loop': GATE + "for store in [p]:\n    store.write_text('outside the completion gate')\n",
    'os-imported-after-under-the-gate-name': GATE + "import os as store\nstore.replace(spare, p)\n",
    'os-imported-before-under-the-gate-name': "import os as store\nstore.replace(spare, p)\n" + GATE,
    'os-imported-inside-a-function': GATE + ("def publish():\n    import os as store\n    store.replace(spare, p)\n"
                                             "publish()\n"),
}


@pytest.mark.parametrize('form', list(SOURCES))
def test_inventory_refuses_a_raw_write_under_the_gates_name(tmp_path, form):
    source = SOURCES[form]
    target, spare = tmp_path / 'ungated.txt', tmp_path / 'ungated.txt.new'
    spare.write_text('outside the completion gate')
    exec(compile(source, form, 'exec'), {'p': target, 'spare': spare})
    assert target.read_text() == 'outside the completion gate', 'the source did not write as this test claims'
    assert unguarded_calls(source), f'the inventory admitted a raw write as a gate operation:\n{source}'
