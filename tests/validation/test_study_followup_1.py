# SPDX-License-Identifier: Apache-2.0
"""The study-trust follow-up's review 1: seven findings, each a test that failed on 18d432d for the reason the review gave, and
the classes they belong to, each removed at its root with an inventory built from the code.

1. Every session's ending reaches the run's ending (findings 1, 4, 5; one_fact_two_rules and failure_read_as_success): every
   session process gives its stop, its exit and its own result to ``run.session_ending`` unchanged, and every ending folds into
   the run's by one rule (``run.run_ending``). The inventory follows the value of every call that gives an ending.
2. Every file a store writer touches is checked (finding 2; guard_covers_less_than_it_guards): one writer for every store file
   (``wecommon.write_whole``), and an inventory of every write in the store modules.
3. Python's own tokenizer over the whole source the patch holds (finding 3; own_parser_for_foreign_format).
4. The rows' writer decides as their reader does, at the moment it writes (findings 6, 7; check_then_act_race and
   one_fact_two_rules).

Fakes only: no Docker, no model, no network."""
from __future__ import annotations

import ast
import difflib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "validation" / "worked_examples"
sys.path.insert(0, str(STUDY))

import evolve  # noqa: E402
import final_review as fr  # noqa: E402
import layers  # noqa: E402
import replay3  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402

SUCCESS = {"type": "result", "subtype": "success", "is_error": False, "session_id": "s1"}


def k3_helpers():
    spec = importlib.util.spec_from_file_location("stfu1_k3_helpers", ROOT / "tests" / "validation" / "test_k3_rule_step.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    return helpers


# --- 1: every session's ending reaches the run's ending ------------------------------------------------------------------------
def test_finding_1_a_host_sessions_own_timeout_survives_its_successful_result(tmp_path, monkeypatch):
    records = [
        {"type": "system", "subtype": "init", "tools": ["Read", "Edit", "Write", "Glob", "Grep", "mcp__box__run"],
         "mcp_servers": [{"name": "box", "status": "connected"}]},
        {"type": "result", "subtype": "success", "is_error": False, "session_id": "s1"},
    ]
    proc = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(b"".join(json.dumps(r).encode() + b"\n" for r in records)),
                           stderr=io.BytesIO(), poll=lambda: 0, wait=lambda **kw: 0)
    monkeypatch.setattr(we, "host_start", lambda *a, **kw: SimpleNamespace(proc=proc))
    monkeypatch.setattr(we, "end_session", lambda *a: "")
    monkeypatch.setattr(we.shutil, "which", lambda name: name)
    real_read, stops = we.read_stream, []

    def read(*a, **kw):             # the result is read just before the session's deadline, then time runs out
        ticks = iter([0.0, 0.0, float("inf")])
        stop = real_read(*a, **kw, clock=lambda: next(ticks))
        stops.append(stop)
        return stop

    monkeypatch.setattr(we, "read_stream", read)
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1, executor="host")
    ctx = SimpleNamespace(model=we.R3_MODELS[1], starter=None, archive=archive, environ={})
    ended = we.host_session(ctx, run, tmp_path, tmp_path / "mcp", tmp_path / "settings", "prompt", "stream.jsonl",
                            seconds=60, tally=c.Tally())
    assert stops == ["timeout"]
    assert ended == "timeout", f"host_session lost its own timeout: {ended!r}"
    assert c.ending(archive, run.rel)["status"] == "timeout"      # written at once, as every ending not on the patch is


CAP = {**SUCCESS, "subtype": "error_max_turns", "is_error": True}


@pytest.mark.parametrize(("stop", "code", "result", "by_g6", "ended"), [
    ("", 0, SUCCESS, False, ""),
    ("timeout", 0, SUCCESS, False, "timeout"),          # its own stop first, whatever its result says (finding 1)
    ("cap_cost", 0, SUCCESS, True, "cap_cost"),
    ("", 1, SUCCESS, False, "error"),                   # a success that exited 1
    ("", None, SUCCESS, False, "error"),                # an exit nobody read
    ("", True, SUCCESS, False, "error"),
    ("", 0, None, False, "error"),                      # no result of its own
    ("", 0, CAP, False, "cap_turns"),                   # its own result's cap, whatever its exit
    ("", 0, {**SUCCESS, "is_error": None}, False, "error"),
    ("", -9, SUCCESS, True, ""),                        # G6 ended it after its success: its exit is G6's
    ("", -9, None, True, "error"),                      # G6 ended it before any result
    ("", -9, CAP, True, "cap_turns"),
])
def test_the_one_rule_of_a_sessions_ending(stop, code, result, by_g6, ended):
    assert we.session_ending(stop, code, result, ended_by_g6=by_g6) == ended


def test_the_session_rule_takes_every_input_and_the_run_rule_keeps_the_first_bad_ending():
    with pytest.raises(TypeError):
        we.session_ending("", 0)                         # no caller can leave its result unread
    assert we.run_ending() == we.run_ending("", "") == ""
    assert we.run_ending("", "timeout", "error") == "timeout" and we.run_ending("error", "") == "error"


class Proc:
    """A container session's client process: it exits with ``code``, or runs on until it is ended (``hangs``)."""

    def __init__(self, lines, code=0, hangs=False):
        self.stdin, self.stderr, self.code, self.hangs, self.killed = io.BytesIO(), io.BytesIO(), code, hangs, False
        self.stdout = io.BytesIO(b"".join(json.dumps(x).encode() + b"\n" for x in lines))

    def poll(self):
        return None if self.hangs and not self.killed else self.code

    def wait(self, timeout=None):
        if self.poll() is None:
            raise subprocess.TimeoutExpired("docker", timeout)
        return self.code

    def kill(self):
        self.killed, self.code = True, -9


def container_session(tmp_path, proc):
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="K2", repeat=1)
    docker = SimpleNamespace(popen=lambda *a, **kw: proc, sh=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b""))
    ctx = SimpleNamespace(docker=docker, client_env=lambda key: {}, archive=archive)
    return we.run_session(ctx, run, "agent", ["exec"], "key", c.Tally(), "note", "repair_1_stream.jsonl", seconds=60.0,
                          stop_usd=10.0)


