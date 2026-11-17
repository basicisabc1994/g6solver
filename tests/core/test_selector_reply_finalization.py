"""Construction resource limits remain inside the final reply envelope."""
import pytest

from g6.core import selector


@pytest.mark.parametrize("failure", [RecursionError, MemoryError])
def test_reply_construction_resource_failure_is_an_input_refusal(monkeypatch, failure):
    def exhausted(*args):
        raise failure("synthetic decoder resource limit")

    monkeypatch.setattr(selector, "_items", exhausted)
    reply = selector.select("review patch", "verify", rerank="lexical")
    assert not reply["ok"] and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
