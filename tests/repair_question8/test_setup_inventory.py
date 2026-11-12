"""Before the confirmed call the runner makes the setup the person confirmed, and nothing else.

The inventory is read from the bootstrap's own code: every import it makes and every call it makes before the call.
Then a module with import-time effects is planted as each outcome's exception module, and a real isolated child shows
that no effect occurs before the call; an outcome's type is found afterwards among what the call itself loaded.
"""
import ast
import subprocess
import sys
import time

import pytest

from g6.core import confine
from g6.harness import behaviour_question as bq, behaviour_runner as runner
from g6.harness.executable_scenario import Scenario
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import TASK, candidate, repo

PROGRAM = confine.CHILD_PRELUDE + runner.BOOTSTRAP
#: The standard-library modules the bootstrap and G6's confinement prelude import for their own work.
OWN_IMPORTS = {'builtins', 'collections', 'copy', 'hashlib', 'importlib', 'json', 'os', 'pathlib', 're', 'struct',
               'sys'}
#: Every call made before the confirmed call, at the top of the program or in a function reached from there.
OWN_CALLS = {
    # the confinement prelude's conveniences
    '_g6_os.environ.get', 'setattr', 'getattr', '_g6_absent_if_refused', '_mkdir', 'method',
    # the request, the channel and the import path
    'json.loads', 'open', 'os.path.dirname', 'os.path.join',
    # the one import: the confirmed function's module, evicted first so that it is fresh
    'list', 'name.startswith', 'module.startswith', 'load', 'importlib.import_module', 'len', 'RuntimeError',
    'os.path.realpath', 'os.path.normcase', 'spec[3].split',
    # a built-in outcome type, read before any repository code runs
    'observable_exception', 'dumps', 'type', 'ancestry', 'any', 'isinstance', 'bool', 'all', 'part.isidentifier',
    'kind.split',
    # The held roots and path, checked before the one import.
    '_import_roots', 'data.get', 'one.split', 'path.split', 'spec[1].replace', 'path.endswith', 'ValueError',
    'module.replace', 'next', 'os.path.isfile',
}
#: The canonical scenario program derives its request from the scenario it holds, instead of reading it from argv.
SCENARIO_CALLS = {"scenario['outcomes'].values"}


def _functions(tree):
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _call_loop(tree):
    """The index of the top-level statement that makes the confirmed call."""
    [index] = [i for i, node in enumerate(tree.body) if isinstance(node, ast.For) and any(
        isinstance(call, ast.Call) and ast.unparse(call) == 'function(*args, **kwargs)' for call in ast.walk(node))]
    return index


def before_the_call(program):
    """(imports anywhere, dynamic imports anywhere, calls made before the call, the load calls) of a program."""
    tree = ast.parse(program)
    functions = _functions(tree)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports |= {alias.name.split('.')[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or '.').split('.')[0])
    dynamic = sorted(ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Call) and (
        ast.unparse(node.func).split('.')[-1] in ('import_module', '__import__', 'reload', 'exec', 'eval', 'compile',
                                                   'run_module', 'run_path', 'exec_module', 'load_module')))
    seen, calls, todo = set(), set(), []

    def visit(node):
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                name = ast.unparse(inner.func)
                calls.add(name)
                if name in functions and name not in seen:
                    seen.add(name)
                    todo.append(functions[name])

    for statement in tree.body[:_call_loop(tree)]:
        if isinstance(statement, ast.FunctionDef):   # a definition runs nothing; its body counts when it is called
            for extra in (*statement.decorator_list, *statement.args.defaults, *statement.args.kw_defaults):
                if extra is not None:
                    visit(extra)
            continue
        visit(statement)
        while todo:
            for inner in todo.pop().body:
                visit(inner)
    loads = sorted(ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and ast.unparse(node.func) == 'load')
    return imports, dynamic, calls, loads


def _programs(tmp_path):
    root = repo(tmp_path, extra={'errors_a.py': 'class ErrorA(Exception):\n    pass\n'})
    question = bq.caller_candidates(TASK, [candidate(outcomes={
        'A': {'returns': 5}, 'B': {'raises': 'errors_a.ErrorA'}})])[0].question('b' * 64)
    return {'bootstrap': PROGRAM, 'canonical scenario program': Scenario.resolve(root, question).program}


def test_inventory_nothing_but_the_confirmed_module_is_imported_before_the_call(tmp_path):
    for label, program in _programs(tmp_path).items():
        imports, dynamic, calls, loads = before_the_call(program)
        assert imports == OWN_IMPORTS and imports <= sys.stdlib_module_names, (label, imports ^ OWN_IMPORTS)
        assert dynamic == ['importlib.import_module(module)'], (label, dynamic)
        assert loads == ["load(data['function'])"], (label, loads)
        named = OWN_CALLS | (SCENARIO_CALLS if label != 'bootstrap' else set())
        assert calls == named, (label, 'the calls before the confirmed call are not the named ones', calls ^ named)
        tree = ast.parse(program)
        after = [node for statement in tree.body[_call_loop(tree):] for node in ast.walk(statement)
                 if isinstance(node, ast.Call) and ast.unparse(node.func) in ('load', 'importlib.import_module')]
        assert not after, (label, 'nothing is imported after the call either: a type is found among what it loaded')


