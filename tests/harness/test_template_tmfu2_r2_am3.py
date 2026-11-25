"""Amendment 3 regressions; execution is reserved for the lead's test lane."""
import json
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_search as search
from tests.harness.test_template_tmfu2_r2 import ABSENT, GOOD, _observe
from tests.harness.test_proposed_checks import result


@pytest.mark.parametrize('route', ['literal', 'computed', 'pool', 'asyncio'])
def test_import_routes_keep_only_the_requested_missing_name(tmp_path, route):
    expression = {
        'literal': f'__import__("{ABSENT}")',
        'computed': f'importlib.import_module("".join([{ABSENT[:8]!r}, {ABSENT[8:]!r}]))',
        'pool': f'pool.submit(importlib.import_module, "{ABSENT}").result()',
        'asyncio': f'loop.run_until_complete(loop.run_in_executor(None, importlib.import_module, "{ABSENT}"))',
    }[route]
    prefix = '' if route == 'literal' else 'import importlib, asyncio, concurrent.futures\n'
    prefix += 'def run(value):\n'
    if route == 'pool':
        prefix += '    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:\n'
        indent, suffix = '        ', ''
    elif route == 'asyncio':
        prefix += '    loop = asyncio.new_event_loop()\n'
        indent, suffix = '    ', '    loop.close()\n'
    else:
        indent, suffix = '    ', ''
    source = (prefix + indent + 'try:\n' + indent + '    ' + expression + '\n' +
              indent + 'except ImportError:\n' + indent + '    pass\n' + suffix + '    return value\n')
    found, _, _ = _observe(tmp_path / 'tree', source,
                           cases=[{'args': [i], 'returns': i} for i in range(3)])
    assert found['result'] == 'passed', found
    assert found['import_evidence']['outcomes'] == [[ABSENT], [ABSENT], [ABSENT]], found
    assert found['fallback_observation']['missing_imports'] == [ABSENT]


def test_bootstrap_optional_import_is_not_case_evidence(tmp_path, monkeypatch):
    helper = (f'def bootstrap_optional_import():\n    try:\n        import {ABSENT}\n'
              '    except ImportError:\n        pass\n\n')
    boot = runner.BOOTSTRAP.replace('def observe_calls():', helper + 'def observe_calls():', 1)
    boot = boot.replace('        function = load(data["function"])',
                        '        bootstrap_optional_import()\n        function = load(data["function"])', 1)
    monkeypatch.setattr(runner, 'BOOTSTRAP', boot)
    found, _, _ = _observe(tmp_path / 'tree', GOOD.replace('return value', 'return 99'))
    assert found['result'] == 'failed', found
    assert 'import_evidence' not in found


@pytest.mark.parametrize('frozen', [False, True], ids=['file-library', 'frozen-library'])
def test_library_module_optional_import_does_not_hide_a_defect(tmp_path, frozen):
    # Model a library module's two real origin spellings inside the isolated interpreter.
    filename = '"<frozen _g6_optional_fixture>"' if frozen else 'where'
    library = f'try:\n    import {ABSENT}\nexcept ImportError:\n    pass\n'
    source = ('import os, sys\ndef run(value):\n'
              '    where = os.path.join(sys.base_prefix, "Lib", "_g6_optional_fixture.py")\n'
              '    scope = {"__name__": "_g6_optional_fixture", "__file__": where}\n'
              f'    exec(compile({library!r}, {filename}, "exec"), scope)\n'
              '    raise RuntimeError("ordinary failure")\n')
    found, _, _ = _observe(tmp_path / 'tree', source)
    assert found['result'] == 'failed', found
    assert 'import_evidence' not in found


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
def test_uncaught_import_keeps_its_specific_unavailable_detail(tmp_path, template):
    source = f'def run(value):\n    import {ABSENT}\n    return value\n'
    found, _, _ = _observe(tmp_path / 'tree', source, template=template)
    assert found['result'] == 'error'
    assert found['detail'] == ('G6_E_OBSERVATION_UNAVAILABLE: import or lazy lookup did not complete: '
                               'builtins.ModuleNotFoundError')
    assert found['import_evidence']['unavailable'] == [0]


@pytest.mark.parametrize('code', [3, None])
def test_stopped_child_without_rerun_does_not_acquire_a_setup_reason(code):
    found = runner.finish(code, b'', {})
    assert found == {'result': 'error',
                     'detail': 'G6_E_OBSERVATION_UNAVAILABLE: no single completed observation'}


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
def test_preparation_stops_before_any_completed_case(tmp_path, template):
    found, _, _ = _observe(tmp_path / 'tree', 'raise ValueError("setup stopped")\n' + GOOD,
                           template=template)
    assert found['result'] == 'error'
    assert found['reason_code'] == ('G6_E_CHECK_SETUP' if template == 'example.v1'
                                    else 'G6_E_OBSERVATION_UNAVAILABLE')
    assert 'observed' not in found and 'observations' not in found


def test_legacy_probe_search_retains_both_missing_side_markers(monkeypatch):
    node = 'g6_check_x/test_probe.py'
    check = search.ProposedCheck.model_validate({
        'id': 'probe', 'requirement_id': 'R1', 'intent': 'change',
        'file': 'test_probe.py', 'source': 'def test_probe():\n    pass\n'})

    def run_check(change, placed, source, wanted, *, at_base, timeout_s):
        pair = {'input': [2] if at_base else [2, 3], 'output': 1}
        return result([node], output='G6-OBSERVATION ' + json.dumps(pair) + '\n')

    monkeypatch.setattr(search, '_run_check', run_check)
    # Printed probes use the legacy load guard, which reads the change's source inventory.
    done = search.search(SimpleNamespace(base_commit='a' * 40, sources=()), [check], {'probe': node},
                         deadline=search._clock() + 60, artifact='b' * 64)
    [activity] = done.activities
    assert activity['differences'] == [
        {'input': '[2]', 'base': '1', 'patch': '(not observed)'},
        {'input': '[2, 3]', 'base': '(not observed)', 'patch': '1'}]


def test_legacy_input_newly_seen_on_repeat_is_unstable():
    first = {'result': 'passed', 'detail': 'observed',
             'observations': [{'input': [2], 'output': 1}]}
    patch = {'result': 'passed', 'detail': 'observed',
             'observations': [{'input': [2, 3], 'output': 1}]}
    again = {**first, 'observations': [*first['observations'], {'input': [2, 3], 'output': 1}]}
    stable = search._stable(search.classify('change', patch, first), first, again)
    assert stable['differences'] == [{'input': '[2]', 'base': '1', 'patch': '(not observed)'}]
    assert stable['unstable'] == [{'input': '[2, 3]', 'base': '(not observed)', 'base_again': '1'}]


def test_a_base_without_probe_reports_cannot_supply_stable_absence():
    first = {'result': 'passed', 'detail': 'passed', 'observations': None}
    patch = {'result': 'passed', 'detail': 'observed', 'observations': [{'input': [2], 'output': 1}]}
    stable = search._stable(search.classify('change', patch, first), first, first)
    assert stable['result'] == search.CHECK_ERROR
