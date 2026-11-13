"""Total host reads, native envelopes, ordered outcomes and captured cleanup."""
import ast
import inspect
import json
import textwrap
from pathlib import Path

import pytest

from g6.core import host_models
from g6.metaharness import loop
from g6.self_maintenance import usage


def json_error(message):
    return json.JSONDecodeError(message, "", 0)


def unicode_error(message):
    return UnicodeDecodeError("utf-8", b"\xff", 0, 1, message)


FAILURES = [ValueError, json_error, TypeError, RecursionError, MemoryError, RuntimeError, OSError,
            UnicodeError, unicode_error, AttributeError, KeyboardInterrupt, SystemExit, BaseException]
HOSTS = [usage.WatchedClaude, usage.WatchedCodex]


def answer(provider):
    return ({"type": "result", "result": "answer", "is_error": False}
            if provider == "claude" else {"type": "turn.completed"})


def stop(provider, kind):
    if provider == "claude":
        return {"type": "result", "is_error": True,
                **({"stop_reason": "refusal"} if kind == "refusal" else
                   {"api_error_status": 429 if kind == "limit" else 529})}
    return {"type": "error", "codex_error_info": {
        "refusal": "cyber_policy", "limit": "usage_limit_exceeded", "capacity": "server_overloaded"}[kind]}


def failure(provider):
    return ({"type": "result", "is_error": True, "result": "ordinary failure"}
            if provider == "claude" else {"type": "turn.failed", "error": {"message": "ordinary failure"}})


@pytest.mark.parametrize("exception", FAILURES)
def test_json_reader_is_total_for_each_exception(monkeypatch, exception):
    def unreadable(*args, **kwargs):
        raise exception("reader interrupted")

    monkeypatch.setattr(usage.json, "loads", unreadable)
    try:
        result = usage._reply_events("{}")
    except BaseException:
        pytest.fail("an exception escaped the total JSON reader")
    assert result == ([], False)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("exception", FAILURES)
