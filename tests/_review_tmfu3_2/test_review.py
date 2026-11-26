"""Review 2 of claude/template-methods-fu-3 (b446f375..b8c5c1ba): real-disk probes, no host services.

Every finding is one failing node beside a passing control. Where a node says "base", the same request is also read
by the runner of b446f375 (``git show``, a read; the text is written under this folder's ``disk``), to show the
reading that repair 2 changed. Nothing here edits the implementation.
"""
import functools
import importlib.util
import json
import os
import py_compile
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import canonical
from g6.harness import behaviour_runner as runner, verification_templates as vt
from g6.harness.executable_scenario import SETUP, Scenario

HERE = Path(__file__).resolve().parent
TREE = HERE.parents[1]
BASE = 'b446f375'
ABSENT = 'review_tmfu3_2_missing_external'
UNAVAILABLE = 'G6_E_OBSERVATION_UNAVAILABLE'


@pytest.fixture
def tmp_path(monkeypatch):
    folder = (HERE / 'disk' / uuid.uuid4().hex).resolve()
    folder.mkdir(parents=True)
    monkeypatch.setattr(tempfile, 'tempdir', str(folder))
    for name in ('TEMP', 'TMP', 'TMPDIR', 'G6_HOME'):
        monkeypatch.setenv(name, str(folder))
    for name in ('G6_CLAUDE', 'G6_CODEX'):
        monkeypatch.setenv(name, r'C:\nonexistent\g6-no-host.exe')
    return folder


@functools.lru_cache(maxsize=None)
def base_runner():
    """The runner as it was before repair 2, loaded beside the current one (never in its place)."""
    text = subprocess.run(['git', '-C', str(TREE), 'show', BASE + ':src/g6/harness/behaviour_runner.py'],
                          capture_output=True, check=True, timeout=60).stdout
    folder = HERE / 'disk' / ('base-' + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    path = folder / 'base_behaviour_runner.py'
    path.write_bytes(text)
    spec = importlib.util.spec_from_file_location('review_tmfu3_2_base_runner', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def observe(folder, source, *, files=None, base_files=None, cases=None, confirmed=False, using=None, known=(),
            raw_files=None):
    """One reading of example.v1 (or the confirmed scenario built from it) on a real folder, as the product runs it:
    host ownership through the request channel, the emitted bootstrap in ``-I -S -B``, and ``finish`` on the frame."""
    using = using or runner
    sources = {'subject.py': source, **(files or {})}
    for name, content in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    for name, content in (raw_files or {}).items():   # on disk only: never in the host's file list
        (folder / name).write_bytes(content)
    base = {**sources, **(base_files or {})}
    tree = vt.Tree(folder, SimpleNamespace(files=()), frozenset(sources) | frozenset(known),
                   frozenset(base) | frozenset(known))
    tree._texts.update({(name, vt.BASE): content for name, content in base.items()})
    definition = vt.REGISTRY['example.v1']
    bound = definition.bindings.model_validate({'function': 'subject.run',
                                                'cases': cases or [{'args': [1], 'returns': 1}]})
    data = definition.applies(tree, bound, vt.PATCH).data
    if confirmed:
        scenario = Scenario(canonical({'version': 'example-scenario.v2', 'setup': SETUP,
            'entrypoint': [*data['function'], {'import_roots': ['.', 'src']}],
            'input': {'args': data['cases'][0]['args'], 'kwargs': data['cases'][0]['kwargs']},
            'outcomes': {'A': {key: value for key, value in data['cases'][0].items()
                               if key not in ('args', 'kwargs')}}}).encode('ascii'))
        instance = vt.TemplateInstance(id='confirmed', template='example.v1', requirement_id='R1',
            intent='preserve', provenance='person_confirmed', bindings={'function': 'subject.run'})
        check, _, _ = vt.compiled_check(folder, instance,
            {'scenario': scenario.record.decode('ascii'), 'option': 'A'}, [], tree=tree)
        data = check.source.observation
    data = using.located(folder, data)
    boot, channel = folder.parent / (folder.name + '-boot.py'), folder.parent / (folder.name + '-frame')
    boot.write_text(data.get('_program', using._fixed_source(using.BOOTSTRAP)), encoding='utf-8')
    request = json.dumps(using.request(data))
    channel.write_text(request, encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'utf8', str(boot), str(folder),
                          str(channel), '@channel'], cwd=folder, capture_output=True, timeout=60)
    found = using.finish(ran.returncode, channel.read_bytes(), data)
    return {**found, 'request_bytes': len(request.encode('utf-8')),
            'child': ran.stderr.decode('utf-8', 'replace')[-200:]}


def shown(found):
    return {key: value for key, value in found.items() if key != 'observed'}


def plain(folder, code):
    return subprocess.run([sys.executable, '-I', '-S', '-B', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); ' + code,
                           str(folder)], capture_output=True, timeout=30)


