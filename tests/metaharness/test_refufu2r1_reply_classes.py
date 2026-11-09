"""A readable failed run is a case; an unread reply and a refusal stay barred."""
import ast
import gc
import inspect
import json
import threading
from pathlib import Path

import pytest

from g6.core import host_models
from g6.core.llm import LLMError
from g6.metaharness import loop
from g6.self_maintenance import usage
from tests.metaharness import test_loop_review, test_provider_stops, test_refufu2r1_roots

launch = test_refufu2r1_roots.launch


@pytest.mark.parametrize("base", [usage.WatchedClaude, usage.WatchedCodex])
@pytest.mark.parametrize("printed", ["", '{"type":"thread.started"}\n', '{"type":"turn.started"}\n'])
def test_a_whole_failed_run_without_a_refusal_can_be_sent_again(launch, monkeypatch, base, printed):
    def run(argv, stdin, *args):
        launch.append(stdin)
        return 1, printed, "untrusted error text"

    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    for _ in range(2):
        with pytest.raises(LLMError, match=host_models.HOST_FAILED):
            loop._Counted(base(), meter).complete("system", "same request")
    assert len(launch) == 2 and not meter.refused and not meter.unknown
    assert not meter.in_flight and not usage._SENT


@pytest.mark.parametrize("base", [usage.WatchedClaude, usage.WatchedCodex])
@pytest.mark.parametrize("code", [0, 1])
@pytest.mark.parametrize("printed", ["not json", '{"type":', '[]',
                                     '{"type":"turn.completed"}\n{bad',
                                     '{"type":"result","result":"answer"}\n{bad'])
def test_an_unparseable_stream_is_unknown_even_after_a_failed_exit(launch, monkeypatch, base, code, printed):
    def run(argv, stdin, cwd, files, *args):
        launch.append(stdin)
        (files / "last_message.txt").write_text("answer", encoding="utf-8")
        return code, printed, ""

    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    for _ in range(2):
        with pytest.raises(LLMError):
            loop._Counted(base(), meter).complete("system", "same request")
    assert len(launch) == 1 and len(meter.unknown) == 1 and not meter.refused
    assert not meter.in_flight and not usage._SENT


@pytest.mark.parametrize("base", [usage.WatchedClaude, usage.WatchedCodex])
def test_a_read_refusal_wins_over_a_bad_tail_and_a_failed_exit(launch, monkeypatch, base):
    refusal = ({"type": "result", "is_error": True, "stop_reason": "refusal"}
               if base is usage.WatchedClaude else {"type": "error", "codex_error_info": "cyber_policy"})

    def run(argv, stdin, *args):
        launch.append(stdin)
        return 1, json.dumps(refusal) + "\n{bad", ""

    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    for _ in range(2):
        with pytest.raises(LLMError):
            loop._Counted(base(), meter).complete("system", "same request")
    assert len(launch) == 1 and len(meter.refused) == 1 and not meter.unknown
    assert not meter.in_flight and not usage._SENT


def test_the_original_sender_assertion_with_fake_launch_folders(launch, monkeypatch):
    test_loop_review.test_the_sender_of_a_request_is_read_at_the_request_for_each_kind_of_caller(monkeypatch)


def test_the_original_pending_send_assertion_with_fake_launch_folders(launch, monkeypatch):
    test_provider_stops.test_ref2_4_no_number_of_failed_sends_evicts_a_pending_sends_record(monkeypatch)


@pytest.mark.parametrize("words", [usage.MODEL_REFUSED + ": policy", "G6_E_LLM_RATE_LIMITED: wait",
                                  usage.AT_CAPACITY + ": wait"])
def test_model_and_stderr_words_have_no_stop_authority_in_a_whole_reply(launch, monkeypatch, words):
    def run(argv, stdin, cwd, files, *args):
        launch.append(stdin)
        (files / "last_message.txt").write_text("answer", encoding="utf-8")
        item = {"type": "item.completed", "item": {"type": "agent_message", "text": words}}
        return 0, json.dumps(item) + "\n" + json.dumps({"type": "turn.completed"}), words

    monkeypatch.setattr(host_models, "_run", run)
    meter = loop._Requests()
    for _ in range(2):
        assert loop._Counted(usage.WatchedCodex(), meter).complete("system", "same request") == "answer"
    assert len(launch) == 2 and not meter.refused and not meter.in_flight and not usage._SENT


def test_claude_keeps_the_bound_producers_result_reader(monkeypatch):
    calls = []
    original = host_models._result

    def parse(printed):
        calls.append(printed)
        return original(printed)

    monkeypatch.setattr(host_models, "_result", parse)
    printed = json.dumps({"type": "result", "is_error": True, "stop_reason": "refusal", "result": ""})
    with pytest.raises(usage.ProviderStop, match=usage.MODEL_REFUSED):
        usage.WatchedClaude()._reply(1, printed, "", None, ("s", "u"))
    assert calls == [printed]


