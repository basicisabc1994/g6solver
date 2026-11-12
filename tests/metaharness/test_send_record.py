# SPDX-License-Identifier: Apache-2.0
"""How a send is counted, settled and known, after review 4 of claude/refusals-recorded: every model request in the process
passes one admission, which takes one place on each meter that encloses it and makes its one record; a call settles what
its children imported once, into that record; what a provider stated is in the store, in the order it was met, before the
tick's charges settle; and whether a request was refused is read from the store. No program starts and no model is called:
each host is stood in for at the one place it is run."""
from __future__ import annotations

import ast
import contextvars
import importlib.util
import inspect
import io
import json
import os
import sys
import threading
import urllib.error
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import g6
from g6.core import host_models, llm, model_calls, stop
from g6.core.host_models import ClaudeCodeCaller, CodexCaller
from g6.core.llm import RATE_LIMITED, FakeLLM, LLMError
from g6.core.model_calls import note_model_call
from g6.metaharness import loop as loop_module
from g6.metaharness.domain import caller_problems, sender_problems
from g6.metaharness.loop import (BUDGET_WAIT, CLASSIFIER_STOP, LoopSettings, ShadowLoop, _AtLimit, _Counted, _Halted,
                                 _Refused, _request_digest, _Requests)
from g6.metaharness.method import Method
from g6.operator.store import OperatorStore
from g6.self_maintenance import usage
from g6.self_maintenance.envelope import (CYCLE_LOG, MAX_FAILED_CYCLES, REFUSED_FILE, CycleLog, note_refusal,
                                          refused_requests)
from g6.self_maintenance.pacing import BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import MODEL_REFUSED, REFRESH, request_identity

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

NOW = datetime(2026, 11, 6, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_key_file(monkeypatch):
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})


@pytest.fixture
def host_folders(tmp_path, monkeypatch):
    """Each host call's folders under the test's own, laid out as ``host_models._folders`` lays them out."""

    @contextmanager
    def folders():
        root = tmp_path / uuid4().hex
        work, files = root / "work", root / "files"
        work.mkdir(parents=True)
        (files / "tmp").mkdir(parents=True)
        yield work, files

    monkeypatch.setattr(host_models, "_folders", folders)


def _busy_once(monkeypatch):
    """An HTTP endpoint that is busy on the first request, asking for a resend at once, and answers after."""
    sent = []

    def open_request(request, timeout):
        sent.append(request.data)
        if len(sent) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {"Retry-After": "0"}, io.BytesIO(b"busy"))
        return io.BytesIO(b'{"choices":[{"message":{"content":"answer"}}]}')

    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    monkeypatch.setattr(llm, "_sleep", lambda seconds: None)
    return sent


def _bare_loop(root, *, keeping=None, on_record=None):
    """A loop with nothing of a domain about it, enough to run ``_run`` around a cycle of a test's own."""
    loop = ShadowLoop.__new__(ShadowLoop)
    loop.root, loop.log, loop.waited, loop.cannot_record = root, CycleLog(root / CYCLE_LOG), 0, ""
    loop.requests = _Requests(now=lambda: NOW, **({"keeping": keeping} if keeping else {}),
                              **({"on_record": on_record} if on_record else {}))
    loop._now, loop._beat = lambda: NOW, lambda *args, **kwargs: None
    loop.domain = SimpleNamespace(name="fake", sealed=lambda: SimpleNamespace(vault=None))
    loop._reserve = lambda: (SimpleNamespace(provider="codex", amount=10, ends=NOW + timedelta(days=1)), None)
    return loop


def _limit_rows(loop):
    return [entry["body"]["until"] for entry in loop.log._chain.entries() if entry["kind"] == "limited"]


# Review 4's nine findings, as reported (review-ref4, tests/_review/test_finding_1.py to test_finding_9.py) -----------------

def test_ref4_1_a_nested_calls_retry_after_resend_takes_one_place(monkeypatch):
    sent = _busy_once(monkeypatch)
    requests = _Requests()
    requests.open(2)
    child = _Counted(llm.OpenAICompatibleCaller("fake", base_url="http://fake.invalid", api_key=None), requests)

    class Parent:
        _inner = child.inner

        def complete(self, system, user, *, max_tokens=1024):
            return child.complete(system, user, max_tokens=max_tokens)

    outcome = None
    try:
        outcome = _Counted(Parent(), requests).complete("system", "request")
    except llm.LLMError as error:
        outcome = str(error)
    assert (len(sent), requests.sent, outcome) == (2, 2, "answer")


def test_ref4_2_a_nested_call_does_not_charge_child_totals_twice():
    class Child(FakeLLM):
        def complete(self, system, user, *, max_tokens=1024):
            answer = super().complete(system, user, max_tokens=max_tokens)
            model_calls.note_child_model_calls(scoped=2, process=2)
            return answer

    requests = _Requests()
    requests.open(3)
    backend = Child(default="answer")
    child = _Counted(backend, requests)

    class Parent:
        _inner = backend

        def complete(self, system, user, *, max_tokens=1024):
            return child.complete(system, user, max_tokens=max_tokens)

    assert _Counted(Parent(), requests).complete("system", "request") == "answer"
    assert len(backend.calls) == 1
    assert requests.sent == 3, "one noted send plus two imported child calls must cost three, even when nested"


def test_ref4_3_every_limit_is_on_the_record_in_the_order_met_before_settlement(tmp_path, monkeypatch):
    loop = _bare_loop(tmp_path)
    monkeypatch.setattr(loop_module, "sealed_closed", lambda vault: nullcontext([]))
    encounters = [NOW + timedelta(minutes=15), NOW + timedelta(seconds=90), NOW + timedelta(seconds=90)]

    class Settled(BaseException):
        pass

    def cycle(recommended):
        for until in encounters:
            try:
                loop.requests.met_a_limit(until, "HTTP 429: provider limit")
            except _AtLimit:
                pass
        raise RuntimeError("end the tick")

    before_settlement = []

    def settle(admission, charged):
        before_settlement.extend(_limit_rows(loop))
        raise Settled()

    loop._cycle = cycle
    loop.meter = SimpleNamespace(settle=settle, limit_reached=lambda provider, until: None)
    with pytest.raises(Settled):
        loop._run()
    assert before_settlement == [until.isoformat() for until in encounters]


def test_ref4_4_identical_sends_on_two_threads_of_one_call_wait_for_the_first(monkeypatch, host_folders):
    requests = _Requests()
    first_out, second_checked, release_first = threading.Event(), threading.Event(), threading.Event()
    sent, outcomes, threads = [], [], []
    monkeypatch.setattr(ClaudeCodeCaller, "locate", lambda self: "fake-claude")
    real_out_elsewhere = requests._out_elsewhere

    def out_elsewhere(identity, owner, thread):
        result = real_out_elsewhere(identity, owner, thread)
        if threading.current_thread().name == "second-send":
            second_checked.set()
        return result

    monkeypatch.setattr(requests, "_out_elsewhere", out_elsewhere)

    def run(argv, stdin, cwd, files, *rest):
        sent.append(usage.request_identity(argv, stdin, files.parent))
        if threading.current_thread().name == "first-send":
            first_out.set()
            assert release_first.wait(5)
            return 1, json.dumps({"type": "result", "is_error": True, "stop_reason": "refusal", "result": ""}), ""
        return 0, json.dumps({"type": "result", "is_error": False, "result": "answer"}), ""

    monkeypatch.setattr(host_models, "_run", run)
    backend = ClaudeCodeCaller()

    def ask():
        try:
            outcomes.append(backend.complete("system", "same request"))
        except BaseException as error:
            outcomes.append(error)

    class Parent:
        _inner = backend

        def complete(self, system, user, *, max_tokens=1024):
            try:
                for name, ready in (("first-send", first_out), ("second-send", second_checked)):
                    context = contextvars.copy_context()
                    thread = threading.Thread(target=context.run, args=(ask,), name=name)
                    threads.append(thread)
                    thread.start()
                    assert ready.wait(5)
            finally:
                release_first.set()
                for thread in threads:
                    thread.join(5)
            return "wrapper answer"

    _Counted(Parent(), requests).complete("outer system", "outer request")
    assert all(not thread.is_alive() for thread in threads)
    assert len(outcomes) == 2 and len(requests.refused) == 1
    assert len(sent) == 1, "the identical second send must wait, then be barred when the first is refused"


