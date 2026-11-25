import pytest

from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, scenario


@pytest.mark.parametrize('effect', [False, True], ids=['inert-control', 'collection-effect'])
@pytest.mark.parametrize('route', ['nested', 'imported-mutation'])
def test_unread_collection_effect_cannot_authorize_map(tmp_path, effect, route):
    source = 'import os\nclass Settings:\n'
    source += ("    def __getattribute__(self, name):\n"
               "        os.environ['PATH'] = 'cmfu3-review4'\n"
               "        return object.__getattribute__(self, name)\n" if effect else '    pass\n')
    if route == 'nested':
        source += 'classes = [[Settings]]\nTestSettings = classes[0][0]\n'
        files = {}
    else:
        files = {'zhelper.py': source + 'classes = [None]\nclasses[0] = Settings\n'}
        source = 'import os\nfrom zhelper import classes\nTestSettings = classes[0]\n'
    test = source + HONEST_TEST.replace(
        '    assert neighbour()',
        "    assert (os.environ.get('PATH') == 'cmfu3-review4') is " + str(effect) + '\n    assert neighbour()')
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {**files, 'test_api.py': test}})
    node = 'test_api.py::test_call'
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [node] and '1 passed' in found['tail'][0], found
    actual = (found['authority'], sorted(found['answer'].get('codes', {})),
              found['answer'].get('unresolved', []), found['sibling']['exercised'],
              found['sibling']['collection'])
    expected = ((False, [], [node], 'uncertain', 'uncertain') if effect else
                (True, [node], [], True, 'available'))
    assert actual == expected, actual
