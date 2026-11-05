# SPDX-License-Identifier: Apache-2.0
"""A provider's own statement, a refusal, a usage limit or a capacity error, is read from the provider's own output, kept
before anything else happens, never sent again, and never scored as a failed case. Codex and Claude Code are stood in for at
the one place each is run, so no program starts and no model is called."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.core import host_models
from g6.core.host_models import ClaudeCodeCaller, CodexCaller
from g6.core.llm import RATE_LIMITED, FakeLLM, LLMError
from g6.metaharness import Model
from g6.metaharness.loop import LoopSettings, ShadowLoop, _AtLimit, _Counted, _Refused, _request_digest, _Requests
from g6.metaharness.method import Method
from g6.self_maintenance.envelope import REFUSED_FILE, refused_requests, stop_reason
from g6.self_maintenance import usage                      # its new names are read in each test, so each fails on its own
from g6.self_maintenance.pacing import ProviderWindow
from g6.self_maintenance.usage import MODEL_REFUSED, REFRESH, WatchedClaude, limit_ends, read_stops, refused_by

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


# The open findings. review-fx4 finding 1 as it was reported. review-l4 finding 3 and review-l4b finding 3 run below through the
# caller the loop really sends through: as reported, each calls a plain host caller's reply parser from inside another caller,
# which no caller the loop watches can see (test_loop_review.py pins that case as a failed case) ----------------------------

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


# The design: each host's own output, read whole by the caller the loop watches ------------------------------------------

CODEX_LOOP = dataclasses.replace(REVIEW_L4B, windows=(ProviderWindow("codex", 1_000_000, START),))
CODEX_L4 = dataclasses.replace(REVIEW_L4, windows=CODEX_LOOP.windows)
STARTED = ({"type": "thread.started", "thread_id": "0"}, {"type": "turn.started"})
COMPLETED = {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}
#: What codex-cli 0.165.0's own recorded sessions on this machine held for two of these: ``codex_error_info`` with Codex's
#: own message. No recorded file held a usage limit, so its message is the program's own text for one.
CAPACITY = "Selected model is at capacity. Please try a different model."
CYBER = "This content was flagged for possible cybersecurity risk. If this seems wrong, try rephrasing your request."
LIMIT = "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), or try again later."


def _jsonl(*events) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


def _failed(message: str, info=None) -> tuple[dict, ...]:
    """A turn Codex reports as failed: its ``error`` event, then ``turn.failed``, each as Codex writes it."""
    extra = {} if info is None else {"codex_error_info": info}
    return {"type": "error", "message": message, **extra}, {"type": "turn.failed", "error": {"message": message, **extra}}


def _problem(user: str) -> str:
    return user.rsplit("Problem: ", 1)[-1].strip()


def _with_model(domain, caller) -> None:
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, models={"local": Model(caller)})


class Codex:
    """Codex's program, stood in for at the one place it is run. It answers as the domain's tutor does, writing its last
    message where it was told and printing its events, unless ``stop(problem)`` gives the events of a stop."""

    def __init__(self, monkeypatch, tutor, stop=lambda problem: None) -> None:
        self.tutor, self.stop, self.sent, self.argvs = tutor, stop, [], []
        monkeypatch.setattr(CodexCaller, "locate", lambda caller: "codex")
        monkeypatch.setattr(host_models, "_run", self)

    def __call__(self, argv, stdin, cwd, files, *rest):
        self.argvs.append(list(argv))
        system = stdin.split("<<<G6_INSTRUCTIONS\n", 1)[1].split("\nG6_INSTRUCTIONS>>>", 1)[0]
        user = stdin.split("<<<G6_REQUEST\n", 1)[1].split("\nG6_REQUEST>>>", 1)[0]
        self.sent.append(_request_digest(system, user))
        stop = self.stop(_problem(user))
        if stop is not None:            # its error stream says a limit whatever happened, and is never read
            return 1, _jsonl(*STARTED, *stop), "ERROR: You've hit your usage limit."
        answer = self.tutor(system, user)
        Path(argv[argv.index("--output-last-message") + 1]).write_text(answer, encoding="utf-8")
        said = {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": answer}}
        return 0, _jsonl(*STARTED, said, COMPLETED), ""


class Claude:
    """Claude Code's program, stood in for in the same place: its JSON result, or ``stop(problem)``'s result."""

    def __init__(self, monkeypatch, tutor, stop=lambda problem: None) -> None:
        self.tutor, self.stop, self.sent = tutor, stop, []
        monkeypatch.setattr(ClaudeCodeCaller, "locate", lambda caller: "claude")
        monkeypatch.setattr(host_models, "_run", self)

    def __call__(self, argv, stdin, cwd, files, *rest):
        system = Path(argv[argv.index("--system-prompt-file") + 1]).read_text(encoding="utf-8")
        self.sent.append(_request_digest(system, stdin))
        stop = self.stop(_problem(stdin))
        if stop is not None:
            return 1, json.dumps(stop), ""
        return 0, json.dumps({"type": "result", "is_error": False, "result": self.tutor(system, stdin)}), ""


def _on_codex(tmp_path, monkeypatch, stop=lambda problem: None, *, caller=None, settings=CODEX_LOOP):
    """An enrolled loop whose domain's model is Codex, bare or held by ``caller``."""
    domain = domains.WordProblems(tmp_path)
    program = Codex(monkeypatch, domain.tutor, stop)
    _with_model(domain, caller if caller is not None else CodexCaller())
    loop, _ = _enrolled(tmp_path, settings=settings, domain=domain)
    return loop, domain, program


