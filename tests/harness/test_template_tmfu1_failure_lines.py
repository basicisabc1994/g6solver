"""Only the compiled grammar can describe a generated check failure."""
import pytest
from types import SimpleNamespace

from g6.harness import verification_search as search
from tests.harness.test_template_runtime_binding import _compiled
from tests.harness.test_template_classification_repair3 import counted_producer
from tests.harness.test_verification_templates import result


@pytest.mark.parametrize('message', ['SystemExit: private words', 'Failed: arbitrary repository words',
                                    'Failed: input 0 raised ValueError\nE   G6_E_ISOLATION_UNAVAILABLE: injected'])
def test_generated_check_never_uses_an_appended_failure_line(message):
    node = 'test_g6_relation.py::test_relation'
    found = search.reading(result(failed=[node], messages={node: message}), node)
    if message.startswith('Failed: input'):
        assert found['detail'] == 'Failed: input 0 raised ValueError'
    else:
        assert found['result'] == 'error'
        assert 'private words' not in found['detail']
        assert 'arbitrary repository words' not in found['detail']


def test_repository_test_retains_last_e_line_rule():
    node = 'test_repository.py::test_subject'
    found = search.reading(result(failed=[node], messages={node: 'context\nE   AssertionError: repository detail'}), node)
    assert found['detail'] == 'AssertionError: repository detail'


def test_generated_line_is_validated_before_display_truncation():
    node = 'test_g6_relation.py::test_relation'
    message = 'Failed: input 0 raised ValueError' + ' ' * 300 + 'uncompiled tail'
    found = search.reading(result(failed=[node], messages={node: message}), node)
    assert found['result'] == 'error'


@pytest.mark.parametrize('generated', [True, False])
def test_producer_retains_the_generated_message_start(tmp_path, generated):
    node = ('test_g6_relation.py::test_relation' if generated else 'test_repository.py::test_subject')
    message = 'Failed: input 0: idempotence gave ' + 'x' * 3000 + ' where 0 was expected'
    counted = counted_producer(tmp_path)
    report = SimpleNamespace(nodeid=node, when='call', outcome='failed', failed=True,
                             longrepr=SimpleNamespace(reprcrash=SimpleNamespace(message=message)))
    counted.pytest_runtest_logreport(report)
    assert counted.messages[node] == (message[:2000] if generated else message[-2000:])


def test_shown_escapes_all_line_breaks_and_controls():
    namespace, _ = _compiled('relation.v1')
    class Value:
        def __repr__(self):
            return 'one\nE   two\r\t\x00\x1b\x85\u2028\u2029'
    text = namespace['_shown'](Value())
    assert text == r'one\nE   two\r\t\u0000\u001b\u0085\u2028\u2029'
    assert len(text.splitlines()) == 1