# --- 1 (must-close). A package the patch deleted, still imported through one of its children, reads "unavailable" ----

DELETED = {
    'control-deleted-module': ('def run(value):\n    import gone\n    return value\n', {}, {'gone.py': ''}),
    'control-deleted-child': ('def run(value):\n    import kept.child\n    return value\n',
                              {'kept/__init__.py': ''}, {'kept/child.py': ''}),
    'deleted-package-child': ('def run(value):\n    import gone.sub\n    return value\n',
                              {}, {'gone/__init__.py': '', 'gone/sub.py': ''}),
    'deleted-package-from': ('def run(value):\n    from gone.sub import thing\n    return value\n',
                             {}, {'gone/__init__.py': '', 'gone/sub.py': 'thing = 1\n'}),
    'deleted-package-at-load': ('from gone.sub import thing\ndef run(value):\n    return value\n',
                                {}, {'gone/__init__.py': '', 'gone/sub.py': 'thing = 1\n'}),
    'deleted-subpackage': ('def run(value):\n    import kept.gone.leaf\n    return value\n',
                           {'kept/__init__.py': ''}, {'kept/gone/__init__.py': '', 'kept/gone/leaf.py': ''}),
}


@pytest.mark.parametrize('shape', list(DELETED))
def test_a_deleted_package_still_imported_is_the_codes_failure(tmp_path, shape):
    source, files, deleted = DELETED[shape]
    before = observe(tmp_path / 'base-runner', source, files=files, base_files=deleted, using=base_runner())
    assert before['result'] == 'failed', shown(before)   # b446f375: the change broke the code
    found = observe(tmp_path / 'tree', source, files=files, base_files=deleted)
    assert found['result'] == 'failed', (
        'the patch deleted the package the code still imports; the failed find was the deleted __init__.py at the '
        f'location Python searched, yet the reading is {found["result"]!r}: {found.get("detail")!r}')


@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
def test_a_deleted_package_cannot_withhold_a_confirmed_return(tmp_path, confirmed):
    source, files, deleted = DELETED['deleted-package-child']
    control = observe(tmp_path / 'control', *DELETED['control-deleted-module'][:1],
                      base_files=DELETED['control-deleted-module'][2], confirmed=confirmed)
    assert control['result'] == 'failed', shown(control)
    found = observe(tmp_path / 'tree', source, files=files, base_files=deleted, confirmed=confirmed)
    assert found['result'] == 'failed', shown(found)


# --- 2 (important). A namespace folder the patch emptied: its owner is dropped ------------------------------------------

@pytest.mark.parametrize('folder_survives', [True, False], ids=['control-folder-kept', 'folder-gone'])
def test_a_deleted_namespace_folder_module_is_the_codes_failure(tmp_path, folder_survives):
    source = 'def run(value):\n    from portion import leaf\n    return value\n'
    files = {'portion/other.py': ''} if folder_survives else {}
    before = observe(tmp_path / 'base-runner', source, files=files, base_files={'portion/leaf.py': ''},
                     using=base_runner())
    assert before['result'] == 'failed', shown(before)
    found = observe(tmp_path / 'tree', source, files=files, base_files={'portion/leaf.py': ''})
    assert found['result'] == 'failed', shown(found)


