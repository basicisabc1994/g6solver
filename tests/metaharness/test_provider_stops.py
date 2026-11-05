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
from g6.self_maintenance.envelope import REFUSED_FILE, note_refusal, refused_requests, stop_reason
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
        self.sent.append(usage.request_identity(argv, stdin, files.parent))      # what the program is started with
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
        self.sent.append(usage.request_identity(argv, stdin, files.parent))
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

# Review 1 of this change: a refused request is known by what is sent, and one check stands in front of every send ---------

def test_ref1_1_a_restart_knows_what_was_refused_before_its_first_request(tmp_path):
    loop, domain, caller = _between_ticks(tmp_path)
    with pytest.raises(LLMError, match=MODEL_REFUSED):
        caller.complete("system", "same request")
    restarted = _restarted(tmp_path, domain)
    before = len(domain.llm.calls)
    with pytest.raises(LLMError, match="G6_E_LOOP_CLASSIFIER_STOP"):
        restarted.workflow.models["local"].caller.complete("system", "same request")
    assert len(domain.llm.calls) == before


def test_ref1_1_records_that_cannot_be_read_when_the_handle_is_built_refuse_every_request(tmp_path):
    loop, domain, caller = _between_ticks(tmp_path)
    (loop.root / REFUSED_FILE).mkdir()
    restarted = _restarted(tmp_path, domain)
    before = len(domain.llm.calls)
    with pytest.raises(LLMError, match="G6_E_LOOP_RECORDS_UNREADABLE"):
        restarted.workflow.models["local"].caller.complete("system", "another request")
    assert len(domain.llm.calls) == before and _code(restarted.tick()) == "G6_E_LOOP_RECORDS_UNREADABLE"


def test_ref1_2_what_a_watched_caller_printed_on_its_error_stream_makes_no_limit(tmp_path, monkeypatch):
    ordinary = {"type": "result", "is_error": True, "api_error_status": 500, "result": "ordinary failure"}
    loop, _, program = _on_claude(tmp_path, monkeypatch, lambda problem: ordinary)

    def run(*args):
        code, printed, _ = program(*args)
        return code, printed, 'tool diagnostic: "api_error_status":429'

    monkeypatch.setattr(host_models, "_run", run)
    assert _code(loop.tick()) != "G6_E_LOOP_BUDGET_WAIT"


def test_ref1_2_the_text_pattern_never_reads_a_watched_callers_error_and_still_reads_any_other(tmp_path, monkeypatch):
    """The two rules cannot both apply to one request: a watched caller's request is read only from its structured fields."""
    from g6.metaharness import loop as loop_module

    read = []
    real = loop_module.limit_ends
    monkeypatch.setattr(loop_module, "limit_ends", lambda said, now: read.append(said) or real(said, now))
    limited = {"type": "result", "is_error": True, "api_error_status": 429, "result": "x" * 400}
    loop, _, program = _on_claude(tmp_path, monkeypatch)
    program.stop = _on_the_held_out_split(loop, limited)
    assert _code(loop.tick()) == "G6_E_LOOP_BUDGET_WAIT"
    assert read and all(not said.startswith("G6_E_LLM_HOST_FAILED") for said in read)
    assert sum(said.startswith(RATE_LIMITED) for said in read) == 1         # told once, and never read again from text
    read.clear()

    def down(system, user):
        raise LLMError(f"{RATE_LIMITED}: HTTP 429 from https://model")

    requests = _Requests()
    requests.open(10)
    with pytest.raises(_AtLimit):
        _Counted(FakeLLM(down), requests).complete("system", "user")
    assert read == [f"{RATE_LIMITED}: HTTP 429 from https://model"]


