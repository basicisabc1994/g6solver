"""Repair 3 provenance, archive ownership and interpreter independence boundaries."""
import ast
import builtins
import io
import json
import random
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_search as vs, verification_templates as vt

ABSENT = 'tmfu2_r3_absent_dependency'
GOOD = 'def run(value):\n    return value\n'


def _observe(folder, source, *, files=None, cases=None, module='subject', deleted=()):
    sources = {module + '.py': source, **(files or {})}
    for rel, content in sources.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    tree = vt.Tree(folder, SimpleNamespace(files=tuple(deleted)), frozenset(sources), frozenset((*sources, *deleted)))
    tree._texts.update({(name, vt.BASE): content for name, content in sources.items()})
    binding = {'function': module + '.run', 'cases': cases or [{'args': [1], 'returns': 1}]}
    definition = vt.REGISTRY['example.v1']
    data = runner.located(folder, definition.applies(tree, definition.bindings.model_validate(binding), vt.PATCH).data)
    boot, channel = folder.parent / (folder.name + '-boot.py'), folder.parent / (folder.name + '-frame')
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_text(json.dumps(runner.request(data)), encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(folder), str(channel), '@channel'],
                         capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
    return runner.finish(ran.returncode, channel.read_bytes(), data), data


def _reading(found):
    return vs.reading({'g6_observation': found, 'verdict': {
        'passed': 'passed', 'failed': 'failed', 'error': 'not_run'}[found['result']],
        'said': found['detail'], 'source_before': '0' * 64, 'seconds': 0.1}, 'test_example.py::test_example')


@pytest.mark.parametrize('broken', [True, False], ids=['platform-library', 'clean-library'])
def test_outer_platform_request_retains_propagated_failure(tmp_path, broken):
    if sys.platform != 'win32':
        pytest.skip('pty has a missing platform dependency on Windows')
    name = 'pty' if broken else 'colorsys'
    source = (f'def run(value):\n    try:\n        import {name}\n'
              '    except ImportError:\n        return value\n    return value\n')
    found, _ = _observe(tmp_path / 'tree', source)
    assert found['result'] == 'passed', found
    if broken:
        assert found['fallback_observation']['missing_imports'] == ['pty'], found
    else:
        assert 'import_evidence' not in found, found


@pytest.mark.parametrize('translated', [False, True], ids=['dependency', 'translated'])
@pytest.mark.parametrize('owned', [False, True], ids=['external-inner', 'owned-inner'])
def test_outer_repository_request_keeps_a_different_inner_name(tmp_path, translated, owned):
    missing = 'held.deleted' if owned else ABSENT
    library = f'import {missing}\n'
    if translated:
        library = f'try:\n    import {missing}\nexcept ImportError:\n    raise ImportError("unavailable")\n'
    source = ('def run(value):\n    try:\n        import helper\n'
              '    except ImportError:\n        return value\n    return 99\n')
    found, _ = _observe(tmp_path / 'tree', source, files={'helper.py': library, 'held/__init__.py': ''},
                        deleted=('held/deleted.py',) if owned else ())
    assert found['fallback_observation']['missing_imports'] == ['helper'], found
    assert found['import_evidence']['details'] == [[missing]], found


def test_escaping_outer_request_keeps_its_mark_during_preparation(tmp_path):
    found, _ = _observe(tmp_path / 'tree', 'import helper\n' + GOOD,
                        files={'helper.py': 'import held.deleted\n', 'held/__init__.py': ''},
                        deleted=('held/deleted.py',))
    assert found['result'] == 'error', found
    assert found['import_evidence']['outcomes'] == [['helper']], found
    assert found['import_evidence']['unavailable'] == [0], found


@pytest.mark.parametrize('translated', [False, True], ids=['builtin', 'subclass'])
def test_preparation_escape_is_unavailable_even_with_matching_marks(tmp_path, translated):
    library = 'import held.deleted\n'
    if translated:
        library = ('class Missing(ImportError):\n    pass\ntry:\n    import held.deleted\n'
                   'except ImportError:\n    raise Missing("dependency")\n')
    files = {'helper.py': library, 'held/__init__.py': ''}
    base, _ = _observe(tmp_path / 'base', 'try:\n    import helper\nexcept ImportError:\n    pass\n' + GOOD,
                       files=files, deleted=('held/deleted.py',))
    patch, _ = _observe(tmp_path / 'patch', 'import helper\n' + GOOD,
                        files=files, deleted=('held/deleted.py',))
    assert patch['result'] == 'error', patch
    assert patch['import_evidence']['outcomes'] == [['helper']], patch
    assert vs.classify('preserve', _reading(patch), _reading(base))['result'] == vs.CHECK_ERROR