# --- 3 (must-close, Windows). A held file whose name differs only in case owns an external package's name ------------

windows = pytest.mark.skipif(os.name != 'nt', reason='path_key folds case only on Windows')
ALIAS = 'def run(value):\n    import Levenshtein\n    return value\n'


@windows
@pytest.mark.parametrize('namesake', [None, 'kept', 'deleted'], ids=['control-no-namesake', 'kept-file', 'deleted-file'])
def test_a_case_alias_of_a_held_file_does_not_own_an_external_import(tmp_path, namesake):
    files = {'levenshtein.py': 'def ratio(a, b):\n    return 1.0\n'} if namesake == 'kept' else {}
    deleted = {'levenshtein.py': ''} if namesake == 'deleted' else {}
    if namesake == 'kept':
        # Python itself never loads levenshtein.py for "import Levenshtein": the request names another module.
        (tmp_path / 'plain').mkdir()
        (tmp_path / 'plain' / 'levenshtein.py').write_text('', encoding='utf-8')
        ran = plain(tmp_path / 'plain', 'import levenshtein; import Levenshtein')
        assert ran.returncode != 0 and b"No module named 'Levenshtein'" in ran.stderr, ran.stderr
    before = observe(tmp_path / 'base-runner', ALIAS, files=files, base_files=deleted, using=base_runner())
    assert before['result'] == 'error' and UNAVAILABLE in before['detail'], shown(before)
    found = observe(tmp_path / 'tree', ALIAS, files=files, base_files=deleted)
    assert found['result'] == 'error' and UNAVAILABLE in found['detail'], (
        'G6 could not import the external package Levenshtein; a held levenshtein.py is another module, yet the '
        f'reading is the code\'s failure: {shown(found)}')


@windows
@pytest.mark.parametrize('namesake', [False, True], ids=['control-no-namesake', 'kept-file'])
def test_a_case_alias_cannot_confirm_module_not_found(tmp_path, namesake):
    files = {'levenshtein.py': ''} if namesake else {}
    found = observe(tmp_path / 'tree', ALIAS, files=files, cases=[{'args': [1], 'raises': 'ModuleNotFoundError'}])
    assert found['result'] == 'error' and UNAVAILABLE in found['detail'], shown(found)


@windows
@pytest.mark.parametrize('namesake', [False, True], ids=['control-no-namesake', 'kept-file'])
def test_a_case_alias_cannot_drop_the_fallback_qualification(tmp_path, namesake):
    source = ('def run(value):\n    try:\n        import Levenshtein\n    except ImportError:\n        return 0\n'
              '    return 1\n')
    files = {'levenshtein.py': ''} if namesake else {}
    found = observe(tmp_path / 'tree', source, files=files, cases=[{'args': [1], 'returns': 0}])
    assert found['result'] == 'passed', shown(found)
    assert found.get('fallback_observation') == {'missing_imports': ['Levenshtein'],
                                                 'satisfied_import_path': 'not observed'}, shown(found)


# --- 4 (must-close). R1': the find that makes the expected ImportError lies outside the call's own extent -------------

IN_CALL = (f'def run(value):\n    try:\n        import {ABSENT}\n    except ImportError:\n'
           '        raise ImportError("this feature needs the optional dependency") from None\n    return value\n')
AT_LOAD = (f'try:\n    import {ABSENT} as dependency\nexcept ImportError:\n    dependency = None\n'
           'def run(value):\n    if dependency is None:\n'
           '        raise ImportError("this feature needs the optional dependency")\n    return value\n')
