# SPDX-License-Identifier: Apache-2.0
"""What admits a ``Retry-After`` resend is told again once the wait is over (0.3.0 issuance window: review l4 finding
2, with l4b finding 1 and l3b finding 2; and what review c4 finding 1, c3 finding 1, c2 finding 4 and l1 finding 2 rest
on).

``g6.core.llm`` adds each wait to the open records of ``waits`` before it is taken, and a record that is a gate (the
loop's) lets the resend in or keeps it in there. Nothing marked the end of the wait, so a resend that woke late, after
whatever it had been admitted under was over, still went. Pinned here, at the bound seam: a record that has a ``woke``
method is told once the wait is over and before the request is sent again; what it raises keeps the resend in; a plain
record is written as before; and a resend is still no model call of its own for the count, since its gate is the
record and not the note.

Offline: the opener is a stand-in that opens nothing, and the sleep is the module's own, replaced.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from g6.core import llm, model_calls

ENDPOINT = "http://127.0.0.1:9"
_ANSWER = json.dumps({"choices": [{"message": {"content": "ok"}}], "message": {"content": "ok"}}).encode()


def _endpoint(monkeypatch, statuses: list[int], events: list[str]) -> None:
    """An endpoint that turns a request away with each of ``statuses`` in turn, asking for a wait of two seconds, then
    answers. Each request it is sent, and each wait taken, is added to ``events`` in the order it happens."""
    left = list(statuses)

    class Opener:
        handlers: list = []

        def open(self, request, timeout):
            events.append("sent")
            if left:
                raise urllib.error.HTTPError(request.full_url, left.pop(0), "busy", {"Retry-After": "2"},
                                             io.BytesIO(b"busy"))
            return io.BytesIO(_ANSWER)

    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *handlers: Opener())
    monkeypatch.setattr(llm, "_sleep", lambda seconds: events.append(f"waited {seconds:g}"))


def _callers() -> list:
    return [llm.OpenAICompatibleCaller("m", base_url=ENDPOINT + "/v1", api_key=None),
            llm.OllamaCaller("m", base_url=ENDPOINT)]


class _Gate(list):
    """A record of waits that is also a gate on each resend, as the loop's is: asked before the wait, told after it."""

    def __init__(self, events: list[str], refuse_after: int | None = None) -> None:
        super().__init__()
        self.events, self.refuse_after, self.told = events, refuse_after, []

    def append(self, wait) -> None:
        self.events.append("let in")
        super().append(wait)

    def woke(self, wait) -> None:
        self.told.append(wait)
        if self.refuse_after is not None and len(self.told) >= self.refuse_after:
            raise llm.LLMError("G6_E_TEST_NOT_ADMITTED: nothing covers this resend any more")
        self.events.append("woke")


def _gated(gate: _Gate, caller) -> str:
    token = llm._WAITS.set((*llm._WAITS.get(), gate))
    try:
        return caller.complete("s", "u")
    finally:
        llm._WAITS.reset(token)


@pytest.mark.parametrize("number", [0, 1])
def test_a_record_that_asks_is_told_once_each_wait_is_over_and_before_the_resend(monkeypatch, number) -> None:
    events: list[str] = []
    _endpoint(monkeypatch, [503, 503], events)
    gate = _Gate(events)
    assert _gated(gate, _callers()[number]) == "ok"
    assert events == ["sent", "let in", "waited 2", "woke", "sent", "let in", "waited 2", "woke", "sent"]
    assert gate.told == list(gate) and [wait["status"] for wait in gate] == [503, 503]


def test_what_woke_raises_keeps_the_resend_in(monkeypatch) -> None:
    events: list[str] = []
    _endpoint(monkeypatch, [503, 503], events)
    gate = _Gate(events, refuse_after=2)      # the second wait ran past what its resend was let in under
    with pytest.raises(llm.LLMError, match="G6_E_TEST_NOT_ADMITTED"):
        _gated(gate, _callers()[0])
    assert events == ["sent", "let in", "waited 2", "woke", "sent", "let in", "waited 2"]
    assert events.count("sent") == 2, "the resend left after it was refused"


def test_every_open_record_that_asks_is_told_and_a_plain_one_is_written_as_before(monkeypatch) -> None:
    events: list[str] = []
    _endpoint(monkeypatch, [429], events)
    outer, inner = _Gate(events), _Gate(events)
    with llm.waits() as waited:
        token = llm._WAITS.set((*llm._WAITS.get(), outer, inner))
        try:
            assert _callers()[0].complete("s", "u") == "ok"
        finally:
            llm._WAITS.reset(token)
    wait = {"url": ENDPOINT + "/v1/chat/completions", "status": 429, "retry_after_s": 2.0, "waited_s": 2.0,
            "cap_s": llm.RETRY_AFTER_CAP_S}
    assert waited == [wait] and outer.told == inner.told == [wait]
    assert events == ["sent", "let in", "let in", "waited 2", "woke", "woke", "sent"]


def test_a_call_with_no_wait_tells_no_record(monkeypatch) -> None:
    events: list[str] = []
    _endpoint(monkeypatch, [], events)
    gate = _Gate(events)
    assert _gated(gate, _callers()[0]) == "ok"
    assert events == ["sent"] and gate.told == []


def test_a_resend_is_no_model_call_of_its_own_for_the_count(monkeypatch) -> None:
    """The count is of requests asked for, as budgets read it; the gate on a resend is the record of its wait."""
    events: list[str] = []
    _endpoint(monkeypatch, [503, 503], events)
    before = model_calls.model_calls()
    with model_calls.counting(before_call=lambda: events.append("noted")) as noted:
        assert _callers()[0].complete("s", "u") == "ok"
    assert noted() == 1 == model_calls.model_calls() - before
    assert events == ["noted", "sent", "waited 2", "sent", "waited 2", "sent"]