@pytest.mark.parametrize(("lines", "code", "hangs", "ended"), [
    ([SUCCESS], 0, False, ""),
    ([SUCCESS], 125, False, "error"),          # its own exit, read in the time it is given
    ([SUCCESS], 0, True, ""),                  # it outlived its stream: given its time, then ended by G6, as on the host
    ([], 0, True, "error"),                    # ended by G6 with no result of its own
    ([CAP], 0, False, "cap_turns"),            # its own result's cap, which the exit of 0 does not hide
])
def test_a_container_session_ends_by_the_hosts_rule(tmp_path, monkeypatch, lines, code, hangs, ended):
    monkeypatch.setattr(we, "unbounded", lambda docker: docker)
    proc = Proc(lines, code, hangs)
    assert container_session(tmp_path, proc) == ended
    assert proc.killed is hangs


def scout_run(tmp_path, monkeypatch, failed_session, wrong_first=False):
    """Arm L8's run with the repository's in-memory Docker fake, the scouts on the real session reader: session
    ``failed_session`` (0 the visible scout, then its second try when ``wrong_first``, then the hidden scouts, then the fixer)
    exits 125, every other 0."""
    helper = k3_helpers()
    original_sh, original_popen = helper._Docker.sh, helper._Docker.popen
    real_scout, real_session = we.run_scout, we.run_session
    codes, gates = [], []

    def sh(self, ctr, script, *a, **kw):
        if script == we.ARM_STATE:
            return self.done(stdout=b'{"mcp_servers": ["g6"], "skills": []}')
        return original_sh(self, ctr, script, *a, **kw)

    def popen(self, *a, **kw):
        proc = original_popen(self, *a, **kw)
        code = 125 if len(codes) == failed_session else 0
        codes.append(code)
        proc.poll = lambda: code
        proc.wait = lambda **kw: code
        return proc

    def scout(*a, **kw):          # the run's own fake of the rule step's session aside: the scouts use the real one
        monkeypatch.setattr(we, "run_session", real_session)
        return real_scout(*a, **kw)

    def gate(*a, **kw):
        gates.append(1)
        return ["the probes collected nothing"] if wrong_first and len(gates) == 1 else []

    monkeypatch.setattr(helper._Docker, "sh", sh)
    monkeypatch.setattr(helper._Docker, "popen", popen)
    monkeypatch.setattr(we, "run_scout", scout)
    monkeypatch.setattr(we, "probe_gate", gate)
    monkeypatch.setattr(we, "run_set", lambda *a, **kw: we.ProbeRun(0, {"test::ok": "passed"}, {}, ""))
    monkeypatch.setattr(we, "repair_loop", lambda *a, **kw: {"status": "no_counterexample_in_domain"})
    record, *_ = helper._run(monkeypatch, tmp_path, "L8")
    assert len(codes) == (5 if wrong_first else 4) and codes.count(125) == (failed_session < len(codes))
    return record


def test_finding_4_a_failed_scout_session_ends_the_run(tmp_path, monkeypatch):
    record = scout_run(tmp_path, monkeypatch, failed_session=0)
    assert record["scout"]["ended"] == "error"
    assert record["status"] == "error", "the run finished despite its failed scout session"


def test_finding_5_a_hidden_scouts_failed_session_is_the_scouts_ending(tmp_path, monkeypatch):
    record = scout_run(tmp_path, monkeypatch, failed_session=1)
    assert record["scout"]["ended"] == "error", "the hidden scout's exit 125 was discarded"
    assert record["status"] == "error"


@pytest.mark.parametrize("failed", [0, 1, 3])
def test_a_scouts_second_try_neither_hides_nor_loses_a_failed_session(tmp_path, monkeypatch, failed):
    """The pattern the old reading missed: the visible scout's ending was overwritten by its second try's, so a failed first try
    then a clean second one read as clean (0); and the hidden scouts' second tries were discarded."""
    record = scout_run(tmp_path, monkeypatch, failed_session=failed, wrong_first=True)
    assert record["scout"]["ended"] == "error" and record["status"] == "error"


def test_clean_scouts_still_let_the_run_finish(tmp_path, monkeypatch):
    record = scout_run(tmp_path, monkeypatch, failed_session=99)
    assert record["scout"]["ended"] == "" and record["status"] == "finished"


# --- the value-following inventory: every ending is passed on ------------------------------------------------------------------
#: The functions whose value is a session's ending: the three the brief names and the two rules an ending is passed through.
SEEDS = {"run_session", "host_session", "session_ending", "step_ending", "run_ending"}
#: Where an ending becomes the run's: its status (``status_of``), its ending written (``RunFolder.end``), its end (``finish``).
SINKS = {"status_of", "end", "finish"}
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _called(node):
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""


