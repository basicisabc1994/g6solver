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
from g6.core import host_models, llm, model_calls
from g6.core.host_models import ClaudeCodeCaller
from g6.core.llm import RATE_LIMITED, FakeLLM, LLMError
from g6.metaharness import loop as loop_module
from g6.metaharness.loop import (BUDGET_WAIT, CLASSIFIER_STOP, LoopSettings, ShadowLoop, _AtLimit, _Counted, _Refused,
                                 _request_digest, _Requests)
from g6.metaharness.method import Method
from g6.operator.store import OperatorStore
from g6.self_maintenance import usage
from g6.self_maintenance.envelope import CYCLE_LOG, REFUSED_FILE, CycleLog, note_refusal, refused_requests
from g6.self_maintenance.pacing import BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import MODEL_REFUSED, REFRESH

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
    assert starts == {("core/host_models.py", "complete"), ("harness/completion_loop.py", "_launch")}
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
        ("_Requests.take", "admit"), ("_Requests.begin", "admit"), ("_Gate.sending", "admit")}
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
    assert changed == {"_Requests.admit", "_Requests.release"}
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
    assert final[0] == "self.requests.close()" and final[1] == "ended = self.requests.closed"
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
