"""A private temporary list must not exhaust bounded effect rereads."""
import pytest

from g6.workforce._collection_effects import CollectionEffects
from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, scenario


@pytest.mark.parametrize('form', ['def', 'lambda'], ids=['inert-control', 'lambda'])
def test_inert_lambda_keeps_real_map_answer(tmp_path, monkeypatch, form):
    visits, passes = [], []
    passive = CollectionEffects.passive
    passive_all = CollectionEffects.passive_all

    def observed_passive(self, file):
        visits.append(file)
        return passive(self, file)

    def observed_all(self):
        start = len(visits)
        result = passive_all(self)
        passes.append({'modules': len(self.trees), 'visits': len(visits) - start,
                       'containers': len(self.mutated_containers), 'revision': self.revision})
        return result

    monkeypatch.setattr(CollectionEffects, 'passive', observed_passive)
    monkeypatch.setattr(CollectionEffects, 'passive_all', observed_all)
    source = ('def initialise(): [0].append(1)\n' if form == 'def' else
              'initialise = lambda: [0].append(1)\n')
    source += 'initialise()\n' + HONEST_TEST
    found = scenario({'root': str(tmp_path / 'repo'), 'api': HONEST_API,
                      'files': {'test_api.py': source}})
    node = 'test_api.py::test_call'
    assert found['collect_exit'] == found['exit'] == 0, found
    assert found['answer']['nodes'] == [node] and '1 passed' in found['tail'][0], found
    actual = (found['authority'], sorted(found['answer'].get('codes', {})),
              found['answer'].get('unresolved', []), found['sibling']['exercised'],
              found['sibling']['collection'])
    assert actual == (True, [node], [], True, 'available'), (actual, passes)