class Endings:
    """A reading of a module, from its own code, of where every session's ending goes. A value "flows" through the positions that
    pass it on unchanged (a conditional's branches, ``and``/``or``, a tuple, list or dict, a starred item, a subscript, ``.get``);
    it "lands" where it is passed on: returned, given to a rule or a sink (``SEEDS``, ``SINKS``) or to a function that gives an
    ending, or stored in a name or a list that must then land in its turn, before it is written over and before its function ends.
    A clean ending ("" or "finished", by a branch's own test) needs nothing more. Producers (functions that give an ending) and
    the names that hold one (a producer as a value, a parameter it is passed to, a loop over a tuple of them) are found from the
    code until nothing changes."""

    def __init__(self, source: str):
        self.tree = ast.parse(source)
        self.parent = {child: node for node in ast.walk(self.tree) for child in ast.iter_child_nodes(node)}
        self.funcs = [n for n in ast.walk(self.tree) if isinstance(n, FUNCS)]
        self.top = {n.name: n for n in self.tree.body if isinstance(n, FUNCS)}
        self.nested: dict[ast.AST, dict[str, ast.AST]] = {}
        for fn in self.funcs:
            self.nested.setdefault(self.scope_of(fn), {}).setdefault(fn.name, fn)
        self.owned = {fn: self._own(fn) for fn in self.funcs}
        self.producers = {self.top[name] for name in SEEDS if name in self.top}
        self.index: dict[ast.AST, set[int]] = {}       # a producer that returns a tuple: which items carry the ending
        self.aliases: dict[ast.AST, set[str]] = {}     # per function: names whose value is a producer
        self.failures: list[tuple[str, int, str]] = []
        self.pending_free: dict[ast.AST, dict[str, set[int]]] = {}
        while self._grow():
            pass
        for fn in sorted(self.funcs, key=self._depth, reverse=True):     # a nested def first: what it leaves, its parent holds
            self._check(fn)

    # -- names and calls ------------------------------------------------------------------------------------------------------
    def scope_of(self, node):
        up = self.parent.get(node)
        while up is not None and not isinstance(up, FUNCS):
            up = self.parent.get(up)
        return up

    def _depth(self, fn) -> int:
        depth, up = 0, self.scope_of(fn)
        while up is not None:
            depth, up = depth + 1, self.scope_of(up)
        return depth

    def resolve(self, name: str, scope):
        """The function or alias ``name`` means in ``scope``: a def nested in it or an enclosing one, an alias there, a module's."""
        while scope is not None:
            if name in self.aliases.get(scope, set()):
                return "alias"
            if name in self.nested.get(scope, {}):
                return self.nested[scope][name]
            scope = self.scope_of(scope)
        return self.top.get(name)

    def gives(self, call, scope) -> bool:
        """Whether a call gives an ending: a producer's, an alias's, a lambda's that gives one, or a rule's."""
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            return False
        got = self.resolve(call.func.id, scope)
        return got == "alias" or got in self.producers

    def carries(self, expr, scope, held: set[str]) -> bool:
        """Whether ``expr``'s value is an ending, or holds one where it flows."""
        if self.gives(expr, scope) or (isinstance(expr, ast.Name) and expr.id in held):
            return True
        return any(self.carries(e, scope, held) for e in self._flows(expr))

    @staticmethod
    def _flows(expr):
        if isinstance(expr, ast.IfExp):
            return [expr.body, expr.orelse]
        if isinstance(expr, ast.BoolOp):
            return list(expr.values)
        if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
            return list(expr.elts)
        if isinstance(expr, ast.Dict):
            return list(expr.values)
        if isinstance(expr, (ast.Starred, ast.NamedExpr, ast.Await)):
            return [expr.value]
        if isinstance(expr, ast.Subscript):
            return [expr.value]
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr in ("get", "pop"):
            return [expr.func.value]
        return []

    def passes_into(self, call, scope) -> bool:
        """Whether an argument of ``call`` is passed on: a rule's, a sink's, or a producer's."""
        name = _called(call.func)
        return name in SEEDS | SINKS or self.gives(call, scope)

    # -- producers and aliases, until nothing changes -------------------------------------------------------------------------
    @staticmethod
    def _own(fn, lambdas: bool = False):
        """The nodes of ``fn``'s own body, not those of a def inside it (nor of a lambda, unless ``lambdas``)."""
        out, todo = [], list(ast.iter_child_nodes(fn))
        while todo:
            node = todo.pop()
            out.append(node)
            if not isinstance(node, FUNCS) and (lambdas or not isinstance(node, ast.Lambda)):
                todo.extend(ast.iter_child_nodes(node))
        return out

    def _held(self, fn) -> set[str]:
        """The names in ``fn`` that are ever given an ending (a fixpoint over its assignments)."""
        held, grew = set(), True
        while grew:
            grew = False
            for node in self.owned[fn]:
                value = getattr(node, "value", None)
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and value is not None \
                        and self.carries(value, fn, held):
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                        for name in (n.id for n in ast.walk(target) if isinstance(n, ast.Name)):
                            if name not in held:
                                held.add(name)
                                grew = True
        return held

    def _grow(self) -> bool:
        grew = False
        for fn in self.funcs:
            held = self._held(fn)
            returns = [n for n in self.owned[fn] if isinstance(n, ast.Return) and n.value is not None]
            for ret in returns:
                if self.carries(ret.value, fn, held):
                    if fn not in self.producers:
                        self.producers.add(fn)
                        grew = True
                    if isinstance(ret.value, ast.Tuple):
                        items = {i for i, e in enumerate(ret.value.elts) if self.carries(e, fn, held)}
                        if not items <= self.index.get(fn, set()):
                            self.index[fn] = self.index.get(fn, set()) | items
                            grew = True
            names = self.aliases.setdefault(fn, set())
            for node in self._own(fn, lambdas=True):
                found: set[str] = set()
                if isinstance(node, ast.Assign) and self._is_producer_value(node.value, fn):
                    found = {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)}
                elif isinstance(node, (ast.For, ast.AsyncFor)) and any(
                        isinstance(n, ast.Name) and self.resolve(n.id, fn) == "alias" for n in ast.walk(node.iter)):
                    found = {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
                elif isinstance(node, ast.Call):
                    grew |= self._pass_alias(node, fn)
                if not found <= names:
                    names |= found
                    grew = True
        return grew

    def _is_producer_value(self, expr, scope) -> bool:
        """A producer itself as a value (not called), a lambda that gives an ending, or a tuple or conditional of them."""
        if isinstance(expr, ast.Name):
            got = self.resolve(expr.id, scope)
            return got == "alias" or got in self.producers
        if isinstance(expr, ast.Lambda):
            return self.carries(expr.body, scope, set())
        return any(self._is_producer_value(e, scope) for e in self._flows(expr))

    def _pass_alias(self, call, scope) -> bool:
        """A producer given to a module's function as an argument makes that parameter an alias there."""
        callee = self.top.get(_called(call.func)) if isinstance(call.func, ast.Name) else None
        if callee is None:
            return False
        params = [a.arg for a in [*callee.args.posonlyargs, *callee.args.args]]
        given = [(params[i] if i < len(params) else None, a) for i, a in enumerate(call.args)]
        given += [(k.arg, k.value) for k in call.keywords]
        names, grew = self.aliases.setdefault(callee, set()), False
        for param, value in given:
            if param and param not in names and self._is_producer_value(value, scope):
                names.add(param)
                grew = True
        return grew

    # -- the check: every ending lands --------------------------------------------------------------------------------------
    def _fail(self, fn, line, what):
        self.failures.append((fn.name, line, what))

    def _landing(self, node, scope):
        """Where the value at ``node`` goes in its statement: ("lands",), ("into", names), or ("drops", why)."""
        child, up = node, self.parent.get(node)
        while True:
            if isinstance(up, ast.Return) or isinstance(up, ast.Lambda) and child is up.body:
                return ("lands",)
            call = self.parent.get(up)
            if isinstance(up, ast.Attribute) and up.attr in ("get", "pop") and isinstance(call, ast.Call) and call.func is up:
                child, up = call, self.parent.get(call)          # a field of what holds it: ``repairs.get("ended")``
                continue
            if isinstance(up, ast.Call):
                if child is up.func:
                    return ("drops", "called")
                if self.passes_into(up, scope):
                    return ("lands",)
                if isinstance(up.func, ast.Attribute) and up.func.attr in ("append", "extend") \
                        and isinstance(up.func.value, ast.Name):
                    return ("into", [up.func.value.id])
                return ("drops", f"given to {_called(up.func)}()")
            if isinstance(up, (ast.Assign, ast.AnnAssign)) and child is up.value:
                return ("into", self._targets(up, node, scope))
            if up is not None and child in self._flows(up):
                child, up = up, self.parent.get(up)
                continue
            return ("drops", "discarded" if isinstance(up, ast.Expr) else f"read only as {type(up).__name__}")

    def _targets(self, assign, value, scope):
        targets = assign.targets if isinstance(assign, ast.Assign) else [assign.target]
        names = []
        for target in targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Tuple) and value is assign.value and isinstance(value, ast.Call):
                callee = self.resolve(value.func.id, scope) if isinstance(value.func, ast.Name) else None
                items = self.index.get(callee, set(range(len(target.elts))))
                names += [e.id for i, e in enumerate(target.elts) if i in items and isinstance(e, ast.Name)]
        return names

    def _check(self, fn):
        free = self._flow(fn, fn.body, {}, fn)
        local = {n.id for n in self.owned[fn] if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        local |= {a.arg for a in ast.walk(fn.args) if isinstance(a, ast.arg)}
        for name, lines in (free or {}).items():
            if name in local:
                for line in sorted(lines):
                    self._fail(fn, line, f"{name}: not passed on before {fn.name} ends")
            else:                            # a list of the enclosing function's: it lands there (``_flow``'s nested defs)
                self.pending_free.setdefault(fn, {}).setdefault(name, set()).update(lines)

    def _expr_events(self, stmt, state, scope):
        """What one simple statement (or a compound one's header) does: the endings it reads that land (a read only stored in a
        record or a field lands nowhere), then the calls that give one, each landing or failing, then what it stores."""
        nodes = [n for n in ast.walk(stmt) if self._inside(n, stmt)]
        for node in nodes:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and state.get(node.id):
                got = self._landing(node, scope)
                if got[0] == "lands" or got[0] == "into" and got[1]:
                    state[node.id] = set()
                    if got[0] == "into":
                        for name in got[1]:
                            state.setdefault("__new__", set()).add(name)
        for node in nodes:
            if isinstance(node, ast.Call) and self.gives(node, scope):
                got = self._landing(node, scope)
                if got[0] == "drops":
                    self._fail(scope, node.lineno, f"{_called(node.func)}(): its ending is {got[1]}")
                elif got[0] == "into":
                    if not got[1]:
                        self._fail(scope, node.lineno, f"{_called(node.func)}(): its ending is stored where it is never read")
                    for name in got[1]:
                        state.setdefault("__new__", set()).add(name)
        new = state.pop("__new__", set())
        stored = set()
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
            targets = (stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if hasattr(stmt, "target")
                       else [i.optional_vars for i in stmt.items if i.optional_vars is not None])
            stored = {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
        for name in stored - new:
            if state.get(name):
                for line in sorted(state[name]):
                    self._fail(scope, line, f"{name}: written over at line {stmt.lineno} before it was passed on")
            state.pop(name, None)
        for name in new:
            if name in stored or not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                if state.get(name) and name in stored:
                    for line in sorted(state[name]):
                        self._fail(scope, line, f"{name}: written over at line {stmt.lineno} before it was passed on")
                state[name] = (state.get(name, set()) if name not in stored else set()) | {stmt.lineno}

    def _inside(self, node, stmt):
        """Whether ``node`` belongs to ``stmt`` itself, not to a statement nested in it or a def or lambda inside it."""
        up = node
        while up is not stmt:
            up = self.parent.get(up)
            if up is None or (up is not stmt and isinstance(up, (ast.stmt, *FUNCS, ast.Lambda))):
                return False
        return True

    @staticmethod
    def _clean(test, branch: bool) -> set[str]:
        """The names a branch's own test shows to hold a clean ending ("" or "finished") in that branch."""
        if isinstance(test, ast.Name):
            return set() if branch else {test.id}
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Name):
            return {test.operand.id} if branch else set()
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and len(test.ops) == 1 \
                and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value in ("", "finished"):
            equal = isinstance(test.ops[0], ast.Eq)
            if isinstance(test.ops[0], (ast.Eq, ast.NotEq)) and branch == equal:
                return {test.left.id}
            return set()
        if isinstance(test, ast.BoolOp):
            if isinstance(test.op, ast.And) and branch or isinstance(test.op, ast.Or) and not branch:
                return set().union(*(Endings._clean(v, branch) for v in test.values))
        return set()

    @staticmethod
    def _merge(*states):
        live = [s for s in states if s is not None]
        if not live:
            return None
        out: dict[str, set[int]] = {}
        for s in live:
            for name, lines in s.items():
                out.setdefault(name, set()).update(lines)
        return out

    def _flow(self, fn, body, state, scope, loop=None):
        """The pending endings after ``body``, from ``state`` (name: lines of endings not yet passed on); None when every path
        through it returns or raises. ``loop`` collects the states a ``break`` or ``continue`` leaves with."""
        state = {k: set(v) for k, v in state.items()}
        for stmt in body:
            if state is None:
                return None
            if isinstance(stmt, (*FUNCS, ast.ClassDef)):
                for name, lines in self.pending_free.get(stmt, {}).items():
                    state.setdefault(name, set()).update(lines)
                continue
            if isinstance(stmt, ast.Return):
                self._expr_events(stmt, state, scope)
                for name, lines in state.items():
                    for line in sorted(lines):
                        self._fail(scope, line, f"{name}: {fn.name} returns at line {stmt.lineno} without passing it on")
                state = None
            elif isinstance(stmt, ast.Raise):
                self._expr_events(stmt, state, scope)
                state = None
            elif isinstance(stmt, (ast.Break, ast.Continue)):
                if loop is not None:
                    loop.append(state)
                state = None
            elif isinstance(stmt, ast.If):
                self._expr_events(stmt, state, scope)
                yes = {k: v for k, v in state.items() if k not in self._clean(stmt.test, True)}
                no = {k: v for k, v in state.items() if k not in self._clean(stmt.test, False)}
                state = self._merge(self._flow(fn, stmt.body, yes, scope, loop), self._flow(fn, stmt.orelse, no, scope, loop))
            elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                self._expr_events(stmt, state, scope)
                exits: list = []
                after = self._flow(fn, stmt.body, state, scope, exits)
                state = self._merge(state, after, *exits)
                if state is not None and stmt.orelse:
                    state = self._flow(fn, stmt.orelse, state, scope, loop)
            elif isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                done = self._flow(fn, stmt.body, state, scope, loop)
                done = self._flow(fn, stmt.orelse, done, scope, loop) if done is not None else None
                caught = [self._flow(fn, h.body, state, scope, loop) for h in stmt.handlers]
                state = self._merge(done, *caught)
                if stmt.finalbody:
                    state = self._flow(fn, stmt.finalbody, state if state is not None else {}, scope, loop) \
                        if state is not None else None
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                self._expr_events(stmt, state, scope)
                state = self._flow(fn, stmt.body, state, scope, loop)
            else:
                self._expr_events(stmt, state, scope)
        return state


def endings_of(source: str) -> Endings:
    return Endings(source)


def test_every_ending_of_every_session_is_passed_on_to_the_runs_ending():
    """Built from run.py's own code, following the value: every call of ``run_session``, ``host_session`` and
    ``session_ending``, of every function that gives what they give, and of every name that holds one, is passed on, never
    discarded, written over or left behind (it replaces study review 4's inventory, which found process waits by name and so
    missed the scouts' sessions)."""
    got = endings_of((STUDY / "run.py").read_text(encoding="utf-8"))
    named = {fn.name for fn in got.producers}
    assert {"run_session", "host_session", "session_ending", "host_ending", "run_scout", "repair_loop", "review_once",
            "reviews_until_clean", "reviews_with_bug_test", "rule_step", "rule_led_start", "turn", "resume", "session",
            "checked_again"} <= named, named
    assert got.failures == []
    assert not [n for n in ast.walk(got.tree) if isinstance(n, ast.keyword) and n.arg == "codes"]


@pytest.mark.parametrize(("source", "fails"), [
    # the scouts as they were: a hidden scout's session discarded, a second try's into "_", the first's written over
    ("def s():\n    return run_session()\ndef f():\n    s()\n", ["f"]),
    ("def s():\n    return run_session()\ndef again():\n    e = s()\n    return e, 1\n"
     "def f():\n    _, left = again()\n    return left\n", ["f"]),
    ("def s():\n    return run_session()\ndef again():\n    e = s()\n    return e, 1\n"
     "def f(w):\n    ended = s()\n    if w:\n        ended, w = again()\n    return {'ended': ended}\n", ["f"]),
    ("def f():\n    x = run_session()\n    print(x)\n", ["f"]),                         # read, but never passed on
    ("def f():\n    stops = []\n    def ask():\n        stops.append(run_session())\n    ask()\n", ["f"]),
    ("def f():\n    record = {}\n    record['scout'] = run_scout()\n    return 1\n"
     "def run_scout():\n    return {'ended': run_session()}\n", ["f"]),            # stored only in the record
    # the patterns that pass it on
    ("def f():\n    stops = []\n    def ask():\n        stops.append(run_session())\n    ask()\n"
     "    return run_ending(*stops)\n", []),
    ("def f(w):\n    ended = run_session()\n    if w:\n        ended = run_ending(ended, run_session())\n"
     "    return {'ended': ended}\n", []),
    ("def f():\n    ended = run_session()\n    if ended:\n        return {'ended': ended}\n    return {}\n", []),
    ("def f(steps):\n    steps = (lambda: run_session(),)\n    for step in steps:\n        ended = step()\n"
     "        if ended != 'finished':\n            return ended\n    return ''\n", []),
])
def test_the_inventory_finds_every_way_an_ending_was_lost(source, fails):
    got = endings_of("def run_session():\n    return ''\ndef run_ending(*e):\n    return ''\n" + source)
    assert sorted({name for name, _, _ in got.failures}) == fails, got.failures


# --- 2: one writer for every store file ----------------------------------------------------------------------------------------
def finished(archive, task="bug", arm="A", **start):
    run = archive.start("run", task, arm=arm, **start)
    run.write("final.patch", b"candidate patch\n")
    run.finish({"agent_started": True}, "finished")
    return run


def test_finding_2_a_reviews_staging_file_cannot_overwrite_a_run_row(tmp_path):
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1)
    run.finish({"agent_started": True}, "finished")
    row = c.keep(archive, {"run": run.rel, "passes": True, "failing": 0})
    before = row.read_bytes()
    store = c.beside(archive, fr.FOLDER)
    store.mkdir()
    sha = "a" * 64
    staging = store / f"{sha}.json.{os.getpid()}.part"
    os.link(row, staging)
    assert os.path.samefile(row, staging)
    try:
        fr.keep(tmp_path, {"run": run.rel, "task": "bug"}, {"said": "none"}, sha=sha)
    except (ValueError, c.ArchiveError):
        pass  # refusing the staging alias made in advance is also correct
    assert row.read_bytes() == before, "review staging overwrote the saved run row through a hard link"


