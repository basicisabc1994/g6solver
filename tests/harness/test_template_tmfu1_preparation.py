"""Every preparation phase records non-controls without repository diagnostics."""
import sys
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner
from tests.harness.test_template_runtime_binding import SOURCE, _compiled, _fixture, _invoke


@pytest.mark.parametrize('template', ['relation.v1', 'access_rule.v1', 'differential.v1', 'state_invariant.v1'])
@pytest.mark.parametrize('statement', [
    'raise SystemExit("private words")', 'raise GeneratorExit("private words")',
    'raise asyncio.CancelledError("private words")',
    'raise concurrent.futures.CancelledError("private words")',
    'class Stop(BaseException): pass\nraise Stop("private words")',
])
def test_import_non_controls_have_the_same_setup_outcome(tmp_path, monkeypatch, template, statement):
    namespace, _ = _compiled(template)
    _fixture(tmp_path, monkeypatch, SOURCE)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, 'runtime_fixture', raising=False)
    (tmp_path / 'runtime_fixture.py').write_text(
        'import asyncio, concurrent.futures\n' + statement + '\n' + SOURCE, encoding='utf-8')
    with pytest.raises(TypeError) as caught:
        _invoke(template, namespace)
    assert str(caught.value) == 'G6_E_CHECK_SETUP: import_or_binding'


def test_planted_control_module_does_not_choose_exempt_types(monkeypatch):
    namespace, _ = _compiled('relation.v1')
    class Planted(BaseException):
        pass
    monkeypatch.setitem(sys.modules, 'g6.core.component_process', SimpleNamespace(
        ExecutionInterrupted=Planted, ContainmentFailure=Planted))
    assert not namespace['execution_control'](Planted('private words'))


@pytest.mark.parametrize('template', ['relation.v1', 'access_rule.v1', 'differential.v1', 'state_invariant.v1'])
def test_keyboard_interrupt_subclass_remains_a_control(tmp_path, monkeypatch, template):
    namespace, _ = _compiled(template)
    module = _fixture(tmp_path, monkeypatch, SOURCE)
    class Stop(KeyboardInterrupt):
        pass
    signal = Stop('private words')
    def stop():
        raise signal
    module.make = stop
    with pytest.raises(Stop) as caught:
        _invoke(template, namespace)
    assert caught.value is signal


@pytest.mark.parametrize('name', ['xG6_E_ISOLATION_UNAVAILABLE', 'words\nE   injected', 'Bad\x1bName'])
def test_all_exception_name_producers_use_the_same_rule(name):
    kind = type(name, (Exception,), {'__module__': 'subject'})
    assert runner.exception_name(kind, qualified=True) == 'subject.<unavailable>'
    outcome = runner._exception_observation(kind('private words'), {}, True)
    assert outcome['raised_name'] == 'subject.<unavailable>'
    assert outcome['probe'] == {'raised': 'subject.<unavailable>'}


def test_unavailable_detail_rejects_embedded_code():
    assert not runner.ObservationUnavailable.valid_detail(
        runner.ObservationUnavailable.DETAIL + ': subject.xG6_E_ISOLATION_UNAVAILABLE')