@pytest.mark.parametrize("reader", ["transport", "events", "facts", "answer"])
def test_every_reply_reader_failure_holds_before_release(fake_host, monkeypatch, host, exception, reader,
                                                         consented_provider_fakes):
    provider = "claude" if host is usage.WatchedClaude else "codex"
    launches = []

    def unreadable(*args, **kwargs):
        raise exception("reader interrupted")

    if reader == "answer":
        monkeypatch.setattr(host_models.ClaudeCodeCaller if provider == "claude" else host_models.CodexCaller,
                            "_reply", unreadable)

    def run(argv, stdin, cwd, files, *args):
        launches.append(stdin)
        if reader == "transport":
            raise exception("transport read interrupted")
        (files / "last_message.txt").write_text("answer", encoding="utf-8")
        if reader == "events":
            monkeypatch.setattr(usage, "_reply_events", unreadable)
        elif reader == "facts":
            owner, name = (host_models, "_result") if provider == "claude" else (usage, "_codex_result")
            monkeypatch.setattr(owner, name, unreadable)
        return 0, json.dumps(answer(provider)), ""

    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    for _ in range(2):
        try:
            loop._Counted(host(), meter).complete("s", "same request")
        except BaseException:
            pass
    assert len(launches) == 1
    assert len(meter.unknown) == 1
    assert not meter.in_flight and not usage._SENT


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("code", [0, 1])
@pytest.mark.parametrize("kind", ["answer", "failure", "refusal", "limit", "capacity"])
def test_foreign_outcome_envelopes_are_unknown(provider, code, kind):
    foreign = "codex" if provider == "claude" else "claude"
    event = answer(foreign) if kind == "answer" else failure(foreign) if kind == "failure" else stop(foreign, kind)
    assert usage._classify_reply(provider, code, json.dumps(event))[0] == "unknown"


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("kind", ["refusal", "limit", "capacity"])
@pytest.mark.parametrize("order,expected", [
    ("stop,error", "stop"), ("answer,stop,error", "stop"),
    ("stop,answer", "answer"), ("stop,answer,error", "failed"),
    ("stop,error,answer", "failed"), ("answer,error,stop", "stop"),
])
def test_every_result_uses_the_same_ordered_rule(provider, kind, order, expected):
    events = {"stop": stop(provider, kind), "answer": answer(provider), "error": failure(provider)}
    printed = "\n".join(json.dumps(events[name]) for name in order.split(","))
    expected = "refusal" if kind == "refusal" else expected
    assert usage._classify_reply(provider, 0, printed)[0] == expected


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("exception", FAILURES)
def test_a_read_refusal_survives_a_reader_failure_in_the_tail(monkeypatch, provider, exception):
    original = json.loads
    printed = json.dumps(stop(provider, "refusal")) + "\nTAIL"

    def loads(text, *args, **kwargs):
        if text == "TAIL":
            raise exception("damaged tail")
        return original(text, *args, **kwargs)

    monkeypatch.setattr(usage.json, "loads", loads)
    try:
        result = usage._classify_reply(provider, 1, printed)
    except BaseException:
        pytest.fail("an exception escaped classification after a read refusal")
    assert result[0] == "refusal"


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("exception", FAILURES)
def test_cleanup_finishes_the_captured_gate_if_forgetting_raises(fake_host, monkeypatch, host, exception,
                                                                 consented_provider_fakes):
    provider = "claude" if host is usage.WatchedClaude else "codex"
    original = usage._forget
    done = loop._Gate.done
    released = []

    def finish(gate, record):
        released.append(record)
        return done(gate, record)

    monkeypatch.setattr(loop._Gate, "done", finish)

    def forget(*args, **kwargs):
        original(*args, **kwargs)
        raise exception("forget interrupted after removal")

    def run(argv, stdin, cwd, files, *args):
        (files / "last_message.txt").write_text("answer", encoding="utf-8")
        monkeypatch.setattr(usage, "_forget", forget)
        return 0, json.dumps(answer(provider)), ""

    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    try:
        loop._Counted(host(), meter).complete("s", "u")
    except BaseException:
        pass
    assert not meter.in_flight and not usage._SENT
    assert len(released) == 1


@pytest.mark.parametrize("outcome", ["answer", "refusal", "unknown"])
def test_session_launch_uses_captured_ownership(fake_host, monkeypatch, outcome):
    from contextlib import contextmanager

    original = usage._open_input
    blocked = set()
    launches = []

    @contextmanager
    def open_input(path, **kwargs):
        if Path(path) in blocked:
            raise PermissionError("reply folder cannot be reopened")
        with original(path, **kwargs) as stream:
            yield stream

    def run(argv, stdin, cwd, files, *args):
        launches.append(stdin)
        blocked.add(files)
        printed = (json.dumps(answer("claude")) if outcome == "answer" else
                   json.dumps(stop("claude", "refusal")) if outcome == "refusal" else "[" * 20_000)
        return 0, printed, ""

    monkeypatch.setattr(usage, "_open_input", open_input)
    monkeypatch.setattr(host_models, "_run", run)
    host = usage.WatchedClaude()

    class Session:
        def complete(self, system, user, **kwargs):
            with host_models._folders() as (work, files), host._invocation():
                argv, stdin = host._command("fake", system, user, work, files)
                host._launch(argv, stdin, work, files, {}, 1)
            return "answer"

    meter = loop._Requests()
    answers = []
    for _ in range(2):
        try:
            answers.append(loop._Counted(Session(), meter).complete("s", "u"))
        except BaseException:
            pass
    assert not usage._SENT and not meter.in_flight
    if outcome == "answer":
        assert answers == ["answer", "answer"] and len(launches) == 2
    else:
        assert not answers and len(launches) == 1
        assert len(meter.refused if outcome == "refusal" else meter.unknown) == 1


