"""Finding 4: the new string proof refuses plain text work whose every binding is a string by its own rules.

``_TextBindings.string`` (tests/harness/test_completion_writer_allowlist.py lines 508-553) proves a name by proving
each value bound to it, and gives up on a name it is already proving (``node.id in seen``, line 510). A name rebound
from itself is therefore never a string: ``text = str(value)`` then ``text = text.strip()`` has two bindings, the
first ``str(...)`` and the second a ``str`` method of the first, and its ``text.replace('a', 'b')`` is refused. The
free-name allowance (``receiver``, lines 555-563) is given to a bare name or a subscript of one and to nothing made
from it, so ``text.replace('{', '{{')`` is admitted and ``text.replace('{', '{{').replace('}', '}}')`` on the same
free ``text`` is refused: that is ``completion_loop.literal`` as it stood, and the reason this change edits four
lines of the loop (``str(text).replace(...)``, lines 203, 232, 238, 861). ``'%s' % value`` and ``'-' * 3`` are exact
``str`` and are not in the proof's vocabulary.

The parent's inventory (673466cb, read from git) admitted each of these, none of them writes anything (the control
runs them), and the three completion files do not have these shapes today, as the change rewrote the four that did.
This fails closed: it costs a false refusal of a later, honest edit.

Not included, as the rule states them ("any binding must be provably str, else the inventory refuses"): a loop
target over a call's result, a default of ``None``, a call such as ``json.dumps(...)``, and a free parameter in one
function beside a binding of the same name in another.
"""
import pytest

from tests._review_wtffu4_3.rev3_inventory import inventory_at
from tests.harness.test_completion_writer_allowlist import unguarded_calls

TEXT = {
    'a-name-rebound-from-itself': ("text = str(value)\ntext = text.strip()\nshown = text.replace('a', 'b')\n", 'b-c'),
    'a-name-rebound-by-its-own-replace': ("text = str(value)\ntext = text.replace('a', 'b')\n"
                                          "text = text.replace('c', 'd')\nshown = text\n", ' b-d '),
    'a-chain-on-a-free-name': ("shown = text.replace('{', '{{').replace('}', '}}')\n", 'a{{c}}'),
    'a-chain-on-a-subscript': ("shown = parts[0].replace('a', 'b').replace('c', 'd')\n", 'b-d'),
    'a-percent-format': ("text = '%s' % value\nshown = text.replace('a', 'b')\n", ' b-c '),
    'a-repeated-text': ("text = '-' * 3\nshown = text.replace('-', '=')\n", '==='),
}
CONTROLS = {
    'one-binding': "text = str(value)\nshown = text.replace('a', 'b')\n",
    'one-replace-on-a-free-name': "shown = text.replace('{', '{{')\n",
    'one-replace-on-a-subscript': "shown = parts[0].replace('a', 'b')\n",
    'a-chain-on-str': "shown = str(text).replace('{', '{{').replace('}', '}}')\n",
}


@pytest.mark.parametrize('form', sorted(TEXT))
def test_text_work_whose_every_binding_is_a_string_is_admitted(form):
    found = unguarded_calls(TEXT[form][0])
    assert not found, f'refused: str.replace on a string, {sorted(found)}'


@pytest.mark.parametrize('form', sorted(TEXT))
def test_control_the_source_is_text_work_and_the_parent_admitted_it(form):
    body, expected = TEXT[form]
    names = {'__builtins__': {'str': str}, 'value': ' a-c ', 'text': 'a{c}', 'parts': ['a-c']}
    exec(compile(body, form, 'exec'), names)       # with ``str`` as its one builtin: it can open or move nothing
    assert type(names['shown']) is str and names['shown'] == expected
    assert not inventory_at()(body)


@pytest.mark.parametrize('form', sorted(CONTROLS))
def test_control_the_one_step_forms_are_admitted(form):
    assert not unguarded_calls(CONTROLS[form])
