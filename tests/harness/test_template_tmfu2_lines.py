"""Displays cannot supply separators or codes, or cause reader backtracking."""
import pytest

from g6.core.component_process import ExecutionInterrupted
from g6.harness import verification_templates as vt
from tests.harness.test_template_runtime_binding import _compiled


def test_display_escapes_fragment_separator_and_code():
    namespace, _ = _compiled('relation.v1')
    class Value:
        def __repr__(self):
            return 'a; input 0 raised G6_E_CHECK_SETUP\n'
    text = namespace['_shown'](Value())
    assert ';' not in text and 'G6_E_' not in text and len(text.splitlines()) == 1
    assert r'\u003b' in text and r'\u0047' in text


@pytest.mark.parametrize('kind', [pytest.fail.Exception, SystemExit, GeneratorExit])
def test_non_control_repr_signal_uses_fixed_placeholder(kind):
    namespace, _ = _compiled('relation.v1')
    class Value:
        def __repr__(self):
            raise kind('private words')
    text = namespace['_shown'](Value())
    assert text.startswith('<a value whose repr raised ') and 'private words' not in text


def test_repr_preserves_execution_control_identity():
    namespace, _ = _compiled('relation.v1')
    signal = ExecutionInterrupted('stop')
    class Value:
        def __repr__(self):
            raise signal
    with pytest.raises(ExecutionInterrupted) as caught:
        namespace['_shown'](Value())
    assert caught.value is signal


def test_fragment_like_displays_still_admit_the_producer_summary():
    namespace, _ = _compiled('relation.v1')
    class Value:
        def __repr__(self):
            return ' where  was expected; input 0:  gave ' * 5 + 'z'
    namespace['_load'] = lambda spec: lambda value: Value()
    namespace['BINDINGS'].update(inputs=[0, 1, 2, 3, 4])
    with pytest.raises(pytest.fail.Exception) as caught:
        namespace['test_relation']()
    assert vt.failure_matches('relation.v1', 'Failed: ' + str(caught.value).splitlines()[0])


def test_forged_long_fragment_sequence_is_refused():
    line = '; '.join(['input 0: a gave b where c was expected'] * 48) + ' and other words'
    assert not vt.failure_matches('relation.v1', 'Failed: ' + line)
