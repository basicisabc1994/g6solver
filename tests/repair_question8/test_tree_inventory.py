"""A confirmed check runs in a folder G6 writes from the manifest, read again with everything a path carries.

Each way a path can carry content outside its bound bytes or name is planted at three places: in the tree under
review before it is bound, in the copy G6 makes of it before the runner reads it, and in the folder the child runs in
after G6 made it and before the child starts. ``calc.add`` returns 5 when it can reach a planted ``VALUE = 5`` and -1
otherwise; the person confirmed ``returns 5``. The observation stays the control's failure or is unknown, never a
bound pass. Only the operating-system boundary is a fixture: the manifests, the copy, the runner, its bootstrap and the
frame reader are the product's, and a real isolated child runs.
"""
import ast
import inspect
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from g6.core import confine
from g6.harness import behaviour_question as bq, behaviour_runner as runner
from g6.harness import executable_scenario as es, verification_search as vs
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import TASK, candidate, repo

WINDOWS = os.name == 'nt'
FIVE, MINUS = b'VALUE = 5\n', b'VALUE = -1\n'
CALC = '''import os
HERE = os.path.dirname(os.path.abspath(__file__))
PLACES = ["carrier.dat:helper.py", "pkg:helper.py", "helper.py", "link.py", "linked/helper.py", "pkg/HELPER.py",
          "__pycache__/helper.py", "unlisted.py"]
def _value(path):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None
    namespace = {}
    exec(text, namespace)
    return namespace.get("VALUE")
def add(a, b):
    found = [_value(os.path.join(HERE, *place.split("/"))) for place in PLACES] + [_value(HERE + ":top.py")]
    return 5 if 5 in found or "VALUE=5" in os.listdir(HERE) else -1
'''
EXTRA = {'carrier.dat': 'unchanged ordinary bytes\n', 'pkg/__init__.py': '', 'pkg/helper.py': MINUS.decode(),
         'helper.py': MINUS.decode(), '.gitignore': 'unlisted.py\n'}


class Refused(Exception):
    """This machine refuses to make the planted file-system feature."""


def _stream(path, name):
    Path(str(path) + ':' + name).write_bytes(FIVE)
    assert Path(str(path) + ':' + name).read_bytes() == FIVE


def _symlink(folder, outside):
    target = outside / 'target.py'
    target.write_bytes(FIVE)
    try:
        os.symlink(target, folder / 'link.py')
    except OSError as exc:
        raise Refused(f'symbolic link: {exc}') from exc


def _junction(folder, outside):
    (outside / 'elsewhere').mkdir(exist_ok=True)
    (outside / 'elsewhere' / 'helper.py').write_bytes(FIVE)
    try:
        if WINDOWS:
            import _winapi
            _winapi.CreateJunction(str(outside / 'elsewhere'), str(folder / 'linked'))
        else:
            os.symlink(outside / 'elsewhere', folder / 'linked', target_is_directory=True)
    except OSError as exc:
        raise Refused(f'junction: {exc}') from exc


def _case_alias(folder, outside):
    if not WINDOWS:   # where every folder tells the two spellings apart, the second is one more listed file
        raise Refused('no folder here reads two spellings of a name as one')
    pkg = folder / 'pkg'
    held = {path.name: path.read_bytes() for path in pkg.iterdir()}
    for name in held:   # Windows marks only an empty folder as case-sensitive: its files are put back as they were
        (pkg / name).unlink()
    made = subprocess.run(['fsutil.exe', 'file', 'setCaseSensitiveInfo', str(pkg), 'enable'],
                          capture_output=True, text=True, timeout=20)
    for name, data in held.items():
        (pkg / name).write_bytes(data)
    (pkg / 'HELPER.py').write_bytes(FIVE)
    if sorted(p.name for p in pkg.iterdir() if p.name.lower() == 'helper.py') != ['HELPER.py', 'helper.py']:
        raise Refused('case-sensitive folder: ' + (made.stdout + made.stderr).strip())


def _cache(folder, outside):
    (folder / '__pycache__').mkdir()
    (folder / '__pycache__' / 'helper.py').write_bytes(FIVE)


#: Each way, as (what to plant in a folder, what a writer does while the child runs, and undoes after it).
WAYS = {
    'stream-on-a-file': lambda folder, outside: _stream(folder / 'carrier.dat', 'helper.py'),
    'stream-on-a-folder': lambda folder, outside: _stream(folder / 'pkg', 'helper.py'),
    'stream-on-the-top-folder': lambda folder, outside: _stream(folder, 'top.py'),
    'hard-link': None,   # planted by _hard_link below: its twin outside the folder is written while the child runs
    'symbolic-link': _symlink,
    'junction': _junction,
    'case-alias': _case_alias,
    'cache-folder': _cache,
    'unlisted-file': lambda folder, outside: (folder / 'unlisted.py').write_bytes(FIVE),
    'unlisted-folder': lambda folder, outside: (folder / 'VALUE=5').mkdir(),   # its name is the content
}
STREAMS = {'stream-on-a-file', 'stream-on-a-folder', 'stream-on-the-top-folder'}
#: What no file's name or bytes show: G6's reading of names and bytes (``_copy_snapshot``) does not hold these.
BEYOND = STREAMS | {'hard-link', 'unlisted-folder'}