EARLIER_CALL = ('available = None\ndef run(value):\n    global available\n    if available is None:\n        try:\n'
                f'            import {ABSENT}\n            available = True\n'
                '        except ImportError:\n            available = False\n'
                '    if value and not available:\n'
                '        raise ImportError("this feature needs the optional dependency")\n    return value\n')
RAISES = [{'args': [1], 'raises': 'ImportError'}]


@pytest.mark.parametrize('confirmed', [False, True], ids=['template', 'confirmed-scenario'])
@pytest.mark.parametrize('source', [IN_CALL, AT_LOAD], ids=['control-find-inside-the-call', 'find-while-loading'])
def test_a_missing_import_while_loading_cannot_confirm_an_expected_import_error(tmp_path, source, confirmed):
    satisfied = observe(tmp_path / 'satisfied', source, files={ABSENT + '.py': ''}, cases=RAISES, confirmed=confirmed)
    assert satisfied['result'] == 'failed', shown(satisfied)   # with the import satisfied the call returns
    found = observe(tmp_path / 'tree', source, cases=RAISES, confirmed=confirmed)
    assert found['result'] == 'error' and UNAVAILABLE in found['detail'], (
        'the only reason the call raised ImportError is the import G6 could not satisfy '
        f'({found.get("fallback_observation")}), yet the confirmed raise reads {found["result"]!r}')


@pytest.mark.parametrize('cases', [[{'args': [1], 'raises': 'ImportError'}],
                                   [{'args': [0], 'returns': 0}, {'args': [1], 'raises': 'ImportError'}]],
                         ids=['control-find-in-the-same-call', 'find-in-an-earlier-call'])
def test_a_missing_import_in_an_earlier_call_cannot_confirm_an_expected_import_error(tmp_path, cases):
    satisfied = observe(tmp_path / 'satisfied', EARLIER_CALL, files={ABSENT + '.py': ''}, cases=cases)
    assert satisfied['result'] == 'failed', shown(satisfied)
    found = observe(tmp_path / 'tree', EARLIER_CALL, cases=cases)
    reruns = [one['result'] for one in found.get('case_reruns', [])]
    assert found['result'] == 'error' and UNAVAILABLE in found['detail'], (
        f'the raising case reads {found["result"]!r}; its own fresh rerun in this record read {reruns}')


# --- 5 (important). A library's own optional import inside the call withholds a real expected ImportError ------------

@pytest.mark.parametrize('library', [None, 'pathlib', 'tempfile'], ids=['control-no-library', 'pathlib', 'tempfile'])
def test_a_librarys_own_optional_import_is_not_the_calls_missing_import(tmp_path, library):
    lazy = f'    import {library}\n' if library else ''
    source = 'def run(value):\n' + lazy + '    raise ImportError("no plug-in named %r" % value)\n'
    before = observe(tmp_path / 'base-runner', source, cases=RAISES, using=base_runner())
    assert before['result'] == 'passed' and 'fallback_observation' not in before, shown(before)
    found = observe(tmp_path / 'tree', source, cases=RAISES)
    assert found['result'] == 'passed', (
        'the code raises the confirmed ImportError itself and requests no import G6 lacks; the standard library\'s '
        f'own optional imports were read as the call\'s: {shown(found)}')


# --- 6 (important). An absent child of a closed tree package: the code's failure, or G6's missing import? -----------

PLUGINS = 'import importlib\ndef run(value):\n    return importlib.import_module("plugins." + value).NAME\n'
PLUGIN_FILES = {'plugins/__init__.py': '', 'plugins/real.py': 'NAME = "real"\n'}


@pytest.mark.parametrize('case,expected', [({'args': ['real'], 'returns': 'real'}, 'passed'),
                                           ({'args': ['nope'], 'returns': 'real'}, 'failed'),
                                           ({'args': ['nope'], 'raises': 'ModuleNotFoundError'}, 'passed')],
                         ids=['control-present-child', 'control-absent-child-is-the-codes-failure',
                              'absent-child-confirmed-raise'])