@pytest.mark.parametrize('module', ['importlib', '_frozen_importlib', '_frozen_importlib_external', 'subject'],
                         ids=['repository-namesake', 'bootstrap-namesake', 'external-namesake', 'stdlib-helper'])
@pytest.mark.parametrize('translated', [False, True], ids=['returned', 'translated'])
def test_frame_path_controls_namesake_and_real_importlib(tmp_path, module, translated):
    request = f'importlib.import_module({ABSENT!r})' if module == 'subject' else f'import {ABSENT}'
    # Frozen modules take precedence over disk on import; a repository frame may still have either name.
    target = module if module in ('subject', 'importlib') else 'subject'
    prefix = 'import importlib\n' if module == 'subject' else f'__name__ = {module!r}\n'
    source = (prefix +
              f'def run(value):\n    try:\n        {request}\n    except ImportError:\n        ' +
              ('raise RuntimeError("fallback")\n' if translated else 'return value\n') + '    return 99\n')
    cases = [{'args': [1], 'raises': 'RuntimeError'}] if translated else None
    found, _ = _observe(tmp_path / 'tree', source, module=target, cases=cases)
    assert found['result'] == 'passed', found
    assert found['fallback_observation']['missing_imports'] == [ABSENT], found


def _package(archive, complete):
    members = {'held/__init__.py': '', 'held/kept.py': ''}
    if complete:
        members['held/deleted.py'] = ''
    if not archive:
        return members
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as zipped:
        for name, text in members.items():
            zipped.writestr(name, text)
    return {'vendor.zip': stream.getvalue()}


@pytest.mark.parametrize('archive', [True, False], ids=['zip', 'unpacked'])
def test_regular_package_owns_a_missing_child(tmp_path, archive):
    source = ("import os, sys\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), 'vendor.zip'))\n"
              'def run(value):\n    import held.deleted\n    return value\n')
    base, data = _observe(tmp_path / 'base', source, files=_package(archive, True))
    patch, _ = _observe(tmp_path / 'patch', source, files=_package(archive, False))
    assert {'held', 'held.kept', 'held.deleted'} <= set(data['own_modules'])
    assert base['result'] == 'passed', base
    assert patch['result'] == 'failed', patch


@pytest.mark.parametrize('alias', [True, False], ids=['drive-case-alias', 'same-case'])
def test_owned_helper_cannot_keep_marked_call_state(tmp_path, alias):
    source = ("import os, sys\nfolder = os.path.join(os.path.dirname(__file__), 'assets')\n" +
              ('folder = os.path.normcase(folder)\n' if alias else '') +
              'sys.path.insert(0, folder)\nimport state\ndef run(value):\n    if value == 0:\n'
              f'        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            state.available = False\n        return 0\n'
              '    return value if state.available else 99\n')
    cases = [{'args': [0], 'returns': 0}, {'args': [1], 'returns': 1}]
    base, _ = _observe(tmp_path / 'base', GOOD, cases=cases)
    patch, _ = _observe(tmp_path / 'patch', source, cases=cases, files={'assets/state.py': 'available = True\n'})
    assert vs.classify('preserve', _reading(patch), _reading(base))['result'] == vs.CHECK_ERROR
    assert patch['case_reruns'][0]['result'] == 'not_reproduced'


@pytest.mark.parametrize('spelling', ['case-alias', 'same-case', 'extended-prefix'])
def test_path_comparisons_share_windows_identity(tmp_path, spelling):
    if sys.platform != 'win32':
        pytest.skip('drive and case aliases use Windows filesystem identity')
    original = str(tmp_path / 'owned.py')
    candidate = original.swapcase() if spelling == 'case-alias' else (
        '\\\\?\\' + original if spelling == 'extended-prefix' else original)
    assert vs._same_copy({candidate: 'a' * 64}, {original: 'a' * 64}, str(tmp_path / 'check.py'))
    assert not vs._same_copy({str(tmp_path / 'other.py'): 'a' * 64}, {original: 'a' * 64},
                             str(tmp_path / 'check.py'))