def _hard_link(folder, outside):
    """Give ``helper.py`` another name outside the folder, with the same bytes; (that other name, those bytes)."""
    twin, held = outside / 'twin.py', (folder / 'helper.py').read_bytes()
    twin.write_bytes(held)
    (folder / 'helper.py').unlink()
    try:
        os.link(twin, folder / 'helper.py')
    except OSError as exc:
        raise Refused(f'hard link: {exc}') from exc
    return twin, held


def _plant(way, folder, outside):
    """Plant ``way`` in ``folder``; the file outside it that a writer changes while the child runs and its bytes, if
    any."""
    if way in STREAMS and not WINDOWS:
        raise Refused('alternate data streams are an NTFS feature')
    try:
        if way == 'hard-link':
            return _hard_link(folder, outside)
        WAYS[way](folder, outside)
        return None
    except Refused as exc:
        pytest.skip(f'{way} cannot be made on this machine ({exc}); test_what_g6_cannot_write_refuses_the_run and '
                    'test_made_folder_is_only_the_manifest cover it without the feature')


def _bound(tmp_path, source_plant=None):
    root = repo(tmp_path, extra=EXTRA)
    (root / 'calc.py').write_text(CALC, encoding='utf-8')
    outside = tmp_path / 'outside'
    outside.mkdir()
    twin = source_plant(root, outside) if source_plant else None
    change = rt.change(root)
    before = vs.bind_execution(change, rt.snapshot(root), deadline=vs._clock() + 120)
    question = bq.caller_candidates(TASK, [candidate()])[0].question('b' * 64)
    scenario = es.Scenario.resolve(root, question)
    scenario.admit(root, question)
    return change, before, runner.CONFIRMED + scenario.source('A'), outside, twin


def _run(monkeypatch, change, before, source, *, in_copy=None, at_start=None, twin=None):
    real_copy = rt.repository_copy
    children = []
    twins = [twin] if twin else []

    @contextmanager
    def copied(*args, **kwargs):
        with real_copy(*args, **kwargs) as copy:
            if in_copy:
                twins.append(in_copy(copy))
            yield copy

    def execute(command, *, cwd, env, timeout_s, log, **kwargs):
        if at_start:
            twins.append(at_start(cwd))
        for one, _ in filter(None, twins):   # a writer outside the run changes the other name's bytes
            one.write_bytes(FIVE)
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
        for one, held in filter(None, twins):   # and puts them back before G6 reads the folder again
            one.write_bytes(held)
        children.append(child.returncode)
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(rt, 'repository_copy', copied)
    monkeypatch.setattr(rt, '_bounded', execute)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('review-fixture', '', spec))
    got = vs._run_check(change, 'test_owned.py', source, 'test_owned.py', at_base=False, timeout_s=120,
                        artefact=before)
    return got, children


def _never_a_bound_pass(way, got):
    assert got.get('verdict') != 'passed', (way, 'content outside the manifest received a bound pass', got)
    if got.get('verdict') == 'failed':   # unchanged: the control's observation
        assert got['g6_observation']['result'] == 'failed', (way, got)
    else:                                # unknown: nothing is counted
        assert got.get('verdict') in ('not_run', rt.CHANGED), (way, got)


def test_control_without_a_plant_observes_the_fresh_call(tmp_path, monkeypatch):
    change, before, source, _, _ = _bound(tmp_path)
    got, children = _run(monkeypatch, change, before, source)
    assert children == [0] and got['verdict'] == 'failed' and got['g6_observation']['result'] == 'failed', got


@pytest.mark.parametrize('way', WAYS)
def test_planted_in_the_tree_under_review(tmp_path, monkeypatch, way):
    try:
        change, before, source, _, twin = _bound(tmp_path, lambda root, outside: _plant(way, root, outside))
    except (rt.RepoRefused, rt.CopyNotRun, OSError) as exc:   # G6 refuses to bind the tree: unknown, nothing runs
        assert way in ('junction', 'symbolic-link', 'case-alias'), (way, exc)
        return
    got, _ = _run(monkeypatch, change, before, source, twin=twin)
    _never_a_bound_pass(way, got)


@pytest.mark.parametrize('way', WAYS)
def test_planted_in_the_copy_before_the_runner_reads_it(tmp_path, monkeypatch, way):
    change, before, source, outside, _ = _bound(tmp_path)
    got, _ = _run(monkeypatch, change, before, source, in_copy=lambda copy: _plant(way, copy, outside))
    _never_a_bound_pass(way, got)


