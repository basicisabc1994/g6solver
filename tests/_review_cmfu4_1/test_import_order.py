"""A re-exported, modeled context manager must not depend on helper spelling."""
import pytest

from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, scenario


@pytest.mark.parametrize('module', ['ahelper', 'zhelper'], ids=['inert-control', 'renamed-helper'])
def test_context_binding_keeps_real_map_answer(tmp_path, module):
    source = ('from ' + module + ' import context\n'
              'with context(None) as settings:\n    pass\n' + HONEST_TEST)
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {module + '.py': 'from contextlib import nullcontext as context\n',
                                'test_api.py': source}})
    node = 'test_api.py::test_call'
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [node] and '1 passed' in found['tail'][0], found
    actual = (found['authority'], sorted(found['answer'].get('codes', {})),
              found['answer'].get('unresolved', []), found['sibling']['exercised'],
              found['sibling']['collection'])
    assert actual == (True, [node], [], True, 'available'), actual
