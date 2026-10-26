# SPDX-License-Identifier: Apache-2.0
"""How a code agent's code runs: a restricted syntax, then a bounded child process; never in G6's process
(AGENT_SMOLAGENTS-02). It is not a security boundary: run agents you do not trust inside your own sandbox (a container
or VM). The owner's rule for code screening: G6 accepts a restricted syntax, and managing a sandbox around it is the
person's.

Each step's code is screened first (:func:`screen`). It may use only the constructs of :data:`ALLOWED_SYNTAX` (an
allow-list of AST node types: the ordinary statements and expressions a data step needs; no ``match``, ``global``,
``nonlocal``, ``class``, ``with``, ``async``, ``yield``, walrus or decorator). On top of that list: it may import only
the modules of :data:`ALLOWED_IMPORTS` (a fixed list, the one smolagents names to the model); it may not name
``exec``, ``eval``, ``compile``, ``open``, ``__import__``, ``getattr`` and their like (:data:`REFUSED_NAMES`), nor
any name or attribute that starts with ``_``, nor the attributes that lead to a running frame
(:data:`FRAME_ATTRIBUTES`); a name it defines (a function, a parameter, a keyword, an import's alias, an exception's
name) keeps to the same rule. Code the screen refuses never runs: the refusal carries :data:`CODE_REFUSED`, the
construct and its line.

Code that passes runs through ``g6.core.sandbox.run_script``: another Python process with a clean environment, its
network refused by the sandbox's socket guard, its memory capped and its whole process tree ended at the step's time
limit. Its prelude installs an audit hook (:func:`refuse_event`, through ``sys.addaudithook``) that refuses starting
a program, ``ctypes`` and socket connections. Inside it the code runs in a namespace of its own, whose builtins lack
the names of :data:`REFUSED_NAMES`; its ``import`` refuses any module off the list, and a module it does import is
shown read-only, without its private names and without the other modules it holds. The earlier steps that ran cleanly
are run again first, quietly, so their variables are there, with ``random`` seeded once per session
(:attr:`SandboxSession.seed`), so a replayed step draws the numbers the model already saw; other sources of change,
such as the clock, can differ on replay. ``final_answer(value)`` ends the agent's run with ``value`` (as JSON, or its
text). What a step prints is what the model sees next.

Each run keeps the limits and the operating-system isolation ``run_script`` says it applied
(:meth:`SandboxSession.record`): a run's record says it was confined only when every code step was, and otherwise names
the step's reason, so an unconfined step is never reported as confined.
"""
from __future__ import annotations

import ast
import inspect
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from g6.core import confine
from g6.core.sandbox import run_script

#: The modules a code step may import: fixed, and the list smolagents itself names to the model.
ALLOWED_IMPORTS: tuple[str, ...] = ("collections", "datetime", "itertools", "math", "queue", "random", "re", "stat",
                                    "statistics", "time", "unicodedata")
#: Names a code step may not use: each runs or reaches code past the screen.
REFUSED_NAMES = frozenset({"exec", "eval", "compile", "open", "__import__", "globals", "locals", "vars", "getattr",
                           "setattr", "delattr", "breakpoint", "input", "help", "memoryview", "exit", "quit"})
#: Attributes that lead from an object to a running frame, its globals or its code.
FRAME_ATTRIBUTES = frozenset({"gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "ag_frame",
                              "ag_code", "ag_await", "f_back", "f_globals", "f_locals", "f_builtins", "f_code",
                              "tb_frame", "tb_next", "co_code", "co_consts", "co_names", "func_globals"})
#: The code of a step the restricted syntax refused (it never ran).
CODE_REFUSED = "G6_E_AGENT_SMOLAGENTS_CODE_REFUSED"
#: The restricted syntax: the AST node types a code step may use. Anything else is refused before it runs.
ALLOWED_SYNTAX = frozenset({
    "Module", "Expr", "Assign", "AugAssign", "For", "While", "If", "Break", "Continue", "Pass", "Import",
    "ImportFrom", "alias", "FunctionDef", "Return", "arguments", "arg", "Lambda", "Try", "ExceptHandler", "Raise",
    "BoolOp", "BinOp", "UnaryOp", "Compare", "Call", "keyword", "IfExp", "Dict", "Set", "List", "Tuple",
    "ListComp", "SetComp", "DictComp", "GeneratorExp", "comprehension", "Constant", "Name", "Attribute",
    "Subscript", "Slice", "Starred", "JoinedStr", "FormattedValue", "Load", "Store", "Del", "And", "Or", "Add",
    "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow", "LShift", "RShift", "BitOr", "BitXor", "BitAnd", "Invert",
    "Not", "UAdd", "USub", "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"})
#: The audit events the child refuses once its prelude has run (a second layer under the restricted syntax): starting
#: a program, and opening a socket connection; every ``ctypes`` event is refused too (:func:`refuse_event`).
REFUSED_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
                            "os.forkpty", "os.startfile", "_winapi.CreateProcess", "socket.connect"})
