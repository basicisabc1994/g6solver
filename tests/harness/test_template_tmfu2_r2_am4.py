"""Amendment 4 attribution and search-error regressions; the lead runs the lane."""
import sys
from types import SimpleNamespace

import pytest

from g6.harness import verification_search as search
from tests.harness.test_proposed_checks import result
from tests.harness.test_template_tmfu2_r2 import ABSENT, _observe


@pytest.mark.parametrize('frozen', [False, True], ids=['file-library', 'frozen-library'])
def test_library_function_owns_its_optional_import(tmp_path, frozen):
    filename = '"<frozen _g6_optional_function>"' if frozen else 'where'
    library = (f'def optional():\n    try:\n        import {ABSENT}\n'
               '    except ImportError:\n        pass\n')
    source = ('import os, sys\ndef run(value):\n'
              '    where = os.path.join(sys.base_prefix, "Lib", "_g6_optional_function.py")\n'
              '    scope = {"__name__": "_g6_optional_function", "__file__": where}\n'
              f'    exec(compile({library!r}, {filename}, "exec"), scope)\n'
              '    scope["optional"]()\n'
              '    raise RuntimeError("ordinary failure")\n')
    found, _, _ = _observe(tmp_path / 'tree', source)
    assert found['result'] == 'failed', found
    assert 'import_evidence' not in found


def test_library_function_forwards_the_repository_requested_name(tmp_path):
    library = 'import importlib\ndef requested(name):\n    return importlib.import_module(name)\n'
    source = ('import os, sys\ndef run(value):\n'
              '    where = os.path.join(sys.base_prefix, "Lib", "_g6_import_helper.py")\n'
              '    scope = {"__name__": "_g6_import_helper", "__file__": where}\n'
              f'    exec(compile({library!r}, where, "exec"), scope)\n'
              '    try:\n'
              f'        scope["requested"]({ABSENT!r})\n'
              '    except ImportError:\n        pass\n'
              '    return value\n')
    found, _, _ = _observe(tmp_path / 'tree', source)
    assert found['result'] == 'passed', found
    assert found['import_evidence']['outcomes'] == [[ABSENT]], found


@pytest.mark.parametrize('route', ['literal', 'helper', 'pool', 'asyncio'])
def test_repository_request_for_a_platform_module_keeps_its_name(tmp_path, route):
    # The name is absent on the child's platform; no production name exclusion is permitted.
    prefix = 'import sys, importlib, asyncio, concurrent.futures\n'
    prefix += 'missing = "fcntl" if sys.platform == "win32" else "msvcrt"\ndef run(value):\n'
    expression = {
        'literal': '__import__(missing)',
        'helper': 'importlib.import_module(missing)',
        'pool': 'pool.submit(importlib.import_module, missing).result()',
        'asyncio': 'loop.run_until_complete(loop.run_in_executor(None, importlib.import_module, missing))',
    }[route]
    setup, cleanup = '', ''
    indent = '    '
    if route == 'pool':
        setup, indent = '    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:\n', '        '
    elif route == 'asyncio':
        setup, cleanup = '    loop = asyncio.new_event_loop()\n', '    loop.close()\n'
    source = (prefix + setup + indent + 'try:\n' + indent + '    ' + expression + '\n' +
              indent + 'except ImportError:\n' + indent + '    pass\n' + cleanup + '    return value\n')
    found, _, _ = _observe(tmp_path / 'tree', source)
    missing = 'fcntl' if sys.platform == 'win32' else 'msvcrt'
    assert found['result'] == 'passed', found
    assert found['import_evidence']['outcomes'] == [[missing]], found
    assert found['fallback_observation']['missing_imports'] == [missing]


def test_check_read_error_is_recorded_and_search_continues(monkeypatch):
    checks = [search.ProposedCheck.model_validate({
        'id': name, 'requirement_id': 'R1', 'intent': 'preserve',
        'file': f'test_{name}.py', 'source': f'def test_{name}():\n    pass\n'})
        for name in ('broken', 'later')]
    placed = {one.id: f'g6_check_x/{one.file}' for one in checks}
    original_reading = search.reading

    def read(ran, node, **kwargs):
        if node == placed['broken']:
            raise ValueError('cannot read this check')
        return original_reading(ran, node, **kwargs)

    def run_check(change, path, source, node, **kwargs):
        return result([node])

    monkeypatch.setattr(search, '_run_check', run_check)
    monkeypatch.setattr(search, 'reading', read)
    done = search.search(SimpleNamespace(base_commit='a' * 40, sources=()), checks, placed,
                         deadline=search._clock() + 60, artifact='b' * 64)
    broken, later = done.activities
    assert broken['check'] == 'broken'
    assert broken['result'] == 'error'
    assert broken['stopped'] == 'the check broken'
    assert broken['reason'] == 'ValueError: cannot read this check'
    assert later['check'] == 'later'
    assert later['result'] == search.NOT_REFUTED
