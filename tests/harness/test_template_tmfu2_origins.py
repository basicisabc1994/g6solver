"""Only an explicit generated origin selects the compiled diagnostic reader."""
from types import SimpleNamespace

from g6.harness import verification_search as search
from g6.harness import verification_templates as vt
from tests.harness.test_template_classification_repair3 import counted_producer
from tests.harness.test_verification_templates import result


def test_same_filename_reads_by_origin():
    node = 'test_g6_relation.py::test_relation'
    run = result(failed=[node], messages={node: 'AssertionError: caller detail'})
    assert search.reading(run, node)['result'] == 'failed'
    assert search.reading(run, node, template='relation.v1')['detail'] == (
        'G6_E_CHECK_SETUP: the generated check recorded no compiled failure line')


def test_generated_origin_keeps_first_line_under_another_filename():
    node = 'checks/test_renamed.py::test_relation'
    run = result(failed=[node], messages={node: 'Failed: input 0 raised ValueError\nE   injected'})
    found = search.reading(run, node, template='relation.v1')
    assert found['result'] == 'failed' and found['detail'] == 'Failed: input 0 raised ValueError'


def test_plugin_uses_supplied_origin_for_message_bound(tmp_path):
    counted = counted_producer(tmp_path)
    counted.generated_checks = {'renamed.py': 'relation.v1'}
    message = 'Failed: input 0 raised ValueError' + 'x' * 2100
    for node in ['renamed.py::test_relation', 'test_g6_relation.py::test_caller']:
        report = SimpleNamespace(nodeid=node, when='call', outcome='failed', failed=True,
                                 longrepr=SimpleNamespace(reprcrash=SimpleNamespace(message=message)))
        counted.pytest_runtest_logreport(report)
    assert counted.messages['renamed.py::test_relation'] == message[:2000]
    assert counted.messages['test_g6_relation.py::test_caller'] == message[-2000:]


def test_probe_no_observation_detail_survives_the_structured_reader():
    node = 'renamed.py::test_probe'
    detail = 'G6_E_OBSERVATION_UNAVAILABLE: no single completed observation'
    run = result(failed=[node], messages={node: 'generated.ObservationUnavailable: ' + detail})
    run['check_errors'] = {node: 'G6_E_OBSERVATION_UNAVAILABLE'}
    assert search.reading(run, node)['detail'] == detail


def test_plugin_resolves_the_origin_against_pytests_root(tmp_path, monkeypatch):
    counted = counted_producer(tmp_path)
    monkeypatch.chdir(tmp_path)
    counted.generated_checks = {'checks/test_renamed.py': 'relation.v1'}
    counted.session = SimpleNamespace(config=SimpleNamespace(rootpath=tmp_path / 'checks'))
    message = 'Failed: input 0 raised ValueError' + 'x' * 2100
    report = SimpleNamespace(nodeid='test_renamed.py::test_relation', when='call', outcome='failed', failed=True,
                             longrepr=SimpleNamespace(reprcrash=SimpleNamespace(message=message)))
    counted.pytest_runtest_logreport(report)
    assert counted.messages[report.nodeid] == message[:2000]


def test_access_steps_keep_the_actual_replay_compilers_origin(tmp_path):
    bindings = {'operation': 'subject.allow', 'refusal': 'falsy_return', 'cases': [
        {'who': 'owner', 'resource': 'record', 'args': [1], 'expected': 'allowed'},
        {'who': 'guest', 'resource': 'record', 'args': [0], 'expected': 'refused'}]}
    [one] = vt.template_instances([{'id': 'steps', 'template': 'access_rule.v1', 'requirement_id': 'R1',
                                    'intent': 'preserve', 'bindings': bindings}], {'R1'})
    _, placed, origin = vt.compiled_check(tmp_path, one, {}, [], definition=vt.REGISTRY['state_invariant.v1'])
    node = placed + '::test_replay'
    run = result(failed=[node], messages={node: 'Failed: trace(s) 0 break the invariant on the real state'})
    assert origin.template == 'access_rule.v1'
    assert search.reading(run, node, template=origin.compiled_template)['result'] == 'failed'
