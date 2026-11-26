"""Library field recording preserves host execution control."""
from types import SimpleNamespace

import pytest

from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
from g6.harness import library_review_gate as gate


@pytest.mark.parametrize("error", [ExecutionInterrupted("stop", stopped=True), ContainmentFailure("cleanup")])
def test_field_recording_lets_execution_control_through(monkeypatch, error):
    prepare(monkeypatch, error)
    with pytest.raises(type(error)) as caught:
        gate.check({"test_app.py": "old"}, {"test_app.py": "new"})
    assert caught.value is error


def test_ordinary_field_io_failure_retains_the_concern(monkeypatch):
    prepare(monkeypatch, OSError("disk unavailable"))
    [found] = gate.check({"test_app.py": "old"}, {"test_app.py": "new"})
    assert found["verdict"] == "concern"
    assert found["outcome"] == "defect_detected"
    assert found["field_recorded"] is False


def prepare(monkeypatch, error):
    from g6.distill import acceptance_gate

    entry = SimpleNamespace(entry_id="entry", candidate_id="candidate", family="test_weakening",
                            status="admitted", authority="A2",
                            evidence=SimpleNamespace(precision=SimpleNamespace(lo95=0.9), n_pos=50, n_neg=50))
    candidate = SimpleNamespace(contract=SimpleNamespace(languages=["python"], frameworks=["pytest"]))
    monkeypatch.setattr(gate.Library, "list", lambda self: [entry])
    monkeypatch.setattr(gate.Stores, "for_model", lambda *a: SimpleNamespace(get=lambda key: candidate))
    monkeypatch.setattr(acceptance_gate, "_run", lambda *a: "concern")

    def record(*args):
        raise error

    monkeypatch.setattr(gate, "record_fire", record)
