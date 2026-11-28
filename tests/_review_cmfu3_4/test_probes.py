import pytest

from tests._review_cmfu3_3.test_bindings import collect


ROUTES = {
    'negative': 'classes = [Settings]\nTestSettings = classes[-1]\n',
    'computed': 'classes = [Settings]\nTestSettings = classes[1-1]\n',
    'nested': 'classes = [[Settings]]\nTestSettings = classes[0][0]\n',
    'nested-parentheses': 'classes = [[Settings]]\nTestSettings = (classes[0])[0]\n',
    'nested-dict': "classes = {'group': [Settings]}\nTestSettings = classes['group'][0]\n",
    'slice': 'classes = [Settings]\nTestSettings = classes[:][0]\n',
    'comprehension': 'classes = [Settings for _ in (1,)]\nTestSettings = classes[0]\n',
    'dict-key': "classes = {True: Settings}\nTestSettings = classes[1]\n",
    'negative-key': 'classes = {-1: Settings}\nTestSettings = classes[-1]\n',
    'dict-duplicate': "classes = {'group': None, 'group': Settings}\nTestSettings = classes['group']\n",
    'dict': "classes = {'chosen': Settings}\nTestSettings = classes['chosen']\n",
    'tuple': 'TestSettings, other = (Settings, None)\n',
    'walrus': 'held = (TestSettings := Settings)\n',
    'decorator': 'def identity(cls): return cls\nTestSettings = identity(Settings)\n',
    'attribute': 'class TestOuter:\n    TestSettings = Settings\n',
    'context-alias': 'from contextlib import nullcontext as ctx\nwith ctx(Settings) as TestSettings: pass\n',
    'comprehension-walrus': 'DATA = [(TestSettings := Settings) for _ in (1,)]\n',
    'method-mutation': 'classes = [None]\nclass Builder:\n    @staticmethod\n    def build():\n        classes[0] = Settings\nBuilder.build()\nTestSettings = classes[0]\n',
    'later-mutation': 'classes = [None]\nTestSettings = classes[0]\nclasses[0] = Settings\nTestSettings = classes[0]\n',
    'alias-mutation': 'classes = [None]\nalias = classes\nalias[0] = Settings\nTestSettings = classes[0]\n',
}


@pytest.mark.parametrize('route', ROUTES)
def test_effect(tmp_path, route):
    body = ('import os\nclass Settings:\n'
            '    def __getattribute__(self, name):\n'
            "        os.environ['PATH'] = 'cmfu3-review3'\n"
            '        return object.__getattribute__(self, name)\n'
            '    def test_call(self): pass\n')
    node = 'test_api.py::' + ('TestOuter::' if route == 'attribute' else '') + 'TestSettings::test_call'
    assert collect(tmp_path, {'test_api.py': body + ROUTES[route]}, node) == (True, False)