_WORDS = {"Match": "match", "Global": "global", "Nonlocal": "nonlocal", "ClassDef": "class", "With": "with",
          "AsyncWith": "async with", "AsyncFor": "async for", "AsyncFunctionDef": "async def", "Await": "await",
          "Yield": "yield", "YieldFrom": "yield from", "NamedExpr": "the walrus :=", "TryStar": "except*",
          "AnnAssign": "an annotated assignment", "Assert": "assert", "Delete": "del", "TypeAlias": "type"}


def refuse_event(event: str, args: tuple) -> None:
    """The child's audit hook: an event of REFUSED_EVENTS, or any ctypes event, raises, which ends that operation;
    every other event passes. Its source is what the prelude installs."""
    if event in REFUSED_EVENTS or event.startswith("ctypes."):
        raise RuntimeError("G6 refused: " + event + " is not allowed in this sandbox")


FINAL = "final_answer.json"
MEM_MB = 512
_TAIL = 2000
_HOOK = inspect.getsource(refuse_event)
_PRELUDE = r'''
import builtins as _b, contextlib as _c, io as _io, json as _j, random as _r, sys as _s, types as _t
REFUSED_EVENTS = frozenset(_j.loads(%(events)r))
%(hook)s
_s.addaudithook(refuse_event)
_real_import, _open, _exec, _compile = _b.__import__, _b.open, _b.exec, _b.compile
_ALLOWED = frozenset(_j.loads(%(allowed)r))
_REFUSED = frozenset(_j.loads(%(refused)r))
_get = object.__getattribute__
class _Shown:
    __slots__ = ("_module",)
    def __init__(self, module):
        object.__setattr__(self, "_module", module)
    def __getattr__(self, name):
        module = _get(self, "_module")
        if name.startswith("_"):
            raise AttributeError("G6 refused: " + name + " is private")
        found = getattr(module, name)
        if isinstance(found, _t.ModuleType):
            if found.__name__.startswith(module.__name__ + "."):
                return _Shown(found)
            raise AttributeError("G6 refused: " + module.__name__ + "." + name + " is another module")
        return found
    def __setattr__(self, name, value):
        raise AttributeError("G6 refused: a module is read-only here")
    def __dir__(self):
        return [n for n in dir(_get(self, "_module")) if not n.startswith("_")]
    def __repr__(self):
        return "<module " + _get(self, "_module").__name__ + ">"
def _guarded(name, globals=None, locals=None, fromlist=(), level=0):
    top = str(name).partition(".")[0]
    if globals is not _ns:
        return _real_import(name, globals, locals, fromlist, level)
    if level == 0 and top.startswith("_"):
        return _real_import(name, globals, locals, fromlist, level)   # a module's own private part, for its C code
    if level != 0 or top not in _ALLOWED:
        raise ImportError("G6 refused: importing " + str(name) + " is not allowed in this sandbox")
    return _Shown(_real_import(name, globals, locals, fromlist, level))
def final_answer(answer):
    try:
        text = _j.dumps(answer)
    except (TypeError, ValueError):
        text = _j.dumps(str(answer))
    with _open(%(final)r, "w", encoding="utf-8") as out:
        out.write(text)
    raise SystemExit(0)
_safe = {k: getattr(_b, k) for k in dir(_b) if not k.startswith("_") and k not in _REFUSED}
_safe["__build_class__"], _safe["__import__"] = _b.__build_class__, _guarded
_ns = {"__builtins__": _safe, "__name__": "__g6_step__", "final_answer": final_answer}
_ns.update(_j.loads(%(variables)r))
_r.seed(%(seed)d)
for _i, _code in enumerate(_j.loads(%(earlier)r)):
    with _c.redirect_stdout(_io.StringIO()), _c.redirect_stderr(_io.StringIO()):
        _exec(_compile(_code, "<earlier step %%d>" %% (_i + 1), "exec"), _ns)
_exec(_compile(_j.loads(%(now)r), "<this step>", "exec"), _ns)
'''


class CodeRefused(Exception):
    """Code the screen refused; it never ran."""


class CodeFailed(Exception):
    """Code that ran in the sandbox and failed, or ran out of time."""


def _named(name: str | None, what: str) -> str:
    """Why a name the code uses or defines is not allowed, in words; ``""`` when it is."""
    if name is not None and (name in REFUSED_NAMES or name.startswith("_")):
        return f"the {what} {name} is not allowed"
    return ""