def test_an_absent_child_of_a_closed_package_has_one_owner(tmp_path, case, expected):
    before = observe(tmp_path / 'base-runner', PLUGINS, files=PLUGIN_FILES, cases=[case], using=base_runner())
    assert before['result'] == expected, shown(before)
    found = observe(tmp_path / 'tree', PLUGINS, files=PLUGIN_FILES, cases=[case])
    assert found['result'] == expected, (
        'the same failed find of plugins.nope is the code\'s failure when a return was confirmed and an import G6 '
        f'could not satisfy when the raise was confirmed: {shown(found)}')


# --- 7 (important). The held-file inventory more than doubles the request; the child refuses it sooner --------------

@pytest.mark.parametrize('count', [50, 2000], ids=['control-50-modules', '2000-modules'])
def test_the_ownership_inventory_still_fits_the_request_channel(tmp_path, count):
    known = [f'src/transformers/models/audio_spectrogram_transformer_{index:04d}/'
             'modeling_audio_spectrogram_transformer.py' for index in range(count)]
    source = 'def run(value):\n    return int(value)\n'
    before = observe(tmp_path / 'base-runner', source, known=known, using=base_runner())
    assert before['result'] == 'passed', shown(before)
    found = observe(tmp_path / 'tree', source, known=known)
    assert found['result'] == 'passed', (
        f'request {found["request_bytes"]} bytes (b446f375: {before["request_bytes"]}): {found.get("detail")!r}; '
        f'child: {found["child"]!r}')


# --- 8 (minor). The find observer consumes a one-shot package path ---------------------------------------------------

@pytest.mark.parametrize('one_shot', [False, True], ids=['control-list', 'iterator'])
def test_the_find_observer_does_not_consume_a_package_path(tmp_path, one_shot):
    path = 'iter(list(__path__))' if one_shot else 'list(__path__)'
    source = 'def run(value):\n    import pkg.child\n    return pkg.child.VALUE\n'
    files = {'pkg/__init__.py': f'__path__ = {path}\n', 'pkg/child.py': 'VALUE = 1\n'}
    found = observe(tmp_path / 'tree', source, files=files)
    ran = plain(tmp_path / 'tree', 'import subject; print(subject.run(1))')
    assert ran.returncode == 0 and ran.stdout.strip() == b'1', ran.stderr
    assert found['result'] == 'passed', shown(found)


# --- 9 (limit; older than this change). G6's own refusal of sourceless bytecode is read as the code's ImportError ----

@pytest.mark.parametrize('sourceless', [False, True], ids=['control-source-module', 'sourceless-module'])
@pytest.mark.parametrize('case', [{'args': [1], 'returns': 1}, {'args': [1], 'raises': 'ImportError'}],
                         ids=['confirmed-return', 'confirmed-import-error'])
def test_g6s_own_sourceless_refusal_is_not_the_codes_outcome(tmp_path, case, sourceless):
    source = 'def run(value):\n    import dependency\n    return dependency.VALUE\n'
    written = tmp_path / 'dependency_source.py'
    written.write_text('VALUE = 1\n', encoding='utf-8')
    compiled = tmp_path / 'dependency.pyc'
    py_compile.compile(str(written), cfile=str(compiled), doraise=True)
    extra = ({'raw_files': {'dependency.pyc': compiled.read_bytes()}} if sourceless
             else {'files': {'dependency.py': 'VALUE = 1\n'}})
    found = observe(tmp_path / 'tree', source, cases=[case], **extra)
    if sourceless:
        ran = plain(tmp_path / 'tree', 'import subject; print(subject.run(1))')
        assert ran.returncode == 0 and ran.stdout.strip() == b'1', ran.stderr   # Python itself runs it
        assert found['result'] == 'error' and UNAVAILABLE in str(found.get('detail')), shown(found)
    else:
        assert found['result'] == ('passed' if 'returns' in case else 'failed'), shown(found)