@pytest.mark.parametrize('planted', [
    'for spec in data["errors"]:\n    load(spec)\n',
    'importlib.import_module(data["errors"][0][1])\n',
    '__import__("errors_a")\n',
    'import errors_a\n',
    'exec("import errors_a")\n',
    'sideways = print\nsideways("unnamed")\n',
])
def test_the_inventory_fails_on_a_planted_import_or_unnamed_call_before_the_call(planted):
    marker = 'function = load(data["function"])\n'
    assert PROGRAM.count(marker) == 1
    imports, dynamic, calls, loads = before_the_call(PROGRAM.replace(marker, planted + marker))
    assert (imports != OWN_IMPORTS or dynamic != ['importlib.import_module(module)']
            or loads != ["load(data['function'])"] or calls - OWN_CALLS), planted


EFFECT = '''import builtins
builtins.G6_PLANTED = True
print("G6_PLANTED_IMPORT", flush=True)
class Planted(Exception):
    pass
'''
CALC = '''import builtins, sys
{top}
def add(a, b):
    seen = ["planted_errors" in sys.modules, hasattr(builtins, "G6_PLANTED")]
    print("G6_CALL_STARTED " + repr(seen), flush=True)
{body}
'''
BODIES = {
    'returns': '    return seen',
    'raises-it': '    import planted_errors\n    raise planted_errors.Planted()',
    'raises-it-loaded-with-the-function': '    raise planted_errors.Planted()',
    'raises-another-type': '    raise ValueError()',
    'raises-another-type-after-loading-it': '    import planted_errors\n    raise ValueError()',
}
#: body -> the reading when the person confirmed "raises planted_errors.Planted" (the other outcome, "returns 5",
#: always reads failed: no body returns 5)
EXPECTED = {'returns': 'failed', 'raises-it': 'passed', 'raises-it-loaded-with-the-function': 'passed',
            'raises-another-type': 'error', 'raises-another-type-after-loading-it': 'failed'}


def observe(tmp_path, files, data):
    """The real runner and a real isolated child on a plain folder; (G6's reading, the child's output)."""
    root = tmp_path / ('copy' + str(len(list(tmp_path.glob('copy*')))))
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding='utf-8')
    output = []

    def execute(command, *, cwd, env, timeout_s, log, **kwargs):
        child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_s)
        output.append(child.stdout + child.stderr)
        log.write_text(child.stdout + child.stderr, encoding='utf-8')
        return child.returncode

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(confine, 'interpreter_paths', lambda python: ())
        patched.setattr(confine, 'plan', lambda spec, python: confine.Plan('review-fixture', '', spec))
        patched.setattr(rt, '_bounded', execute)
        ran = runner.run(root, data, 'test_owned.py', timeout_s=60, deadline=time.monotonic() + 120)
    return ran.get('g6_observation', {'result': 'error', 'detail': ran.get('said')}), output[-1]


@pytest.mark.parametrize('body', BODIES)
@pytest.mark.parametrize('planted_as', ['A', 'B'])
def test_no_outcome_module_is_loaded_or_initialised_before_the_call(tmp_path, planted_as, body):
    """The module with import-time effects is the exception module of outcome A, then of outcome B."""
    top = 'import planted_errors' if body == 'raises-it-loaded-with-the-function' else ''
    files = {'calc.py': CALC.format(top=top, body=BODIES[body]), 'planted_errors.py': EFFECT}
    root = repo(tmp_path, extra=files)
    other = 'B' if planted_as == 'A' else 'A'
    question = bq.caller_candidates(TASK, [candidate(outcomes={
        planted_as: {'raises': 'planted_errors.Planted'}, other: {'returns': 5}})])[0].question('b' * 64)
    scenario = Scenario.resolve(root, question)
    for option, expected in ((planted_as, EXPECTED[body]), (other, 'failed')):
        got, output = observe(tmp_path, files, {**scenario.comparison(option), '_program': scenario.program})
        assert f'G6_CALL_STARTED {[bool(top), bool(top)]!r}' in output, (
            'the outcome module was loaded or initialised before the call', body, option, output)
        before = output[:output.index('G6_CALL_STARTED')]
        assert ('G6_PLANTED_IMPORT' in before) == bool(top), (body, option, output)
        assert got['result'] == expected, (body, option, got)
        if expected == 'error':
            assert 'unresolved expected error' in got['detail'], got
        loaded = body != 'returns' and body != 'raises-another-type'
        assert all((token is not None) == loaded for token in got.get('observed', {'errors': {}})['errors'].values())
        if body == 'returns':
            [one] = got['observed']['outcomes']
            assert one['returned'] == runner._fingerprint('[false, false]'), one


