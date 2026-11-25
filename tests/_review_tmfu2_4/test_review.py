"""Review 4: real disk, isolated interpreters, no host or network calls."""
import io
import json
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner, verification_search as vs, verification_templates as vt

ABSENT = 'review4_absent_external'
GOOD = 'def run(value):\n    return value\n'


@pytest.fixture
def tmp_path(monkeypatch):
    folder = (Path(__file__).parent / 'disk' / uuid.uuid4().hex).resolve()
    folder.mkdir(parents=True)
    monkeypatch.setattr(tempfile, 'tempdir', str(folder))
    for name in ('TEMP', 'TMP', 'TMPDIR', 'G6_HOME'):
        monkeypatch.setenv(name, str(folder))
    for name in ('G6_CLAUDE', 'G6_CODEX'):
        monkeypatch.setenv(name, r'C:\nonexistent\g6-no-host.exe')
    return folder


def observe(folder, source, *, files=None, cases=None, template='example.v1'):
    sources = {'subject.py': source, **(files or {})}
    for name, content in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    tree = vt.Tree(folder, SimpleNamespace(files=()), frozenset(sources), frozenset(sources))
    tree._texts.update({(name, vt.BASE): content for name, content in sources.items()})
    cases = cases or [{'args': [1], 'returns': 1}]
    bindings = {'function': 'subject.run'}
    bindings.update({'cases': cases} if template == 'example.v1' else {
        'mode': 'outputs', 'calls': [{'args': case['args']} for case in cases]})
    definition = vt.REGISTRY[template]
    data = runner.located(folder, definition.applies(tree, definition.bindings.model_validate(bindings), vt.PATCH).data)
    boot = folder.parent / (folder.name + '-boot.py')
    channel = folder.parent / (folder.name + '-frame')
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_text(json.dumps(runner.request(data)), encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'utf8', str(boot), str(folder), str(channel), '@channel'],
                         cwd=folder, capture_output=True, timeout=20)
    assert ran.returncode == 0, ran.stderr.decode('utf-8', 'replace')
    return runner.finish(ran.returncode, channel.read_bytes(), data), data


def wire(found):
    return {'g6_observation': found, 'verdict': {
        'passed': 'passed', 'failed': 'failed', 'error': 'not_run'}[found['result']],
        'said': found['detail'], 'source_before': '0' * 64, 'seconds': 0.1}


def read(found):
    return vs.reading(wire(found), 'test_g6_example.py::test_example')