def test_ref1_3_no_thread_sees_a_refusal_before_it_is_on_the_disk(tmp_path):
    writing, release, attempting, escaped = (threading.Event() for _ in range(4))
    errors, durable_when_raised = [], []
    request = _request_digest("system", "request")

    def keep(request, said):
        writing.set()
        assert release.wait(5)
        assert note_refusal(tmp_path, request, said)

    requests = _Requests(keeping=keep)
    requests.open(10)

    def first_refusal():
        try:
            requests.at_a_refusal(f"{MODEL_REFUSED}: denied", request)
        except BaseException as exc:
            errors.append(exc)

    def another_request():
        attempting.set()
        try:
            requests.take()
        except _Refused:
            durable_when_raised.append(request in refused_requests(tmp_path))
            escaped.set()

    first, other = threading.Thread(target=first_refusal), threading.Thread(target=another_request)
    first.start()
    try:
        assert writing.wait(5)
        other.start()
        assert attempting.wait(5)
        escaped.wait(0.2)
    finally:
        release.set()
        first.join(5)
        if other.ident is not None:
            other.join(5)
    assert len(errors) == 1 and isinstance(errors[0], _Refused) and durable_when_raised == [True]


def test_ref1_4_a_refusal_no_file_would_keep_stops_the_next_request_between_ticks(tmp_path, monkeypatch):
    from g6.metaharness import loop as loop_module

    loop, domain, caller = _between_ticks(tmp_path)
    monkeypatch.setattr(loop_module, "note_refusal", lambda *args: False)
    with pytest.raises(LLMError, match=MODEL_REFUSED):
        caller.complete("system", "same request")
    before = len(domain.llm.calls)
    with pytest.raises(LLMError, match="G6_E_LOOP_STOPPED"):
        caller.complete("system", "a different request")
    assert len(domain.llm.calls) == before


def test_ref1_4_a_stop_refuses_a_request_between_ticks_too(tmp_path):
    from g6.self_maintenance.envelope import ask_to_stop

    loop, domain, caller = _between_ticks(tmp_path)
    ask_to_stop(loop.root, "the owner's review")
    before = len(domain.llm.calls)
    with pytest.raises(LLMError, match="G6_E_LOOP_STOPPED"):
        caller.complete("system", "a request")
    assert len(domain.llm.calls) == before


class CatchesAll:
    """A wrapper of a domain's own that catches anything at all, a limit included, and answers itself."""

    def __init__(self, inner, tutor) -> None:
        self._inner, self.tutor, self.caught = inner, tutor, []

    def complete(self, system, user, *, max_tokens=1024):
        try:
            return self._inner.complete(system, user, max_tokens=max_tokens)
        except BaseException as exc:
            self.caught.append(exc)
            return self.tutor(system, user)


def test_ref1_5_a_limit_a_wrapper_swallowed_on_the_last_call_still_spends_nothing(tmp_path, monkeypatch):
    domain = domains.WordProblems(tmp_path)
    wrapper = CatchesAll(CodexCaller(), domain.tutor)
    program = Codex(monkeypatch, domain.tutor)
    _with_model(domain, wrapper)
    loop, _ = _enrolled(tmp_path, settings=dataclasses.replace(CODEX_LOOP, eval_calls=1), domain=domain)
    held, calls = {case.input for case in loop.cut.held_out}, []

    def limited_on_the_second(problem):
        if problem in held:
            calls.append(problem)
            return _failed(LIMIT) if len(calls) == 2 else None
        return None

    program.stop = limited_on_the_second
    loop.tick()
    assert len(wrapper.caught) == 1 and isinstance(wrapper.caught[0], _AtLimit)
    assert loop.mh.held_out_left() == 5


def test_ref1_5_a_refusal_a_wrapper_swallowed_leaves_its_call_as_a_refusal(tmp_path, monkeypatch):
    """What the tick latched decides: the watched caller told the tick, and the wrapper's answer is not taken for one."""
    _program_that_refuses(monkeypatch, json.dumps({"type": "turn.failed", "error": {"message": "x", "codex_error_info": "cyber_policy"}}))
    wrapper = CatchesAll(CodexCaller(), lambda system, user: "an answer")
    requests = _Requests()
    requests.open(10)
    with pytest.raises(_Refused):
        _Counted(wrapper, requests).complete("system", "user")
    assert len(wrapper.caught) == 1 and isinstance(wrapper.caught[0], _Refused) and requests.refusal is not None


