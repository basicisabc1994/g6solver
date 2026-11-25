"""Shipped collection bootstrap -> identity reader -> changed-code test map."""
import pytest

from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, scenario


@pytest.mark.parametrize('effect', [False, True], ids=['inert-control', 'imported-module-hook'])
def test_unread_hook_cannot_authorize_the_change_map(tmp_path, effect):
    helper = 'VALUE = 1\n'
    if effect:
        helper = ("import os\ndef __getattr__(name):\n"
                  "    os.environ['PATH'] = 'cmfu3-review2'\n    raise AttributeError(name)\n")
    comparison = '==' if effect else '!='
    test = 'import os\nimport helper\n' + HONEST_TEST.replace(
        '    assert neighbour()',
        "    assert os.environ.get('PATH') " + comparison + " 'cmfu3-review2'\n    assert neighbour()")
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {'helper.py': helper, 'test_api.py': test}})
    node = 'test_api.py::test_call'
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [node] and '1 passed' in found['tail'][0], found
    state = (found['authority'], sorted(found['answer'].get('codes', {})),
             found['answer'].get('unresolved', []), found['sibling']['exercised'],
             found['sibling']['collection'])
    expected = (False, [], [node], 'uncertain', 'uncertain') if effect else (True, [node], [], True, 'available')
    assert state == expected, state