def _linked_temp(monkeypatch, alias: Path):
    """``mkstemp`` as an attacker who sees the folder meets it: the temporary file, once made, given a second name."""
    real = c.tempfile.mkstemp

    def mkstemp(**kw):
        handle, part = real(**kw)
        os.link(part, alias)
        return handle, part

    monkeypatch.setattr(c.tempfile, "mkstemp", mkstemp)


@pytest.mark.parametrize("writer", ["review", "bug record", "row", "cegis"])
def test_no_store_writer_puts_a_file_with_a_second_name_in_place(tmp_path, monkeypatch, writer):
    """Every store writer goes through the one writer, which refuses a temporary file given a second name after it was made
    (an unpredictable name is not enough when the folder can be read) and leaves the destination as it was."""
    task = we.load_tasks()[0]
    archive = c.Archive(tmp_path)
    run = finished(archive, task.id, "K2", repeat=1)
    (tmp_path / c.SAVED).mkdir(exist_ok=True)
    alias = tmp_path / "alias.json"
    writes = {
        "review": lambda: fr.keep(tmp_path, {"run": run.rel, "task": task.id}, {"said": "none"}, sha="b" * 64),
        "bug record": lambda: replay3.review_bug(SimpleNamespace(archive=archive), tmp_path, task,
                                                 [{"run": run.rel, "task": task.id}], "t", "i",
                                                 ask=lambda *a, **kw: ({"verdict": "none", "rule": "r", "why": "", "test": "x",
                                                                        "error": ""}, None, None),
                                                 run=lambda *a, **kw: pytest.fail("no patch runs once the record is refused")),
        "row": lambda: c.keep(archive, {"run": run.rel, "passes": True, "failing": 0}),
        "cegis": lambda: c.write_whole(lambda: c.beside(archive, "cegis.json"), b"{}"),
    }
    _linked_temp(monkeypatch, alias)
    with pytest.raises(c.ArchiveError, match="second name"):
        writes[writer]()
    assert not [p for p in (tmp_path / c.SAVED).rglob("*") if p.is_file()]          # nothing put in place, no temporary left


