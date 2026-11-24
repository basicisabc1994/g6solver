"""Missing imports belong to regular held modules, independently of phase or spelling."""
import importlib
import sys

import pytest

from g6.harness import behaviour_runner as runner


@pytest.mark.parametrize('phase', ['module_import', 'missing_callable', 'method_lookup', 'constructor'])
def test_external_import_is_unavailable_in_every_phase(monkeypatch, tmp_path, phase):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(runner.ObservationUnavailable):
        runner.setup_call(phase, lambda: importlib.import_module('no_such_tmfu1_external'))


@pytest.mark.parametrize('init', [None, '__path__ = __import__("pkgutil").extend_path(__path__, __name__)\n',
                                '__path__ += ["elsewhere"]\n'])
def test_ambiguous_package_portions_do_not_claim_missing_submodules(tmp_path, monkeypatch, init):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    package = tmp_path / 'tmfu1_portion'
    package.mkdir()
    (package / 'mine.py').write_text('VALUE = 1\n', encoding='utf-8')
    if init is not None:
        (package / '__init__.py').write_text(init, encoding='utf-8')
    exc = ModuleNotFoundError('private words', name='tmfu1_portion.generated')
    assert not runner.own_import_absent(exc, ())


@pytest.mark.parametrize('source', ['from tmfu1_held import missing', 'from tmfu1_helper import gone',
                                   'import tmfu1_held.missing'])
def test_held_missing_imports_share_one_rule(tmp_path, monkeypatch, source):
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    package = tmp_path / 'tmfu1_held'
    package.mkdir()
    (package / '__init__.py').write_text('', encoding='utf-8')
    (tmp_path / 'tmfu1_helper.py').write_text('kept = 1\n', encoding='utf-8')
    for name in ('tmfu1_held', 'tmfu1_held.missing', 'tmfu1_helper'):
        monkeypatch.delitem(sys.modules, name, raising=False)
    with pytest.raises(ImportError) as caught:
        runner.setup_call('module_import', exec, source, {})
    assert str(caught.value) in ('call failed: builtins.ImportError', 'call failed: builtins.ModuleNotFoundError')


def test_from_import_path_outside_tree_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'tmfu1_held').mkdir()
    (tmp_path / 'tmfu1_held' / '__init__.py').write_text('', encoding='utf-8')
    exc = ImportError('private words', name='tmfu1_held', path=str(tmp_path.parent / 'installed.py'))
    assert not runner.own_import_absent(exc, ())