class ScoutWrapper:
    """A domain's own caller that sends one fixed scout prompt through a session, whatever it is asked."""

    def __init__(self, root) -> None:
        from g6.harness.completion_loop import ToolSession

        self._inner, self.root = ToolSession(), root

    def complete(self, system, user, *, max_tokens=1024):
        return self._inner.run("the same fixed scout prompt", self.root, self.root, 1)


def _program_that_refuses(monkeypatch, printed: str, kind=CodexCaller):
    sent = []
    monkeypatch.setattr(kind, "locate", lambda self: "fake-program")

    def run(argv, stdin, *rest):
        sent.append(stdin)
        return 1, printed, ""

    monkeypatch.setattr(host_models, "_run", run)
    return sent


def test_ref1_6_a_session_prompt_is_known_by_what_is_sent_whatever_the_outer_request(tmp_path, monkeypatch):
    from g6.harness.completion_loop import ToolSession

    sent = _program_that_refuses(monkeypatch, json.dumps({"type": "result", "is_error": True, "stop_reason": "refusal",
                                                          "result": ""}), ToolSession)
    requests = _Requests(keeping=lambda request, said: note_refusal(tmp_path, request, said))
    caller = _Counted(ScoutWrapper(tmp_path), requests)
    for user in ("outer request one", "outer request two"):
        requests.open(10)
        with pytest.raises((LLMError, _Refused)):
            caller.complete("system", user)
        requests.close()
    assert sent == ["the same fixed scout prompt"]


def test_ref1_7_the_same_codex_input_is_one_request_however_its_parts_were_split(tmp_path, monkeypatch):
    boundary = "\nG6_INSTRUCTIONS>>>\n<<<G6_REQUEST\n"
    pairs = (("first" + boundary + "second", "third"), ("first", "second" + boundary + "third"))
    assert host_models._prompt(*pairs[0]) == host_models._prompt(*pairs[1])
    sent = _program_that_refuses(monkeypatch, json.dumps({"type": "turn.failed", "error": {"message": "blocked",
                                                                                           "codex_error_info": "cyber_policy"}}))
    requests = _Requests(keeping=lambda request, said: note_refusal(tmp_path, request, said))
    caller = _Counted(CodexCaller(), requests)
    for system, user in pairs:
        requests.open(10)
        with pytest.raises((LLMError, _Refused)):
            caller.complete(system, user)
        requests.close()
    assert len(sent) == 1


class Rewrites:
    def __init__(self, inner) -> None:
        self._inner = inner

    def complete(self, system, user, *, max_tokens=1024):
        return self._inner.complete(system, "actual refused request", max_tokens=max_tokens)


def test_ref1_8_one_identity_is_kept_so_a_death_after_keeping_it_loses_nothing(tmp_path, monkeypatch):
    class ProcessDied(BaseException):
        pass

    sent = _program_that_refuses(monkeypatch, json.dumps({"type": "error", "message": "blocked",
                                                          "codex_error_info": "cyber_policy"}))
    kept = []

    def keep_then_die(request, said):
        assert note_refusal(tmp_path, request, said)
        kept.append(request)
        raise ProcessDied()

    requests = _Requests(keeping=keep_then_die)
    requests.open(10)
    with pytest.raises(ProcessDied):
        actual = _request_digest("system", "actual refused request")       # an outer request that sorts first, as an alias did
        outer = next(f"outer {i}" for i in range(100) if _request_digest("system", f"outer {i}") < actual)
        _Counted(Rewrites(CodexCaller()), requests).complete("system", outer)
    assert len(kept) == 1 and refused_requests(tmp_path) == set(kept)          # one identity, and no alias of it
    restarted = _Requests(keeping=lambda request, said: note_refusal(tmp_path, request, said))
    restarted.refused |= refused_requests(tmp_path)
    restarted.open(10)
    with pytest.raises(LLMError, match="G6_E_LOOP_CLASSIFIER_STOP"):
        _Counted(CodexCaller(), restarted).complete("system", "actual refused request")
    assert len(sent) == 1