def test_the_one_writer_refuses_a_destination_with_a_second_name_and_leaves_both_as_they_were(tmp_path):
    (tmp_path / c.SAVED).mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"another file's bytes")
    os.link(outside, tmp_path / c.SAVED / "evolve.json")
    with pytest.raises(c.ArchiveError, match="second name"):
        c.write_whole(lambda: c.beside(tmp_path, "evolve.json"), b"{}")
    assert outside.read_bytes() == b"another file's bytes" and len(list((tmp_path / c.SAVED).iterdir())) == 1


class _Stop(BaseException):
    """A stop between two steps of a write (a kill, a power cut as the process sees it)."""


@pytest.mark.parametrize("at", ["fsync", "replace", "check again"])
def test_a_stop_between_any_two_steps_of_a_write_leaves_the_store_as_it_was_and_a_restart_writes_it(tmp_path, monkeypatch, at):
    (tmp_path / c.SAVED).mkdir()
    dest = tmp_path / c.SAVED / "cegis.json"
    dest.write_bytes(b'{"old": 1}')
    asked = []

    def where():
        asked.append(1)
        if at == "check again" and len(asked) == 2:
            raise _Stop()
        return c.beside(tmp_path, "cegis.json")

    def stop(*a, **kw):
        raise _Stop()

    with monkeypatch.context() as patch:
        if at == "fsync":
            patch.setattr(c.os, "fsync", stop)
        elif at == "replace":
            patch.setattr(c.os, "replace", stop)
        with pytest.raises(_Stop):
            c.write_whole(where, b'{"new": 1}')
    assert dest.read_bytes() == b'{"old": 1}' and [p.name for p in dest.parent.iterdir()] == ["cegis.json"]
    assert c.write_whole(lambda: c.beside(tmp_path, "cegis.json"), b'{"new": 1}') == dest
    assert dest.read_bytes() == b'{"new": 1}' and [p.name for p in dest.parent.iterdir()] == ["cegis.json"]


