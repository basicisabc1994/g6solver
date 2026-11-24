# SPDX-License-Identifier: Apache-2.0
"""Lead amendment: trusted carriers, exact setup lines and visible compiler bindings."""
import ast

import pytest

from g6.harness import behaviour_runner as runner, verification_search as search, verification_templates as vt
from g6.workforce.repo_tests import RepoRefused
from tests.harness.test_template_exception_boundaries import _coded_exception_types
from tests.harness.test_template_runtime_binding import TEMPLATES, Tree, _compiled
from tests.harness.test_verification_templates import _bindings_segment, result


def test_owned_carrier_inventory_matches_g6_declarations():
    assert set(runner.CODE_CARRIERS) == set(_coded_exception_types())


@pytest.mark.parametrize('template', ('host', *TEMPLATES))
def test_real_carrier_keeps_code_but_same_named_repository_class_does_not(template):
    namespace = vars(runner) if template == 'host' else _compiled(template)[0]
    real = RepoRefused('G6_E_ISOLATION_UNAVAILABLE', 'setup refused')
    forged = type('RepoRefused', (ValueError,), {
        '__module__': 'g6.workforce.repo_tests', 'code': real.code})(real.code + ': forged')
    for error, expected in ((real, real.code + ': method_lookup'),
                            (forged, 'G6_E_CHECK_SETUP: method_lookup')):
        def lookup():
            raise error
        with pytest.raises(TypeError) as caught:
            namespace['setup_call']('method_lookup', lookup)
        assert str(caught.value) == expected
        assert caught.value.__cause__ is error


@pytest.mark.parametrize('template', TEMPLATES)
def test_anonymous_compilation_writes_an_exact_qualified_setup_line(template):
    namespace, _ = _compiled(template)
    error = namespace['CheckSetupError']('method_lookup')
    line = f'{type(error).__name__}: {error}'
    assert line == f'test_g6_{template.split(".")[0]}.CheckSetupError: G6_E_CHECK_SETUP: method_lookup'
    node = 'test_generated.py::test_runtime'
    assert search.reading(result(failed=[node], messages={node: line}), node)['result'] == 'error'


@pytest.mark.parametrize('reason', runner.CheckSetupError.REASONS)
def test_exact_setup_line_and_structured_record_are_the_only_setup_authorities(reason):
    node = 'test_g6_access_rule.py::test_access_rule'
    line = f'test_g6_access_rule.CheckSetupError: G6_E_CHECK_SETUP: {reason}'
    for text, expected in (
            (line, 'error'), ('E   ' + line, 'error'),
            (line.replace('test_g6_access_rule.', ''), 'failed'),
            (line.replace('test_g6_access_rule.', 'builtins.'), 'failed'),
            (line.replace('test_g6_access_rule.', 'test_g6_relation.'), 'failed'),
            (line + ': repository text', 'failed'),
            (line.replace(reason, 'unknown_reason'), 'failed')):
        recorded = result(failed=[node], messages={node: text})
        assert search.reading(recorded, node)['result'] == expected
    recorded = result(failed=[node], messages={node: line})
    recorded['check_errors'] = {}
    assert search.reading(recorded, node)['result'] == 'failed'
    recorded['check_errors'] = {node: 'G6_E_CHECK_SETUP'}
    recorded['failure_messages'] = {node: 'an opaque failure'}
    assert search.reading(recorded, node)['result'] == 'error'


@pytest.mark.parametrize('template', TEMPLATES)
def test_compiler_keeps_bindings_visible_and_fixed_text_unchanged(template):
    _, data = _compiled(template)
    definition = vt.REGISTRY[template]
    source = vt.compile_text(definition, data)
    literal = _bindings_segment(source)
    assert ast.literal_eval(literal) == data
    assert source.replace(literal, '') == definition.text.replace(vt.SLOT, '')
    assert f'\nTEMPLATE = {template!r}\n' in source
    large = {**data, 'padding': 'x' * 30000}
    enlarged = vt.compile_text(definition, large)
    assert ast.literal_eval(_bindings_segment(enlarged)) == large
    assert len(enlarged.encode('utf-8')) <= search.MAX_SOURCE_BYTES


def test_maximal_example_case_count_and_bindings_fit_together():
    import json

    bindings = {'function': 'runtime_fixture.Machine.act', 'constructor': {'call': 'runtime_fixture.make'},
                'cases': [{'args': [''], 'returns': 0} for _ in range(vt.MAX_INPUTS)]}
    remaining = vt.MAX_BINDINGS_CHARS - len(json.dumps(bindings))
    bindings['cases'][0]['args'][0] = 'x' * remaining
    assert len(json.dumps(bindings)) == vt.MAX_BINDINGS_CHARS
    definition = vt.REGISTRY['example.v1']
    data = definition.applies(Tree(), definition.bindings.model_validate(bindings), vt.BASE).data
    source = vt.compile_text(definition, data)
    assert len(source.encode('utf-8')) <= search.MAX_SOURCE_BYTES
    assert ast.literal_eval(_bindings_segment(source)) == data