def test_ref1_9_a_request_let_in_before_a_refusal_of_the_same_bytes_cannot_leave_after_it(tmp_path, monkeypatch):
    first_sent, release_first, second_checked, release_second = (threading.Event() for _ in range(4))
    sent, failures = [], []

    def locate(self):
        if threading.current_thread().name == "second-request":
            second_checked.set()
            assert release_second.wait(5)
        return "fake-codex"

    def run(argv, stdin, *args):
        sent.append(stdin)
        if threading.current_thread().name == "first-request":
            first_sent.set()
            assert release_first.wait(5)
        return 1, json.dumps({"type": "error", "codex_error_info": "cyber_policy", "message": "blocked"}), ""

    monkeypatch.setattr(CodexCaller, "locate", locate)
    monkeypatch.setattr(host_models, "_run", run)
    requests = _Requests(keeping=lambda request, said: note_refusal(tmp_path, request, said))
    requests.open(10)
    caller = _Counted(CodexCaller(), requests)

    def ask():
        try:
            caller.complete("system", "same request")
        except BaseException as exc:
            failures.append(exc)

    first = threading.Thread(target=ask, name="first-request")
    second = threading.Thread(target=ask, name="second-request")
    first.start()
    try:
        assert first_sent.wait(5)
        second.start()
        assert second_checked.wait(5)
        release_first.set()
        first.join(5)
        assert refused_requests(tmp_path)
    finally:
        release_first.set()
        release_second.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert len(sent) == 1 and len(failures) == 2


def test_ref1_9_an_identical_request_waits_while_one_is_out_and_then_goes_if_that_one_answered(tmp_path, monkeypatch):
    first_out, release = threading.Event(), threading.Event()
    sent, answers = [], []
    monkeypatch.setattr(CodexCaller, "locate", lambda self: "fake-codex")

    def run(argv, stdin, cwd, files, *rest):
        sent.append(threading.current_thread().name)
        if threading.current_thread().name == "first":
            first_out.set()
            assert release.wait(5)
        Path(argv[argv.index("--output-last-message") + 1]).write_text("an answer", encoding="utf-8")
        return 0, _jsonl(COMPLETED), ""

    monkeypatch.setattr(host_models, "_run", run)
    requests = _Requests()
    caller = _Counted(CodexCaller(), requests)
    threads = [threading.Thread(target=lambda: answers.append(caller.complete("system", "same")), name=name)
               for name in ("first", "second")]
    threads[0].start()
    assert first_out.wait(5)
    threads[1].start()
    threads[1].join(0.3)
    assert sent == ["first"]                                  # held while the identical one is out
    release.set()
    for thread in threads:
        thread.join(5)
    assert sent == ["first", "second"] and answers == ["an answer", "an answer"]


def test_ref1_10_a_failed_turn_of_no_known_kind_is_a_failed_request_whatever_else_says(tmp_path, monkeypatch):
    monkeypatch.setattr(CodexCaller, "locate", lambda self: "fake-codex")

    def run(argv, stdin, cwd, files, *args):
        (files / "last_message.txt").write_text("an incomplete answer", encoding="utf-8")
        return 0, json.dumps({"type": "turn.failed", "error": {"message": "new failure", "codex_error_info": "unseen_kind"}}), ""

    monkeypatch.setattr(host_models, "_run", run)
    requests = _Requests()
    requests.open(10)
    with pytest.raises(LLMError, match="turn failed"):
        _Counted(CodexCaller(), requests).complete("system", "request")