def test_a_store_kept_as_text_keeps_the_bytes_it_always_had(tmp_path):
    """The reviews, the replays' records and the training's files were written as text (``Path.write_text``): the one writer
    gives them the same bytes, so the frozen review stays byte for byte what it was; a row is written as the bytes given."""
    (tmp_path / c.SAVED).mkdir()
    text, expected = '{\n "a": 1\n}', tmp_path / "expected.json"
    expected.write_text(text, "utf-8")
    assert c.write_whole(lambda: c.beside(tmp_path, "cegis.json"), text).read_bytes() == expected.read_bytes()
    assert c.write_whole(lambda: c.beside(tmp_path, "evolve.json"), text.encode()).read_bytes() == text.encode()


def test_a_destination_that_is_a_folder_is_refused_and_no_temporary_file_stays(tmp_path):
    (tmp_path / c.SAVED / "evolve.json").mkdir(parents=True)
    with pytest.raises((OSError, c.ArchiveError)):
        c.write_whole(lambda: c.beside(tmp_path, "evolve.json"), b"{}")
    assert [p.name for p in (tmp_path / c.SAVED).iterdir()] == ["evolve.json"]


def test_evolves_own_files_are_written_by_the_one_writer(tmp_path, monkeypatch):
    seen = []
    real = c.write_whole
    monkeypatch.setattr(c, "write_whole", lambda where, data: (seen.append(where().name), real(where, data))[1])
    runner = SimpleNamespace(best=None, stopped=False, made=[], rows=[], failed=[])
    monkeypatch.setattr(evolve, "Training", lambda *a, **kw: runner)
    assert evolve.train(tmp_path, "g6", ["t"], "L9", None, first=1)["cegis"]["status"] == "not run"
    assert seen == ["evolve.json"] and json.loads((tmp_path / c.SAVED / "evolve.json").read_text("utf-8"))["parent"] == "L9"


#: Every write in the modules that keep a store beside the saved rows, by module and function, as the code reads now: what it
#: writes, and why it is not a store's file. Only ``wecommon.write_whole`` makes, syncs and replaces a store's file.
WRITES = {
    ("evolve", "Training.proposed"): ["write", "write", "write_text"],     # the distill run's folder; the harness file, in git
    ("final_review", "held"): ["write_bytes"],                             # the critic's private copy, in critic_packs
    ("final_review", "_review"): ["open", "write_bytes", "copyfile", "write_text", "write_text"],   # the critic's pack
    ("final_review", "_step"): ["open", "write_bytes", "copyfile", "write_text", "write_text"],     # the critic's pack
    ("loop", "Generations.distilled"): ["write", "write_text"],            # the distill run's folder; the harness file, in git
    ("product_review", "read_at"): ["replace", "replace"],                 # str.replace: no file
    ("replay3", "run_on"): ["copy"],                                       # copy.copy of a context: no file
    ("replay3", "review_bug"): ["write_bytes"],                            # the patch's copy, in critic_packs
    ("replay3", "one_runner"): ["open", "write"],                          # the replay's lock: O_CREAT|O_EXCL, never replaced
    ("wecommon", "sha256_file"): ["open"],                                 # read
    ("wecommon", "Redactor.__call__"): ["replace"],                        # str.replace: no file
    ("wecommon", "RunFolder.write"): ["write_bytes"],                      # a run's own folder, never a store
    ("wecommon", "RunFolder.write_json"): ["write"],                       # RunFolder.write: the run's own folder
    ("wecommon", "_locked"): ["open"],                                     # the index's lock
    ("wecommon", "Archive.append"): ["open", "open", "write", "write", "truncate"],    # the index, and its torn tail moved aside
    ("wecommon", "_unique"): ["replace", "replace"],                       # dataclasses.replace: no file
    ("wecommon", "durable"): ["open"],                                     # r+b, for fsync only
    ("wecommon", "write_whole"): ["replace", "mkstemp", "fdopen", "write", "replace"],     # str.replace of line ends; os.replace
}
PRIMITIVES = {"write_text", "write_bytes", "write", "replace", "rename", "link", "hardlink_to", "symlink_to", "mkstemp", "dump",
              "copy", "copy2", "copyfile", "copytree", "move", "truncate", "open", "fdopen", "touch"}