@pytest.mark.parametrize("stop", [None, "refusal", "limit", "capacity"])
def test_ref4_5_a_nested_host_caller_on_another_meter_holds_to_the_outer_tick(monkeypatch, host_folders, stop):
    outer, inner = _Requests(), _Requests()
    outer.open(1 if stop is None else 10)
    sent = []
    monkeypatch.setattr(ClaudeCodeCaller, "locate", lambda self: "fake-claude")

    def run(argv, stdin, *rest):
        sent.append(stdin)
        if stop:
            fields = {"stop_reason": "refusal"} if stop == "refusal" else {"api_error_status": 429 if stop == "limit" else 529}
            return 1, json.dumps({"type": "result", "is_error": True, "result": "", **fields}), ""
        return 0, json.dumps({"type": "result", "is_error": False, "result": "answer"}), ""

    monkeypatch.setattr(host_models, "_run", run)
    child = _Counted(ClaudeCodeCaller(), inner)

    class Parent:
        _inner = child.inner

        def complete(self, system, user, *, max_tokens=1024):
            for number in range(3):
                try:
                    child.complete(system, f"child request {number}")
                except BaseException:
                    if stop:
                        return "swallowed provider stop"
                    raise
            return "answer"

    outcome = None
    try:
        outcome = _Counted(Parent(), outer).complete("system", "parent request")
    except (LLMError, _AtLimit, _Refused) as error:
        outcome = error
    if stop is None:
        assert (len(sent), outer.sent) == (1, 1), "every host send in this tick must take one of its reserved places"
    else:
        expected = _Refused if stop == "refusal" else _AtLimit
        assert isinstance(outcome, expected), "the outer tick must re-raise the watched provider stop swallowed by its wrapper"


def test_ref4_6_a_rejected_note_offsets_no_imported_child_usage():
    class Child(FakeLLM):
        def complete(self, system, user, *, max_tokens=1024):
            answer = super().complete(system, user, max_tokens=max_tokens)
            model_calls.note_child_model_calls(scoped=2, process=2)
            try:
                super().complete(system, "one request beyond the reservation", max_tokens=max_tokens)
            except LLMError:
                pass
            return answer

    requests = _Requests()
    requests.open(1)
    backend = Child(default="answer")
    assert _Counted(backend, requests).complete("system", "request") == "answer"
    assert len(backend.calls) == 1  # the second attempt was stopped before it was counted or sent
    assert requests.sent == 3, "one successful note plus two imported child calls must still cost three"


def test_ref4_7_a_limit_met_while_the_tick_closes_is_kept_before_settlement(tmp_path, monkeypatch):
    first, second = NOW + timedelta(minutes=15), NOW + timedelta(hours=2)
    loop = _bare_loop(tmp_path)
    loop._row = lambda: {"outcome": "idle"}
    monkeypatch.setattr(loop_module, "sealed_closed", lambda vault: nullcontext([]))
    deliver, received = threading.Event(), threading.Event()
    observed_generation = []

    def late_limit():
        assert deliver.wait(5)
        observed_generation.append((loop.requests.generation, loop.requests.limit))
        try:
            loop.requests.met_a_limit(second, "HTTP 429: second request's limit")
        except _AtLimit:
            pass
        finally:
            received.set()

    thread = threading.Thread(target=late_limit)
    real_keep = loop._keep_limit

    def keep(provider, until):
        real_keep(provider, until)
        deliver.set()
        assert received.wait(5)

    loop._keep_limit = keep

    def cycle(recommended):
        thread.start()
        loop.requests.met_a_limit(first, "HTTP 429: first request's limit")

    class Settled(BaseException):
        pass

    rows_at_settlement = []

    def settle(admission, charged):
        rows_at_settlement.extend(_limit_rows(loop))
        raise Settled()

    loop._cycle = cycle
    loop.meter = SimpleNamespace(settle=settle, limit_reached=lambda provider, until: None)
    try:
        with pytest.raises(Settled):
            loop._run()
    finally:
        deliver.set()
        if thread.ident is not None:
            thread.join(5)
    assert not thread.is_alive()
    if observed_generation[0][1] == 10:  # closing atomically before finalization would also fix the race
        assert len(loop.requests.met) == 2
        assert second.isoformat() in rows_at_settlement, "the second limit arrived before settlement, yet has no row"


def test_ref4_8_a_refusal_latched_before_the_tick_closes_decides_its_outcome(tmp_path, monkeypatch):
    loop = _bare_loop(tmp_path, keeping=lambda identity, said: note_refusal(tmp_path, identity, said))
    monkeypatch.setattr(loop_module, "sealed_closed", lambda vault: nullcontext([]))
    deliver, received = threading.Event(), threading.Event()
    held_before_close, active_when_received = [], []
    refused = "a" * 64

    def late_refusal():
        assert deliver.wait(5)
        active_when_received.append(loop.requests.limit == 10)
        try:
            loop.requests.at_a_refusal("G6_E_MODEL_REFUSED: refused", refused, loop.requests.generation)
        except _Refused:
            held_before_close.append(loop.requests.refusal)
        finally:
            received.set()

    thread = threading.Thread(target=late_refusal)
    real_close = loop.requests.close

    def close():
        # _run has read its local snapshot; the actual close has not taken the admission lock yet.
        deliver.set()
        assert received.wait(5)
        real_close()

    monkeypatch.setattr(loop.requests, "close", close)

    def cycle(recommended):
        thread.start()
        return {"outcome": "idle"}

    loop._cycle, loop._record = cycle, lambda row: row
    loop.meter = SimpleNamespace(settle=lambda admission, charged: charged,
                                 reading=lambda provider, when: SimpleNamespace(provider=provider, day=1, used=0,
                                                                               target=10, ceiling=10, estimated=False))
    try:
        outcome = loop._run()
    finally:
        deliver.set()
        if thread.ident is not None:
            thread.join(5)
    assert not thread.is_alive()
    assert refused_requests(tmp_path) == {refused}
    if active_when_received == [True]:  # a refusal after an atomic close need not end that tick
        assert held_before_close == [{"said": "G6_E_MODEL_REFUSED: refused", "request": refused}]
        assert outcome.get("code") == CLASSIFIER_STOP, "the refusal latched before close, but the tick returned idle"


def test_ref4_9_another_handle_of_the_same_loop_never_resends_what_the_store_holds_refused(tmp_path):
    domain = domains.WordProblems(tmp_path)
    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, NOW),))
    handles = [ShadowLoop(domain, tmp_path / "loop", settings, proposer=FakeLLM(), now=lambda: NOW) for _ in range(2)]
    assert all(handle.workflow is not None for handle in handles)

    def refuse(system, user):
        raise LLMError("G6_E_MODEL_REFUSED: policy refusal")

    domain.llm._responder = refuse
    for handle in handles:
        try:
            handle.workflow.models["local"].caller.complete("system", "same request")
        except LLMError:
            pass
        assert refused_requests(tmp_path / "loop")
    assert len(domain.llm.calls) == 1, "another handle in the same process sent the already-fsynced refusal again"


# The design, built from the code: one admission, one record, the facts read as the tick closes ----------------------------

def _source_tree(module):
    return ast.parse(inspect.getsource(module))


def _function(tree, qualified: str):
    """The function a dotted name gives (``Class.method`` or ``function``), from a module's tree."""
    node = tree
    for name in qualified.split("."):
        node = next(child for child in ast.walk(node) if isinstance(child, ast.FunctionDef | ast.ClassDef)
                    and child.name == name)
    return node


def _calls_in(node, attribute: str) -> list[ast.Call]:
    return [call for call in ast.walk(node) if isinstance(call, ast.Call)
            and (getattr(call.func, "attr", None) == attribute or getattr(call.func, "id", None) == attribute)]


def test_every_place_a_request_can_leave_reaches_the_one_admission():
    """Built from the code. A request leaves at one of three kinds of boundary: a note (``model_calls.note_model_call``), which
    every metered call observes through ``counting``; a ``Retry-After`` resend, which ``g6.core.llm`` adds to every open
    ``waits`` record and nowhere else; and the start of a host program, which only two places in G6 make, each behind a step
    the watched callers take over. Each of the loop's observers of those reaches ``_Requests.admit``, through
    ``take`` or ``_Gate.sending``, and nothing else in the loop calls it but the start of a call."""
    source = Path(g6.__file__).resolve().parent
    starts, resends = set(), set()
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        where = path.relative_to(source).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                text = ast.unparse(call.func)
                if text == "host_models._run" or (text == "_run" and where == "core/host_models.py"):
                    starts.add((where, node.name))
                if text.endswith("found.append") and "_WAITS" in ast.unparse(node):
                    resends.add((where, node.name))
    assert starts == {("core/host_models.py", "complete"), ("harness/completion_loop.py", "_launch"),
                      ("self_maintenance/usage.py", "_complete_host"), ("self_maintenance/usage.py", "_launch")}
    assert resends == {("core/llm.py", "_post_json")}
    host = _source_tree(host_models)
    complete = _function(host, "_HostCaller.complete")
    order = [ast.unparse(call.func) for call in ast.walk(complete) if isinstance(call, ast.Call)]
    assert order.index("self._command") < order.index("_run")                 # what starts the program is made first
    assert {"_command", "_launch"} <= set(vars(usage.WatchedClaude)) and "_command" in vars(usage.WatchedCodex)
    loop_tree = _source_tree(loop_module)
    observers = [ast.unparse(keyword.value) for call in _calls_in(loop_tree, "counting") for keyword in call.keywords]
    assert observers == ["next_request"]
    assert {(qualified, name) for qualified, name in _callers(loop_tree, {"admit"})} == {
        ("_Requests.take", "admit"), ("_Requests.begin", "admit"), ("_Gate.sending", "admit"),
        ("_Counted.complete.next_request", "admit")}
    assert {qualified for qualified, _ in _callers(loop_tree, {"take"})} == {
        "_Counted.complete.next_request", "_Counted.complete.resend", "_Resends.append"}