@pytest.mark.parametrize("printed,failed", [(_jsonl({"type": "error", "message": "?"}, COMPLETED), True),
                                            (_jsonl({"type": "turn.completed", "error": {"message": "?"}}), True),
                                            (_jsonl({"type": "error", "message": LIMIT}, COMPLETED), False),
                                            (_jsonl(*STARTED, COMPLETED), False)])
def test_ref1_10_an_explicit_failure_of_no_known_kind_fails_and_a_known_one_on_the_way_does_not(printed, failed):
    assert usage._codex_turn(printed)[1] is failed


def test_ref1_11_distinct_requests_never_share_an_identity_and_old_ones_keep_theirs():
    import hashlib

    assert _request_digest("a\0b", "c") != _request_digest("a", "b\0c")
    assert _request_digest("\ud800", "x") != _request_digest("\ud801", "x")
    assert _request_digest("system", "user") == hashlib.sha256(b"system\0user").hexdigest()    # as kept before

    def reply(system, user):
        if (system, user) == ("a\0b", "c"):
            raise LLMError(f"{MODEL_REFUSED}: denied")
        return "allowed answer"

    caller = _Counted(FakeLLM(reply), _Requests())
    with pytest.raises(LLMError, match=MODEL_REFUSED):
        caller.complete("a\0b", "c")
    assert caller.complete("a", "b\0c") == "allowed answer"


def test_ref1_on_the_loops_own_requests_one_identity_is_one_input_and_one_input_one_identity(tmp_path, monkeypatch):
    """Every request a tick sent through Codex and through Claude Code, with what its program read: an identity never stands for
    two inputs, nor an input for two identities."""
    for on, settings in ((_on_codex, None), (_on_claude, None)):
        seen: dict[str, set] = {}
        loop, _, program = on(tmp_path / on.__name__, monkeypatch)
        real = host_models._run

        def watching(argv, stdin, cwd, files, *rest, real=real):
            written = sorted((p.name, p.read_bytes()) for p in Path(files).iterdir() if p.is_file())
            seen.setdefault(usage.request_identity(argv, stdin, Path(files).parent), set()).add((stdin, tuple(written)))
            return real(argv, stdin, cwd, files, *rest)

        monkeypatch.setattr(host_models, "_run", watching)
        loop.tick()
        inputs = [given for found in seen.values() for given in found]
        assert len(seen) > 20 and all(len(found) == 1 for found in seen.values()) and len(set(inputs)) == len(inputs)


def test_ref1_every_way_a_request_can_leave_passes_the_one_check():
    """The places in the code that let a request out, read from the code: each goes through ``_Requests.admit``. A new one
    has to be added here, and to the check, on purpose."""
    import ast
    import inspect

    from g6.metaharness import loop as loop_module

    def callers(module, names):
        found = set()

        def walk(node, where):
            for child in ast.iter_child_nodes(node):
                inner = where
                if isinstance(child, ast.FunctionDef | ast.ClassDef):
                    inner = f"{where}.{child.name}" if where else child.name
                if isinstance(child, ast.Call):
                    func = child.func
                    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
                    if name in names:
                        found.add((inner, name))
                walk(child, inner)

        walk(ast.parse(inspect.getsource(module)), "")
        return found

    assert callers(loop_module, {"admit", "take", "sending"}) == {
        ("_Requests.take", "admit"), ("_Gate.sending", "admit"), ("_Counted.complete", "admit"),
        ("_Counted.complete.take", "take"), ("_Counted.complete.next_request", "take"), ("_Resends.append", "take")}
    assert callers(usage, {"sending", "_sending"}) == {
        ("_sending", "sending"), ("WatchedClaude._command", "_sending"), ("WatchedClaude._launch", "_sending"),
        ("WatchedCodex._command", "_sending")}