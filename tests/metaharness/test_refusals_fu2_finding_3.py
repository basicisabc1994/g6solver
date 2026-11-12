"""An interruption after settling a call must still release its admitted sends."""
import pytest
from g6.core.llm import FakeLLM
from g6.metaharness.loop import _Counted, _Requests


def test_an_interruption_after_call_settlement_releases_its_send(monkeypatch):
    class Interrupted(BaseException):
        pass

    death = Interrupted('interrupted after call settlement')
    meter = _Requests()
    original = meter.settle

    def settle_then_interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise death

    monkeypatch.setattr(meter, 'settle', settle_then_interrupted)
    backend = FakeLLM(default='answer')
    try:
        with pytest.raises(Interrupted) as raised:
            _Counted(backend, meter).complete('system', 'user')
        assert raised.value is death
        assert len(backend.calls) == meter.sent == 1
        assert not meter.in_flight, 'an ended call retained its send after an interruption during settlement'
    finally:
        # Do not leave a global record behind even when the reproduction fails.
        for held in meter.in_flight.values():
            for record in held:
                meter.release(record)
