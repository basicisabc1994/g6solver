"""Every diagnostic read is expendable; the in-call exception is not."""
import inspect

import pytest

from g6.harness import behaviour_runner as runner
from g6.harness import behaviour_question as bq
from g6.harness.executable_scenario import Scenario
from tests.repair_question2.helpers import TASK, candidate, repo
from tests.repair_question5.test_observation_inventory import observe


META = '''
class Hostile(type):
    def __repr__(cls):
        raise KeyboardInterrupt()
    def __str__(cls):
        raise SystemExit()
    def __getattribute__(cls, name):
        if name in ('__module__', '__qualname__', '__mro__'):
            raise GeneratorExit()
        return super().__getattribute__(name)
'''

CASES = {
    'module-none': 'DomainError.__module__ = None',
    'module-number': 'DomainError.__module__ = 7',
    'module-odd': "DomainError.__module__ = 'a\\n\\x00\\ud800'",
    'qualname-odd': "DomainError.__qualname__ = 'a\\n\\x00\\ud800'",
    **{f'{field}-string-subclass': (
        'class OddString(str):\n'
        '    def __add__(self, other):\n        raise KeyboardInterrupt()\n'
        '    def __len__(self):\n        raise SystemExit()\n'
        f'DomainError.{field} = OddString("diagnostic")')
       for field in ('__module__', '__qualname__')},
    **{f'{field}-{value}': (
        'class Metadata(type):\n'
        '    def __getattribute__(cls, name):\n'
        f'        if name == {field!r}:\n'
        + ('            raise AttributeError(name)\n' if value == 'missing' else
           f'            return {dict(none="None", number="7")[value]}\n')
        + '        return super().__getattribute__(name)\n'
        + 'class DomainError(Exception, metaclass=Metadata):\n    pass')
       for field in ('__module__', '__qualname__') for value in ('none', 'number', 'missing')},
    'metaclass': META + '\nclass DomainError(Exception, metaclass=Hostile):\n    pass',
    'str': 'DomainError.__str__ = lambda self: (_ for _ in ()).throw(KeyboardInterrupt())',
    'repr': 'DomainError.__repr__ = lambda self: (_ for _ in ()).throw(SystemExit())',
    'args': 'class Unprintable:\n    def __repr__(self):\n        raise GeneratorExit()',
    'args-access': ('DomainError.__getattribute__ = lambda self, name: '
                    '(_ for _ in ()).throw(SystemExit()) if name == "args" '
                    'else BaseException.__getattribute__(self, name)'),
    **{name: f'DomainError = {name}' for name in ('SystemExit', 'KeyboardInterrupt', 'GeneratorExit')},
}


@pytest.mark.parametrize('name, mutation', CASES.items(), ids=CASES)
def test_exception_metadata_never_erases_matching_or_differing_outcome(tmp_path, name, mutation):
    program = 'class DomainError(Exception):\n    pass\n' + mutation + '\n'
    expression = 'DomainError(Unprintable())' if name == 'args' else 'DomainError()'
    program += f'def add():\n    raise {expression}\n'
    root = repo(tmp_path)
    (root / 'calc.py').write_text(program, encoding='utf-8')
    item = bq.caller_candidates(TASK, [candidate(args=[], outcomes={
        'A': {'raises': 'calc.DomainError'}, 'B': {'returns': 5}})])[0]
    question = item.question('b' * 64)
    scenario = Scenario.resolve(root, question)
    assert 'raises calc.DomainError' in bq.form(question, item, scenario=scenario)[0]
    got, size = observe(tmp_path, program, scenario.comparison('A'), scenario=scenario)
    assert got['result'] == 'passed', (name, got)
    assert size < 8192
    assert runner.finish(0, runner.frame(got['observed']), scenario.comparison('B'))['result'] == 'failed'
    one = got['observed']['outcomes'][0]
    assert isinstance(one['raised_name'], str)
    assert set(one['exception_detail']) == {'str', 'repr', 'arguments', 'metaclass'}
    assert set(one['exception_detail'].values()) == {'<unavailable>'}
    if name in ('module-none', 'module-number') or (name.startswith('__module__-') and not name.endswith('string-subclass')):
        assert one['raised_name'] == '<unavailable>.DomainError'
    if name.startswith('__qualname__-') and not name.endswith('string-subclass'):
        assert one['raised_name'] == 'calc.<unavailable>'
    if name == '__module__-string-subclass':
        assert one['raised_name'] == 'diagnostic.DomainError'
    if name == '__qualname__-string-subclass':
        assert one['raised_name'] == 'calc.diagnostic'
    if name == 'metaclass':
        assert one['raised_name'] == '<unavailable>.<unavailable>'
    if name in ('str', 'repr', 'args', 'args-access', 'metaclass'):
        assert one['exception_detail'][{'args': 'arguments', 'args-access': 'arguments'}.get(name, name)] == '<unavailable>'


def test_exception_recording_has_one_bootstrap_owner():
    source = inspect.getsource(runner._exception_observation)
    assert source in runner.BOOTSTRAP
    assert runner.BOOTSTRAP.count('outcomes.append(_exception_observation(') == 1
    assert 'observed_type.__module__' not in runner.BOOTSTRAP


def test_exception_diagnostics_are_optional_bounded_v2_fields():
    detail = dict.fromkeys(('str', 'repr', 'arguments', 'metaclass'), '<unavailable>')
    one = {'raised': [1], 'raised_name': '<unavailable>.<unavailable>', 'exception_detail': detail}
    observed = {'version': 'observation.v2', 'outcomes': [one], 'errors': {}}
    assert runner.read_frame(runner.frame(observed)) == observed
    for bad in (None, {}, {**detail, 'str': 3}, {**detail, 'str': 'x' * 201}, {**detail, 'extra': '?'}):
        one['exception_detail'] = bad
        assert runner.read_frame(runner.frame(observed)) is None
    one['exception_detail'] = detail
    observed['version'] = 'observation.v1'
    assert runner.read_frame(runner.frame(observed)) is None


def test_hostile_metaclass_cannot_erase_exception_ancestry(tmp_path):
    program = META + '\nclass DomainError(ValueError, metaclass=Hostile):\n    pass\ndef add():\n    raise DomainError()\n'
    data = {'function': ['module', 'calc', 'add', 'calc.py'], 'cases': [
        {'args': [], 'expect': 'raises', 'error': ['builtin', '', 'ValueError']}]}
    got, _ = observe(tmp_path, program, data)
    assert got['result'] == 'passed', got
