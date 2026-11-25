"""Preparation diagnostics retain type detail without granting failure authority."""
import pytest

from g6.harness import behaviour_runner as runner, verification_search as search
from tests.harness.test_template_runtime_binding import SOURCE, _compiled, _invoke
from tests.harness.test_template_tmfu2_r3 import ABSENT, GOOD, _observe
from tests.harness.test_verification_templates import result


@pytest.mark.parametrize('kind', ['ImportError', 'ModuleNotFoundError'])
@pytest.mark.parametrize('marked', [True, False], ids=['unavailable-outer', 'owned-absence'])
def test_preparation_failure_type_keeps_its_evidence_authority(kind, marked):
    outcome = {'version': 'call_failure.v1', 'detail': 'call failed: builtins.' + kind}
    if marked:
        outcome.update(missing_import=runner.ObservationUnavailable.DETAIL,
                       missing_imports=['helper'], missing_import_details=['held.deleted'])
    data = {'cases': [{'args': [1], 'expect': 'returns', 'value': 1}]}
    found = runner.finish(0, runner.frame(outcome), data)
    if marked:
        assert found['detail'] == runner.ObservationUnavailable.DETAIL + ': builtins.' + kind
        assert found['result'] == 'error'
        assert found['reason_code'] == 'G6_E_OBSERVATION_UNAVAILABLE'
        assert found['import_evidence']['outcomes'] == [['helper']]
        assert found['import_evidence']['details'] == [['held.deleted']]
        assert found['import_evidence']['unavailable'] == [0]
        assert found['import_evidence']['reading']['result'] == 'failed'
    else:
        assert found == {'result': 'failed', 'detail': outcome['detail']}


@pytest.mark.parametrize('phase', ['module', 'lazy-lookup'])
def test_escaping_preparation_import_keeps_its_qualified_type(tmp_path, phase):
    source = ('import ' + ABSENT + '\n' + GOOD if phase == 'module' else
              # Keep a statically resolvable target, then defer its runtime lookup to preparation.
              GOOD + 'del run\ndef __getattr__(name):\n    import ' + ABSENT + '\n')
    found, _ = _observe(tmp_path / 'tree', source)
    assert found['detail'] == runner.ObservationUnavailable.DETAIL + ': builtins.ModuleNotFoundError'
    assert found['result'] == 'error'
    assert found['import_evidence']['outcomes'] == [[ABSENT]]
    assert found['import_evidence']['unavailable'] == [0]


@pytest.mark.parametrize('intent', ['preserve', 'change'])
def test_portable_preparation_type_text_cannot_replace_structured_authority(tmp_path, monkeypatch, intent):
    namespace, _ = _compiled('example.v1')
    (tmp_path / 'runtime_fixture.py').write_text('import ' + ABSENT + '\n' + SOURCE, encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    with pytest.raises(namespace['ObservationUnavailable']) as caught:
        _invoke('example.v1', namespace)
    assert str(caught.value) == runner.ObservationUnavailable.DETAIL + ': builtins.ModuleNotFoundError'
    node = 'test_generated.py::test_runtime'
    recorded = result(failed=[node], messages={node: str(caught.value)})
    missing = search.reading(recorded, node)
    passing = search.reading(result(passed=[node]), node)
    patch, base = (missing, passing) if intent == 'preserve' else (passing, missing)
    assert search.classify(intent, patch, base)['result'] == (
        search.COUNTEREXAMPLE if intent == 'preserve' else search.NOT_REFUTED)
    recorded['check_errors'] = {node: 'G6_E_OBSERVATION_UNAVAILABLE'}
    unavailable = search.reading(recorded, node)
    assert unavailable['result'] == 'error'
    assert unavailable['detail'] == str(caught.value)
    assert search.classify('preserve', unavailable, passing)['result'] == search.CHECK_ERROR