def test_collecting_an_ended_host_while_registering_another_cannot_deadlock(monkeypatch):
    class Registry(dict):
        armed = False

        def __setitem__(self, key, value):
            if self.armed:
                gc.collect()
            super().__setitem__(key, value)

    registry = Registry()
    monkeypatch.setattr(usage, "_HOSTS", registry)
    automatic = gc.isenabled()
    gc.disable()
    errors = []
    old = host_models.ClaudeCodeCaller()
    old.cycle = old
    current = host_models.CodexCaller()
    del old
    registry.armed = True
    lock = usage._HOSTS_LOCK

    def register():
        try:
            usage._remember_host(current)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=register, daemon=True)
    try:
        thread.start()
        thread.join(2)
        finished = not thread.is_alive()
    finally:
        # A failing non-reentrant lock must not leave the diagnostic process stuck.
        if thread.is_alive() and isinstance(lock, type(threading.Lock())):
            lock.release()
            thread.join(2)
        registry.armed = False
        if automatic:
            gc.enable()
    assert finished, "host registration deadlocked when collection re-entered weak host cleanup"
    assert not errors


def test_the_code_lists_every_reply_classification_site():
    tree = ast.parse(Path(usage.__file__).read_text(encoding="utf-8"))
    sites = set()

    def walk(node, scope=""):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            scope = f"{scope}.{node.name}".lstrip(".")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"_classify_reply", "_observe_reply", "_codex_result", "claude_said", "_codex_turn",
                                "_reply_events", "_read_reply", "_reply_outcome", "_event_outcome", "_stopped", "ProviderStop"}:
                sites.add((scope, node.func.id))
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "host_models._result":
            sites.add((scope, "host_models._result"))
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(tree)
    assert sites == {
        ("_event_outcome", "claude_said"), ("_classify_reply", "_codex_result"),
        ("_observe_reply", "_classify_reply"),
        ("_complete_host", "_observe_reply"), ("WatchedClaude._launch", "_observe_reply"),
        ("WatchedClaude._reply", "_observe_reply"), ("WatchedCodex._reply", "_observe_reply"),
        ("_codex_turn", "_codex_result"), ("codex_said", "_codex_turn"),
        ("_read_reply", "_reply_events"), ("_classify_reply", "_read_reply"), ("_codex_turn", "_read_reply"),
        ("_classify_reply", "_reply_outcome"), ("_codex_result", "_reply_outcome"),
        ("_reply_outcome", "_event_outcome"),
        ("_observe_reply", "_stopped"), ("_stopped", "ProviderStop"),
        ("_read_reply", "host_models._result"),
    }
    class_returns = set()
    for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
        for node in ast.walk(function):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple) and node.value.elts:
                first = node.value.elts[0]
                if isinstance(first, ast.Constant) and first.value in {"unknown", "failed", "refusal", "answer", "stop"}:
                    class_returns.add(function.name)
    assert class_returns == {"_classify_reply"}
    # Compatibility stop readers do not decide whether a physical reply may be retried.
    for reader in (usage._codex_turn, usage.codex_said):
        assert "_stopped" not in inspect.getsource(reader)
    for path in (usage._complete_host, usage.WatchedClaude._launch):
        source = inspect.getsource(path)
        assert "_stopped(" not in source and "_observe_reply" in source
    decision = inspect.getsource(usage._classify_reply)
    assert all(f'"{kind}"' in decision for kind in ("unknown", "failed", "refusal", "answer", "stop"))
    # Every in-memory unknown hold is created, admitted and kept through the same locked record path.
    unknown_sites = set()
    host_sites = set()
    weak_sites = set()

    def unknowns(node, scope=""):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            scope = f"{scope}.{node.name}".lstrip(".")
        if isinstance(node, ast.Attribute) and node.attr == "unknown":
            unknown_sites.add((scope, type(node.ctx).__name__))
        if isinstance(node, ast.Name) and node.id in {"_HOSTS", "_HOSTS_LOCK"}:
            host_sites.add((scope, node.id))
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "weakref.ref":
            weak_sites.add(scope)
        for child in ast.iter_child_nodes(node):
            unknowns(child, scope)

    unknowns(ast.parse(Path(loop.__file__).read_text(encoding="utf-8")))
    unknowns(tree)
    assert unknown_sites == {("_Requests.__init__", "Store"), ("_Requests.admit", "Load"),
                             ("_Requests.at_a_refusal", "Load")}
    assert "meter.refused | meter.unknown | set(meter.on_record())" in inspect.getsource(loop._Requests.admit)
    assert host_sites == {(scope, name) for scope in ("", "_host_kind", "_remember_host", "_remember_host.forget")
                          for name in ("_HOSTS", "_HOSTS_LOCK")}
    assert weak_sites == {"_remember_host"}