def store_writes() -> dict[tuple[str, str], list[str]]:
    """Every write primitive in every module that defines or calls a store's writer or checker (``beside``, ``keep``,
    ``write_whole``), by the function it is in, from the code."""
    found: dict[tuple[str, str], list[str]] = {}
    for path in sorted(STUDY.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {_called(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        names |= {n.name for n in ast.walk(tree) if isinstance(n, FUNCS)}
        if not names & {"beside", "keep", "write_whole"}:
            continue

        def walk(node, where):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (*FUNCS, ast.ClassDef)):
                    walk(child, f"{where}.{child.name}" if where else child.name)
                    continue
                if isinstance(child, ast.Call) and _called(child.func) in PRIMITIVES:
                    found.setdefault((path.stem, where), []).append(_called(child.func))
                walk(child, where)

        walk(tree, "")
    return {k: sorted(v) for k, v in found.items()}


def test_every_write_in_the_store_modules_is_listed_and_only_the_one_writer_replaces_a_stores_file():
    assert store_writes() == {k: sorted(v) for k, v in WRITES.items()}
    tree = ast.parse((STUDY / "wecommon.py").read_text(encoding="utf-8"))
    writer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "write_whole")
    calls = [_called(n.func) for n in ast.walk(writer) if isinstance(n, ast.Call)]
    assert {"mkstemp", "fsync", "replace", "lstat"} <= set(calls) and calls.count("where") == 2


# --- 3: Python's own tokenizer over the whole source the patch holds ----------------------------------------------------------
def _patch(base: str, code: str, name: str = "api.py") -> str:
    return f"diff --git a/{name} b/{name}\n" + "".join(difflib.unified_diff(
        base.splitlines(keepends=True), code.splitlines(keepends=True), fromfile=f"a/{name}", tofile=f"b/{name}"))


def _values(sources, name, *args):
    got = []
    for source in sources:
        namespace: dict = {}
        exec(compile(source, "api.py", "exec"), namespace)
        got.append(namespace[name](*args))
    return got


def test_finding_3_comment_looking_text_inside_a_multiline_string_is_code():
    base = 'def value():\n    return """\n# original\n"""\n'
    candidates = [base.replace("# original", "# first"), base.replace("# original", "# second")]
    values = _values(candidates, "value")
    assert values[0] != values[1]
    assert layers.change(_patch(base, candidates[0])) != layers.change(_patch(base, candidates[1])), \
        "distinct string changes collapsed to the same change"


def test_a_multiline_f_string_and_a_continued_string_keep_their_text():
    base = 'def value(a, b):\n    return f"""\n# {a}\n"""\n'
    fstrings = [base.replace("{a}", "{b}"), base.replace("# {a}", "# x {a}")]
    assert len({*_values([base, *fstrings], "value", 1, 2)}) == 3
    assert len({layers.change(_patch(base, one)) for one in fstrings}) == 2
    cont = 'def value():\n    return "abc\\\n# one"\n'          # a string continued past its line's end by a backslash
    other, third = cont.replace("# one", "# two"), cont.replace("# one", "# three")
    assert len(set(_values([cont, other, third], "value"))) == 3
    assert layers.change(_patch(cont, other)) != layers.change(_patch(cont, third))


def test_a_continued_line_is_its_tokens_and_its_comment_still_goes():
    base = "def value():\n    x = 1 + \\\n        2  # note\n    return x\n"
    two = base.replace("2  # note", "3  # note")
    assert layers.change(_patch(base, two)) == layers.change(_patch(base, base.replace("2  # note", "3  # another")))
    assert layers.change(_patch(base, two)) != layers.change(_patch(base, base.replace("2  # note", "4  # note")))


def test_a_hunk_that_starts_inside_a_block_and_leaves_it_is_read_as_python_reads_it():
    """A hunk's span starts at any indentation and may dedent past where it began: the tokenizer is given every level a dedent in
    the span can reach first (``layers._ladder``), so the span reads as it would inside its file, comments and blank or bracketed
    line breaks dropped, strings kept."""
    base = ("class A:\n    def f(self):\n        a = 0\n        b = 0\n        c = 0\n        if x:\n            y = 1\n"
            "        return y\n\n    def g(self):\n        return 2\n")
    one = base.replace("y = 1", "y  =  3   # set").replace("return 2", "return 4")
    same = base.replace("y = 1", "y = 3").replace("return 2", "return 4  # four")
    assert _patch(base, one).splitlines()[3].startswith("@@ -4,")      # the span starts inside f, and leaves it
    assert layers.change(_patch(base, one)) == layers.change(_patch(base, same))
    got = layers.change(_patch(base, one))[0][1]          # its tokens one after another, each with its type (follow-up review 2)
    assert any(got[i:i + 3] == (("-", "NAME", "y"), ("-", "OP", "="), ("-", "NUMBER", "1")) for i in range(len(got)))


def test_a_span_the_tokenizer_cannot_read_keeps_its_lines_as_written():
    """A hunk that starts inside a string the patch does not open, where the tokenizer stops: what it could not read is kept as
    written, so two different lines are never one change, and a comment-looking line is never dropped."""
    def patch(new):
        return ("diff --git a/api.py b/api.py\n--- a/api.py\n+++ b/api.py\n@@ -5,4 +5,4 @@\n don't\n x\n-# original\n"
                f"+{new}\n x\n")

    assert layers.change(patch("# first")) != layers.change(patch("# second"))
    assert ("+", layers.AS_WRITTEN, "# first") in layers.change(patch("# first"))[0][1]


def test_python_is_tokenized_in_one_place_over_a_whole_span():
    """own_parser_for_foreign_format, met before: layers reads Python with Python's tokenizer only, once a hunk's side, and has
    no reading of its own (no line taken for a comment by its first character)."""
    tree = ast.parse((STUDY / "layers.py").read_text(encoding="utf-8"))
    tokenizing = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  for call in ast.walk(n) if isinstance(call, ast.Call) and _called(call.func) in ("generate_tokens", "tokenize")}
    assert tokenizing == {"_side"}
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _called(n.func) == "startswith"
                and any(isinstance(a, ast.Constant) and a.value == "#" for a in n.args)]


