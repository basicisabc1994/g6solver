"""Amendment 1: compiler failures survive the actual producer and reader."""
import json
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner
from g6.harness import verification_search as search, verification_templates as vt
from tests.harness.test_template_classification_repair3 import counted_producer
from tests.harness.test_template_runtime_binding import SOURCE, SETUP_FAILURES, _compiled, _fixture
from tests.harness.test_verification_templates import result


def _produced(tmp_path, template, invoke, monkeypatch, *, node=None, raw=False):
    node = node or 'test_g6_' + template.partition('.')[0] + '.py::test_check'
    counted = counted_producer(tmp_path)
    monkeypatch.setattr(sys, '_g6_check_setup_types', [], raising=False)
    with pytest.raises(BaseException) as caught:
        invoke()
    crash = caught.getrepr(style='short').reprcrash
    report = SimpleNamespace(nodeid=node, when='call', outcome='failed', failed=True,
                             longrepr=SimpleNamespace(reprcrash=crash))
    counted.pytest_exception_interact(None, SimpleNamespace(excinfo=caught), report)
    counted.pytest_runtest_logreport(report)
    reading = result(failed=[node], messages=counted.messages)
    reading['check_errors'] = counted.check_errors
    return (reading if raw else search.reading(reading, node)), counted.messages[node]


@pytest.mark.parametrize('template', ['example.v1', 'relation.v1', 'access_rule.v1',
                                    'differential.v1', 'state_invariant.v1'])
@pytest.mark.parametrize('failure', ['wrong_result', 'missing_callable'])
def test_every_runtime_compiler_failure_survives_producer_and_reader(tmp_path, monkeypatch, template, failure):
    namespace, data = _compiled(template)
    source = SOURCE.replace('return value', 'self.n = -1\n        return value + 1')
    if failure == 'missing_callable':
        source = source.replace('def act(', 'def absent(')
    _fixture(tmp_path, monkeypatch, source)
    monkeypatch.syspath_prepend(str(tmp_path))
    if template == 'differential.v1':
        # A differential probe fails only when its entry point disappears; output differences are observations.
        if failure == 'wrong_result':
            found = namespace['_outcome'](data['calls'][0])
            assert found == {'returned': 1}
            return
        def invoke():
            return namespace['test_differential'](SimpleNamespace(disabled=nullcontext))
    elif template == 'state_invariant.v1':
        def invoke():
            return namespace['test_replay'](SimpleNamespace(disabled=nullcontext))
    else:
        invoke = namespace['test_' + template.partition('.')[0]]
    found, message = _produced(tmp_path, template, invoke, monkeypatch)
    assert found['result'] == 'failed', (found, message)
    assert 'no compiled failure line' not in found['detail']


@pytest.mark.parametrize('template', ['relation.v1', 'access_rule.v1', 'state_invariant.v1'])
def test_operation_exceptions_survive_producer_and_reader(tmp_path, monkeypatch, template):
    namespace, _ = _compiled(template)
    _fixture(tmp_path, monkeypatch, SOURCE.replace('return value', 'raise ValueError("private words")'))
    if template == 'state_invariant.v1':
        def invoke():
            return namespace['test_replay'](SimpleNamespace(disabled=nullcontext))
    else:
        invoke = namespace['test_' + template.partition('.')[0]]
    found, message = _produced(tmp_path, template, invoke, monkeypatch)
    assert found['result'] == 'failed', (found, message)
    assert 'private words' not in found['detail']


def test_long_real_summary_keeps_complete_fragments_through_producer(tmp_path, monkeypatch):
    namespace, _ = _compiled('relation.v1')
    class Value:
        def __repr__(self):
            return 'x' * 200
    namespace['_load'] = lambda spec: lambda value: Value()
    namespace['BINDINGS'].update(inputs=list(range(32)))
    found, message = _produced(tmp_path, 'relation.v1', namespace['test_relation'], monkeypatch)
    assert found['result'] == 'failed', (found, message)
    assert len(message) <= 2000
    assert message.endswith(' more')


@pytest.mark.parametrize('field', ['__module__', '__qualname__'])
def test_safe_name_subclasses_keep_their_text_without_hooks(field):
    class Name(str):
        def __str__(self):
            raise KeyboardInterrupt()
        def __len__(self):
            raise SystemExit()
        def __add__(self, other):
            raise GeneratorExit()
    kind = type('DomainError', (Exception,), {'__module__': 'calc'})
    setattr(kind, field, Name('diagnostic'))
    wanted = 'diagnostic.DomainError' if field == '__module__' else 'calc.diagnostic'
    assert runner.exception_name(kind, qualified=True) == wanted
    assert runner._exception_observation(kind(), {}, True)['raised_name'] == wanted