def test_reader_and_ownership_inventory_from_the_code():
    tree = ast.parse(Path(usage.__file__).read_text(encoding="utf-8"))
    calls = {}

    def walk(node, scope=""):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            scope = f"{scope}.{node.name}".lstrip(".")
        if isinstance(node, ast.Call):
            calls.setdefault(ast.unparse(node.func), set()).add(scope)
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(tree)
    assert calls["json.loads"] == {"_reply_events", "read_limits.talk"}
    # Other JSON reads concern account readings; every host reply read is inventoried here.
    assert calls["host_models._result"] == {"_read_reply"}
    assert calls["_reply_events"] == {"_read_reply"}
    assert calls["_read_reply"] == {"_classify_reply", "_codex_turn"}
    for function in (usage._complete_host, usage.WatchedClaude._launch):
        source = inspect.getsource(function)
        assert "_released((owned,))" in source
        assert "_note_launch(owned)" in source
        assert "str(files)" not in source
        parsed = ast.parse(textwrap.dedent(source))
        assert any(isinstance(node, ast.Try) and "_released((owned,))" in ast.unparse(node.finalbody)
                   for node in ast.walk(parsed))
    assert "_open_input" not in inspect.getsource(usage._note_launch)
    released = inspect.getsource(usage._released)
    assert "finally:" in released and "_finish_record" in released
    classify = inspect.getsource(usage._classify_reply)
    assert "except BaseException" in classify
    assert calls["_event_outcome"] == {"_reply_outcome"}
    assert calls["_reply_outcome"] == {"_classify_reply", "_codex_result"}
    assert calls["claude_said"] == {"_event_outcome"}
    assert calls["_codex_kind"] == {"_event_outcome"}
    assert calls["_sending"] == {"_complete_host", "WatchedClaude._launch"}
    assert calls["_note_launch"] == {"_complete_host", "WatchedClaude._launch"}
    assert calls["_released"] == {"_complete_host", "WatchedClaude._launch", "_Watched._invocation"}
    assert calls["_finish_record"] == {"_sending", "_stopped", "_released"}
    assert calls["_forget"] == {"_sending", "_stopped", "_released"}
    # All bound reads on the owned path sit inside a BaseException handler that observes an unknown reply.
    for function in (usage._complete_host, usage.WatchedClaude._launch):
        parsed = ast.parse(textwrap.dedent(inspect.getsource(function)))
        for name in ("host_models._run", "reply"):
            guards = [node for node in ast.walk(parsed) if isinstance(node, ast.Try)
                      and any(isinstance(call, ast.Call) and ast.unparse(call.func) == name
                              for statement in node.body for call in ast.walk(statement))
                      and any(handler.type is not None and ast.unparse(handler.type) == "BaseException"
                              and "_observe_reply" in ast.unparse(handler.body) for handler in node.handlers)]
            assert guards, name
    bound = ast.parse(Path(host_models.__file__).read_text(encoding="utf-8"))
    reply_reads = set()

    def bound_reads(node, scope=""):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            scope = f"{scope}.{node.name}".lstrip(".")
        if scope in {"_run", "_result", "ClaudeCodeCaller._reply", "CodexCaller._reply"} and isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name.rsplit(".", 1)[-1] in {"read", "read_text", "loads", "stat", "is_file", "decode", "_result"}:
                reply_reads.add((scope, name))
        for child in ast.iter_child_nodes(node):
            bound_reads(child, scope)

    bound_reads(bound)
    assert reply_reads == {
        ("_run", "out.read"), ("_run", "err.read"), ("_run", "printed.decode"), ("_run", "errors.decode"),
        ("_result", "json.loads"), ("ClaudeCodeCaller._reply", "_result"),
        ("CodexCaller._reply", "last.is_file"), ("CodexCaller._reply", "last.stat"),
        ("CodexCaller._reply", "last.read_text"),
    }
