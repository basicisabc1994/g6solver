"""Generated observation programs carry their own imports and helper definitions."""
import builtins
import symtable

import pytest

from g6.core import confine
from g6.core.ledger import canonical
from g6.harness import behaviour_runner as runner, executable_scenario as es, verification_templates as vt


def unresolved(source):
    compile(source, '<generated child>', 'exec')
    table = symtable.symtable(source, '<generated child>', 'exec')
    defined = {s.get_name() for s in table.get_symbols()
               if s.is_assigned() or s.is_imported() or s.is_namespace()}
    available = defined | set(vars(builtins)) | {'__name__', '__file__', '__builtins__'}

    def reads(scope):
        names = {s.get_name() for s in scope.get_symbols() if s.is_referenced() and s.is_global()}
        return names | set().union(*(reads(child) for child in scope.get_children()))

    return reads(table) - available


@pytest.mark.parametrize('kind', ['returns', 'raises', 'effect'])
@pytest.mark.parametrize('large', [False, True])
def test_generated_observation_templates_resolve_their_own_names(kind, large):
    outcomes = {
        'returns': {'expect': 'returns', 'value': 5},
        'raises': {'expect': 'raises', 'error': ['exception', 'builtins', 'ValueError']},
        'effect': {'expect': 'effect', 'argument': 0, 'value': ['done']},
    }
    scenario = es.Scenario(canonical({
        'version': 'example-scenario.v2', 'setup': es.SETUP,
        'entrypoint': ['module', 'calc', 'add', 'calc.py', {'import_roots': ['.', 'src']}],
        'input': {'args': ['x' * 60000 if large else 2], 'kwargs': {}},
        'outcomes': {'A': outcomes[kind], 'B': {'expect': 'returns', 'value': 6}},
    }).encode('ascii'))
    templates = {
        'observation bootstrap': confine.CHILD_PRELUDE + runner.BOOTSTRAP,
        'portable example': runner.example_text(repr({'function': 'calc.add', 'cases': []})),
        'canonical scenario program': scenario.program,
        **{f'canonical wrapper {option}': runner.expanded(scenario.source(option)) for option in ('A', 'B')},
    }
    for name, source in templates.items():
        assert unresolved(source) == set(), name


def test_child_name_check_rejects_a_helper_missing_from_its_template():
    assert unresolved('def check(record):\n    return _decode_json(record)\n') == {'_decode_json'}


@pytest.mark.parametrize('template', sorted(vt.REGISTRY))
def test_every_registered_child_template_resolves_its_own_names(template):
    definition = vt.REGISTRY[template]
    # Structural templates with no program do not create a child. Every text-bearing registry entry is scanned,
    # including new entries, with a literal binding slot; no check or child is run by this test.
    if definition.text:
        assert unresolved(vt.compile_text(definition, {})) == set(), template