def _nodes(tree):
    """Each node of a module's tree, with the qualified name of the function or class it is in."""
    found = []

    def walk(node, where):
        for child in ast.iter_child_nodes(node):
            inner = f"{where}.{child.name}" if where and isinstance(child, ast.FunctionDef | ast.ClassDef) else (
                child.name if isinstance(child, ast.FunctionDef | ast.ClassDef) else where)
            found.append((inner, child))
            walk(child, inner)

    walk(tree, "")
    return found


def _callers(tree, names):
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

    walk(tree, "")
    return found


def test_every_meter_reads_what_it_has_taken_from_the_one_record():
    """Built from the code: no meter holds a count of its own. What a meter has taken and what it has out are views of the
    one record, which only admission and the settling of a call charge, and only admission and a send's outcome change."""
    assert isinstance(inspect.getattr_static(_Requests, "sent"), property)
    assert isinstance(inspect.getattr_static(_Requests, "in_flight"), property)
    assert not hasattr(_Requests, "count")
    tree = _source_tree(loop_module)
    stores = [(where, node.attr) for where, node in _nodes(tree) if isinstance(node, ast.Attribute)
              and isinstance(node.ctx, ast.Store) and node.attr in ("sent", "in_flight", "taken", "out", "books")]
    assert sorted(stores) == [("_Record.__init__", "books"), ("_Record.__init__", "out"), ("_Record.__init__", "taken")]
    assert {qualified for qualified, _ in _callers(tree, {"charge"})} == {"_Requests.admit", "_Requests.settle"}
    changes = {f"_RECORD.out.{name}" for name in ("setdefault", "pop", "popitem", "clear", "update")}
    changed = {where for where, node in _nodes(tree) if (isinstance(node, ast.Call) and ast.unparse(node.func) in changes)
               or (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store | ast.Del)
                   and ast.unparse(node.value) == "_RECORD.out")}
    assert changed == {"_Requests.admit", "_Requests._release_locked"}
    one, two = _Requests(), _Requests()
    one.open(5)
    two.open(5)
    _Counted(Delegates(_Counted(FakeLLM(default="an answer"), two)), one).complete("system", "user")
    assert one.sent == two.sent == 1 == loop_module._RECORD.taken[one][one.generation] == loop_module._RECORD.taken[two][two.generation]


def test_every_path_that_ends_a_tick_reads_what_it_latched_as_it_closes():
    """Built from the code: a tick is opened and closed only in ``ShadowLoop._run``, and it closes first thing in the
    ``finally`` that every way out of its cycle passes, in one step under the lock; what it ends on, the limits it writes and
    the charges it settles are all read from what that step kept, and nothing of the meter's latches is read after it."""
    tree = _source_tree(loop_module)
    opens = {qualified for qualified, _ in _callers(tree, {"open"}) if not qualified.startswith("_Requests")}
    closes = {qualified for qualified, _ in _callers(tree, {"close"}) if not qualified.startswith("_Requests")}
    assert opens == closes == {"ShadowLoop._run"}
    run = _function(tree, "ShadowLoop._run")
    tried = next(node for node in ast.walk(run) if isinstance(node, ast.Try) and node.finalbody)
    final = [ast.unparse(statement) for statement in tried.finalbody]
    # The close comes first, and what it kept is read in the step after it, whatever the close raised (the freeze
    # follow-up keeps what a cycle met before anything that can fail): a close that raised before it ended the tick (its
    # generation unmoved) leaves the tick's latches, read in one step under the lock (``_Requests.latched``).
    closing = tried.finalbody[0]
    assert isinstance(closing, ast.Try) and not closing.handlers and [ast.unparse(s) for s in closing.body] == [
        "self.requests.close()"]
    assert ast.unparse(closing.finalbody[0]) == (
        "ended = self.requests.closed if self.requests.generation != opened else self.requests.latched()")
    body = [ast.unparse(s) for s in run.body]
    opening = body.index("self.requests.open(spend.amount, spend.ends)")
    assert body[opening + 1] == "opened = self.requests.generation", "the tick's generation is read as it opens"
    assert any("ended.met" in statement and "_keep_limit" in statement for statement in final)
    assert final.index(next(s for s in final if "_keep_limit" in s)) < final.index(next(s for s in final if "settle" in s))
    assert all(handler.name in (None, "reached", "exc") for handler in tried.handlers)
    assert not any(isinstance(handler.type, ast.Name) and handler.type.id == "_AtLimit" and handler.name
                   for handler in tried.handlers)
    latches = ("refusal", "limited", "met", "unseen", "stopped", "sent")
    read = [ast.unparse(node) for node in ast.walk(run) if isinstance(node, ast.Attribute) and node.attr in latches
            and ast.unparse(node.value) == "self.requests"]
    assert read == []