def _on_claude(tmp_path, monkeypatch, stop=lambda problem: None):
    domain = domains.WordProblems(tmp_path)
    program = Claude(monkeypatch, domain.tutor, stop)
    _with_model(domain, ClaudeCodeCaller())
    loop, _ = _enrolled(tmp_path, settings=REVIEW_L4B, domain=domain)
    return loop, domain, program


def _on_the_held_out_split(loop, stop):
    held = {case.input for case in loop.cut.held_out}
    return lambda problem: stop if problem in held else None


def _at_capacity() -> str:
    return usage.AT_CAPACITY            # read at call time, so on the base each test fails on its own


def _code(reply) -> str:
    return reply["warning_card"]["code"]


def _nothing_scored(loop) -> None:
    assert loop.mh.held_out_left() == 5 and loop.log.cycles() == [] and loop.log.failed_streak() == 0
    assert not any(loop.mh.ledger.rows(kind) for kind in ("evaluation", "report", "decision"))


def test_the_loops_codex_prints_its_events_as_json_and_the_plain_caller_does_not():
    plain = CodexCaller().argv("codex", work="w", last_message="m")
    watched = usage.WatchedCodex().argv("codex", work="w", last_message="m")
    assert "--json" not in plain and watched == ["codex", "exec", "--json", *plain[2:]]


@pytest.mark.parametrize("stop", [_failed(LIMIT), _failed("You\u2019ve hit your usage limit.", "usage_limit_exceeded"),
                                  _failed("Quota exceeded. Check your plan and billing details.")])
def test_review_l4_3_a_codex_limit_during_confirmation_leaves_the_split_unspent(tmp_path, monkeypatch, stop):
    """review-l4 finding 3, on the reviewer's settings, through the Codex the loop really calls: a limit in its own events is a
    wait, and spends none of the five."""
    loop, _, program = _on_codex(tmp_path, monkeypatch, settings=CODEX_L4)
    program.stop = _on_the_held_out_split(loop, stop)
    before = loop.mh.held_out_left()
    got = loop.tick()
    assert program.sent, "confirmation must actually reach the limited provider"
    assert loop.mh.held_out_left() == before, (
        f"provider limit spent a held-out evaluation: {before} -> {loop.mh.held_out_left()}; outcome={got.get('outcome')!r}; "
        f"decision rows={len(loop.mh.ledger.rows('decision'))}")
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["wait_until"] == (NOW + REFRESH).isoformat()
    assert "Codex reported a limit" in got["message"] and all("--json" in argv for argv in program.argvs)
    _nothing_scored(loop)
    assert loop.log.limits()["codex"] == NOW + REFRESH


@pytest.mark.parametrize("stop", [_failed(CAPACITY, "server_overloaded"), _failed(CAPACITY),
                                  _failed("Flex capacity unavailable.", "flex_unavailable")])
def test_a_codex_capacity_error_scores_no_case_for_either_arm_and_waits(tmp_path, monkeypatch, stop):
    loop, _, program = _on_codex(tmp_path, monkeypatch)
    program.stop = _on_the_held_out_split(loop, stop)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and _at_capacity() in got["message"]
    _nothing_scored(loop)


