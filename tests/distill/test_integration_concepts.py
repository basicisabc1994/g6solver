"""The concept's first send shares S's four-send ceiling and host pacing."""
from types import SimpleNamespace

from g6.core.llm import EmptyRetry, FakeLLM
from g6.distill.synthesis import Synthesizer, propose_concept
from tests.distill.test_synthesis import fixture


def test_failed_concept_cannot_spend_a_second_send_on_retry():
    _, case = fixture()
    fake = FakeLLM(default='invalid JSON')
    synth = Synthesizer(caller=fake)
    assert propose_concept([case], synthesizer=synth)['warning_card']['code'] == 'G6_E_SYNTH_MODEL'
    assert propose_concept([case], synthesizer=synth)['warning_card']['code'] == 'G6_E_SYNTH_CALL_LIMIT'
    assert len(fake.calls) == 1


def test_concept_inner_retry_stops_before_a_second_model_send():
    _, case = fixture()
    fake = FakeLLM(default='')
    result = propose_concept([case], synthesizer=Synthesizer(caller=EmptyRetry(fake)))
    assert result['warning_card']['code'] == 'G6_E_SYNTH_MODEL'
    assert len(fake.calls) == 1


def test_online_concept_without_pacing_never_enters_caller():
    _, case = fixture()
    class Caller:
        def complete(self, *args, **kwargs):
            raise AssertionError('unreserved send')
    result = propose_concept([case], synthesizer=Synthesizer(caller=Caller()))
    assert result['warning_card']['code'] == 'G6_E_SYNTH_PACING'


def test_unbound_concept_reservation_never_sends_and_is_charged():
    _, case = fixture()
    from g6.core.model_calls import note_model_call
    class Caller:
        def complete(self, *args, **kwargs):
            note_model_call()
            raise AssertionError('send went beyond preflight')
    charges = []
    admission = SimpleNamespace(admitted=True, amount=.01, ends=None)
    pacing = SimpleNamespace(reserve=lambda *a: admission, settle=lambda a, used: charges.append(used))
    synth = Synthesizer(caller=Caller(), pacing=pacing, provider='codex', worst_case=.01)
    result = propose_concept([case], synthesizer=synth)
    assert result['warning_card']['code'] == 'G6_E_SYNTH_MODEL'
    assert charges == [.01]