class Delegates:
    """A wrapper of a domain's own that hands each request on."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def complete(self, system, user, *, max_tokens=1024):
        return self._inner.complete(system, user, max_tokens=max_tokens)


def _nested(backend, meters):
    """``backend`` metered by ``meters[0]``, and each next meter around the one before it, through a wrapper of its own."""
    caller = _Counted(backend, meters[0])
    for meter in meters[1:]:
        caller = _Counted(Delegates(caller), meter)
    return caller


class _Imports(FakeLLM):
    """A caller that answers, then imports two calls a child of its own made, which pass no boundary."""

    def complete(self, system, user, *, max_tokens=1024):
        answer = super().complete(system, user, max_tokens=max_tokens)
        model_calls.note_child_model_calls(scoped=2, process=2)
        return answer


def _answers_as_claude(monkeypatch, sent):
    monkeypatch.setattr(ClaudeCodeCaller, "locate", lambda self: "fake-claude")

    def run(argv, stdin, *rest):
        sent.append(stdin)
        return 0, json.dumps({"type": "result", "is_error": False, "result": "answer"}), ""

    monkeypatch.setattr(host_models, "_run", run)


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("shared", [True, False])
@pytest.mark.parametrize("kind,places,out", [("note", 1, 1), ("host", 1, 1), ("resend", 2, 2), ("imported", 3, 1)])
def test_a_send_takes_one_place_on_each_meter_around_it_at_any_depth(monkeypatch, host_folders, depth, shared, kind,
                                                                      places, out):
    """Nesting depth 0, 1 and 2, on one meter or one each: a note, a host program, a resend after Retry-After, and a child's
    imported totals each cost what they are, once, on every meter around them, and a reservation of exactly that is enough."""
    sent = []
    if kind == "resend":
        sent = _busy_once(monkeypatch)
        backend = llm.OpenAICompatibleCaller("fake", base_url="http://fake.invalid", api_key=None)
    elif kind == "host":
        _answers_as_claude(monkeypatch, sent)
        backend = ClaudeCodeCaller()
    else:
        backend = (_Imports if kind == "imported" else FakeLLM)(lambda system, user: sent.append(user) or "answer")
    meters = [_Requests()] * (depth + 1) if shared else [_Requests() for _ in range(depth + 1)]
    for meter in set(meters):
        meter.open(places)
    assert _nested(backend, meters).complete("system", "request") == "answer"
    assert len(sent) == out and all(meter.sent == places for meter in meters)
    assert not any(meter.in_flight for meter in meters)


@pytest.mark.parametrize("depth", [0, 1, 2])
def test_a_rejected_note_offsets_nothing_at_any_depth(depth):
    class Child(FakeLLM):
        def complete(self, system, user, *, max_tokens=1024):
            answer = super().complete(system, user, max_tokens=max_tokens)
            model_calls.note_child_model_calls(scoped=2, process=2)
            try:
                super().complete(system, "one request beyond the reservation", max_tokens=max_tokens)
            except LLMError:
                pass
            return answer

    meter = _Requests()
    meter.open(1)
    backend = Child(default="answer")
    assert _nested(backend, [meter] * (depth + 1)).complete("system", "request") == "answer"
    assert len(backend.calls) == 1 and meter.sent == 3


def test_two_threads_of_nested_calls_take_no_more_than_was_reserved():
    meter, started = _Requests(), threading.Barrier(2)
    meter.open(3)
    backend = FakeLLM(lambda system, user: "answer")
    caller, outcomes = _nested(backend, [meter, meter]), []

    def sends(name):
        started.wait(5)
        for number in range(3):
            try:
                outcomes.append(caller.complete("system", f"{name} {number}"))
            except LLMError as stopped:
                outcomes.append(str(stopped))

    threads = [threading.Thread(target=sends, args=(name,)) for name in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert outcomes.count("answer") == len(backend.calls) == meter.sent == 3
    assert all("TICK_BUDGET_SPENT" in outcome for outcome in outcomes if outcome != "answer")


def test_a_send_made_inside_a_watched_send_on_a_thread_of_its_own_never_waits_for_it(monkeypatch, host_folders):
    """The identical request, sent from a thread the program's own step started with the call's context, while the send it
    is made inside is still out: waiting for that send would wait for itself."""
    monkeypatch.setattr(ClaudeCodeCaller, "locate", lambda self: "fake-claude")
    meter, sent, done = _Requests(), [], []
    caller = _Counted(ClaudeCodeCaller(), meter)

    def run(argv, stdin, *rest):
        sent.append(stdin)
        if len(sent) == 1:
            context = contextvars.copy_context()
            inside = threading.Thread(target=context.run, args=(lambda: done.append(caller.complete("system", "same")),))
            inside.start()
            inside.join(10)
            assert not inside.is_alive(), "the send inside waited for the send it was made inside of"
        return 0, json.dumps({"type": "result", "is_error": False, "result": "answer"}), ""

    monkeypatch.setattr(host_models, "_run", run)
    assert caller.complete("system", "same") == "answer" and done == ["answer"] and len(sent) == 2


def test_unwatched_identical_sends_of_one_call_on_two_threads_go_out_and_are_each_charged():
    """The limit of the wait: an unwatched send has no outcome of its own before its call ends, so one of the same call on
    another thread does not wait for it, which would wait for the call itself. Both are charged."""
    meter, both = _Requests(), threading.Barrier(2)
    meter.open(5)
    backend = FakeLLM(lambda system, user: both.wait(5) and "answer" or "answer")

    class FansOut:
        def complete(self, system, user, *, max_tokens=1024):
            answers, threads = [], []
            for _ in range(2):
                context = contextvars.copy_context()
                threads.append(threading.Thread(target=context.run, args=(lambda: answers.append(
                    backend.complete(system, user)),)))
                threads[-1].start()
            for thread in threads:
                thread.join(10)
            return answers[0]

    assert _Counted(FansOut(), meter).complete("system", "same") == "answer"
    assert len(backend.calls) == meter.sent == 2


@pytest.mark.parametrize("stop", ["refusal", "limit"])
def test_a_stop_on_a_send_nested_two_deep_ends_every_tick_around_it(stop):
    kept = []
    meters = [_Requests(keeping=lambda request, said, n=n: kept.append(n)) for n in range(3)]
    for meter in meters:
        meter.open(10)
    said = f"{MODEL_REFUSED}: policy" if stop == "refusal" else f"{RATE_LIMITED}: HTTP 429 from provider"

    def stops(system, user):
        raise LLMError(said)

    with pytest.raises(_Refused if stop == "refusal" else _AtLimit):
        _nested(FakeLLM(stops), meters).complete("system", "request")
    for meter in meters:
        assert (meter.refusal is not None) if stop == "refusal" else (meter.limited is not None and len(meter.met) == 1)
    if stop == "refusal":
        assert sorted(kept) == [0, 1, 2] and all(meter.refused == {_request_digest("system", "request")} for meter in meters)


def test_a_handle_reads_what_was_refused_from_the_store_at_every_send(tmp_path):
    """Another process keeps a refusal in the folder's store while this handle is open, or the store becomes unreadable:
    the next send of this handle reads the store as it is let in."""
    domain = domains.WordProblems(tmp_path)
    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, NOW),))
    loop = ShadowLoop(domain, tmp_path / "loop", settings, proposer=FakeLLM(), now=lambda: NOW)
    caller = loop.workflow.models["local"].caller
    assert note_refusal(loop.root, _request_digest("system", "kept elsewhere"), "refused in another process")
    with pytest.raises(LLMError, match=CLASSIFIER_STOP):
        caller.complete("system", "kept elsewhere")
    (loop.root / REFUSED_FILE).unlink()
    (loop.root / REFUSED_FILE).mkdir()
    with pytest.raises(LLMError, match="G6_E_LOOP_RECORDS_UNREADABLE"):
        caller.complete("system", "anything else")
    assert domain.llm.calls == []


STEPS = ("before the send is let in", "while it is out", "swallowed by a wrapper", "after its call returned",
         "as the tick closes", "after the tick closed")


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("fact", ["refusal", "limit"])
def test_a_limit_and_a_refusal_at_each_step_from_admission_to_close(tmp_path, monkeypatch, host_folders, fact, step):
    """Wherever the provider's statement comes, up to the step that closes the tick, the tick ends with it, the refusal is in
    the store and the limit on the record before the charges settle; once the tick has closed it ends nothing of it, and what
    was refused is still never sent again."""
    loop = _bare_loop(tmp_path, keeping=lambda identity, said: note_refusal(tmp_path, identity, said),
                      on_record=lambda: refused_requests(tmp_path))
    monkeypatch.setattr(loop_module, "sealed_closed", lambda vault: nullcontext([]))
    said = f"{MODEL_REFUSED}: policy" if fact == "refusal" else f"{RATE_LIMITED}: HTTP 429 from provider"
    result = ({"stop_reason": "refusal"} if fact == "refusal" else {"api_error_status": 429})
    printed = json.dumps({"type": "result", "is_error": True, "result": "", **result})
    monkeypatch.setattr(ClaudeCodeCaller, "locate", lambda self: "fake-claude")
    monkeypatch.setattr(host_models, "_run", lambda argv, stdin, *rest: (1, printed, ""))
    refused = _request_digest("system", "refused request")

    def stops(system, user):
        raise LLMError(said)

    def tell():
        try:
            loop.requests.provider_said(said, refused, loop.requests.generation)
        except (_Refused, _AtLimit):
            pass

    class CatchesAll(Delegates):
        def complete(self, system, user, *, max_tokens=1024):
            try:
                return self._inner.complete(system, user, max_tokens=max_tokens)
            except BaseException:
                return "an answer"

    heard, late = threading.Event(), []

    class AnswersThenHearsLate:
        """Answers, and has the provider's reply read on a thread of its own, in the call's context, once ``heard`` is set."""

        def complete(self, system, user, *, max_tokens=1024):
            answer, context = FakeLLM(default="an answer").complete(system, user), contextvars.copy_context()
            late.append(threading.Thread(target=context.run, args=(lambda: heard.wait(5) and _quietly(
                usage.WatchedClaude()._reply, 1, printed, "", None, ("system", "refused request")),)))
            late[-1].start()
            return answer

    def cycle(recommended):
        if step == "before the send is let in":
            tell()
            _Counted(FakeLLM(default="sent after the stop"), loop.requests).complete("system", "another")
        elif step == "while it is out":
            _Counted(FakeLLM(stops), loop.requests).complete("system", "refused request")
        elif step == "swallowed by a wrapper":
            _Counted(CatchesAll(ClaudeCodeCaller()), loop.requests).complete("system", "refused request")
        elif step == "after its call returned":
            assert _Counted(AnswersThenHearsLate(), loop.requests).complete("system", "asked") == "an answer"
            heard.set()                                   # the call has returned, and the tick is still open
            late[0].join(10)
        return {"outcome": "idle"}

    real_close = loop.requests.close

    def close():
        if step == "as the tick closes":
            tell()
        real_close()
        if step == "after the tick closed":
            tell()

    monkeypatch.setattr(loop.requests, "close", close)
    rows_at_settlement = []

    def settle(admission, charged):
        rows_at_settlement.extend(_limit_rows(loop))
        return charged

    loop._cycle, loop._record, loop._row = cycle, (lambda row: row), (lambda: {"outcome": "idle"})
    loop.meter = SimpleNamespace(settle=settle, limit_reached=lambda provider, until: None,
                                 reading=lambda provider, when: SimpleNamespace(provider=provider, day=1, used=0,
                                                                               target=10, ceiling=10, estimated=False))
    got = loop._run()
    code = got.get("code") or got.get("warning_card", {}).get("code")
    if step == "after the tick closed":
        assert code not in (CLASSIFIER_STOP, BUDGET_WAIT) and rows_at_settlement == []
    elif fact == "refusal":
        assert code == CLASSIFIER_STOP
    else:
        assert code == BUDGET_WAIT and rows_at_settlement == [(NOW + REFRESH).isoformat()]
    if fact == "refusal":                     # kept once, and the store alone bars it from here: this process's set is cleared
        stored = refused_requests(tmp_path)
        assert len(stored) == 1 and (step == "swallowed by a wrapper" or stored == {refused})
        loop.requests.open(10)
        loop.requests.refused.clear()
        ran = []
        monkeypatch.setattr(host_models, "_run", lambda argv, stdin, *rest: ran.append(stdin) or (
            0, json.dumps({"type": "result", "is_error": False, "result": "sent again"}), ""))
        backend = FakeLLM(default="sent again")
        again = _Counted(ClaudeCodeCaller() if step == "swallowed by a wrapper" else backend, loop.requests)
        with pytest.raises(LLMError, match=CLASSIFIER_STOP):
            again.complete("system", "refused request")
        assert backend.calls == [] and ran == []


def _quietly(call, *args):
    try:
        return call(*args)
    except BaseException as exc:
        return exc


class Killed(BaseException):
    """The process dying at that instant."""


START = datetime(2026, 10, 26, 8, tzinfo=UTC)
RESTART = START + timedelta(days=2)
ONE_CYCLE = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),),
                         method=Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1),
                         audit_per_tick=60)


def _handle(tmp_path, domain):
    return ShadowLoop(domain, tmp_path / "loop", ONE_CYCLE, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: RESTART, clock=domains.Clock())


def _stops_the_third(domain, said):
    seen, tutor = [], domain.tutor

    def respond(system, user):
        seen.append((system, user))
        if len(seen) == 3:
            raise LLMError(said)
        return tutor(system, user)

    domain.llm._responder = respond
    return seen


@pytest.mark.parametrize("dies_after", ["note_refusal", "settle", "cycle", "pause"])
def test_a_refusal_survives_a_stop_and_a_restart_between_every_two_writes(tmp_path, monkeypatch, dies_after):
    """A tick that meets a refusal writes, in order: the refusal to its store, its charges, its cycle and its pause. Killed
    after any one of them, a restart never sends the refused request again."""
    domain = domains.WordProblems(tmp_path)
    loop = _handle(tmp_path, domain)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    seen = _stops_the_third(domain, f"{MODEL_REFUSED}: policy")
    targets = {"note_refusal": (loop_module, "note_refusal"), "settle": (loop.meter, "settle"), "cycle": (loop.log, "cycle"),
               "pause": (loop.log, "pause")}
    owner, name = targets[dies_after]
    real = getattr(owner, name)

    def then_dies(*args, **kwargs):
        real(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as dying:
        dying.setattr(owner, name, then_dies)
        with pytest.raises(Killed):
            loop.tick()
    refused = seen[2]
    domain.llm._responder = lambda system, user: seen.append((system, user)) or domain.tutor(system, user)
    again = _handle(tmp_path, domain)
    if again.log.paused() or again._must_stop():
        assert again.resume("Ada")["completion_state"] == "qualified-draft"
    again.tick()
    assert seen.count(refused) == 1 and len(seen) > 3


@pytest.mark.parametrize("dies_after", ["limit", "settle"])
def test_a_limit_survives_a_stop_and_a_restart_between_every_two_writes(tmp_path, monkeypatch, dies_after):
    """A tick that meets a limit writes it to its records and then settles its charges. Killed after either, a restart
    waits the limit out and sends nothing."""
    domain = domains.WordProblems(tmp_path)
    loop = _handle(tmp_path, domain)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    seen = _stops_the_third(domain, f"{RATE_LIMITED}: HTTP 429 from https://model")
    owner, name = (loop.log, "limit") if dies_after == "limit" else (loop.meter, "settle")
    real = getattr(owner, name)

    def then_dies(*args, **kwargs):
        real(*args, **kwargs)
        raise Killed()

    with monkeypatch.context() as dying:
        dying.setattr(owner, name, then_dies)
        with pytest.raises(Killed):
            loop.tick()
    before = len(seen)
    got = _handle(tmp_path, domain).tick()
    assert got["warning_card"]["code"] == BUDGET_WAIT and got["wait_until"] == (RESTART + REFRESH).isoformat()
    assert len(seen) == before


def test_a_ticks_limits_stand_until_the_furthest_of_them_and_a_later_ticks_replace_them(tmp_path):
    """A tick's limits are on the record in the order met, which need not be the order they end in: a restart, and this
    process's meter, wait for the furthest of them. A later tick's row, or a row of no tick, replaces them, as the latest
    always did (test_loop_envelope.py)."""
    log = CycleLog(tmp_path / CYCLE_LOG)
    far, near, later = NOW + timedelta(minutes=15), NOW + timedelta(seconds=90), NOW + timedelta(minutes=5)
    log.limit("codex", far.isoformat(), "one tick")
    log.limit("codex", near.isoformat(), "one tick")
    log.limit("claude", near.isoformat())
    assert CycleLog(tmp_path / CYCLE_LOG).limits() == {"codex": far, "claude": near}
    log.limit("codex", later.isoformat(), "the next tick")
    assert CycleLog(tmp_path / CYCLE_LOG).limits() == {"codex": later, "claude": near}
    meter = BudgetMeter((ProviderWindow("codex", 1000, NOW - timedelta(days=1)),), store=OperatorStore(":memory:"))
    meter.limit_reached("codex", far)
    meter.limit_reached("codex", near)
    assert meter.reserve("codex", NOW, 1).wait_until == far


def test_the_rows_a_tick_writes_are_its_own_and_in_the_order_it_met_them(tmp_path, monkeypatch):
    loop = _bare_loop(tmp_path)
    monkeypatch.setattr(loop_module, "sealed_closed", lambda vault: nullcontext([]))
    met = [NOW + timedelta(minutes=15), NOW + timedelta(seconds=90)]

    def cycle(recommended):
        for until in met:
            try:
                loop.requests.met_a_limit(until, f"{RATE_LIMITED}: a limit")
            except _AtLimit:
                pass

    loop._cycle, loop._row = cycle, (lambda: {"outcome": "idle"})
    loop.meter = SimpleNamespace(settle=lambda admission, charged: charged, limit_reached=lambda provider, until: None)
    for _ in range(2):
        assert loop._run()["warning_card"]["code"] == BUDGET_WAIT
    rows = [entry["body"] for entry in loop.log._chain.entries() if entry["kind"] == "limited"]
    assert [row["until"] for row in rows] == [until.isoformat() for until in met] * 2
    assert rows[0]["tick"] == rows[1]["tick"] != rows[2]["tick"] == rows[3]["tick"]
    assert loop.log.limits() == {"codex": met[0]}


# Review 1 of the refusals follow-up: its two findings, as reported (review-refu1, tests/_review/test_finding_1.py and
# test_finding_2.py), each with the root of its class -----------------------------------------------------------------------

UNNAMED = "G6_E_LOOP_UNNAMED_REQUEST"


def _review_handle(root):
    """A handle as review refu1's first finding builds one: enough of a loop to send through and to keep a refusal."""
    loop = ShadowLoop.__new__(ShadowLoop)
    loop.root = root
    loop.log = CycleLog(root / CYCLE_LOG)
    loop.cannot_record = loop.unreadable = ""
    loop.requests = _Requests(halted=loop._halted, keeping=loop._keep_refusal, on_record=lambda: refused_requests(root))
    return loop


