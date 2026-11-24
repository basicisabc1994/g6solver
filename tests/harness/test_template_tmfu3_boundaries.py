"""Import provenance and display normalization at the isolated observation boundary."""
import ast
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_templates as vt
from tests.harness.test_template_runtime_binding import _compiled


RUNS = 'def run(value):\n    return value\n'
MISSING = 'tmfu3_absent_external'
GUARD = f'try:\n    import {MISSING}\n    fast = 1\nexcept ImportError:\n    pass\n'


def _observe(tmp_path, source, *, template='example.v1', extra=None, expected=None,
             known=(), base=(), changed=(), cases=None):
    (tmp_path / 'subject.py').write_text(RUNS, encoding='utf-8')
    tree = vt.Tree(tmp_path, SimpleNamespace(files=tuple(changed)),
                   frozenset({'subject.py', *known}), frozenset(base))
    definition = vt.REGISTRY[template]
    bindings = {'function': 'subject.run'}
    if template == 'example.v1':
        bindings['cases'] = cases or [{'args': [1], **({'raises': expected} if expected else {'returns': 1})}]
    else:
        bindings.update(mode='outputs', calls=[{'args': [1]}])
    data = definition.applies(tree, definition.bindings.model_validate(bindings), vt.PATCH).data
    (tmp_path / 'subject.py').write_text(source, encoding='utf-8')
    for rel, text in (extra or {}).items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    data = runner.located(tmp_path, data)
    # Keep the importing bootstrap outside the repository tree, as the product does.
    boot, channel = tmp_path.parent / (tmp_path.name + '-boot.py'), tmp_path.parent / (tmp_path.name + '-frame')
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_bytes(b'')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=20)
    return runner.finish(ran.returncode, channel.read_bytes(), data)


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
@pytest.mark.parametrize('ending', [
    "raise ImportError('install the package') from None",
    "raise RuntimeError('unavailable') from exc",
    'return 0',
])
def test_a_caught_missing_import_retains_its_cause(tmp_path, template, ending):
    source = (f'def run(value):\n    try:\n        import {MISSING}\n'
              f'    except ImportError as exc:\n        {ending}\n    return value\n')
    found = _observe(tmp_path, source, template=template)
    assert found['result'] == 'error' and 'G6_E_OBSERVATION_UNAVAILABLE:' in found['detail']
    assert 'observations' not in found


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
def test_a_held_name_uses_imports_recorded_inside_the_call(tmp_path, template):
    found = _observe(tmp_path, 'def run(value):\n    from held import fast\n    return value\n',
                     template=template, extra={'held/__init__.py': GUARD})
    assert found['result'] == 'error' and 'G6_E_OBSERVATION_UNAVAILABLE:' in found['detail']


@pytest.mark.parametrize('body', ["raise RuntimeError('required')", 'return held.fast'])
def test_a_preparation_guard_makes_a_nonmatching_reading_unavailable(tmp_path, body):
    source = f'import held\ndef run(value):\n    {body}\n'
    found = _observe(tmp_path, source, extra={'held/__init__.py': GUARD})
    assert found['result'] == 'error' and 'G6_E_OBSERVATION_UNAVAILABLE:' in found['detail']


def test_a_matching_fallback_still_supports_the_confirmed_value(tmp_path):
    source = f'def run(value):\n    try:\n        import {MISSING}\n    except ImportError:\n        return 1\n'
    assert _observe(tmp_path, source)['result'] == 'passed'


def test_a_missing_import_in_a_later_call_cannot_leave_an_earlier_mismatch_supported(tmp_path):
    source = (f'def run(value):\n    if value:\n        try:\n            import {MISSING}\n'
              '        except ImportError:\n            pass\n    return 0\n')
    found = _observe(tmp_path, source, cases=[{'args': [0], 'returns': 1}, {'args': [1], 'returns': 0}])
    assert found['result'] == 'error' and 'G6_E_OBSERVATION_UNAVAILABLE:' in found['detail']


@pytest.mark.parametrize('local', [
    'def configure():\n    fast = 1\n    return fast\n',
    'class Config:\n    fast = 1\n',
    'def configure():\n    exec("fast = 1")\n',
    'def configure():\n    setattr(object(), "fast", 1)\n',
    'configure = lambda: (fast := 1)\n',
])
def test_only_module_level_bindings_can_guard_a_held_export(tmp_path, local):
    # A real repository-requested missing import isolates the scope rule from stdlib filtering.
    guard = f'try:\n    import {MISSING}\nexcept ImportError:\n    pass\n'
    found = _observe(tmp_path, 'from held import fast\n' + RUNS, extra={'held/__init__.py': guard + local})
    assert found == {'result': 'failed', 'detail': 'call failed: builtins.ImportError'}


def test_standard_library_optional_imports_do_not_taint_a_repository_failure(tmp_path):
    source = "import subprocess\ndef run(value):\n    raise RuntimeError('ordinary failure')\n"
    assert _observe(tmp_path, source)['result'] == 'failed'


@pytest.mark.parametrize('evidence', ['known', 'base', 'changed'])
@pytest.mark.parametrize('rel,module', [('helpers.py', 'helpers'), ('src/helpers.py', 'helpers'),
                                      ('held/__init__.py', 'held')])
def test_a_deleted_top_level_module_has_host_owned_file_evidence(tmp_path, evidence, rel, module):
    found = _observe(tmp_path, f'def run(value):\n    import {module}\n    return value\n',
                     **{evidence: [rel]})
    assert found['result'] == 'failed'


def test_an_unknown_top_level_module_remains_unavailable(tmp_path):
    found = _observe(tmp_path, f'def run(value):\n    import {MISSING}\n    return value\n')
    assert found['result'] == 'error' and 'G6_E_OBSERVATION_UNAVAILABLE:' in found['detail']


@pytest.mark.parametrize('expected', ['ImportError', 'ModuleNotFoundError'])
def test_an_unavailable_import_never_supports_a_confirmed_raise(tmp_path, expected):
    found = _observe(tmp_path, f'def run(value):\n    import {MISSING}\n    return value\n', expected=expected)
    assert found['result'] == 'error' and 'G6_E_OBSERVATION_UNAVAILABLE:' in found['detail']


def test_an_explicit_import_error_without_a_missing_import_remains_observed(tmp_path):
    assert _observe(tmp_path, "def run(value):\n    raise ImportError('refused')\n",
                    expected='ImportError')['result'] == 'passed'


@pytest.mark.parametrize('copy', ['bootstrap', 'relation.v1', 'differential.v1'])
def test_display_normalizes_addresses_before_escaping_codes(copy):
    if copy == 'bootstrap':
        nodes = ast.parse(runner.BOOTSTRAP).body
        helpers = [node for node in nodes if isinstance(node, ast.FunctionDef) and node.name in ('_shown', '_name')]
        namespace = {'json': json, 're': __import__('re'), 'exception_name': runner.exception_name}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), '<display helpers>', 'exec'), namespace)
    else:
        namespace, _ = _compiled(copy)

    class Display:
        def __repr__(self):
            return 'G6_E at 0x0_CHECK_SETUP; next\nline'

    shown = namespace['_shown'](Display())
    assert 'G6_E_' not in shown and '0x0' not in shown
    assert r'\u0047' in shown and r'\u003b' in shown and len(shown.splitlines()) == 1
