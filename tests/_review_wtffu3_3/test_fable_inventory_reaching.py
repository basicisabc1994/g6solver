"""Finding 2 (minor): the writer inventory's reaching-binding resolution still admits a rebound gate name.

Each source below is first executed: it performs a real raw write to a real file that never passes the
storage gate (``store`` no longer names the gate when the write runs). The inventory is then asked about the
same source and must flag the call it admitted as a gate call. The controls are the unchanged gate, and the
direct forms repair 2 does refuse (a called rebinding function, an ``if`` branch).
"""
import pytest

from tests.harness.test_completion_writer_allowlist import unguarded_calls

GATE = 'from g6.harness import _completion_storage as store\n'
REBINDER = 'def change(*_):\n    global store\n    store = p\n    return 0\n'
RAW = "store.write_text('raw')\n"
COUNTED = 'count = 0\nwhile count < 2:\n    store.write_text(p, \'raw\')\n    store = alternate\n    count += 1\n'
ADMITTED = {
    # the binding is made by a scope the resolver leaves without carrying it to the module
    'class-body-global': GATE + 'class Change:\n    global store\n    store = p\n' + RAW,
    # the rebinding function runs, but never as a plain ``name()`` call at module level
    'decorator': GATE + REBINDER.replace('return 0', 'return _[0]') + '@change\ndef noop():\n    pass\n' + RAW,
    'builtin-callback': GATE + REBINDER + 'list(map(change, [0]))\n' + RAW,
    'sort-key': GATE + REBINDER + 'sorted([0], key=change)\n' + RAW,
    'called-in-class-body': GATE + REBINDER + 'class Run:\n    change()\n' + RAW,
    'called-in-comprehension': GATE + REBINDER + '[change() for _ in range(1)]\n' + RAW,
    'called-through-a-function': GATE + REBINDER + 'def go():\n    change()\ngo()\n' + RAW,
    # the second pass of a loop body runs with the binding its first pass made
    'for-back-edge': GATE + "for index in range(2):\n    store.write_text(p, 'raw')\n    store = alternate\n",
    'while-back-edge': GATE + COUNTED,
}
REFUSED = {
    'control-called-rebinder': GATE + REBINDER + 'change()\n' + RAW,
    'control-branch': GATE + 'if flag:\n    store = p\n' + RAW,
    'control-loop-then-use': GATE + 'for index in range(1):\n    store = p\n' + RAW,
}


class Alternate:
    """Not the gate: writes with pathlib directly, as any object a rebound name can hold."""

    def __init__(self):
        self.raw = []

    def write_text(self, target, text):
        self.raw.append(target)
        target.write_text(text)


def _run(source, tmp_path):
    target, alternate = tmp_path / 'raw.txt', Alternate()
    exec(compile(source, 'inventory-form', 'exec'), {'p': target, 'alternate': alternate, 'flag': True})
    return target, alternate


@pytest.mark.parametrize('form', list(ADMITTED))
def test_inventory_flags_a_raw_write_that_really_ran(tmp_path, form):
    source = ADMITTED[form]
    target, alternate = _run(source, tmp_path)
    assert target.read_text() == 'raw'
    if 'back-edge' in form:
        assert alternate.raw == [target], 'the second pass must have written outside the gate'
    found = unguarded_calls(source)
    assert any(call == 'store.write_text' for _, call in found), (
        f'{form}: the raw write ran, and the inventory lists only {sorted(found)}')


@pytest.mark.parametrize('form', list(REFUSED))
def test_control_the_direct_forms_are_refused(tmp_path, form):
    source = REFUSED[form]
    target, _ = _run(source, tmp_path)
    assert target.read_text() == 'raw'
    assert any(call == 'store.write_text' for _, call in unguarded_calls(source))


def test_control_the_unchanged_gate_is_admitted(tmp_path):
    source = GATE + "store.write_text(p, 'gated')\n"
    target, alternate = _run(source, tmp_path)
    assert target.read_text() == 'gated' and alternate.raw == []
    assert not unguarded_calls(source)


PLAIN = {
    'comprehension-in-function': "def read():\n    return [store.read_task(p) for p in paths]",
    'generator-in-function': "def read():\n    return any(store.task_present(p) for p in paths)",
    'lambda-after-local-import': "def read():\n    " + GATE.strip() + "\n    return sorted(paths, key=lambda p: store.task_present(p))",
    'same-import-on-both-branches': ("def read():\n    if flag:\n        " + GATE.strip() + "\n    else:\n        "
                                     + GATE.strip() + "\n    return store.task_path(p)"),
    'method': "class Reader:\n    def read(self):\n        return store.read_task(p)",
    'loop-body': "for p in paths:\n    store.task_present(p)",
    'try-except-finally': ("try:\n    store.read_task(p)\nexcept ValueError:\n    store.task_present(p)\n"
                           "finally:\n    store.task_path(p)"),
    'match-cases': "match p:\n    case None:\n        store.task_present(p)\n    case _:\n        store.task_path(p)",
    'with-publication': "with store.task_publication(p):\n    store.write_retained(p, body)",
}


@pytest.mark.parametrize('form', list(PLAIN))
def test_control_plain_gate_uses_are_not_refused(form):
    """The other direction: none of these ordinary uses of the unchanged gate is newly refused."""
    assert not unguarded_calls(GATE + PLAIN[form])
