# SPDX-License-Identifier: Apache-2.0
"""Caller code cannot impersonate an owned type or escape the deadline through a reply."""
from __future__ import annotations

import ast
import gc
import inspect
import socket
import threading
import time

import pytest

from g6._agentic import kernel
from g6.core import host_models, llm
from g6.self_maintenance import usage
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, in_process_child, no_child

GRID = '{"value":[0.5]}'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("no socket connect is allowed")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def prepare(caller, **kwargs):
    request_id = "value-boundary-fixture"
    return kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=caller, request_id=request_id, **kwargs)


def finish(pending):
    return kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, "Fixture person"))


def impostor(target, operations):
    class Meta(type(target)):
        def __eq__(cls, other):
            operations.append("eq")
            return other is target or other is cls

        def __hash__(cls):
            operations.append("hash")
            return hash(target)

    class Custom(metaclass=Meta):
        model, timeout, base_url = "fixture", 30.0, "http://127.0.0.1:9"

        def complete(self, *args, **kwargs):
            pytest.fail("custom class executed")

    return Custom


@pytest.mark.parametrize("target", [usage.WatchedClaude, llm.OllamaCaller, llm.NoModel])
def test_forged_class_is_refused_without_comparing_or_hashing_it(monkeypatch, target):
    no_child(monkeypatch)
    operations = []
    caller = impostor(target, operations)()
    reply = prepare(caller)
    assert isinstance(reply, dict) and reply["agentic"]["code"] == kernel.CALLER_UNMETERED
    assert operations == []


def test_host_seam_does_not_accept_an_equal_class_with_a_final_method():
    operations = []
    cls = impostor(usage.WatchedClaude, operations)
    with pytest.raises(llm.LLMError, match="G6_E_LOOP_UNWATCHED_HOST"):
        usage._check_seam(cls)
    assert operations == []


def test_configured_guard_registry_never_hashes_or_compares_classes(monkeypatch):
    operations = []
    cls = impostor(usage.WatchedClaude, operations)
    raw = llm.FakeLLM()
    cls._guards = raw
    caller = cls()
    monkeypatch.setattr(kernel, "_CONFIGURED_GUARDS", type(kernel._CONFIGURED_GUARDS)())
    kernel._remember_configured(caller)
    assert kernel._raw(caller) is raw
    assert operations == []


def test_collected_guard_releases_its_registry_entry(monkeypatch):
    monkeypatch.setattr(kernel, "_CONFIGURED_GUARDS", type(kernel._CONFIGURED_GUARDS)())
    caller = kernel.consent_callers.guard(llm.OllamaCaller("fixture", base_url="http://127.0.0.1:9"))
    kernel._remember_configured(caller)
    assert len(kernel._CONFIGURED_GUARDS) == 1
    del caller
    gc.collect()
    assert len(kernel._CONFIGURED_GUARDS) == 0


@pytest.mark.parametrize("replacement", ["unknown", "other_kind", "recorded_kind"])
def test_snapshot_kind_is_derived_again_before_any_child_or_constructor(monkeypatch, replacement):
    started = no_child(monkeypatch)
    pending = prepare(host_models.CALLERS["claude_code"]())
    assert isinstance(pending, kernel.Pending)
    if replacement == "unknown":
        object.__setattr__(pending.caller, "cls", impostor(usage.WatchedClaude, []))
    elif replacement == "other_kind":
        object.__setattr__(pending.caller, "cls", llm.FakeLLM)
    else:
        object.__setattr__(pending.caller, "kind", "local")
    reply = finish(pending)
    assert reply["agentic"]["code"] == kernel.CALLER_UNMETERED
    assert started == []


@pytest.mark.parametrize("kind", ["host", "local"])
def test_fresh_never_constructs_unknown_classes_for_an_allowed_kind(kind):
    made = []

    class Custom:
        def __init__(self, *args, **kwargs):
            made.append(True)

    snapshot = kernel._CallerSnapshot(Custom, kind, weights="fixture")
    with pytest.raises(ValueError, match=kernel.CALLER_UNMETERED):
        kernel._fresh(snapshot, [])
    assert made == []