@pytest.mark.parametrize('failure', SETUP_FAILURES)
def test_differential_setup_frame_remains_unavailable(tmp_path, failure):
    _, data = _compiled('differential.v1')
    (tmp_path / 'runtime_fixture.py').write_text(SETUP_FAILURES[failure], encoding='utf-8')
    boot, channel = tmp_path / 'bootstrap.py', tmp_path / 'result.bin'
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_bytes(b'')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=10)
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    assert found['result'] == 'error'
    assert found['detail'].startswith('G6_E_OBSERVATION_UNAVAILABLE:')
    assert 'observed' not in found and 'observations' not in found


def test_keyboard_interrupt_in_the_observed_call_keeps_exception_matching(tmp_path, monkeypatch):
    _, data = _compiled('example.v1')
    data['cases'] = [{'args': [0], 'kwargs': {}, 'expect': 'raises',
                      'error': ['builtin', '', 'KeyboardInterrupt']}]
    source = SOURCE.replace('return value', 'raise KeyboardInterrupt()')
    _fixture(tmp_path, monkeypatch, source)
    boot, channel = tmp_path / 'bootstrap.py', tmp_path / 'result.bin'
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_bytes(b'')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=10)
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    assert found['result'] == 'passed', found


def test_failure_inventory_reads_comparison_without_running_a_check(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError('executed')))
    assert vt.failure_matches('example.v1',
                              'Failed: the confirmed outcome did not match the observed call(s): [0]')


@pytest.mark.parametrize('name', ['Cafe\u0301Error', '\u2118Error', 'DomainError'])
def test_valid_unicode_exception_names_survive_the_reader(tmp_path, monkeypatch, name):
    namespace, _ = _compiled('relation.v1')
    module = _fixture(tmp_path, monkeypatch, SOURCE)
    kind = type(name, (Exception,), {'__module__': 'runtime_fixture'})
    def fail(value):
        raise kind()
    module.Machine.act = lambda self, value: fail(value)
    found, message = _produced(tmp_path, 'relation.v1', namespace['test_relation'], monkeypatch)
    assert found['result'] == 'failed', (found, message)
    assert name in found['detail']


@pytest.mark.parametrize('name', ['xG6_E_CHECK_SETUP', 'Bad-Name', 'Bad\x1bName'])
def test_forged_exception_names_never_match_the_compiled_inventory(name):
    node = 'test_g6_relation.py::test_relation'
    message = 'Failed: input 0 raised ' + name
    found = search.reading(result(failed=[node], messages={node: message}), node)
    assert found['result'] == 'error'


@pytest.mark.parametrize('message', ['case 0 returned -1, expected 5', 'case 0 raised ValueError',
                                    'case 0 returned 5 where ValueError was expected',
                                    'case 0 left argument 0 as [], expected [1]'])
def test_previous_example_compilers_keep_their_failure_lines(message):
    node = 'test_g6_example.py::test_example'
    found = search.reading(result(failed=[node], messages={node: 'E   Failed: ' + message}), node)
    assert found['result'] == 'failed', found


@pytest.mark.parametrize('patch_fails', [False, True])
def test_confirmed_replay_and_reproduction_use_real_compiler_failure_records(tmp_path, monkeypatch, patch_fails):
    from g6.harness import confirmed_checks
    from g6.workforce import repo_tests
    from tests.repair_question2.helpers import TASK, repo, standard

    root = repo(tmp_path)
    before = search.bind_execution(repo_tests.change(root), repo_tests.snapshot(root), deadline=search._clock() + 60)
    seen = []

    def execute(change, placed, source, node, *, at_base, **kwargs):
        seen.append(kwargs.get('artefact'))
        fails = at_base or patch_fails
        (root / 'calc.py').write_text('def add(a,b):\n    return a' + ('-' if fails else '+') + 'b\n')
        with monkeypatch.context() as patch:
            patch.chdir(root)
            namespace = {'__name__': 'test_g6_example'}
            exec(source, namespace)
            if not fails:
                namespace['test_example']()
                return result(passed=[node + '::test_example'])
            produced, _ = _produced(tmp_path, 'example.v1', namespace['test_example'], patch,
                                    node=node + '::test_example', raw=True)
            return produced

    monkeypatch.setattr(search, '_run_check', execute)
    [found] = confirmed_checks.run(root, repo_tests.change(root), standard(), task=TASK,
                                   deadline=search._clock() + 60, artifact='b' * 64, before=before)
    assert found['result'] == ('failed' if patch_fails else 'passed'), found
    assert len(seen) == (2 if patch_fails else 3)
    assert all(binding is before for binding in seen)
    if patch_fails:
        assert 'reproduction' in found
        assert 'the confirmed outcome did not match' in found['reproduction']['on_patch']
    else:
        assert found['runs']['again']['result'] == 'passed'
