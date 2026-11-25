"""Honest class configuration must not bypass collection-effect evidence."""
import pytest

from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, scenario


@pytest.mark.parametrize('route', ['classmethod-flag', 'subclass-flag', 'classmethod-protocol'])
@pytest.mark.parametrize('effect', [False, True], ids=['inert-control', 'effect'])
def test_class_write_cannot_authorize_map(tmp_path, route, effect):
    source = 'import os\ndef lookup(self, name):\n'
    if effect:
        source += "    os.environ['PATH'] = 'cmfu4-review1'\n"
    source += '    return object.__getattribute__(self, name)\n'
    source += {
        'classmethod-flag': (
            'class Settings:\n'
            '    __test__ = False\n'
            '    __getattribute__ = lookup\n'
            '    @classmethod\n'
            '    def configure(cls): cls.__test__ = True\n'
            'Settings.configure()\n'),
        'subclass-flag': (
            'class Base:\n'
            '    def __init_subclass__(cls): cls.__test__ = True\n'
            'class Settings(Base):\n'
            '    __test__ = False\n'
            '    __getattribute__ = lookup\n'),
        'classmethod-protocol': (
            'class TestSettings:\n'
            '    @classmethod\n'
            '    def configure(cls): cls.__getattribute__ = lookup\n'
            'TestSettings.configure()\n'),
    }[route]
    source += HONEST_TEST.replace(
        '    assert neighbour()',
        "    assert (os.environ.get('PATH') == 'cmfu4-review1') is "
        + str(effect) + '\n    assert neighbour()')
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {'test_api.py': source}})
    node = 'test_api.py::test_call'
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [node] and '1 passed' in found['tail'][0], found
    actual = (found['authority'], sorted(found['answer'].get('codes', {})),
              found['answer'].get('unresolved', []), found['sibling']['exercised'],
              found['sibling']['collection'])
    expected = ((False, [], [node], 'uncertain', 'uncertain') if effect else
                (True, [node], [], True, 'available'))
    assert actual == expected, actual
