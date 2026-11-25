"""Collection bootstrap -> identity -> changed-code map, on real disk."""
import pytest

from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, scenario


@pytest.mark.parametrize('effect', [False, True], ids=['inert-control', 'subscript-binding'])
def test_unread_binding_cannot_authorize_map(tmp_path, effect):
    source = 'import os\nclass Settings:\n'
    source += ("    def __getattribute__(self, name):\n"
               "        os.environ['PATH'] = 'cmfu3-review3'\n"
               "        return object.__getattribute__(self, name)\n" if effect else '    pass\n')
    source += 'classes = [Settings]\nTestSettings = classes[0]\n'
    test = source + HONEST_TEST.replace(
        '    assert neighbour()',
        "    assert (os.environ.get('PATH') == 'cmfu3-review3') is " + str(effect) + '\n    assert neighbour()')
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {'test_api.py': test}})
    node = 'test_api.py::test_call'
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [node] and '1 passed' in found['tail'][0], found
    actual = (found['authority'], sorted(found['answer'].get('codes', {})),
              found['answer'].get('unresolved', []), found['sibling']['exercised'],
              found['sibling']['collection'])
    expected = ((False, [], [node], 'uncertain', 'uncertain') if effect else
                (True, [node], [], True, 'available'))
    assert actual == expected, actual


@pytest.mark.parametrize('flag', [False, True], ids=['disabled-control', 'nested-enabled-class'])
def test_class_inside_data_is_not_a_collection_holder(tmp_path, flag):
    helper = (
        'import os\nclass Settings:\n    __test__ = ' + str(flag) + '\n'
        "    def __getattribute__(self, name):\n"
        "        os.environ['PATH'] = 'cmfu3-review3'\n"
        "        return object.__getattribute__(self, name)\n"
        'SETTINGS = [Settings]\n')
    test = 'import os\nfrom helper import SETTINGS\n' + HONEST_TEST.replace(
        '    assert neighbour()',
        "    assert os.environ.get('PATH') != 'cmfu3-review3'\n    assert neighbour()")
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {'helper.py': helper, 'test_api.py': test}})
    node = 'test_api.py::test_call'
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [node] and '1 passed' in found['tail'][0], found
    actual = (found['authority'], sorted(found['answer'].get('codes', {})),
              found['sibling']['exercised'], found['sibling']['collection'])
    assert actual == (True, [node], True, 'available'), actual
