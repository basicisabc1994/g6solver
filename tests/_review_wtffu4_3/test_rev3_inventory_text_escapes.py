"""Finding 2: ``os.replace`` is admitted as text behind receivers the new string proof takes for ``str``.

2b07c0bf replaces the text allowance with ``_TextBindings`` (tests/harness/test_completion_writer_allowlist.py lines
444-564): a two-argument ``.replace`` is text when its receiver is "proven to be exact str", a free name keeps its
allowance, and any binding must be provably ``str``, else the inventory refuses. Each body below is run on two real
files first: it moves the file, so its ``.replace`` is ``os.replace`` (``Path.replace`` for one) and no
``str.replace``. The inventory returns nothing for it.

New with this change (the parent's inventory, 673466cb read from git, refuses each):

* ``a-decode-of-anything``, ``a-read-text-of-anything``: ``string`` returns True for every call of a method named
  ``decode`` or ``read_text``, whatever it is called on (line 547), so the result of any object's method of that
  name is a string;
* ``any-free-name``, ``any-free-name-with-a-path``: ``receiver`` (lines 555-563) admits every name with no binding in
  the module, where the parent admitted the two names ``text`` and ``data``: a parameter ``fs`` given ``os``, a
  parameter ``src`` given a ``Path`` (two starred arguments make ``Path.replace(dst)`` a two-argument call);
* ``str-as-a-parameter``: ``str(...)`` is a string unless ``str`` is in ``values`` (line 545), and a parameter with
  no default is never put there (lines 498-504 record defaults alone);
* ``starred-in-an-inline-list``: a constant index is looked up in the literal's elements as written (line 529), so a
  starred element before it shifts what the index names.

Admitted by the parent as well, and still admitted under the rule that "any binding must be provably str":

* ``starred-before-a-tuple-index``, ``starred-in-a-paired-unpack``: the same shift, through a named tuple (line 529)
  and through a paired unpacking (lines 455-458: targets and values are paired by position when their counts match);
* ``a-list-behind-a-conditional``, ``a-list-inside-a-tuple``, ``a-dict-inside-a-tuple``: a mutable container is
  refused only when it is bound to the name directly (line 514); behind a conditional expression or inside a tuple
  its elements as first written stand as the proof, though ``clear``, ``append`` and ``update`` (all in ``VALUES``)
  change them with no binding;
* ``a-global-set-through-the-module``: an assignment to an attribute is recorded against the attribute's root name
  (lines 461-465), so ``me.text = os`` on the module itself rebinds the global ``text`` and leaves its proof standing
  (``text`` is in ``DATA_ATTRIBUTES``, so nothing else refuses the line).

Controls: the parent refuses the six new forms; the forms review 2 named are still refused; a text ``replace`` is
admitted.
"""
import pytest

from tests._review_wtffu4_3.rev3_inventory import inventory_at, moves_a_file
from tests.harness.test_completion_writer_allowlist import unguarded_calls

NEW = {
    'a-decode-of-anything': 'import os\nclass K:\n    def decode(self):\n        return os\nK().decode().replace(p, q)\n',
    'a-read-text-of-anything': ('import os\nclass K:\n    def read_text(self):\n        return os\n'
                                'K().read_text().replace(p, q)\n'),
    'any-free-name': 'import os\ndef f(fs, a, b):\n    fs.replace(a, b)\nf(os, p, q)\n',
    'any-free-name-with-a-path': 'def f(src, dst):\n    src.replace(*[dst], *[])\nf(p, q)\n',
    'str-as-a-parameter': 'import os\ndef f(str):\n    str(1).replace(p, q)\nf(lambda _: os)\n',
    'starred-in-an-inline-list': "import os\n[*[os, os], 'b'][1].replace(p, q)\n",
}
STILL = {
    'starred-before-a-tuple-index': "import os\nt = (*[os, os], 'b')\nt[1].replace(p, q)\n",
    'starred-in-a-paired-unpack': "import os\na, text, *rest = *[1, os], 'y', 'z'\ntext.replace(p, q)\n",
    'a-list-behind-a-conditional': ("import os\nrows = ['a'] if p else ['b']\nrows.clear()\nrows.append(os)\n"
                                    'rows[0].replace(p, q)\n'),
    'a-list-inside-a-tuple': "import os\nt = (['a'],)\nt[0].clear()\nt[0].append(os)\nt[0][0].replace(p, q)\n",
    'a-dict-inside-a-tuple': "import os\nt = ({'k': 'a'},)\nt[0].update(k=os)\nt[0]['k'].replace(p, q)\n",
    'a-global-set-through-the-module': ("import os\nimport rev3_body as me\ntext = 'a'\nme.text = os\n"
                                        'text.replace(p, q)\n'),
}
ESCAPES = {**NEW, **STILL}
REVIEW_2 = {
    'comprehension-target': 'import os\n[text.replace(p, q) for text in [os]]\n',
    'boolean-operator': "import os\ndata = ''\ndata = data or os\ndata.replace(p, q)\n",
    'a-calls-result': 'import os\ndef pick():\n    return os\ntext = pick()\ntext.replace(p, q)\n',
    'a-list-built-by-a-call': 'import os\nrows = list([os])\nrows[0].replace(p, q)\n',
    'text-assigned-the-module': 'import os\ntext = os\ntext.replace(p, q)\n',
}


@pytest.mark.parametrize('form', sorted(ESCAPES))
def test_inventory_refuses_a_file_move_behind_the_string_proof(tmp_path, form):
    assert moves_a_file(ESCAPES[form], tmp_path), 'the body must really move the file for this test to mean anything'
    assert unguarded_calls(ESCAPES[form]), 'admitted: a file was moved by a call the inventory read as str.replace'


@pytest.mark.parametrize('form', sorted(NEW))
def test_control_the_parents_inventory_refused_the_new_forms(tmp_path, form):
    assert moves_a_file(NEW[form], tmp_path)
    assert inventory_at()(NEW[form])


@pytest.mark.parametrize('form', sorted(REVIEW_2))
def test_control_the_forms_review_2_named_are_refused(tmp_path, form):
    assert moves_a_file(REVIEW_2[form], tmp_path)
    assert unguarded_calls(REVIEW_2[form])


@pytest.mark.parametrize('body', [
    "shown = text.replace('a', 'b')\n", "shown = parts[0].replace('a', 'b')\n",
    "text = str(value)\nshown = text.replace('a', 'b')\n", "shown = 'a-b'.replace('-', ' ')\n",
])
def test_control_a_text_replace_is_admitted(body):
    assert not unguarded_calls(body)
