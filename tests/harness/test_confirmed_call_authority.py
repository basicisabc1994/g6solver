"""Real child imports decide a confirmed call, without caller roots or outcome setup."""
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_question as bq, executable_scenario as es, verification_templates as vt
from g6.mcp import behaviour
from tests.harness.test_confirmed_setup_authority import portable
from tests.repair_question2.helpers import TASK, ask, candidate, repo


@pytest.mark.parametrize('compiler', ['ordinary', 'registry'])
def test_later_caller_roots_cannot_replace_the_confirmed_name(tmp_path, monkeypatch, compiler):
    root = repo(tmp_path, extra={'alternate/calc.py': 'def add(a,b):\n    return 5\n'})
    (root / 'calc.py').write_text('def add(a,b):\n    return a-b\n', encoding='utf-8')
    assert ask(root)['status'] == 'confirmed'
    [expectation] = behaviour.enforced(str(root), TASK).expectations
    scenario = es.load(expectation.question)
    # A later edit to the confirmed file is still exercised through the held path.
    (root / 'calc.py').write_text('def add(a,b):\n    return a-b\n# task patch\n', encoding='utf-8')
    definition = {'ordinary': vt.TEMPLATES['example.v1'], 'registry': vt.REGISTRY['example.v1']}[compiler]
    monkeypatch.chdir(root)
    honest = {}
    exec(compile(vt.compile_text(definition, scenario.comparison('B')), '<honest roots>', 'exec'), honest)
    honest['test_example']()
    # The caller now puts a same-named implementation first. The real import in
    # the old portable wrapper followed that path; no parent sys.modules is read.
    monkeypatch.chdir(root)
    monkeypatch.syspath_prepend(str(root / 'alternate'))
    monkeypatch.delitem(sys.modules, 'calc', raising=False)
    source = vt.compile_text(definition, scenario.comparison('A'))
    namespace = {}
    exec(compile(source, '<confirmed roots>', 'exec'), namespace)
    try:
        with pytest.raises(pytest.fail.Exception, match='the confirmed outcome did not match'):
            namespace['test_example']()
    finally:
        sys.modules.pop('calc', None)


@pytest.mark.parametrize('label', ['A', 'B'])
@pytest.mark.parametrize('compiler', ['ordinary', 'registry', 'confirmed', 'scenario'])
def test_exception_outcome_setup_cannot_turn_a_fresh_return_into_a_raise(tmp_path, label, compiler):
    root = repo(tmp_path, extra={'domain_errors.py':
        'import calc\ncalc.ready = True\nclass DomainError(Exception):\n    pass\n'})
    (root / 'calc.py').write_text(
        'ready = False\ndef add(a,b):\n    if ready:\n'
        '        from domain_errors import DomainError\n        raise DomainError()\n    return a-b\n',
        encoding='utf-8')
    other = 'B' if label == 'A' else 'A'
    question = bq.caller_candidates(TASK, [candidate(outcomes={
        label: {'raises': 'domain_errors.DomainError'}, other: {'returns': -1}})])[0].question('b' * 64)
    scenario = es.Scenario.resolve(root, question)
    # Independent control: the advertised fresh call returns -1 and loads no
    # exception module. Importing either outcome's module first changes the call.
    fresh = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'utf8', '-c',
        'import sys,json; sys.path.insert(0,sys.argv[1]); import calc; '
        'print(json.dumps([calc.add(2,3), "domain_errors" in sys.modules]))', str(root)],
        capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(fresh.stdout) == [-1, False]
    if compiler == 'scenario':
        source = scenario.source(label)
    else:
        definition = {'ordinary': vt.TEMPLATES['example.v1'], 'registry': vt.REGISTRY['example.v1'],
                      'confirmed': vt._CONFIRMED_EXAMPLE}[compiler]
        source = vt.compile_text(definition, scenario.comparison(label))
    ran = portable(root, source)
    assert ran.returncode == 1, ran.stdout + ran.stderr
    assert '1 failed' in ran.stdout and 'the confirmed outcome did not match' in ran.stdout


def test_ordinary_example_keeps_the_import_root_it_was_resolved_against(tmp_path, monkeypatch):
    root = repo(tmp_path, extra={'alternate/calc.py': 'def add(a,b):\n    return 17\n'})
    from g6.workforce import repo_tests
    tree = vt.Tree.read(root, repo_tests.change(root), None, ('alternate',))
    definition = vt.TEMPLATES['example.v1']
    bound = vt.ExampleBindings.model_validate({'function': 'calc.add', 'cases': [{'args': [2, 3], 'returns': 17}]})
    applied = definition.applies(tree, bound, vt.PATCH)
    assert applied.data['function'] == ['module', 'calc', 'add', 'alternate/calc.py']
    namespace = {}
    exec(compile(vt.compile_text(definition, applied.data), '<ordinary resolved root>', 'exec'), namespace)
    monkeypatch.chdir(root)
    monkeypatch.syspath_prepend(str(root / 'alternate'))
    monkeypatch.delitem(sys.modules, 'calc', raising=False)
    try:
        namespace['test_example']()
    finally:
        sys.modules.pop('calc', None)


@pytest.mark.parametrize('roots', [(), ('alternate', 'secondary'), ('src',)])
def test_ordinary_example_preserves_the_order_of_all_resolved_import_roots(tmp_path, monkeypatch, roots):
    root = tmp_path / 'project'
    root.mkdir()
    function = 'from helper import VALUE\ndef add(a,b):\n    return VALUE\n'
    if not roots:
        files = {'src/calc.py': function, 'helper.py': 'VALUE = 17\n', 'src/helper.py': 'VALUE = 99\n'}
    elif roots == ('src',):
        files = {'calc.py': 'def add(a,b):\n    return 99\n', 'src/calc.py': function,
                 'helper.py': 'VALUE = 99\n', 'src/helper.py': 'VALUE = 17\n'}
    else:
        files = {'alternate/calc.py': function, 'secondary/helper.py': 'VALUE = 17\n',
                 'src/helper.py': 'VALUE = 99\n'}
    for relative, program in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(program, encoding='utf-8')
    tree = vt.Tree(root, SimpleNamespace(base_commit='fixture'), frozenset(files), frozenset(), roots=roots)
    definition = vt.TEMPLATES['example.v1']
    bindings = vt.ExampleBindings.model_validate({'function': 'calc.add', 'cases': [{'args': [2, 3], 'returns': 17}]})
    applied = definition.applies(tree, bindings, vt.PATCH)
    namespace = {}
    exec(compile(vt.compile_text(definition, applied.data), '<ordinary ordered roots>', 'exec'), namespace)
    monkeypatch.chdir(root)
    for folder in reversed([*roots, '.', 'src']):
        monkeypatch.syspath_prepend(str(root / folder))
    for name in ('calc', 'helper'):
        monkeypatch.delitem(sys.modules, name, raising=False)
    try:
        namespace['test_example']()
    finally:
        for name in ('calc', 'helper'):
            sys.modules.pop(name, None)