@pytest.mark.parametrize('state', ['environment', 'cwd', 'sys-path', 'library-global', 'none'])
def test_rerun_starts_a_new_interpreter_before_confirming(tmp_path, state):
    change, result = {
        'environment': ("os.environ['G6_TMFU2_R3_STATE'] = 'changed'", "99 if 'G6_TMFU2_R3_STATE' in os.environ else value"),
        'cwd': ('os.chdir(os.path.dirname(__file__))',
                '99 if os.path.samefile(os.getcwd(), os.path.dirname(__file__)) else value'),
        'sys-path': ("sys.path.append('tmfu2-r3-state')", "99 if 'tmfu2-r3-state' in sys.path else value"),
        'library-global': ('colorsys.tmfu2_r3_state = True', "99 if hasattr(colorsys, 'tmfu2_r3_state') else value"),
        'none': ('pass', '99'),
    }[state]
    source = ('import os, sys, colorsys\ndef run(value):\n    if value == 0:\n'
              f'        try:\n            import {ABSENT}\n'
              f'        except ImportError:\n            {change}\n        return 0\n    return {result}\n')
    cases = [{'args': [0], 'returns': 0}, {'args': [1], 'returns': 1}]
    base, _ = _observe(tmp_path / 'base', GOOD, cases=cases)
    patch, _ = _observe(tmp_path / 'patch', source, cases=cases)
    verdict = vs.classify('preserve', _reading(patch), _reading(base))
    assert verdict['result'] == (vs.COUNTEREXAMPLE if state == 'none' else vs.CHECK_ERROR), verdict
    [rerun] = patch['case_reruns']
    assert rerun['ran'] is True
    assert rerun['result'] == ('reproduced' if state == 'none' else 'not_reproduced')


def test_rerun_handlers_use_inventory_resolvable_exception_names():
    fresh = next(node for node in ast.parse(runner.BOOTSTRAP).body
                 if isinstance(node, ast.FunctionDef) and node.name == 'fresh_observation')
    for handler in (node for node in ast.walk(fresh) if isinstance(node, ast.ExceptHandler)):
        kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        for kind in kinds:
            actual = getattr(builtins, ast.unparse(kind), None)
            assert isinstance(actual, type) and issubclass(actual, Exception), ast.unparse(kind)


@pytest.mark.parametrize('template', ['relation.v1', 'access_rule.v1', 'differential.v1'])
def test_shared_helpers_do_not_turn_caller_words_into_identifiers(template):
    source = vt.REGISTRY[template].text.replace(vt.SLOT, '{}')
    names = {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)}
    names.update(node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute))
    assert not names.intersection({'encode', 'decode', 'normalise', 'Forbidden', 'notes', 'cli'})


def test_fresh_process_preserves_startup_random_state_not_fallback_state(tmp_path):
    # The initial load can choose any case, but the independent load must start with the same PRNG state.
    source = ('import random\nmarked = random.randrange(3)\ndef run(value):\n    if value == marked:\n'
              f'        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            pass\n    return 99\n')
    cases = [{'args': [index], 'returns': 99} for index in range(3)]
    found, _ = _observe(tmp_path / 'tree', source, cases=cases)
    marks = found['import_evidence']['outcomes']
    [marked] = [index for index, names in enumerate(marks) if names]
    assert found['result'] == 'passed', found
    assert [one['case'] for one in found.get('case_reruns', [])] == list(range(marked + 1, 3))
    for rerun in found.get('case_reruns', []):
        assert rerun['result'] == 'reproduced' and rerun['marks'] == [], found


def test_repository_reseeding_is_not_replaced_by_runner_random_state(tmp_path):
    source = ('import random\nrandom.seed(13)\ndef run(value):\n    if value == 0:\n'
              f'        try:\n            import {ABSENT}\n'
              '        except ImportError:\n            random.seed(27)\n        return 0\n'
              '    return random.random()\n')
    cases = [{'args': [0], 'returns': 0}, {'args': [1], 'returns': random.Random(13).random()}]
    found, _ = _observe(tmp_path / 'tree', source, cases=cases)
    assert found['result'] == 'error', found
    assert found['import_evidence']['reading']['result'] == 'failed', found
    [rerun] = found['case_reruns']
    assert rerun['result'] == 'not_reproduced', found
    assert runner._matches_encoding(rerun['outcome']['outcomes'][0]['returned'], random.Random(13).random())