def test_a_capacity_error_while_tuning_is_no_failed_case_either(tmp_path, monkeypatch):
    loop, _, _ = _on_codex(tmp_path, monkeypatch, lambda problem: _failed(CAPACITY, "server_overloaded"))
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and "outcome" not in got
    assert loop.log.cycles() == [] and loop.mh.ledger.rows("tuning_run") == []


@pytest.mark.parametrize("stop", [_failed(CYBER, "cyber_policy"), _failed(CYBER),
                                  _failed("blocked", "bio_policy"), _failed("blocked", "too_many_denials")])
def test_a_codex_refusal_ends_the_tick_is_kept_and_is_never_sent_again(tmp_path, monkeypatch, stop):
    """The Codex refusal gap: it was a failed case, and the same request went out on a later tick."""
    refused = []

    def third(problem):
        refused.append(problem)
        return stop if len(refused) == 3 else None

    loop, domain, program = _on_codex(tmp_path, monkeypatch, third)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_CLASSIFIER_STOP" and "stopped a request" in loop.log.paused()
    it = program.sent[2]
    assert it in refused_requests(loop.root) and loop.log.cycles()[-1]["classifier_stop"]["request"] == it
    assert loop.mh.held_out_left() == 5
    program.stop = lambda problem: None
    again, _ = _enrolled_again(tmp_path, domain)
    assert again.resume("Ada")["completion_state"] == "qualified-draft"
    again.tick()
    assert program.sent.count(it) == 1


def _enrolled_again(tmp_path, domain, settings=CODEX_LOOP):
    """A restart: a new handle on the same folder, which a person need not enrol again."""
    return ShadowLoop(domain, tmp_path / "loop", settings, proposer=domains.proposes({"prompt": domains.WORKING}), now=lambda: NOW,
                      clock=domains.Clock()), domain