def _rule(node: ast.AST) -> str:
    """Why ``node``, a construct on the list, breaks the name, attribute or import rules; ``""`` when it does not."""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        if isinstance(node, ast.ImportFrom) and node.level:
            return "a relative import is not allowed"
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
        for name in names:
            if name.partition(".")[0] not in ALLOWED_IMPORTS:
                return f"importing {name} is not allowed; the modules allowed are {', '.join(ALLOWED_IMPORTS)}"
        return ""
    if isinstance(node, ast.Name):
        return _named(node.id, "name")
    if isinstance(node, ast.Attribute):
        return f"the attribute {node.attr} is not allowed" if (node.attr.startswith("_") or
                                                               node.attr in FRAME_ATTRIBUTES) else ""
    if isinstance(node, ast.FunctionDef):
        return "a decorator is not allowed" if node.decorator_list else _named(node.name, "name")
    if isinstance(node, ast.arg):
        return _named(node.arg, "name")
    if isinstance(node, ast.keyword):
        return _named(node.arg, "name")
    if isinstance(node, ast.alias):
        return _named(node.asname, "name")
    if isinstance(node, ast.ExceptHandler):
        return _named(node.name, "name")
    return ""


def screen(code: str) -> str:
    """Why ``code`` may not run, in words, with the construct and its line; ``""`` when it may (the module
    docstring)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"it is not valid Python: {exc.msg} (line {exc.lineno})"
    stack: list[tuple[ast.AST, int]] = [(tree, 1)]
    while stack:
        node, line = stack.pop()
        line = getattr(node, "lineno", line)
        kind = type(node).__name__
        if kind not in ALLOWED_SYNTAX:
            return f"{_WORDS.get(kind, kind)} (line {line}) is not in the restricted syntax G6 accepts"
        why = _rule(node)
        if why:
            return f"{why} (line {line})"
        stack.extend((child, line) for child in reversed(list(ast.iter_child_nodes(node))))
    return ""


@dataclass
class SandboxSession:
    """One code agent's run: each step's code screened, then run in the sandbox after the earlier clean steps. A step
    gets ``timeout_s`` or, when ``remaining`` is given, the run's remaining seconds if they are fewer. ``seed`` seeds
    ``random`` before every replay, the same for the whole session. ``applied``: each run's limits and isolation, as
    ``run_script`` gave them (:meth:`record`)."""

    timeout_s: float = 30.0
    variables: dict[str, Any] = field(default_factory=dict)
    earlier: list[str] = field(default_factory=list)
    runs: int = 0
    remaining: Callable[[], float] | None = None
    seed: int = field(default_factory=lambda: secrets.randbits(32))
    applied: list[dict[str, Any]] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        """How this session's code steps ran: ``os_sandbox`` true only when at least one ran and every one ran
        confined by the operating system; ``isolation`` the first that was not, with its reason, else the first
        (``g6.core.confine.summary``; no run: none, "no untrusted code was run"); ``runs`` each run's limits."""
        return {"os_sandbox": bool(self.applied) and all(run["limits"].get("os_sandbox") is True
                                                         and run["isolation"].get("backend") for run in self.applied),
                "isolation": confine.summary([run["isolation"] for run in self.applied]),
                "runs": [run["limits"] for run in self.applied]}

    def run(self, code: str) -> tuple[Any, str, bool]:
        """``(output, what it printed, whether it gave the final answer)``; :class:`CodeRefused` or
        :class:`CodeFailed` otherwise."""
        why = screen(code)
        if why:
            raise CodeRefused(f"[{CODE_REFUSED}] G6 refused this code before running it: {why}")
        script = _PRELUDE % {"allowed": json.dumps(list(ALLOWED_IMPORTS)), "final": FINAL,
                             "refused": json.dumps(sorted(REFUSED_NAMES)), "hook": _HOOK, "seed": self.seed,
                             "events": json.dumps(sorted(REFUSED_EVENTS)),
                             "variables": json.dumps(self.variables, default=str), "earlier": json.dumps(self.earlier),
                             "now": json.dumps(code)}
        seconds = self.timeout_s if self.remaining is None else min(self.timeout_s, self.remaining())
        if seconds <= 0:
            raise CodeFailed("the run has no time left for this code step, so it was not run")
        self.runs += 1
        done = run_script(script, timeout_s=seconds, mem_mb=MEM_MB)
        self.applied.append({"limits": dict(done.limits), "isolation": dict(done.isolation) or {
            "backend": None, "why": done.stderr[-_TAIL:] or "the sandbox gave no isolation record"}})
        printed = done.stdout[-_TAIL * 4:]
        if done.status == "timeout":
            raise CodeFailed(f"the code {done.stderr.replace('the script ', '')}; what it printed: {printed[-_TAIL:]}")
        if done.status != "ok":
            raise CodeFailed(f"the code failed in G6's sandbox: {done.stderr[-_TAIL:]}\nwhat it printed: "
                             f"{printed[-_TAIL:]}")
        self.earlier.append(code)
        if FINAL in done.outputs:
            return json.loads(done.outputs[FINAL].decode("utf-8")), printed, True
        return None, printed, False


__all__ = ["ALLOWED_IMPORTS", "ALLOWED_SYNTAX", "CODE_REFUSED", "FRAME_ATTRIBUTES", "REFUSED_EVENTS", "REFUSED_NAMES",
           "CodeFailed", "CodeRefused", "SandboxSession", "refuse_event", "screen"]
