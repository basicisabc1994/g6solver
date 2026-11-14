# SPDX-License-Identifier: Apache-2.0
"""The 0.3.0 issuance window's admission findings, at the loop's metered caller. Each was seen failing first.

* Review c4 finding 1 (with c3 finding 1, c2 finding 4, l1 finding 2): a ``Retry-After`` resend is a request of its
  own to the tick. The loop's gate on resends already took a place for each before its wait, so this one passes before
  the window's change as after it; it is here so the check after the wait can never make a resend two places.
* Review l4 finding 2 (with l4b finding 1, l3b finding 2): a resend that wakes late, after the week it reserved in
  ended, after its tick closed or after its request was refused, is not sent. The reviewers' own tests answer 429, which
  the loop no longer resends at all; these answer 503, which it does.
* Review b13 finding 2: what a caller's backend sends from a thread it starts inside the call passes the tick's gate
  before it goes.
* Review refu2 finding 1: an error a sender re-raises after other sends bars its own request and no other (the
  reviewer's own case is ``test_send_identity``'s; a wrapped one is here).
* Review l4b finding 3: a Claude Code limit whose message is long still ends the tick as a wait.

Offline: every endpoint is a stand-in that opens nothing, and every wait is the module's own sleep, replaced.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.core import host_models, llm
from g6.core.consent_callers import CallerWrapper
from g6.core.llm import FakeLLM, LLMError
from g6.core.model_calls import note_model_call
from g6.metaharness.loop import LoopSettings, ShadowLoop, _Counted, _Requests
from g6.metaharness.method import Method
from g6.self_maintenance.pacing import ProviderWindow
from g6.self_maintenance.usage import MODEL_REFUSED, request_digest

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

pytestmark = pytest.mark.usefixtures("consented_provider_fakes")

#: the unused endpoint ``consented_provider_fakes`` grants the tests' synthetic inputs to
UNUSED = "https://unused.invalid"
RESET = datetime(2026, 11, 2, 8, tzinfo=UTC)
START = datetime(2026, 10, 26, 8, tzinfo=UTC)
NOW = START + timedelta(days=2)


def _busy(monkeypatch, times: int, asks: str, on_wait) -> list:
    """An endpoint that answers 503 ``times`` times, asking for a wait of ``asks`` seconds, then answers. ``on_wait`` is
    what the wait does instead of sleeping. What it was sent is returned."""
    sent: list = []

    def open_request(request, timeout):
        sent.append(request)
        if len(sent) <= times:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {"Retry-After": asks}, io.BytesIO(b"busy"))
        return io.BytesIO(b'{"choices":[{"message":{"content":"answer"}}]}')

    class Opener:
        handlers: list = []        # no proxy handler, so the sharing check reads the first hop as the endpoint itself
        open = staticmethod(open_request)

    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *handlers: Opener())
    monkeypatch.setattr(llm, "_sleep", on_wait)
    return sent


def _http() -> llm.OpenAICompatibleCaller:
    return llm.OpenAICompatibleCaller("local", base_url=UNUSED, api_key=None)


# -- a resend is one request: one place, and its own admission once its wait is over ---------------------------------------

def test_a_resend_takes_one_place_and_none_goes_past_what_was_reserved(monkeypatch):
    sent = _busy(monkeypatch, 3, "0", lambda seconds: None)
    requests = _Requests()
    requests.open(3)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(_http(), requests).complete("system", "user")
    assert len(sent) == requests.sent == 3
    sent.clear()
    requests.open(4)
    assert _Counted(_http(), requests).complete("system", "user") == "answer"
    assert len(sent) == requests.sent == 4


def test_a_resend_that_wakes_after_its_week_ended_is_not_sent(monkeypatch):
    now = [RESET - timedelta(seconds=5)]

    def wakes_late(seconds):           # asked for two seconds, back after the week's end: a suspended or loaded machine
        now[0] = RESET + timedelta(seconds=1)

    sent = _busy(monkeypatch, 1, "2", wakes_late)
    requests = _Requests(lambda: now[0])
    requests.open(5, RESET)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(_http(), requests).complete("system", "user")
    assert len(sent) == 1, "a resend left in a week with nothing reserved"
    assert "week ended" in requests.stopped


def test_a_resend_that_wakes_in_time_is_sent_on_the_place_it_took(monkeypatch):
    now = [RESET - timedelta(seconds=5)]

    def wakes_in_time(seconds):
        now[0] += timedelta(seconds=seconds)

    sent = _busy(monkeypatch, 1, "2", wakes_in_time)
    requests = _Requests(lambda: now[0])
    requests.open(5, RESET)
    assert _Counted(_http(), requests).complete("system", "user") == "answer"
    assert len(sent) == requests.sent == 2


def test_a_resend_that_wakes_after_its_tick_closed_is_not_sent(monkeypatch):
    requests = _Requests()

    def next_tick(seconds):            # the tick the resend took its place in ends while it waits, and another opens
        requests.close()
        requests.open(5)

    sent = _busy(monkeypatch, 1, "0", next_tick)
    requests.open(5)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(_http(), requests).complete("system", "user")
    assert len(sent) == 1 and requests.sent == 0, "a resend left under a tick that reserved nothing for it"


def test_a_resend_refused_while_it_waited_is_not_sent(monkeypatch):
    requests = _Requests()

    def refused_meanwhile(seconds):    # the same request is refused elsewhere while this resend waits
        requests.refused.add(request_digest("system", "user"))

    sent = _busy(monkeypatch, 1, "0", refused_meanwhile)
    requests.open(5)
    with pytest.raises(LLMError, match="G6_E_LOOP_CLASSIFIER_STOP"):
        _Counted(_http(), requests).complete("system", "user")
    assert len(sent) == 1


# -- a backend that sends from a thread it starts inside the call ----------------------------------------------------------

class _FromAPool(CallerWrapper):
    """A wrapper by the published contract whose requests leave from a pool it makes for each call."""

    def complete(self, system, user, *, max_tokens=1024):
        def work():
            return [self.inner.complete(system, user, max_tokens=max_tokens) for _ in range(3)][-1]

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=20)


class _FromAThread(CallerWrapper):
    """The same from a bare thread, which hands what it met back to the call."""

    def complete(self, system, user, *, max_tokens=1024):
        got: list = []

        def work():
            try:
                got.extend(self.inner.complete(system, user, max_tokens=max_tokens) for _ in range(3))
            except LLMError as error:
                got.append(error)

        worker = threading.Thread(target=work)
        worker.start()
        worker.join(timeout=20)
        if isinstance(got[-1], LLMError):
            raise got[-1]
        return got[-1]


@pytest.mark.parametrize("kind", [_FromAPool, _FromAThread])
def test_a_backend_thread_started_inside_the_call_cannot_send_above_the_reservation(kind):
    backend = FakeLLM()
    requests = _Requests()
    requests.open(2)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(kind(backend), requests).complete("system", "user")
    assert len(backend.calls) == requests.sent == 2, f"sent {len(backend.calls)} requests against a reservation of 2"


@pytest.mark.parametrize("kind", [_FromAPool, _FromAThread])
def test_what_a_backend_thread_sends_within_the_reservation_is_charged_as_it_goes(kind):
    backend = FakeLLM()
    requests = _Requests()
    requests.open(5)
    assert _Counted(kind(backend), requests).complete("system", "user") == "OK"
    assert len(backend.calls) == requests.sent == 3 and not requests.unseen


# -- an error re-raised after other sends bars its own request --------------------------------------------------------------

def test_a_refusal_wrapped_and_raised_after_a_later_send_bars_its_own_request():
    sent, meter = [], _Requests()

    # the function that sends names its input as one pair; the frame of the wrapper that called it names a system text and
    # a request, which is the later request by the time the first one's refusal comes out
    def transport(pair):
        note_model_call()
        sent.append(pair)
        if pair[1] == "refused request":
            raise LLMError(f"{MODEL_REFUSED}: policy")
        return "answer"

    class Batch(CallerWrapper):
        def complete(self, system, user, *, max_tokens=1024):
            errors = []
            for user in ("refused request", "successful request"):
                try:
                    transport((system, user))
                except LLMError as error:
                    errors.append(error)
            if errors:                 # a new error that says what the first one said, raised from it
                raise LLMError(str(errors[0])) from errors[0]
            return "answer"

    counted = _Counted(Batch(FakeLLM()), meter)
    for _ in range(2):
        with pytest.raises(LLMError):
            counted.complete("system", "outer request")
    assert meter.refused == {request_digest("system", "refused request")}
    assert sent.count(("system", "refused request")) == 1, "the refused request left again"
    assert sent.count(("system", "successful request")) == 2, "the request that answered was barred in its place"


def test_a_sends_boundary_is_kept_while_its_call_is_open_and_let_go_once_it_is_over(monkeypatch):
    """The record of a note holds a frame, and a frame holds the stack it ran under: it is held only as long as a failure
    of the call could still be read back to it."""
    taken, held = [], []
    real = _Requests.take

    def take(self, *args, **kwargs):
        taken.append(real(self, *args, **kwargs))
        return taken[-1]

    monkeypatch.setattr(_Requests, "take", take)

    class Looks(CallerWrapper):
        def complete(self, system, user, *, max_tokens=1024):
            answer = self.inner.complete(system, user, max_tokens=max_tokens)
            held.append(taken[-1].note.frame is not None)
            return answer

    assert _Counted(Looks(FakeLLM()), _Requests()).complete("system", "user") == "OK"
    assert held == [True] and len(taken) == 1 and taken[0].note.frame is None


# -- a long Claude Code limit still ends the tick as a wait ------------------------------------------------------------------

def test_a_long_claude_limit_result_ends_the_tick_as_a_wait_and_spends_nothing(tmp_path, monkeypatch):
    """Review l4b finding 3. A domain's caller that reaches Claude Code through the plain host reader, which the loop
    does not watch: the status fields of its error result are all that says a limit was met, and the 300 characters the
    reader keeps of a long message held none of them."""
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    domain = domains.WordProblems(tmp_path)
    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), audit_per_tick=0,
                            method=Method(cycles=1, propose_budget=120, select_budget=400, race_block=12,
                                          reflect_calls=1, use_recovery=False, use_reflection=False))
    loop = ShadowLoop(domain, tmp_path / "loop", settings, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    held_out = {case.input for case in loop.cut.held_out}
    tutor, met = domain.tutor, []
    printed = json.dumps({"type": "result", "is_error": True, "api_error_status": 429,
                          "api_error": "usage_limit_reached",
                          "result": "You've hit your usage limit. " + "Please wait for the reset. " * 20})
    host = host_models.ClaudeCodeCaller()

    def respond(system, user):
        if user.rsplit("Problem: ", 1)[-1].strip() in held_out:
            met.append(user)
            return host_models.ClaudeCodeCaller._reply(host, 1, printed, "", Path("."), ())   # no program is started
        return tutor(system, user)

    domain.llm._responder = respond
    before = loop.mh.held_out_left()
    reply = loop.tick()
    assert met, "the confirmation never reached the limited provider"
    assert reply["warning_card"]["code"] == "G6_E_LOOP_BUDGET_WAIT"
    assert loop.mh.held_out_left() == before
    assert loop.mh.ledger.rows("decision") == [] and loop.log.cycles() == []