@pytest.mark.parametrize('way', WAYS)
def test_planted_in_the_folder_the_child_runs_in_before_it_starts(tmp_path, monkeypatch, way):
    change, before, source, outside, _ = _bound(tmp_path)
    got, children = _run(monkeypatch, change, before, source, at_start=lambda cwd: _plant(way, cwd, outside))
    _never_a_bound_pass(way, got)
    assert children == [0]
    if way in BEYOND:   # the child read it, and G6's reading after the run names it and nothing else
        assert got['verdict'] == rt.CHANGED, got
        assert len(got['changed']) == 1 and (':' in got['changed'][0] or got['changed'][0] in (
            'helper.py (another name)', 'VALUE=5/')), got


def _direct(tmp_path, way):
    """The runner alone, handed a folder that already holds the plant: no caller compared it with a manifest."""
    copy, outside = tmp_path / 'copy', tmp_path / 'outside'
    for rel, text in {**EXTRA, 'calc.py': CALC}.items():
        (copy / rel).parent.mkdir(parents=True, exist_ok=True)
        (copy / rel).write_text(text, encoding='utf-8')
    outside.mkdir()
    twin = _plant(way, copy, outside)
    data = {'function': ['module', 'calc', 'add', 'calc.py'],
            'cases': [{'args': [2, 3], 'expect': 'returns', 'value': 5}]}

    def execute(command, *, cwd, env, timeout_s, log, **kwargs):
        if twin:
            twin[0].write_bytes(FIVE)
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
        if twin:
            twin[0].write_bytes(twin[1])
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(confine, 'interpreter_paths', lambda python: ())
        patched.setattr(confine, 'plan', lambda spec, python: confine.Plan('review-fixture', '', spec))
        patched.setattr(rt, '_bounded', execute)
        try:
            return runner.run(copy, data, 'test_owned.py', timeout_s=60, deadline=time.monotonic() + 120)
        except rt.CopyNotRun as exc:
            return {'verdict': 'not_run', 'said': str(exc)}


@pytest.mark.parametrize('way', WAYS)
def test_the_runner_alone_never_runs_what_its_manifest_does_not_hold(tmp_path, way):
    got = _direct(tmp_path, way)
    if way == 'unlisted-file':   # handed to the runner in the folder, it is in the runner's own manifest: bound
        assert got['verdict'] == 'passed', got
        return
    _never_a_bound_pass(way, got)
    if way in BEYOND:   # not carried into the folder G6 made: the fresh call is observed
        assert got['verdict'] == 'failed', got
    if way in ('junction', 'symbolic-link', 'cache-folder', 'case-alias'):
        assert got['verdict'] == 'not_run', got


def test_inventory_the_child_is_started_in_the_made_folder_and_never_in_the_copy():
    """From the runner's code: every use of the copy, and where the child is started."""
    tree = ast.parse(inspect.getsource(runner.run))
    uses = sorted({ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and any(isinstance(arg, ast.Name) and arg.id == 'copy' for arg in node.args)})
    assert uses == ['_made(tree, copy, before, deadline)', 'located(copy, data)',
                    'rt._copy_snapshot(copy, deadline=deadline)'], uses
    loads = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == 'copy'
             and isinstance(node.ctx, ast.Load)]
    attributes = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                  and isinstance(node.value, ast.Name) and node.value.id == 'copy']
    assert len(loads) == len(uses) + len(attributes) and attributes == ['copy.parent'], (attributes, len(loads))
    [started] = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and ast.unparse(node.func) == 'rt._bounded']
    assert ast.unparse(next(k.value for k in started.keywords if k.arg == 'cwd')) == 'tree'
    [command] = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                 and ast.unparse(node.targets[0]) == 'command']
    assert 'str(tree)' in ast.unparse(command.value) and 'copy' not in ast.unparse(command.value)
    [plan] = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and ast.unparse(node.func) == 'confine.Confinement']
    assert ast.unparse(next(k.value for k in plan.keywords if k.arg == 'read_write')) == '(str(own),)'
    [after] = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and ast.unparse(node.targets[0]) == 'after']
    assert ast.unparse(after.value) == '{**rt._copy_snapshot(tree, deadline=deadline), **_beyond(tree)}'


def test_inventory_the_folder_is_written_from_the_manifest_and_nothing_is_found_by_walking():
    made = ast.parse(inspect.getsource(runner._made))
    called = {ast.unparse(node.func) for node in ast.walk(made) if isinstance(node, ast.Call)}
    walking = {'os.walk', 'os.scandir', 'os.listdir', 'shutil.copytree', 'shutil.copy', 'shutil.copy2',
               'shutil.copyfile', 'copy.iterdir', 'copy.rglob', 'copy.glob', 'os.link', 'os.symlink', 'os.replace',
               'os.rename'}
    assert not called & walking and not any(name.split('.')[-1] in ('iterdir', 'glob', 'rglob', 'walk', 'scandir')
                                            for name in called), called
    [loop] = [node for node in ast.walk(made) if isinstance(node, ast.For)]
    assert ast.unparse(loop.iter) == 'enumerate(sorted(files.items()), 1)'
    opened = sorted(ast.unparse(node) for node in ast.walk(made) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == 'open')
    assert opened == ["copy.joinpath(*parts).open('rb')", "target.open('xb')"], opened