def test_owned_constructor_is_inside_the_deadline_worker(monkeypatch):
    in_process_child(monkeypatch)
    monkeypatch.setattr(kernel.ledger, "append", lambda entry: "fixture-ledger")
    pending = prepare(llm.FakeLLM(default=GRID), budget=kernel.Budget(max_wall_clock_s=0.2))
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    original = llm.FakeLLM.__init__

    def constructor(self, *args, **kwargs):
        entered.set()
        try:
            release.wait(1.2)
            original(self, *args, **kwargs)
        finally:
            exited.set()

    monkeypatch.setattr(llm.FakeLLM, "__init__", constructor)
    started = time.monotonic()
    try:
        reply = finish(pending)
        took = time.monotonic() - started
        assert entered.is_set() and took < 0.8
        assert reply["agentic"]["reason"] == "budget_exhausted"
    finally:
        release.set()
        assert exited.wait(2)


class HostileText(str):
    def __len__(self):
        return 0

    def strip(self, *args):
        raise AssertionError("reply strip dispatched to caller")

    def __str__(self):
        raise AssertionError("reply str dispatched to caller")


@pytest.mark.parametrize("kind", ["own_function", "local", "endpoint", "host"])
def test_every_reply_is_plain_before_count_strip_and_decode(monkeypatch, kind):
    in_process_child(monkeypatch)
    monkeypatch.setattr(kernel.ledger, "append", lambda entry: "fixture-ledger")
    text = HostileText(GRID)
    if kind == "own_function":
        caller = llm.FakeLLM(lambda s, u: text)
    elif kind == "local":
        caller = llm.FakeLLM(default=GRID)
        monkeypatch.setattr(llm.FakeLLM, "complete", lambda *a, **k: text)
    elif kind == "endpoint":
        caller = llm.OllamaCaller("fixture", base_url="http://127.0.0.1:9")
        monkeypatch.setattr(llm.OllamaCaller, "complete", lambda *a, **k: text)
    else:
        caller = host_models.CALLERS["claude_code"]()
        monkeypatch.setattr(usage, "_complete_host", lambda *a, **k: text)
    pending = prepare(caller)
    assert isinstance(pending, kernel.Pending), pending
    reply = finish(pending)
    assert reply["agentic"]["outcome"] == "substituted", reply["agentic"]
    assert reply["agentic"]["cost"]["output_chars"] == 15


def test_subclass_length_cannot_hide_a_reply_over_budget(monkeypatch):
    in_process_child(monkeypatch)
    monkeypatch.setattr(kernel.ledger, "append", lambda entry: "fixture-ledger")
    text = HostileText(GRID + " " * 30_000)
    reply = finish(prepare(llm.FakeLLM(lambda s, u: text)))
    assert reply["agentic"]["reason"] == "budget_exhausted"
    assert reply["agentic"]["cost"]["output_chars"] == 30_015


def test_non_text_reply_is_refused_without_running_its_methods(monkeypatch):
    in_process_child(monkeypatch)
    monkeypatch.setattr(kernel.ledger, "append", lambda entry: "fixture-ledger")

    class Other:
        def __str__(self):
            pytest.fail("non-text reply formatted")

    reply = finish(prepare(llm.FakeLLM(lambda s, u: Other())))
    assert reply["agentic"]["reason"] == "model_failed"
    assert reply["agentic"]["cost"]["output_chars"] == 0


def test_error_formatting_is_also_inside_the_deadline_worker(monkeypatch):
    in_process_child(monkeypatch)
    monkeypatch.setattr(kernel.ledger, "append", lambda entry: "fixture-ledger")
    release, exited = threading.Event(), threading.Event()

    class SlowError(Exception):
        def __str__(self):
            try:
                release.wait(1.2)
                return "fixture error"
            finally:
                exited.set()

    def answer(*args):
        raise SlowError()

    pending = prepare(llm.FakeLLM(answer), budget=kernel.Budget(max_wall_clock_s=0.2))
    started = time.monotonic()
    try:
        reply = finish(pending)
        assert time.monotonic() - started < 0.8
        assert reply["agentic"]["reason"] == "budget_exhausted"
    finally:
        release.set()
        assert exited.wait(2)