# --- 4: the rows' writer decides as their reader does, when it writes ----------------------------------------------------------
def test_finding_6_keep_refuses_an_ending_changed_during_its_checks(tmp_path, monkeypatch):
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1, executor="host")
    run.finish({"agent_started": True, "executor": "host"}, "finished")
    row = {"run": run.rel, "passes": True, "failing": 0, c.MADE_UNDER: c.run_facts(archive, run.rel).ending_sha256}
    original_folders, changed = c._folders, []

    def folders(root):              # after keep's read of the run's facts, before its digest comparison and its write
        run.failed("failure while the writer checks its destination", agent_started=True)
        changed.append(c.run_facts(archive, run.rel).ending_sha256)
        assert changed[-1] != row[c.MADE_UNDER]
        return original_folders(root)

    monkeypatch.setattr(c, "_folders", folders)
    with pytest.raises(c.ArchiveError, match="ending changed"):
        c.keep(archive, row)
    assert changed and changed[0] != row[c.MADE_UNDER]
    assert not (tmp_path / c.SAVED / "A-bug-1.json").exists()


def test_keep_reads_the_ending_again_just_before_the_replace(tmp_path, monkeypatch):
    """The same race one step later: the ending changes after every check of the first reading, while the bytes are written."""
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1, executor="host")
    run.finish({"agent_started": True, "executor": "host"}, "finished")
    row = {"run": run.rel, "passes": True, "failing": 0, c.MADE_UNDER: c.run_facts(archive, run.rel).ending_sha256}
    real = c.os.fsync

    def fsync(fd):
        run.failed("failure while the row's bytes are written", agent_started=True)
        return real(fd)

    monkeypatch.setattr(c.os, "fsync", fsync)
    with pytest.raises(c.ArchiveError, match="ending changed"):
        c.keep(archive, row)
    assert not [p for p in (tmp_path / c.SAVED).iterdir()]


def test_finding_7_keep_refuses_a_second_file_for_the_same_run(tmp_path):
    archive = c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="A", repeat=1)
    run.finish({"agent_started": True}, "finished")
    row = {"run": run.rel, "passes": True, "failing": 0}
    c.keep(archive, row, file="first.json")
    assert len(c.shelf(archive, lambda task: None).rows) == 1
    try:
        c.keep(archive, row, file="second.json")
    except c.ArchiveError:
        pass
    assert len(c.shelf(archive, lambda task: None).rows) == 1, "the writer created duplicates that its reader refuses"


def test_keep_refuses_what_its_reader_would_bar_and_writes_what_it_would_read(tmp_path):
    archive = c.Archive(tmp_path)
    first = finished(archive, "bug", "A", repeat=1)
    again = finished(archive, "bug", "A", repeat=1)          # a second run of the same key
    c.keep(archive, {"run": first.rel, "passes": True, "failing": 0}, file="first.json")
    with pytest.raises(c.ArchiveError, match="runs' rows hold its key"):
        c.keep(archive, {"run": again.rel, "passes": True, "failing": 0})
    other = finished(archive, "bug", "A", repeat=2)
    (tmp_path / c.SAVED / "torn.json").write_bytes(b'{"run": "' + other.rel.encode())   # torn: bars nothing, as the reader reads it
    assert c.keep(archive, {"run": other.rel, "passes": False, "failing": 1}).name == "A-bug-2.json"
    assert {s.file for s in c.shelf(archive, lambda task: None).rows} == {"first.json", "A-bug-2.json"}
    assert c.keep(archive, {"run": first.rel, "passes": False, "failing": 1}, file="first.json").name == "first.json"
    amended = c.keep(archive, {"run": first.rel, "passes": True, "amended_passes": True}, amended=True, file="first.json")
    assert amended.parent.name == c.AMENDED      # the amended folder is its own: a row of the run in the rows' folder bars nothing


def test_the_rows_writer_and_reader_share_one_rule_for_one_file_a_run():
    tree = ast.parse((STUDY / "wecommon.py").read_text(encoding="utf-8"))
    users = {fn.name for fn in tree.body if isinstance(fn, ast.FunctionDef)
             for n in ast.walk(fn) if isinstance(n, ast.Call) and _called(n.func) == "_unique"}
    assert users == {"shelf", "keep"}


def test_a_stop_while_a_row_is_written_leaves_no_row_and_a_restart_writes_it(tmp_path, monkeypatch):
    archive = c.Archive(tmp_path)
    run = finished(archive, "bug", "A", repeat=1)
    row = {"run": run.rel, "passes": True, "failing": 0}
    with monkeypatch.context() as patch:
        patch.setattr(c.os, "replace", lambda *a: (_ for _ in ()).throw(_Stop()))
        with pytest.raises(_Stop):
            c.keep(archive, row)
    assert not [p for p in (tmp_path / c.SAVED).iterdir()]
    assert c.keep(archive, row).name == "A-bug-1.json" and len(c.shelf(archive, lambda task: None).rows) == 1


def test_a_row_file_that_cannot_be_read_is_never_written_over(tmp_path):
    archive = c.Archive(tmp_path)
    run = finished(archive, "bug", "A", repeat=1)
    (tmp_path / c.SAVED).mkdir()
    (tmp_path / c.SAVED / "A-bug-1.json").mkdir()            # not a plain file: refused by the reader's own rule
    with pytest.raises(c.ArchiveError, match="no plain file"):
        c.keep(archive, {"run": run.rel, "passes": True, "failing": 0})