def _disk_full(*args, **kwargs):
    raise OSError("disk full: neither the refusal nor the fallback stop can be written")


def _refuses(system, user):
    raise LLMError(f"{MODEL_REFUSED}: policy")


def test_refu1_1_a_refusal_no_file_would_keep_stops_another_handle_of_the_folder(tmp_path, monkeypatch):
    """The review's test as reported, in a folder of the test's own."""
    monkeypatch.setattr(stop, "engaged", lambda: None)
    first, second = _review_handle(tmp_path), _review_handle(tmp_path)
    monkeypatch.setattr(loop_module, "note_refusal", _disk_full)
    monkeypatch.setattr(loop_module, "ask_to_stop", _disk_full)
    try:
        _Counted(FakeLLM(_refuses), first.requests).complete("system", "request")
    except LLMError:
        pass
    assert first.cannot_record
    assert refused_requests(tmp_path) == set()
    backend = FakeLLM(default="sent again")
    try:
        _Counted(backend, second.requests).complete("system", "request")
    except LLMError:
        pass
    assert backend.calls == [], "the second handle resent the refusal that this process could not persist"


def test_refu1_2_a_function_that_sends_is_known_by_what_it_sent():
    """The review's test as reported."""
    sent = []

    def transport(system, user):
        note_model_call()
        sent.append((system, user))
        raise LLMError(f"{MODEL_REFUSED}: policy")

    class CanonicalCaller(FakeLLM):
        def complete(self, system, user, *, max_tokens=1024):
            return transport(system, "canonical request")

    meter = _Requests()
    caller = _Counted(CanonicalCaller(), meter)
    assert caller_problems({"canonical": caller.inner}) == []
    assert sender_problems(caller.inner, "claude") == []
    for user in ("first wrapper input", "second wrapper input"):
        try:
            caller.complete("system", user)
        except LLMError:
            pass
    assert sent == [("system", "canonical request")], "the same refused bytes were sent again under a wrapper alias"


# Finding 1's class (state_not_durable): what only this process knows of a folder is the process's, for every handle -------

def _open(tmp_path, domain, folder="loop"):
    return ShadowLoop(domain, tmp_path / folder, LoopSettings(windows=(ProviderWindow("claude", 1_000_000, NOW),)),
                      proposer=FakeLLM(), now=lambda: NOW)


def _latch(how, first, domain, monkeypatch):
    """The cannot-record stop, latched on ``first`` by each way there is to it, with no ``STOP`` file to stand for it."""
    monkeypatch.setattr(loop_module, "ask_to_stop", _disk_full)
    if how == "refusal":                         # a refusal no file would keep
        monkeypatch.setattr(loop_module, "note_refusal", _disk_full)
        domain.llm._responder = _refuses
        with pytest.raises(LLMError, match=MODEL_REFUSED):
            first.workflow.models["local"].caller.complete("system", "refused request")
        domain.llm._responder = domain.tutor
    elif how == "lost cycles":                   # cycles the log would not take, as many in a row as stop the loop
        for _ in range(MAX_FAILED_CYCLES):
            first.lost_cycle(OSError("the log is full"))
    else:                                        # a cycle that held a refusal, which the log would not take
        first.lost_cycle(OSError("the log is full"), refusal={"said": f"{MODEL_REFUSED}: policy", "request": "a" * 64})
    assert first.cannot_record and not (first.root / "STOP").exists()