class TriesAgain:
    """A wrapper of a domain's own that sends any failed request again."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def complete(self, system, user, *, max_tokens=1024):
        for _ in range(3):
            try:
                return self._inner.complete(system, user, max_tokens=max_tokens)
            except LLMError:
                continue
        return "no answer"


class Swallows(TriesAgain):
    """One that turns any failure into a blank reply, which would then read as a failed case."""

    def complete(self, system, user, *, max_tokens=1024):
        try:
            return self._inner.complete(system, user, max_tokens=max_tokens)
        except LLMError:
            return ""


@pytest.mark.parametrize("wrap", [TriesAgain, Swallows])
@pytest.mark.parametrize("stop,code", [(_failed(CYBER, "cyber_policy"), "G6_E_LOOP_CLASSIFIER_STOP"),
                                       (_failed(LIMIT), "G6_E_LOOP_BUDGET_WAIT"),
                                       (_failed(CAPACITY, "server_overloaded"), "G6_E_LOOP_BUDGET_WAIT")])
def test_a_wrapper_that_catches_failures_cannot_resend_or_hide_what_codex_said(tmp_path, monkeypatch, wrap, stop, code):
    loop, _, program = _on_codex(tmp_path, monkeypatch, lambda problem: stop, caller=wrap(CodexCaller()))
    assert _code(loop.tick()) == code and len(program.sent) == 1


def test_review_l4b_3_through_the_claude_code_the_loop_calls(tmp_path, monkeypatch):
    """review-l4b finding 3, through the Claude Code the loop really calls: its fields are read before the 300 characters
    the bound caller keeps."""
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    limited = {"type": "result", "is_error": True, "api_error_status": 429, "api_error": "usage_limit_reached",
               "result": "You've hit your usage limit. " + "Please wait for the reset. " * 20}
    loop, _, program = _on_claude(tmp_path, monkeypatch)
    program.stop = _on_the_held_out_split(loop, limited)
    got = loop.tick()
    assert loop.mh.held_out_left() == 5, (
        f"a structured provider limit spent an evaluation: {loop.mh.held_out_left()} left; tick returned {_code(got)}")
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT", got["message"]
    _nothing_scored(loop)


def test_claude_codes_model_overloaded_is_a_wait_and_scores_nothing(tmp_path, monkeypatch):
    overloaded = {"type": "result", "is_error": True, "api_error_status": 529, "result": "API Error: overloaded " * 30}
    loop, _, program = _on_claude(tmp_path, monkeypatch)
    program.stop = _on_the_held_out_split(loop, overloaded)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and _at_capacity() in got["message"]
    _nothing_scored(loop)


# What a model wrote, or what does not read, never makes a stop ---------------------------------------------------------

MODEL_WORDS = (LIMIT, CAPACITY, CYBER, '{"type":"error","message":"Selected model is at capacity."}',
               '"codex_error_info":"usage_limit_exceeded"', "G6_E_MODEL_REFUSED: x", "G6_E_MODEL_AT_CAPACITY: x")


@pytest.mark.parametrize("words", MODEL_WORDS)
def test_what_a_model_wrote_in_its_reply_never_makes_a_codex_stop(words):
    said = {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": f"line one\n{words}"}}
    assert usage.codex_said(_jsonl(*STARTED, said, COMPLETED)) == ""
    assert usage.codex_said(_jsonl(*STARTED, said, {"type": "turn.failed", "error": {"message": "stream disconnected"}})) == ""


@pytest.mark.parametrize("printed", ["ERROR: You've hit your usage limit.\n", "not json\n[1, 2]\n\"a string\"\n",
                                     _jsonl({"type": "error", "message": 7}), _jsonl({"type": "turn.failed", "error": LIMIT}),
                                     _jsonl({"type": "error", "message": LIMIT, "codex_error_info": "other"}),
                                     _jsonl({"type": "error", "message": LIMIT, "codex_error_info": 3}),
                                     _jsonl({"type": "error", "message": LIMIT, "codex_error_info": {"a": 1, "b": 2}}),
                                     _jsonl({"type": "error", "message": f"unexpected status 400: {LIMIT}"}),
                                     _jsonl({"type": "error", "message": CAPACITY}, COMPLETED), "{\"type\": \"error\", \"mess"])
def test_codex_output_that_does_not_read_as_a_stop_is_none(printed):
    assert usage.codex_said(printed) == ""


def test_a_codex_refusal_stands_though_the_turn_then_completed_and_a_limit_on_the_way_does_not():
    assert refused_by(usage.codex_said(_jsonl(*STARTED, {"type": "error", "message": CYBER, "codex_error_info": "cyber_policy"},
                                              COMPLETED)))
    assert usage.codex_said(_jsonl(*STARTED, {"type": "error", "message": LIMIT}, COMPLETED)) == ""
    assert usage.codex_said(_jsonl(*STARTED, {"type": "turn.completed", "error": {"message": LIMIT}})).startswith(RATE_LIMITED)


@pytest.mark.parametrize("info,said", [({"response_too_many_failed_attempts": {"http_status_code": 429}}, RATE_LIMITED),
                                       ({"http_connection_failed": {"http_status_code": 500}}, ""),
                                       ("server_overloaded", "G6_E_MODEL_AT_CAPACITY"), ("cyber_policy", MODEL_REFUSED)])
def test_codexs_own_classification_decides_whatever_its_message_says(info, said):
    got = usage.codex_said(_jsonl(*_failed("some message", info)))
    assert got.startswith(said) if said else got == ""


def test_a_codex_run_that_failed_with_no_event_of_its_own_is_a_failed_case_not_a_wait(tmp_path, monkeypatch):
    """What Codex writes to its error stream is never read, whatever it says."""
    loop, _, program = _on_codex(tmp_path, monkeypatch, lambda problem: ())
    got = loop.tick()
    assert _code(got) != "G6_E_LOOP_BUDGET_WAIT" and len(loop.log.cycles()) == 1 and len(program.sent) > 1


@pytest.mark.parametrize("result", [{"type": "result", "is_error": False, "api_error_status": 429, "result": "4"},
                                    {"type": "result", "is_error": True, "api_error_status": True, "result": "x"},
                                    {"type": "result", "is_error": True, "api_error_status": "429", "result": "x"},
                                    {"type": "result", "is_error": True, "result": '"api_error_status":429 529'}])
def test_a_claude_result_that_says_no_limit_in_its_own_fields_is_none(result):
    assert usage.claude_said(result) == ""


def test_g6s_own_capacity_code_is_a_limit_looked_at_again_and_a_body_naming_it_is_not():
    assert limit_ends(f"{_at_capacity()}: Codex reported its model at capacity", NOW) == NOW + REFRESH
    assert limit_ends(f"HTTP 500 from https://model: {_at_capacity()}: x", NOW) is None
    assert limit_ends(f"G6_E_LLM_HOST_FAILED: codex exited 1: {_at_capacity()}", NOW) is None


# Kept before anything else happens, whatever stops between two steps ---------------------------------------------------

class Killed(BaseException):
    """The process dying at that instant."""


def test_a_codex_refusal_survives_a_death_before_its_cycle_is_written(tmp_path, monkeypatch):
    loop, domain, program = _on_codex(tmp_path, monkeypatch, lambda problem: _failed(CYBER, "cyber_policy"))
    with monkeypatch.context() as dying:
        dying.setattr(loop.log, "cycle", lambda row: (_ for _ in ()).throw(Killed()))
        with pytest.raises(Killed):
            loop.tick()
    it = program.sent[0]
    program.stop = lambda problem: None
    again, _ = _enrolled_again(tmp_path, domain)
    again.tick()
    assert program.sent.count(it) == 1


def test_a_refusal_kept_and_then_a_death_before_it_reaches_the_tick_is_still_never_sent_again(tmp_path, monkeypatch):
    """Between keeping it and anything else: the note is on the disk, then the process dies."""
    from g6.metaharness import loop as loop_module

    real = loop_module.note_refusal

    def kept_then_killed(root, request, said):
        assert real(root, request, said)
        raise Killed()

    loop, domain, program = _on_codex(tmp_path, monkeypatch, lambda problem: _failed(CYBER, "cyber_policy"))
    with monkeypatch.context() as dying:
        dying.setattr(loop_module, "note_refusal", kept_then_killed)
        with pytest.raises(Killed):
            loop.tick()
    it = program.sent[0]
    assert loop.log.cycles() == [] and it in refused_requests(loop.root)
    program.stop = lambda problem: None
    again, _ = _enrolled_again(tmp_path, domain)
    again.tick()
    assert program.sent.count(it) == 1


def test_a_limit_is_kept_before_the_ticks_charges_are_settled(tmp_path, monkeypatch):
    """A death at the settling of what the tick spent: the limit is already in the records, so a restart waits it out."""
    loop, domain, program = _on_codex(tmp_path, monkeypatch, lambda problem: _failed(LIMIT))
    with monkeypatch.context() as dying:
        dying.setattr(loop.meter, "settle", lambda *args: (_ for _ in ()).throw(Killed()))
        with pytest.raises(Killed):
            loop.tick()
    sent = len(program.sent)
    again, _ = _enrolled_again(tmp_path, domain)
    got = again.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["wait_until"] == (NOW + REFRESH).isoformat()
    assert len(program.sent) == sent


def test_a_limit_any_caller_raised_is_kept_before_the_ticks_charges_are_settled(tmp_path, monkeypatch):
    """The same death with a limit G6 always read: it was written only after the charges were settled, so it was lost."""
    domain = domains.WordProblems(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain)

    def limited(system, user):
        raise LLMError(f"{RATE_LIMITED}: HTTP 429 from https://model")

    domain.llm._responder = limited
    with monkeypatch.context() as dying:
        dying.setattr(loop.meter, "settle", lambda *args: (_ for _ in ()).throw(Killed()))
        with pytest.raises(Killed):
            loop.tick()
    sent = len(domain.llm.calls)
    got = _restarted(tmp_path, domain).tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["wait_until"] == (NOW + REFRESH).isoformat()
    assert len(domain.llm.calls) == sent


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


def test_a_limit_a_watched_caller_reads_leaves_the_tick_past_any_wrapper():
    requests = _Requests()
    requests.open(10)
    with pytest.raises(_AtLimit):
        requests.provider_said(f"{RATE_LIMITED}: Codex reported a limit of the account", "a")
    with pytest.raises(_AtLimit):
        requests.take()


def test_every_host_caller_the_loop_sends_through_is_watched_and_keeps_what_it_was():
    for caller, watched in ((ClaudeCodeCaller("m", timeout=7.0), WatchedClaude), (CodexCaller("m", timeout=7.0), usage.WatchedCodex)):
        kind = type(caller)
        read_stops(caller)
        assert isinstance(caller, watched) and isinstance(caller, kind) and type(caller).__name__ == kind.__name__
        assert (caller.model, caller.timeout) == ("m", 7.0)
    assert refused_by(usage.codex_said(_jsonl(*_failed(CYBER, "cyber_policy"))))