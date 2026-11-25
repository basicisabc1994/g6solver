import ast
import time

import pytest

from g6.workforce._collection_effects import CollectionEffects
from tests.core.test_change_map_fu2_effects import inventory


@pytest.mark.parametrize('form', ['direct', 'alias', 'subclass'])
@pytest.mark.parametrize('effect', [False, True], ids=['inert-control', 'effect'])
def test_class_receiver_write_order(tmp_path, monkeypatch, form, effect):
    body = 'import os\ndef lookup(self, name):\n'
    if effect:
        body += "    os.environ['PATH'] = 'cmfu4-review2'\n"
    body += '    return object.__getattribute__(self, name)\n'
    if form == 'subclass':
        body += ('class Base:\n    def __init_subclass__(cls):\n'
                 '        alias = cls\n        alias.__test__ = True\n'
                 'class Settings(Base):\n    __test__ = False\n    __getattribute__ = lookup\n')
    else:
        store = 'cls.__test__ = True' if form == 'direct' else 'alias = cls; alias.__test__ = True'
        body += ('class Settings:\n    __test__ = False\n    __getattribute__ = lookup\n'
                 '    @classmethod\n    def configure(cls): ' + store + '\nSettings.configure()\n')
    answers = []
    for name in ('ahelper', 'zhelper'):
        files = {name + '.py': body, 'test_api.py': 'from ' + name + ' import Settings\ndef test_call(): pass\n'}
        answers.append(inventory(tmp_path / name, monkeypatch, files)[1])
        for reverse in (False, True):
            trees = [(file, ast.parse(source)) for file, source in files.items()]
            reader = CollectionEffects.from_trees(dict(reversed(trees) if reverse else trees))
            answers.append(reader.passive_all())
    assert answers == [not effect] * 6


def test_ordinary_repository_pass_count(tmp_path, monkeypatch):
    files = {f'helper_{i:03}.py': 'initialise = lambda: [0].append(1)\ninitialise()\n'
             for i in range(150)}
    files['test_api.py'] = 'def test_call(): pass\n'
    visits = []
    original = CollectionEffects.passive

    def observe(self, file):
        visits.append(file)
        return original(self, file)

    monkeypatch.setattr(CollectionEffects, 'passive', observe)
    started = time.monotonic()
    assert inventory(tmp_path, monkeypatch, files)[1] is True
    elapsed = time.monotonic() - started
    print({'files': len(files), 'module_visits': len(visits), 'seconds': elapsed})
    assert len(visits) == 2 * len(files)