@pytest.mark.parametrize("how", ["refusal", "lost cycles", "refusal cycle"])
def test_the_cannot_record_stop_is_the_processs_on_every_handle_of_the_folder(tmp_path, monkeypatch, how):
    """Whichever way it is latched, on one handle: that handle, one of the same folder open before it, and one made after it
    each send nothing more, whatever they are asked to send, and say why alike; a handle of another folder sends on."""
    domain = domains.WordProblems(tmp_path)
    first, open_before = _open(tmp_path, domain), _open(tmp_path, domain)
    _latch(how, first, domain, monkeypatch)
    made_after, elsewhere = _open(tmp_path, domain), _open(tmp_path, domain, "another loop")
    calls = len(domain.llm.calls)
    for handle in (first, open_before, made_after):
        assert handle.stopped_by() == first.cannot_record
        # this process's own stop for the folder, by its own code (``ShadowLoop._stop``), as the freeze and stop
        # follow-ups rule
        with pytest.raises(LLMError, match="G6_E_LOOP_CANNOT_RECORD"):
            handle.workflow.models["local"].caller.complete("system", "another request")
    assert len(domain.llm.calls) == calls
    assert elsewhere.stopped_by() == "" and elsewhere.workflow.models["local"].caller.complete("system", "another request")
    assert len(domain.llm.calls) == calls + 1


def test_cycles_two_handles_of_a_folder_lost_are_one_count(tmp_path, monkeypatch):
    """The count that stops the loop is the process's for the folder: cycles lost on two handles in turn reach it."""
    domain = domains.WordProblems(tmp_path)
    one, two = _open(tmp_path, domain), _open(tmp_path, domain)
    monkeypatch.setattr(loop_module, "ask_to_stop", _disk_full)
    one.lost_cycle(OSError("the log is full"))
    two.lost_cycle(OSError("the log is full"))
    assert one.lost_cycles == two.lost_cycles == 2 and not one.cannot_record
    one.lost_cycle(OSError("the log is full"))
    assert two.cannot_record == one.cannot_record and "3 of the 3" in two.cannot_record


def test_a_refusal_this_process_met_stays_refused_on_every_handle_whatever_the_store_says(tmp_path):
    """Kept in the store and then lost from it: every handle of the folder in this process, one made after too, still refuses
    it, and a store that cannot be read sends nothing either."""
    domain = domains.WordProblems(tmp_path)
    first, other = _open(tmp_path, domain), _open(tmp_path, domain)
    domain.llm._responder = _refuses
    with pytest.raises(LLMError, match=MODEL_REFUSED):
        first.workflow.models["local"].caller.complete("system", "refused request")
    domain.llm._responder = domain.tutor
    (tmp_path / "loop" / REFUSED_FILE).unlink()             # nothing on disk says it was refused
    calls = len(domain.llm.calls)
    for handle in (first, other, _open(tmp_path, domain)):
        with pytest.raises(LLMError, match=CLASSIFIER_STOP):
            handle.workflow.models["local"].caller.complete("system", "refused request")
    (tmp_path / "loop" / REFUSED_FILE).mkdir()              # nor can anything be read there
    for handle in (first, other):
        with pytest.raises(LLMError, match="G6_E_LOOP_RECORDS_UNREADABLE"):
            handle.workflow.models["local"].caller.complete("system", "another request")
    assert len(domain.llm.calls) == calls


def test_the_process_knows_a_folder_however_its_path_is_spelled(tmp_path, monkeypatch):
    monkeypatch.setattr(stop, "engaged", lambda: None)
    monkeypatch.setattr(loop_module, "note_refusal", _disk_full)
    monkeypatch.setattr(loop_module, "ask_to_stop", _disk_full)
    folder = tmp_path / "loop"
    (folder / "sub").mkdir(parents=True)
    first = _review_handle(folder)
    with pytest.raises(LLMError, match=MODEL_REFUSED):
        _Counted(FakeLLM(_refuses), first.requests).complete("system", "request")
    spellings = [folder / "sub" / ".."]
    if os.name == "nt":                                       # the case Windows ignores, and a junction to the folder
        import _winapi

        _winapi.CreateJunction(str(folder), str(tmp_path / "junction"))
        spellings += [Path(str(folder).upper()), tmp_path / "junction"]
    else:
        (tmp_path / "link").symlink_to(folder, target_is_directory=True)
        spellings.append(tmp_path / "link")
    for spelled in spellings:
        backend = FakeLLM(default="sent again")
        with pytest.raises(LLMError, match="G6_E_LOOP_CANNOT_RECORD"):     # this process's own stop, by its own code
            _Counted(backend, _review_handle(spelled).requests).complete("system", "another request")
        assert backend.calls == []


def test_a_limit_one_handle_met_holds_another_handle_of_the_folder_at_its_next_tick(tmp_path):
    """A limit is on the record as it is met (the log), and each handle reads the record before it reserves a tick."""
    domain = domains.WordProblems(tmp_path)
    first, other = _handle(tmp_path, domain), _handle(tmp_path, domain)
    assert first.enrol("Ada")["completion_state"] == "qualified-draft"
    seen = _stops_the_third(domain, f"{RATE_LIMITED}: HTTP 429 from https://model")
    assert first.tick()["warning_card"]["code"] == BUDGET_WAIT
    before = len(seen)
    got = other.tick()
    assert got["warning_card"]["code"] == BUDGET_WAIT and got["wait_until"] == (RESTART + REFRESH).isoformat()
    assert len(seen) == before


def _restart(monkeypatch):
    """A new process: nothing of this one's record of sends and folders, and only what the folder's files hold."""
    monkeypatch.setattr(loop_module, "_RECORD", loop_module._Record())


@pytest.mark.parametrize("written", ["nothing", "the stop", "the cycle"])
def test_after_a_restart_a_refusal_no_file_would_keep_is_refused_only_if_the_folders_files_hold_it(
        tmp_path, monkeypatch, written):
    """The process holds the stop and what was refused, so a restart has only what the folder's files hold: the ``STOP`` its
    stop asked for, or the cycle a tick then wrote, which holds the refused request. With nothing written, a restart sends it
    again: the limit of a refusal no file would take."""
    domain = domains.WordProblems(tmp_path)
    loop = _handle(tmp_path, domain)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    monkeypatch.setattr(loop_module, "note_refusal", _disk_full)
    if written != "the stop":
        monkeypatch.setattr(loop_module, "ask_to_stop", _disk_full)
    if written == "the cycle":                                 # a tick meets it and its cycle is written; then it dies
        seen = _stops_the_third(domain, f"{MODEL_REFUSED}: policy")
        real = loop.log.cycle

        def then_dies(row):
            real(row)
            raise Killed()

        monkeypatch.setattr(loop.log, "cycle", then_dies)
        with pytest.raises(Killed):
            loop.tick()
        refused = seen[2]
    else:                                                      # between ticks, where no cycle is written
        domain.llm._responder = _refuses
        with pytest.raises(LLMError, match=MODEL_REFUSED):
            loop.workflow.models["local"].caller.complete("system", "refused request")
        refused = ("system", "refused request")
    domain.llm._responder = domain.tutor
    assert loop.cannot_record and _handle(tmp_path, domain).stopped_by() == loop.cannot_record   # this process: stopped
    _restart(monkeypatch)
    again = _handle(tmp_path, domain)
    caller, calls = again.workflow.models["local"].caller, len(domain.llm.calls)
    if written == "the stop":
        assert "would not keep it" in again.stopped_by()
        with pytest.raises(LLMError, match="G6_E_LOOP_STOPPED"):
            caller.complete(*refused)
        assert len(domain.llm.calls) == calls
    elif written == "the cycle":
        assert again.stopped_by() == ""
        with pytest.raises(LLMError, match=CLASSIFIER_STOP):
            caller.complete(*refused)
        assert len(domain.llm.calls) == calls and caller.complete("system", "another request")
    else:
        assert again.stopped_by() == "" and refused_requests(again.root) == set()
        assert caller.complete(*refused) and len(domain.llm.calls) == calls + 1


#: Each attribute a handle of the loop sets on itself, and where what it holds is kept.
HANDLE_STATE = {
    # the process's, for the folder, in the one record (``_Folder``): every handle of the folder reads it, one made after too
    "cannot_record": "process", "lost_cycles": "process",
    # what the handle was given, or made from it once
    "domain": "given", "settings": "given", "_now": "given", "domain_file": "given", "_read_now": "given", "_clock": "given",
    "root": "given", "beat_fields": "given", "requests": "given", "_proposer": "given", "provider": "given",
    "problems": "given", "cut": "given", "workflow": "given",
    # whether this handle beats: set as it is made, and false only where a person's command made it
    "beats": "given",
    # the folder's files, read again before each operation (``_exclusive``), each reservation (``_reserve``) or each admission
    "log": "files", "mh": "files", "meter": "files", "unreadable": "files",
    # the stop reader's parse of the folder's records, which stands only for the very bytes it reads again at every
    # admission (``stop_in_force``'s ``known``): what decides is the files
    "_records_known": "files",
    # one tick's working state, which ends with the tick
    "evaluating": "tick", "tuned": "tick", "_rca": "tick", "_tuning_failed": "tick",
    "_began": "tick", "_met": "tick", "_written": "tick", "_frozen_seen": "tick",
    # a count a row reports, which stops nothing
    "waited": "reported",
}