RESOLUTION = {
    # the module under the confirmed name was not loaded from the file G6 resolved
    'another-file': ('import sys, types\n'
                     'def add():\n'
                     '    made = types.ModuleType("errors_a")\n'
                     '    made.__file__ = __file__\n'
                     '    made.ErrorA = type("ErrorA", (Exception,), {})\n'
                     '    sys.modules["errors_a"] = made\n'
                     '    raise made.ErrorA()\n'),
    # the entry under that name is not a module, and looking into it would run its hooks
    'not-a-module': ('import sys\n'
                     'class Trap:\n'
                     '    def __getattribute__(self, name):\n'
                     '        print("G6_HOOK:" + name, flush=True)\n'
                     '        raise ValueError(name)\n'
                     'def add():\n'
                     '    sys.modules["errors_a"] = Trap()\n'
                     '    raise KeyError()\n'),
    # keys that claim to equal any name
    'hostile-keys': ('import sys\n'
                     'class Key(str):\n'
                     '    def __hash__(self):\n'
                     '        return hash("errors_a")\n'
                     '    def __eq__(self, other):\n'
                     '        print("G6_HOOK:key", flush=True)\n'
                     '        return True\n'
                     'class Fake(Exception):\n'
                     '    pass\n'
                     'def add():\n'
                     '    sys.modules[Key("planted")] = sys.modules[__name__]\n'
                     '    globals()[Key("ErrorA")] = Fake\n'
                     '    raise Fake()\n'),
}


@pytest.mark.parametrize('case', RESOLUTION)
def test_a_type_that_the_call_did_not_load_from_its_resolved_file_is_unknown_and_no_hook_runs(tmp_path, case):
    files = {'calc.py': RESOLUTION[case], 'errors_a.py': 'class ErrorA(Exception):\n    pass\n'}
    data = {'function': ['module', 'calc', 'add', 'calc.py'], 'cases': [
        {'args': [], 'expect': 'raises', 'error': ['module', 'errors_a', 'ErrorA', 'errors_a.py']}]}
    got, output = observe(tmp_path, files, data)
    assert got['result'] == 'error' and 'unresolved expected error' in got['detail'], got
    assert 'G6_HOOK' not in output, output
    wrong = {**data, 'cases': [{'args': [], 'expect': 'returns', 'value': 5}]}
    got, output = observe(tmp_path, files, wrong)
    assert got['result'] == 'failed', 'the raise is still observed as itself against a returned outcome'
    assert 'G6_HOOK' not in output, output


def test_a_module_level_getattr_is_never_asked_for_an_outcome_type(tmp_path):
    files = {'calc.py': 'import errors_a\ndef add():\n    raise KeyError()\n',
             'errors_a.py': 'def __getattr__(name):\n    print("G6_HOOK:" + name, flush=True)\n'
                            '    return KeyError\n'}
    data = {'function': ['module', 'calc', 'add', 'calc.py'], 'cases': [
        {'args': [], 'expect': 'raises', 'error': ['module', 'errors_a', 'ErrorA', 'errors_a.py']}]}
    got, output = observe(tmp_path, files, data)
    assert got['result'] == 'error' and 'G6_HOOK' not in output, (got, output)


def test_frame_grammar_null_is_an_unloaded_type_in_v2_only():
    spec = ['module', 'errors_a', 'ErrorA', 'errors_a.py']
    key = runner._encoded(spec)
    data = {'function': ['module', 'calc', 'add', 'calc.py'],
            'cases': [{'args': [], 'expect': 'raises', 'error': spec}]}
    returned = {'returned': runner._fingerprint('5'), 'arguments': []}
    raised = {'raised': [7], 'raised_name': 'builtins.KeyError'}
    for outcome, expected in ((returned, 'failed'), (raised, 'error')):
        frame = {'version': 'observation.v2', 'outcomes': [outcome], 'errors': {key: None}}
        assert runner.read_frame(runner.frame(frame)) == frame
        assert runner.finish(0, runner.frame(frame), data)['result'] == expected
        assert runner.finish(0, runner.frame({**frame, 'errors': {key: 7}}), data)['result'] == (
            'failed' if outcome is returned else 'passed')
        for bad in ('7', 7.0, True, [7], {}):
            assert runner.read_frame(runner.frame({**frame, 'errors': {key: bad}})) is None, bad
    legacy = {'version': 'observation.v1', 'outcomes': [raised], 'errors': {key: None}}
    assert runner.read_frame(runner.frame(legacy)) is None, 'existing v1 frames keep their field language'
