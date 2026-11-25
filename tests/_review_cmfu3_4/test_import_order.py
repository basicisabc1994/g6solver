import pytest

from tests._review_cmfu3_3.test_bindings import collect


@pytest.mark.parametrize('module', ['ahelper', 'zhelper'])
@pytest.mark.parametrize('effect', [False, True], ids=['inert-control', 'effect'])
def test_imported_container_mutation(tmp_path, module, effect):
    helper = 'import os\nclass Settings:\n'
    helper += ("    def __getattribute__(self, name):\n"
               "        os.environ['PATH'] = 'cmfu3-review3'\n"
               "        return object.__getattribute__(self, name)\n" if effect else '    pass\n')
    helper += '    def test_call(self): pass\nclasses = [None]\nclasses[0] = Settings\n'
    files = {module + '.py': helper,
             'test_api.py': 'from ' + module + ' import classes\nTestSettings = classes[0]\n'}
    wrote, certain = collect(tmp_path, files, 'test_api.py::TestSettings::test_call')
    assert wrote is effect
    if effect:
        assert certain is False, 'imported mutation became a collection holder but read certain'
    else:
        assert certain is (module == 'zhelper')