def test_every_fact_a_handle_holds_is_the_processs_the_folders_files_or_its_own():
    """Built from the code. Each attribute a handle sets on itself is listed with where it is kept, and a new one fails here
    until it is. What only this process knows of a folder, and what stops its sends, is the process's: a view of the one record
    for the folder (``_Folder``), kept there however the folder's path is spelled. What stops a send is read in
    ``ShadowLoop._halted`` and ``stopped_by``, at every admission and by the runner, from nothing a handle holds alone. A
    meter's refused requests are read, at every admission, with what the process holds for the folder, and a limit from the
    record before each tick reserves."""
    tree = _source_tree(loop_module)
    assigned = {node.attr for node in ast.walk(_function(tree, "ShadowLoop")) if isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Store) and ast.unparse(node.value) == "self"}
    # No method stores the folder's stop through the handle: each stores it on the record it has found, so the stop
    # never waits on a second lookup (``_stop_folder``, ``lost_cycle``); the setter is all that looks the record up.
    assert assigned == set(HANDLE_STATE) - {"cannot_record"}
    for name in (name for name, kept in HANDLE_STATE.items() if kept == "process"):
        held = inspect.getattr_static(ShadowLoop, name)
        assert isinstance(held, property) and "self._here." in inspect.getsource(held.fget)
    assert "_RECORD.folder(self.root)" in inspect.getsource(inspect.getattr_static(ShadowLoop, "_here").fget)
    # What stops a send is read in ``_halted``, ``stopped_by`` and the stop reader both call (``_stop``); a read through
    # ``getattr(self, "<name>", ...)`` is a read of that attribute as surely as ``self.<name>`` is.
    readers = [_function(tree, qualified) for qualified in ("ShadowLoop._halted", "ShadowLoop.stopped_by", "ShadowLoop._stop")]
    read = {node.attr for reader in readers for node in ast.walk(reader) if isinstance(node, ast.Attribute)
            and ast.unparse(node.value) == "self" and node.attr in HANDLE_STATE}
    read |= {node.args[1].value for reader in readers for node in ast.walk(reader) if isinstance(node, ast.Call)
             and ast.unparse(node.func) == "getattr" and ast.unparse(node.args[0]) == "self"
             and isinstance(node.args[1], ast.Constant)}
    assert read <= set(HANDLE_STATE), f"a stop reader reads what the handle list does not classify: {read - set(HANDLE_STATE)}"
    assert read and {HANDLE_STATE[name] for name in read} <= {"process", "files", "given"}
    init = ast.unparse(_function(tree, "ShadowLoop.__init__"))
    assert "self._keep_refusal, self._on_record)" in init
    assert "refusal_rows(self.root)" in ast.unparse(_function(tree, "ShadowLoop._on_record"))
    assert "| self._here.refused |" in ast.unparse(_function(tree, "ShadowLoop._on_record"))

    def statements(qualified):                  # a function's statements, its docstring left out
        return [ast.unparse(s) for s in _function(tree, qualified).body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]

    reserve = statements("ShadowLoop._reserve")
    limits = next(i for i, s in enumerate(reserve) if "self.log.limits()" in s)
    assert limits < next(i for i, s in enumerate(reserve) if "self.meter.reserve(" in s)
    # The disk is tried first and waits on no record of the folder. The folder's record holds the refusal once the
    # durable write has ended, whatever became of it, so no handle reads it there before it is on the disk; a lookup that
    # fails is no exception here (``_found`` gives ``None``), and the stop below is reached whatever the hold did.
    keeps = statements("ShadowLoop._keep_refusal")
    assert keeps[:2] == ["kept: bool | None = None", "here = None"] and len(keeps) == 3
    writes = [node for node in _function(tree, "ShadowLoop._keep_refusal").body
              if isinstance(node, ast.Try) and "note_refusal(self.root, request, said)" in ast.unparse(node.body)]
    assert len(writes) == 1 and len(writes[0].finalbody) == 1 and isinstance(writes[0].finalbody[0], ast.Try)
    assert "self._here" not in ast.unparse(writes[0].body) and "_found" not in ast.unparse(writes[0].body)
    held, stopped = writes[0].finalbody[0].body, writes[0].finalbody[0].finalbody
    assert ast.unparse(held[0]) == "here = self._found() or self._found()"
    assert "here.refused.add(request)" in ast.unparse(held[-1]) and "note_refusal" not in ast.unparse(held)
    assert [ast.unparse(step.test) for step in stopped] == ["kept is False or (kept and here is None)"]
    assert ast.unparse(stopped[0].body[0]) == "self._stop_folder(here, request, said, kept)"


# Finding 2's class (guard_covers_less_than_it_guards): a send is known by what it sent, whoever sends it -------------------

class _Fixed(FakeLLM):
    """A caller the loop accepts that sends one fixed request whatever it is asked, through ``send``, as finding 2's does."""

    def __init__(self, send, sent):
        super().__init__()
        self.send, self.sent = send, sent

    def complete(self, system, user, *, max_tokens=1024):
        return self.send(self, system, "canonical request")

    def deliver(self, pair):
        """A step of its own that sends, naming no system and user, which a function of its own calls."""
        return _noted_and_refused(self, pair)


def _noted_and_refused(caller, pair):
    note_model_call()
    caller.sent.append(pair)
    raise LLMError(f"{MODEL_REFUSED}: policy")


def _by_function(caller, system, user):
    return _noted_and_refused(caller, (system, user))


class _Transport:
    def send(self, caller, system, user):
        return _noted_and_refused(caller, (system, user))


def _on_a_thread(caller, system, user):
    """The send made on a thread of the wrapper's own, which it handed its context to, and its failure raised here."""
    failed = []
    context = contextvars.copy_context()
    worker = threading.Thread(target=context.run, args=(lambda: failed.append(_quietly(_by_function, caller, system, user)),))
    worker.start()
    worker.join(10)
    raise failed[0]


def _between(caller, system, user):
    """A function of the sender's own, between two of its frames: the one that names what is sent."""
    return caller.deliver((system, user))


SHAPES = {"a function": _by_function, "a lambda": lambda caller, system, user: _noted_and_refused(caller, (system, user)),
          "a method of another object": lambda caller, system, user: _Transport().send(caller, system, user),
          "a function on a thread of its own": _on_a_thread, "a function between the sender's own frames": _between}


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_refused_request_is_known_by_the_innermost_frame_that_names_it_whoever_sends_it(shape, depth):
    """Whatever sends it, a function, a lambda, a method of an object of its own, or one on a thread of the wrapper's own,
    behind a wrapper whose input varies and at any depth of metered calls: the refused bytes leave once."""
    sent = []
    meters = [_Requests() for _ in range(depth + 1)]
    caller = _nested(_Fixed(SHAPES[shape], sent), meters)
    for user in ("first wrapper input", "second wrapper input", "third wrapper input"):
        _quietly(caller.complete, "system", user)
    assert sent == [("system", "canonical request")]
    assert all(meter.refused == {_request_digest("system", "canonical request")} for meter in meters)


def test_a_refusal_raised_in_a_function_binds_the_send_it_raised_for_among_several(monkeypatch):
    """Two sends from functions in one call, the second refused: the refusal binds the second's request, not the first's, and
    not none."""
    def answered(system, user):
        note_model_call()
        return "an answer"

    def refused(system, user):
        note_model_call()
        raise LLMError(f"{MODEL_REFUSED}: policy")

    class TwoSends(FakeLLM):
        def complete(self, system, user, *, max_tokens=1024):
            answered("system", "first request")
            return refused("system", "second request")

    meter = _Requests()
    meter.open(10)
    with pytest.raises(_Refused):
        _Counted(TwoSends(), meter).complete("system", "asked")
    assert meter.refused == {_request_digest("system", "second request")}


def test_a_resend_a_function_makes_is_known_by_what_it_sends_again(monkeypatch):
    sent = _busy_once(monkeypatch)
    seen = []

    class Admits(_Requests):
        def admit(self, *args, **kwargs):
            record = super().admit(*args, **kwargs)
            if record is not None:
                seen.append(record.identity)
            return record

    def by_http(caller, system, user):
        llm._start_model_call()
        return str(llm._post_json("https://model.invalid/v1/chat/completions", {"messages": [system, user]}, {}, 5))

    _Counted(_Fixed(by_http, []), Admits()).complete("system", "first wrapper input")
    assert len(sent) == 2 and seen == [_request_digest("system", "canonical request")] * 2


class _OtherNames(FakeLLM):
    """A caller the loop accepts whose frames name what it sends by other names."""

    def complete(self, instructions, prompt, *, max_tokens=1024):
        note_model_call()
        self.calls.append((instructions, prompt))
        return "an answer"