def zipped(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return stream.getvalue()


@pytest.mark.parametrize('archive', [False, True], ids=['control-no-archive', 'backup-archive'])
def test_backup_archive_cannot_confirm_an_unavailable_exception(tmp_path, archive):
    source = f'def run(value):\n    import {ABSENT}\n    return value\n'
    files = {'data/backup.zip': zipped({ABSENT + '.py': ''})} if archive else {}
    cases = [{'args': [1], 'raises': 'ModuleNotFoundError'}]
    found, _ = observe(tmp_path / 'tree', source, files=files, cases=cases)
    satisfied, _ = observe(tmp_path / 'satisfied', source, files={**files, ABSENT + '.py': ''}, cases=cases)
    assert satisfied['result'] == 'failed', satisfied
    assert found['result'] == 'error', found


@pytest.mark.parametrize('marked_repeat', [False, True], ids=['control-clean-repeat', 'dependent-repeat'])
def test_dependent_repeat_cannot_erase_a_real_difference(tmp_path, monkeypatch, marked_repeat):
    cases = [{'args': [0]}, {'args': [1]}]
    source = ('import random\noptional = random.getrandbits(1)\naffected = False\n'
              'def run(value):\n    global affected\n    if value == 0:\n        if optional:\n'
              f'            try:\n                import {ABSENT}\n'
              '            except ImportError:\n                affected = True\n        return 0\n'
              '    return int(affected)\n')

    def select(label, marked):
        for index in range(64):
            found, _ = observe(tmp_path / f'{label}{index}', source, cases=cases, template='differential.v1')
            if bool(found.get('import_evidence')) == marked:
                return found
        pytest.fail('could not select import evidence')

    first = select('first', False)
    again = select('again', marked_repeat)
    patch, data = observe(tmp_path / 'patch', GOOD, cases=cases, template='differential.v1')
    satisfied, _ = observe(tmp_path / 'satisfied', source, files={ABSENT + '.py': ''},
                           cases=cases, template='differential.v1')
    assert [one['output'] for one in satisfied['observations']] == [{'returned': 0}, {'returned': 0}]
    assert vs.classify('preserve', read(patch), read(first))['result'] == vs.BEHAVIOUR_CHANGED
    if marked_repeat:
        assert again['case_reruns'][0]['result'] == 'not_reproduced', again
        assert again['import_evidence']['dependent'] == [1], again
    instance = vt.TemplateInstance(id='one', template='differential.v1', requirement_id='R1', intent='preserve',
        bindings={'function': 'subject.run', 'mode': 'outputs', 'calls': cases})
    check, placed, origin = vt.compiled_check(tmp_path, instance, data, [])
    runs = iter([patch, first, again])
    monkeypatch.setattr(vs, '_run_check', lambda *args, **kwargs: wire(next(runs)))
    done = vs.search(SimpleNamespace(base_commit='a' * 40), [check], {'one': placed},
        deadline=vs._clock() + 60, artifact='b' * 64, origins={'one': origin})
    [activity] = done.activities
    assert len(activity['runs']) == 3, activity
    assert activity['result'] == (vs.CHECK_ERROR if marked_repeat else vs.COUNTEREXAMPLE), activity


@pytest.mark.parametrize('nested', [False, True], ids=['control-operation', 'module-initializer'])
def test_explicit_import_error_is_not_missing_dependency(tmp_path, nested):
    source = ('def run(value):\n    import helper\n    return value\n' if nested else
              'def run(value):\n    raise ImportError("unsupported configuration")\n')
    base, _ = observe(tmp_path / 'base', GOOD)
    found, _ = observe(tmp_path / 'tree', source,
                       files={'helper.py': 'raise ImportError("unsupported configuration")\n'})
    verdict = vs.classify('preserve', read(found), read(base))
    assert verdict['result'] == vs.COUNTEREXAMPLE, (verdict, found)


@pytest.mark.parametrize('route', ['import', 'reload', 'lazy-from'], ids=['control-import', 'reload', 'lazy-from'])
def test_distinct_outer_requests_do_not_become_comparable(tmp_path, route):
    if route == 'import':
        helper = f'import {ABSENT}\n'
        prefix = ''
        request = lambda name: f'import {name}'
    elif route == 'reload':
        helper = f'if globals().get("loaded"):\n    import {ABSENT}\nloaded = True\n'
        prefix = 'import importlib, helper_a, helper_b\n'
        request = lambda name: f'importlib.reload({name})'
    else:
        helper = f'def __getattr__(name):\n    import {ABSENT}\n    return 1\n'
        prefix = 'import helper_a, helper_b\n'
        request = lambda name: f'from {name} import value'
    files = {'helper_a.py': helper, 'helper_b.py': helper}

    def source(name, fallback):
        return (prefix + f'def run(value):\n    try:\n        {request(name)}\n'
                f'    except ImportError:\n        return {fallback}\n    return value\n')

    base, _ = observe(tmp_path / 'base', source('helper_a', 1), files=files)
    patch, _ = observe(tmp_path / 'patch', source('helper_b', 99), files=files)
    satisfied, _ = observe(tmp_path / 'satisfied', source('helper_b', 99), files={**files, ABSENT + '.py': ''})
    assert satisfied['result'] == 'passed', satisfied
    verdict = vs.classify('preserve', read(patch), read(base))
    assert verdict['result'] == vs.CHECK_ERROR, (verdict, base.get('import_evidence'), patch.get('import_evidence'))
