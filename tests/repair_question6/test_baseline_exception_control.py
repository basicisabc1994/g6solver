"""The pre-change example template handled the metadata used in finding 1."""
import ast
import subprocess

import pytest


@pytest.mark.parametrize('outcome', ['raises', 'returns'])
def test_68ca0eb_decides_exception_with_none_module(outcome):
    text = subprocess.run(['git', 'show', '68ca0eb:src/g6/harness/verification_templates.py'],
                          check=True, capture_output=True, timeout=20).stdout.decode('utf-8')
    constants = {}
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {'_HEADER', '_COMMON', '_EXAMPLE'}:
                constants[node.targets[0].id] = ast.literal_eval(node.value)

    class DomainError(Exception):
        __module__ = None

    def call():
        raise DomainError()

    case = {'args': [], 'kwargs': {}, 'expect': outcome}
    case.update(error=['error']) if outcome == 'raises' else case.update(value=5)
    data = {'function': ['function'], 'cases': [case]}
    source = constants['_HEADER'] + '\nBINDINGS = ' + repr(data) + constants['_COMMON'] + constants['_EXAMPLE']
    namespace = {}
    exec(compile(source, '<68ca0eb example template>', 'exec'), namespace)
    namespace['_load'] = lambda spec: call if spec == ['function'] else DomainError
    if outcome == 'raises':
        namespace['test_example']()
    else:
        with pytest.raises(pytest.fail.Exception, match='case 0 raised'):
            namespace['test_example']()