class _NotText(FakeLLM):
    """One whose innermost frame that names a system and a user names a user that is not text."""

    def complete(self, system, user, *, max_tokens=1024):
        return self._send(system, [{"role": "user", "content": user}])

    def _send(self, system, user):
        note_model_call()
        self.calls.append((system, str(user)))
        return "an answer"


@pytest.mark.parametrize("ticking", [False, True])
@pytest.mark.parametrize("kind", [_OtherNames, _NotText])
def test_a_request_no_frame_names_is_not_sent_and_takes_no_place(kind, ticking):
    """A sender the loop accepts, whose own frames and the functions it calls name no system and user as text, behind a
    wrapper of a domain's own that does: it is not known by what the wrapper was asked, but refused with a stable code before
    it goes out or takes a place. In a tick it leaves the tick as a sender the loop may not send through does; outside one it
    is a failed request."""
    backend, meter = kind(), _Requests()
    assert caller_problems({"accepted": backend}) == [] and sender_problems(backend, "claude") == []
    if ticking:
        meter.open(5)
    with pytest.raises((LLMError, _Halted)) as raised:
        _Counted(Delegates(backend), meter).complete("system", "user")
    if ticking:
        assert isinstance(raised.value, _Halted) and raised.value.code == UNNAMED
    else:
        assert isinstance(raised.value, LLMError) and str(raised.value).startswith(UNNAMED)
    assert backend.calls == [] and meter.sent == 0


def test_a_tick_whose_caller_names_no_request_sends_nothing_and_holds_the_loop_for_a_person(tmp_path):
    domain = domains.WordProblems(tmp_path)
    domain.llm = _OtherNames()
    loop = _handle(tmp_path, domain)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    got = loop.tick()
    assert got["warning_card"]["code"] == UNNAMED and domain.llm.calls == []
    assert loop.log.cycles()[-1]["code"] == UNNAMED and loop._must_stop()
    assert loop.tick()["warning_card"]["code"] == "G6_E_LOOP_PAUSED" and domain.llm.calls == []


#: Each place in G6 that notes a request, and how what it sends is known: by what a frame above the note names (``frame``);
#: by what a host program is started with (``program``); or not at all, because the loop sends nothing through it
#: (``refused``). ``None`` is the note the others share.
NOTES = {
    ("core/llm.py", "_start_model_call"): None,
    ("core/llm.py", "FakeLLM.complete"): "frame",
    ("core/llm.py", "OllamaCaller._chat"): "frame",
    ("core/llm.py", "OpenAICompatibleCaller._chat"): "frame",
    ("core/llm.py", "AnthropicCaller._create"): "refused",
    ("core/host_models.py", "_HostCaller.complete"): "program",
    ("self_maintenance/usage.py", "_note_launch"): "program",
    ("harness/completion_loop.py", "ToolSession._launch"): "program",
    ("grounding/judges/nli.py", "LocalNLICaller.complete"): "refused",
    ("agents/agent_claude/adapter.py", "ClaudeCodeRuntime.run"): "refused",
    ("agents/agent_codex/adapter.py", "CodexRuntime.run"): "refused",
}


def _names_pair(function) -> bool:
    return {"system", "user"} <= {arg.arg for arg in (*function.args.args, *function.args.kwonlyargs)}


def test_every_way_a_send_is_known_and_every_sender_the_loop_accepts_names_what_it_sent(monkeypatch, host_folders):
    """Built from the code. What a send is known by is found in one of three ways and nowhere else: what the innermost frame
    above its note names as a system and a user (``loop._named``, read as it is noted or resent, ``_sender``, and as it fails,
    ``_raised_by``); what a watched host caller's program is started with (``usage.request_identity``, at ``usage._sending``);
    and, for a reply read with no step that sends, what the bound caller hands over (``usage._received``). Every place in G6
    that notes a request is listed with how what it sends is known, and that is checked against what the loop accepts. Then
    each kind of caller the loop accepts is sent through bare, behind a wrapper of a domain's own, and behind one that sends a
    fixed request whatever it is asked, and each is known by what it was given to send. A new place that notes, or a sender
    the loop accepts that is not known by what it sent, fails here."""
    from g6.agents.agent_claude.adapter import ClaudeCodeRuntime
    from g6.agents.agent_codex.adapter import CodexRuntime
    from g6.grounding.judges.nli import LocalNLICaller
    from g6.harness.completion_loop import ToolSession

    loop_tree, usage_tree = _source_tree(loop_module), _source_tree(usage)
    assert {where for where, _ in _callers(loop_tree, {"_request_digest"})} == set()
    assert {where for where, _ in _callers(loop_tree, {"text"})} == {"_named"}
    assert {where for where, _ in _callers(loop_tree, {"_named"})} == {"_sender", "_raised_by"}
    assert {where for where, node in _nodes(loop_tree) if isinstance(node, ast.Constant) and node.value in ("system", "user")
            } == {"_named"}
    assert {where for where, _ in _callers(usage_tree, {"request_identity"})} == {"Request.host"}
    assert {where for where, _ in _callers(usage_tree, {"request_digest"})} == {"_received"}
    assert {where for where, _ in _callers(usage_tree, {"host"})} == {"_sending"}
    source, noting = Path(g6.__file__).resolve().parent, set()
    for path in source.rglob("*.py"):
        for where, node in _nodes(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and ast.unparse(node.func).rsplit(".", 1)[-1] in ("note_model_call",
                                                                                             "_start_model_call"):
                noting.add((path.relative_to(source).as_posix(), where))
    assert noting == set(NOTES)
    llm_tree = _source_tree(llm)
    for (_, qualified), how in NOTES.items():
        if how == "frame" and not _names_pair(_function(llm_tree, qualified)):    # then every method that reaches it names it
            owner, name = qualified.split(".")
            reaching = [fn for fn in _function(llm_tree, owner).body if isinstance(fn, ast.FunctionDef)
                        and any(ast.unparse(call.func) == f"self.{name}" for call in _calls_in(fn, name))]
            assert reaching and all(_names_pair(fn) for fn in reaching), qualified
    kinds = {"FakeLLM.complete": [FakeLLM()], "OllamaCaller._chat": [llm.OllamaCaller()],
             "OpenAICompatibleCaller._chat": [llm.OpenAICompatibleCaller("m", base_url="http://model.invalid", api_key=None)],
             "AnthropicCaller._create": [llm.AnthropicCaller(client=object())],
             "_HostCaller.complete": [ClaudeCodeCaller(), CodexCaller(), usage.WatchedClaude(), usage.WatchedCodex()],
             "_note_launch": [ClaudeCodeCaller(), CodexCaller(), usage.WatchedClaude(), usage.WatchedCodex()],
             "ToolSession._launch": [ToolSession()], "LocalNLICaller.complete": [LocalNLICaller("weights")],
             "ClaudeCodeRuntime.run": [ClaudeCodeRuntime()], "CodexRuntime.run": [CodexRuntime()]}
    assert set(kinds) == {qualified for (_, qualified), how in NOTES.items() if how is not None}
    accepted = []
    for (_, qualified), how in NOTES.items():
        for kind in kinds.get(qualified, ()):
            takes = any(sender_problems(kind, provider) == [] for provider in ("claude", "codex"))
            assert takes == (how != "refused"), qualified
            assert isinstance(kind, ClaudeCodeCaller | CodexCaller) == (how == "program"), qualified
            if takes:
                accepted.append(kind)
    monkeypatch.setattr(llm, "_post_json", lambda *args: {"message": {"content": "x"}, "choices": [{"message": {"content": "x"}}]})
    for host in (ClaudeCodeCaller, CodexCaller):
        monkeypatch.setattr(host, "locate", lambda self: "unused-program")
    monkeypatch.setattr(CodexCaller, "_reply", lambda self, *args: "x")
    started: list[str] = []

    def program(argv, stdin, cwd, files, *rest):
        started.append(request_identity(argv, stdin, files.parent))
        printed = {"type": "turn.completed"} if "exec" in argv else {"type": "result", "is_error": False, "result": "x"}
        return 0, json.dumps(printed), ""

    monkeypatch.setattr(host_models, "_run", program)
    for kind in accepted:
        for hold, given in ((lambda inner: inner, ("system", "asked")), (Delegates, ("system", "asked")),
                            (Rewrites, ("fixed system", "fixed request"))):
            seen = []

            class Admits(_Requests):
                def admit(self, *args, **kwargs):
                    record = super().admit(*args, **kwargs)
                    if record is not None:
                        seen.append(record.identity)
                    return record

            started.clear()
            meter = Admits()
            _Counted(hold(kind), meter).complete("system", "asked")
            known = started if isinstance(kind, ClaudeCodeCaller | CodexCaller) else [_request_digest(*given)]
            checks = 2 if isinstance(kind, ClaudeCodeCaller | CodexCaller) else 1
            assert seen == known * checks and len(known) == meter.sent == 1, (type(kind).__name__, hold)


class Rewrites(Delegates):
    """A wrapper of a domain's own that sends one request whatever it is asked, through a function of its own."""

    def complete(self, system, user, *, max_tokens=1024):
        return _send_fixed(self._inner, max_tokens)


def _send_fixed(inner, max_tokens):
    return inner.complete("fixed system", "fixed request", max_tokens=max_tokens)
