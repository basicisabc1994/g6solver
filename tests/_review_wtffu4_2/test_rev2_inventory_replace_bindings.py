"""Finding 1: ``os.replace`` is still admitted behind the text allowance, through bindings 009ac979 does not track.

009ac979 ("Track module values behind text replacements") refuses ``text = os``, a default, a plain loop target and a
named list holding the module (review 1, finding 5). ``text_replace`` still admits a two-argument ``.replace`` whose
receiver is ``text``/``data`` or a subscript whenever the module reached that name some other way: a comprehension
target, an unpacked loop target, a boolean operator, a ``match`` capture, a ``global`` set in another function, a
call's result, or a list built by a call. Each body below is run on two real files first: it moves the file, so it is
``os.replace`` and no ``str.replace``. The inventory returns nothing for it, as it does for text.

Controls: the forms review 1 named are refused, and a real text ``replace`` is admitted.
"""
import pytest

from tests.harness.test_completion_writer_allowlist import unguarded_calls

ESCAPES = {
    'comprehension-target': 'import os\n[text.replace(p, q) for text in [os]]\n',
    'generator-target': 'import os\nlist(text.replace(p, q) for text in [os])\n',
    'unpacked-loop-target': 'import os\nfor n, text in enumerate([os]):\n    text.replace(p, q)\n',
    'boolean-operator': "import os\ndata = ''\ndata = data or os\ndata.replace(p, q)\n",
    'match-capture': 'import os\nmatch os:\n    case text:\n        text.replace(p, q)\n',
    'global-set-elsewhere': 'import os\ndef arm():\n    global data\n    data = os\narm()\ndata.replace(p, q)\n',
    'a-calls-result': 'import os\ndef pick():\n    return os\ntext = pick()\ntext.replace(p, q)\n',
    'a-list-built-by-a-call': 'import os\nrows = list([os])\nrows[0].replace(p, q)\n',
}
CONTROLS_REFUSED = {
    'text-assigned-the-module': 'import os\ntext = os\ntext.replace(p, q)\n',
    'text-as-a-loop-target': 'import os\nfor text in [os]:\n    text.replace(p, q)\n',
    'a-named-list-holding-the-module': 'import os\nmods = [os]\nmods[0].replace(p, q)\n',
}


def _moves_a_file(body, folder):
    p, q = folder / 'p', folder / 'q'
    p.write_text('x', encoding='utf-8')
    exec(compile(body, 'body', 'exec'), {'p': p, 'q': q})
    return q.is_file() and not p.exists()


@pytest.mark.parametrize('form', sorted(ESCAPES))
def test_inventory_refuses_os_replace_behind_an_untracked_binding(tmp_path, form):
    assert _moves_a_file(ESCAPES[form], tmp_path), 'the body must be a real os.replace for this test to mean anything'
    assert unguarded_calls(ESCAPES[form]), 'admitted: os.replace, read as str.replace'


@pytest.mark.parametrize('form', sorted(CONTROLS_REFUSED))
def test_control_the_forms_review_1_named_are_refused(tmp_path, form):
    assert _moves_a_file(CONTROLS_REFUSED[form], tmp_path)
    assert unguarded_calls(CONTROLS_REFUSED[form])


@pytest.mark.parametrize('body', ["shown = text.replace('a', 'b')\n", "shown = parts[0].replace('a', 'b')\n"])
def test_control_a_text_replace_is_admitted(body):
    assert not unguarded_calls(body)
