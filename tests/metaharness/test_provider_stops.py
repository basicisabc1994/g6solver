# SPDX-License-Identifier: Apache-2.0
"""A refusal is kept before anything else happens and never sent again, whether or not a tick is open."""
from __future__ import annotations

import importlib.util
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.core.llm import FakeLLM, LLMError
from g6.metaharness.loop import LoopSettings, ShadowLoop, _Counted, _Refused, _request_digest, _Requests
from g6.metaharness.method import Method
from g6.self_maintenance.envelope import REFUSED_FILE, refused_requests, stop_reason
from g6.self_maintenance.pacing import ProviderWindow
from g6.self_maintenance.usage import MODEL_REFUSED

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, 8, tzinfo=UTC)
NOW = START + timedelta(days=2)
REVIEW_L4 = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),),
                         method=Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1),
                         audit_per_tick=60)
REVIEW_L4B = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),),
                          method=Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1,
                                        use_recovery=False, use_reflection=False),
                          audit_per_tick=0)


def _enrolled(tmp_path, *, settings=REVIEW_L4, domain=None, proposer=None, now=lambda: NOW):
    domain = domain or domains.WordProblems(tmp_path)
    proposer = proposer or domains.proposes({"prompt": domains.WORKING})
    loop = ShadowLoop(domain, tmp_path / "loop", settings, proposer=proposer, now=now, clock=domains.Clock())
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop, domain


# review-fx4 finding 1, as it was reported -------------------------------------------------------------------------------

def test_review_fx4_1_a_closed_ticks_late_refusal_is_never_sent_again():
    entered, release = threading.Event(), threading.Event()
    failures, persisted = [], []

    def reply(system, user):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
            raise LLMError("G6_E_MODEL_REFUSED: refusal")
        return "the refused request was sent again"

    backend = FakeLLM(responder=reply)
    requests = _Requests(keeping=lambda request, said: persisted.append(request))
    requests.open(10)
    caller = _Counted(backend, requests)

    def pending():
        try:
            caller.complete("system", "same request")
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=pending)
    worker.start()
    try:
        assert entered.wait(5)
        requests.close()
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], (LLMError, _Refused))

    requests.open(10)
    assert requests.refusal is None and requests.limited is None
    try:
        caller.complete("system", "same request")
    except (LLMError, _Refused):
        pass
    assert len(backend.calls) == 1, "the late refusal was forgotten and the identical request was sent twice"
    assert persisted, "the late refusal must survive a restart too"


# A refusal that comes back with no tick open is kept all the same --------------------------------------------------------

def _code(reply) -> str:
    return reply["warning_card"]["code"]


def _between_ticks(tmp_path):
    """The loop's own metered caller, used with no tick open, as a person's study of a profile does. Its model refuses the
    request ``same request`` and answers any other."""
    domain = domains.WordProblems(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain)
    tutor = domain.tutor

    def refuses(system, user):
        if user == "same request":
            raise LLMError(f"{MODEL_REFUSED}: cyber")
        return tutor(system, user)

    domain.llm._responder = refuses
    return loop, domain, loop.workflow.models["local"].caller


def _restarted(tmp_path, domain):
    return ShadowLoop(domain, tmp_path / "loop", REVIEW_L4, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())


def test_a_refusal_between_ticks_is_kept_on_the_disk_and_a_restart_never_sends_it(tmp_path):
    """review-fx4 finding 1 on the loop itself: the refusal came with no tick open."""
    loop, domain, caller = _between_ticks(tmp_path)
    with pytest.raises(LLMError, match=MODEL_REFUSED):
        caller.complete("system", "same request")
    assert _request_digest("system", "same request") in refused_requests(loop.root)
    again = _restarted(tmp_path, domain)
    again.tick()                                    # a tick reads what was refused
    calls = len(domain.llm.calls)
    with pytest.raises(LLMError, match="G6_E_LOOP_CLASSIFIER_STOP"):
        again.workflow.models["local"].caller.complete("system", "same request")
    assert len(domain.llm.calls) == calls


def test_a_refusal_no_file_would_keep_stops_this_process_and_asks_a_person(tmp_path, monkeypatch):
    from g6.metaharness import loop as loop_module

    loop, domain, caller = _between_ticks(tmp_path)
    monkeypatch.setattr(loop_module, "note_refusal", lambda *args: False)
    with pytest.raises(LLMError, match=MODEL_REFUSED):
        caller.complete("system", "same request")
    assert REFUSED_FILE in loop.cannot_record and "would not keep it" in stop_reason(loop.root)
    calls = len(domain.llm.calls)
    assert _code(loop.tick()) == "G6_E_LOOP_STOPPED" and len(domain.llm.calls) == calls
    assert _code(_restarted(tmp_path, domain).tick()) == "G6_E_LOOP_STOPPED" and len(domain.llm.calls) == calls


def test_a_refusal_told_and_then_raised_is_kept_once():
    kept = []
    requests = _Requests(keeping=lambda request, said: kept.append(request))
    requests.at_a_refusal(f"{MODEL_REFUSED}: refusal", "a")
    requests.at_a_refusal(LLMError(f"{MODEL_REFUSED}: refusal"), "a")
    assert kept == ["a"] and requests.refused == {"a"} and requests.refusal is None