def test_classification_ast_has_no_class_equality_membership_or_hash_tables():
    # This census is required by F1 in addition to the behavioral adversaries above.
    found = []
    for module in (kernel, usage):
        tree = ast.parse(inspect.getsource(module))
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            # In usage kind is a class; in the kernel it is a plain string returned by classification.
            class_names = {"cls", "native", "owned"}
            if function.name == "_host_program":
                class_names.add("host")
            if module is usage and function.name in {"_check_seam", "_new_host"}:
                class_names.add("kind")
                class_names.add("base")
            for node in ast.walk(function):
                if isinstance(node, ast.Compare) and any(isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn))
                                                        for op in node.ops):
                    values = (node.left, *node.comparators)
                    if any(isinstance(v, ast.Name) and v.id in class_names or
                           isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "type"
                           for v in values):
                        found.append((function.name, node.lineno))
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Name) \
                        and node.slice.id in class_names:
                    found.append((function.name, node.lineno))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                        and any(isinstance(arg, ast.Name) and arg.id == "cls" for arg in node.args):
                    found.append((function.name, node.lineno))
    assert found == []
    assert boundary_operations(ast.parse(inspect.getsource(kernel))) == []


def boundary_operations(tree):
    """Census every function, following caller parameters through attribute chains.

    G6 snapshot/execution annotations declare the owned side of the boundary; self is an owned implementation.
    Plain scalar locals are checked separately by the class census and the behavioral adversaries.
    """
    found = []
    held_attributes = set()
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        caller_args = {a.arg for a in function.args.args if a.arg != "self" and
                       (a.annotation is None or ast.unparse(a.annotation) not in {"_CallerSnapshot", "_Execution"})}
        for assignment in (n for n in ast.walk(function) if isinstance(n, ast.Assign)):
            if isinstance(assignment.value, ast.Name) and assignment.value.id in caller_args:
                held_attributes.update(ast.unparse(t) for t in assignment.targets if isinstance(t, ast.Attribute))
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        untrusted = {a.arg for a in function.args.args if a.arg != "self" and
                     (a.annotation is None or ast.unparse(a.annotation) not in {"_CallerSnapshot", "_Execution"})}
        # A canonicalizer assignment creates a G6-owned value. The helper's own parameter is still scanned.
        for assignment in (n for n in ast.walk(function) if isinstance(n, ast.Assign)):
            if isinstance(assignment.value, ast.Call) and isinstance(assignment.value.func, ast.Name) \
                    and assignment.value.func.id in {"_plain_budget", "_json_copy"}:
                for target in assignment.targets:
                    if isinstance(target, ast.Name):
                        untrusted.discard(target.id)

        def held(node):
            if not isinstance(node, ast.Attribute):
                return False
            while isinstance(node, ast.Attribute):
                if ast.unparse(node) in held_attributes:
                    return True
                node = node.value
            return isinstance(node, ast.Name) and node.id in untrusted

        for node in ast.walk(function):
            if isinstance(node, ast.Compare) and any(isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn,
                                                                     ast.Lt, ast.Gt, ast.LtE, ast.GtE))
                                                     for op in node.ops):
                values = (node.left, *node.comparators)
                if any(held(v) for v in values):
                    found.append((function.name, node.lineno, "caller attribute comparison"))
                if any(isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "vars"
                       for v in values):
                    found.append((function.name, node.lineno, "vars membership"))
            if isinstance(node, ast.BinOp) and any(held(v) for v in (node.left, node.right)):
                found.append((function.name, node.lineno, "caller number arithmetic"))
    return found


def test_boundary_census_recognizes_review3_shapes_anywhere():
    source = '''
def previously_unlisted(x, y):
    if "complete" in vars(x): pass
    if x.attr != y: pass
    return x.seconds - y
class Other:
    def __init__(self, budget):
        self.budget = budget
    def time_left(self, elapsed):
        return self.budget.seconds - elapsed
'''
    # The module-wide census must also reject the current unsafe source, beyond its former eight-function list.
    assert len(boundary_operations(ast.parse(source))) == 4
    assert boundary_operations(ast.parse(inspect.getsource(kernel))) == []
