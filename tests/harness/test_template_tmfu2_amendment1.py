"""Keep the eight earlier diagnostic guarantees with explicit generated origins."""
from types import SimpleNamespace

import pytest

from g6.harness import verification_search as search
from tests.harness.test_template_classification_repair3 import counted_producer
from tests.harness.test_verification_templates import result


@pytest.mark.parametrize('message', [
    'SystemExit: private words',
    'Failed: arbitrary repository words',
    'Failed: input 0 raised ValueError\nE   G6_E_ISOLATION_UNAVAILABLE: injected',
])
def test_generated_check_never_uses_an_appended_failure_line(message):
    node = 'test_g6_relation.py::test_relation'
    found = search.reading(result(failed=[node], messages={node: message}), node, template='relation.v1')
    if message.startswith('Failed: input'):
        assert found['detail'] == 'Failed: input 0 raised ValueError'
    else:
        assert found['result'] == 'error'
        assert 'private words' not in found['detail']
        assert 'arbitrary repository words' not in found['detail']


def test_generated_line_is_validated_before_display_truncation():
    node = 'test_g6_relation.py::test_relation'
    message = 'Failed: input 0 raised ValueError' + ' ' * 300 + 'uncompiled tail'
    found = search.reading(result(failed=[node], messages={node: message}), node, template='relation.v1')
    assert found['result'] == 'error'


def test_producer_retains_the_generated_message_start(tmp_path):
    node = 'test_g6_relation.py::test_relation'
    message = 'Failed: input 0: idempotence gave ' + 'x' * 3000 + ' where 0 was expected'
    counted = counted_producer(tmp_path)
    counted.generated_checks = {'test_g6_relation.py': 'relation.v1'}
    report = SimpleNamespace(nodeid=node, when='call', outcome='failed', failed=True,
                             longrepr=SimpleNamespace(reprcrash=SimpleNamespace(message=message)))
    counted.pytest_runtest_logreport(report)
    assert counted.messages[node] == message[:2000]


@pytest.mark.parametrize('name', ['xG6_E_CHECK_SETUP', 'Bad-Name', 'Bad\x1bName'])
def test_forged_exception_names_never_match_the_compiled_inventory(name):
    node = 'test_g6_relation.py::test_relation'
    message = 'Failed: input 0 raised ' + name
    found = search.reading(result(failed=[node], messages={node: message}), node, template='relation.v1')
    assert found['result'] == 'error'
