# SPDX-License-Identifier: Apache-2.0
"""The extension conformance kit (Phase 0.6): the definition of done's gates and depth checks as plain functions.

Each check takes a :class:`Target` (one component's name, package and files) and returns a list of :class:`Problem`
(file relative to ``oss/``, line, gate, message, and the flagged text a waiver's ``match`` is a substring of). A check
only reports what it found; nothing here fixes, labels or approves anything. So the same functions run over every
registered component (``test_conformance.py``) and over the deliberately bad fixture (``test_bad_fixture.py``).

A check marked ``extensions_only`` is required of family members and entry-point extensions and only reported for the
core components; every other check is required of every component. A check marked ``shared`` also runs over each
family's shared code (:func:`shared_targets`: the family package's files outside its members' folders), which belongs
to no member, and is held to the extensions' checks. ``g6.core`` is the home of the shared helpers (sandbox, http,
outside, llm) and is not a component, so it is never scanned. D7 is enforced by ``tests/core/test_component_skills.py``
and is not repeated here; D8 lands in 0.7.
"""
from __future__ import annotations

import ast
import contextlib
import functools
import importlib.metadata
import importlib.util
import inspect
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import tomllib
import typing
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import g6

OSS = Path(__file__).resolve().parents[2]
PYPROJECT = OSS / "pyproject.toml"
CLOSURES = OSS / "tools" / "data" / "extra_closures.json"
LICENCE_TABLE = OSS / "tools" / "data" / "licence_table.json"
CATALOGUE = OSS / "web" / "data" / "component_catalogue.json"
WAIVERS = Path(__file__).resolve().parent / "waivers.toml"
GATES = ("G1", "G2", "G5", "G6", "G7", "G8", "G9", "D2", "D4", "D6", "D9", "C1")
STATES = ("verified", "qualified-draft", "blocked-escalated")
#: An error that starts with a stable code of its own (the door's G6_E_INVOKE_FAILED is what an uncoded error becomes).
OWN_CODE = re.compile(r"^\[G6_E_[A-Z0-9_]+\]")
_G6_CODE = re.compile(r"^G6_E_[A-Z0-9_]+$")
#: D6: g6_find's default limit. A component must rank within it for each mode it guards, or, where more components
#: guard that mode than the limit holds, be outranked only by them (measured 2026-10-20: 55 of the core's 60 queries
#: rank first, 5 second, each of those a mode two components guard).
FIND_LIMIT = 5
#: D9: how long one child process may take to run one component's examples without one extra.
CHILD_TIMEOUT_S = 180

#: Filled by ``test_conformance.py`` with the hits of checks that are reported, not required (core components).
REPORTED: dict[str, dict[str, list[Problem]]] = {}


@dataclass(frozen=True)
class Problem:
    file: str      # relative to oss/, with forward slashes
    line: int      # 1-based; 0 for a hit with no source line (a runtime check)
    gate: str
    message: str
    text: str = ""  # the flagged source line; for a runtime check, the message

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.gate} {self.message}"


@dataclass(frozen=True)
class Target:
    name: str
    package: str
    root: Path
    files: tuple[Path, ...]
    family: str | None
    source: str  # "builtin" | "extension"
    skip: tuple[Path, ...] = ()  # folders under root that are not this target's (a family's members, for its shared code)

    @property
    def extension(self) -> bool:
        """A family member, a family's shared code or an entry-point extension: the checks marked ``extensions_only``
        are required of it."""
        return self.family is not None or self.source == "extension"

    @property
    def shared(self) -> bool:
        """A family's shared code (named ``g6.<family>``), not a registered component."""
        return self.family is not None and self.package == f"g6.{self.family}"

    @property
    def init(self) -> Path:
        return self.root / "__init__.py" if (self.root / "__init__.py").is_file() else self.files[0]


def target(name: str, registry: Any = None) -> Target:
    """The registered component ``name`` as a kit target (its package's ``.py`` files, found without importing it)."""
    from g6.core.registry import get_registry

    ref = (registry or get_registry()).refs[name]
    package = ref.module.split(":", 1)[0]
    spec = importlib.util.find_spec(package)
    if spec is None or spec.origin is None:
        raise LookupError(f"{name}: package {package} cannot be found")
    if spec.submodule_search_locations:
        root = Path(list(spec.submodule_search_locations)[0])
        files = tuple(sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts))
    else:
        root, files = Path(spec.origin).parent, (Path(spec.origin),)
    return Target(name, package, root, files, ref.family, ref.source)


def shared_target(family: str, registry: Any = None) -> Target:
    """A family's shared code as a kit target, named ``g6.<family>`` (a waiver names it so): every ``.py`` file of the
    family package outside its registered members' folders (its ``__init__.py``, its ``_``-prefixed modules and
    folders, anything else beside the members), found without importing it. So every file of a family is scanned once,
    by its member's target or by this one, also while the family has no member."""
    from g6.core.registry import get_registry

    package = f"g6.{family}"
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        raise LookupError(f"{family}: package {package} cannot be found")
    root = Path(list(spec.submodule_search_locations)[0])
    members = tuple(root / ref.module.split(":", 1)[0].split(".")[2]
                    for ref in (registry or get_registry()).refs.values() if ref.family == family)
    files = tuple(sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts
                         and not any(p.is_relative_to(m) for m in members)))
    return Target(package, package, root, files, family, "builtin", members)


def shared_targets(registry: Any = None) -> list[Target]:
    """Each family package's shared code (``g6.core.registry.FAMILIES``), one target per family that is present."""
    from g6.core import registry as registry_module

    out = []
    for family in sorted(registry_module.FAMILIES):
        with contextlib.suppress(LookupError, ModuleNotFoundError):
            out.append(shared_target(family, registry))
    return out


def rel(path: Path | str) -> str:
    """A path relative to ``oss/``; an installed ``g6`` maps to ``src/g6/...``, as in a checkout."""
    path = Path(path).resolve()
    with contextlib.suppress(ValueError):
        return path.relative_to(OSS).as_posix()
    for portion in g6.__path__:
        with contextlib.suppress(ValueError):
            return "src/g6/" + path.relative_to(Path(portion).resolve()).as_posix()
    return path.as_posix()


_TREES: dict[tuple[str, int, int], tuple[ast.Module, list[str]]] = {}


def _parsed(path: Path) -> tuple[ast.Module, list[str]]:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _TREES:
        source = path.read_text(encoding="utf-8")
        _TREES[key] = (ast.parse(source, filename=str(path)), source.splitlines())
    return _TREES[key]


def _at(path: Path, lines: list[str], lineno: int, gate: str, message: str) -> Problem:
    text = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    return Problem(rel(path), lineno, gate, message, text)


def _runtime(path: Path, gate: str, message: str) -> Problem:
    return Problem(rel(path), 0, gate, message, message)


def _module_of(t: Target, path: Path) -> str:
    """The dotted package a file's relative imports resolve against."""
    parts = path.relative_to(t.root).with_suffix("").parts if path.is_relative_to(t.root) else ()
    return ".".join([t.package, *parts[:-1]]) if parts else t.package


def _relative(name: str, level: int, base: str) -> str:
    """``name`` imported ``level`` dots up from the package ``base``."""
    if not level:
        return name
    up = base.split(".")
    up = up[: len(up) - (level - 1)] if level > 1 else up
    return ".".join(up + ([name] if name else []))


#: How a module is imported by name; ``__import__`` gives the top-level package.
_BY_NAME = frozenset({"importlib.import_module", "importlib.__import__", "builtins.__import__"})
#: Builtins the checks read through: a bare name stands for ``builtins.<name>`` unless an import rebinds it.
_BUILTINS = frozenset({"exec", "eval", "getattr", "__import__"})


def _by_name(call: ast.Call, names: Mapping[str, str]) -> str | None:
    """The module a call imports by name (``importlib.import_module("m")``, ``__import__("m")``, aliases too), when a
    string literal names it; relative names stay relative (leading dots)."""
    if not (call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
        return None
    return call.args[0].value if _absolute(call.func, names) in _BY_NAME else None


def _absolute(node: ast.AST, names: Mapping[str, str]) -> str:
    """What an expression names through the file's imports, as an absolute dotted name: ``sp.Popen`` after ``import
    subprocess as sp``, ``getattr(os, "system")``, ``importlib.import_module("os").system`` and ``os.system`` after
    ``import os.path`` all give ``os.system``. ``""`` when it is not reached through an import."""
    if isinstance(node, ast.Name):
        return names.get(node.id) or (f"builtins.{node.id}" if node.id in _BUILTINS else "")
    if isinstance(node, ast.Attribute):
        inner = _absolute(node.value, names)
        return f"{inner}.{node.attr}" if inner else ""
    if isinstance(node, ast.Call):
        if (_absolute(node.func, names) == "builtins.getattr" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            inner = _absolute(node.args[0], names)
            return f"{inner}.{node.args[1].value}" if inner else ""
        module = _by_name(node, names)
        if module and not module.startswith("."):
            return module.partition(".")[0] if _absolute(node.func, names).endswith("__import__") else module
    return ""


def _names(t: Target, path: Path, tree: ast.Module) -> dict[str, str]:
    """Each name the file binds to something imported, and what it stands for (an absolute dotted name): ``import
    a.b`` binds ``a``; ``import a.b as x`` binds ``x`` to ``a.b``; ``from a import b`` binds ``b`` to ``a.b``; and a
    plain assignment of one of these, or of a module imported by name (``m = importlib.import_module("a")``), binds
    its target too. One table per file: a name bound so anywhere counts everywhere."""
    base = _module_of(t, path)
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.partition(".")[0]
                names[alias.asname or top] = alias.name if alias.asname else top
        elif isinstance(node, ast.ImportFrom):
            module = _relative(node.module or "", node.level, base)
            for alias in node.names:
                if alias.name != "*":
                    names[alias.asname or alias.name] = f"{module}.{alias.name}"
    for _ in range(2):  # an alias of an alias
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and (found := _absolute(node.value, names))):
                names.setdefault(node.targets[0].id, found)
    return names


def _imports(t: Target, path: Path, tree: ast.Module,
             names: Mapping[str, str] | None = None) -> Iterable[tuple[ast.AST, str]]:
    """Each module an import brings in, by absolute dotted name: the import statements, and each module imported by
    name (``importlib.import_module("m")`` or ``__import__("m")`` with a string literal; the node is then the call).
    ``from a import b`` gives ``a.b`` (``b`` may be a name defined in ``a``, so callers match by prefix)."""
    base = _module_of(t, path)
    names = _names(t, path, tree) if names is None else names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = _relative(node.module or "", node.level, base)
            for alias in node.names:
                yield node, module if alias.name == "*" else f"{module}.{alias.name}"
        elif isinstance(node, ast.Call) and (module := _by_name(node, names)):
            stripped = module.lstrip(".")
            yield node, _relative(stripped, len(module) - len(stripped), base)


def _under(name: str, modules: Iterable[str]) -> str | None:
    for module in modules:
        if name == module or name.startswith(module + "."):
            return module
    return None


def _type_checking(tree: ast.Module) -> set[int]:
    """The nodes under an ``if TYPE_CHECKING:`` (its body only, which never runs)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _dotted(node.test) in ("TYPE_CHECKING", "typing.TYPE_CHECKING"):
            for stmt in node.body:
                out.update(id(n) for n in ast.walk(stmt))
    return out


def _strings(tree: ast.Module) -> dict[str, set[str]]:
    """Each name assigned a string literal anywhere in the file (module level or in a function), with every string it
    is assigned, so ``os.getenv(NAME)`` reads through a module constant or a local name."""
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
            node, (ast.AnnAssign, ast.NamedExpr)) else []
        value = getattr(node, "value", None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for tgt in targets:
                if isinstance(tgt, ast.Name):
                    found.setdefault(tgt.id, set()).add(value.value)
    return found


def _bound_once(tree: ast.Module) -> dict[str, ast.expr]:
    """The names bound exactly once in the whole file, by a plain ``name = value``, with that value: the constants G1
    reads through (a name rebound anywhere, a parameter or a loop variable is not one)."""
    plain: dict[int, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if len(targets) == 1 and isinstance(targets[0], ast.Name):
                plain[id(targets[0])] = node.value
    bound: dict[str, list[ast.expr | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.setdefault(node.id, []).append(plain.get(id(node)))
        elif isinstance(node, ast.arg):
            bound.setdefault(node.arg, []).append(None)
    return {name: values[0] for name, values in bound.items() if len(values) == 1 and values[0] is not None}


def _is_tests(t: Target, path: Path) -> bool:
    parts = path.relative_to(t.root).parts if path.is_relative_to(t.root) else (path.name,)
    return (any(p in ("tests", "test") for p in parts[:-1]) or path.name.startswith("test_")
            or path.name.endswith("_test.py") or path.name == "conftest.py")


# --- G1: nothing marks its own output verified ------------------------------------------------------------------------

LABELS = frozenset({"verified", "proved", "compliant", "certified", "safe", "cleared"})


def _flows(value: ast.expr) -> Iterable[ast.expr]:
    """The expressions that flow into a value unchanged: the branches of ``a if c else b`` and of ``a or b``."""
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.IfExp):
            stack += [node.body, node.orelse]
        elif isinstance(node, ast.BoolOp):
            stack += node.values
        else:
            yield node


def _number(node: ast.expr) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
    return isinstance(node, ast.Constant) and type(node.value) in (int, float)


def _target_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        return node.slice.value
    return ""


def g1_labels(t: Target) -> list[Problem]:
    """A label word (``verified``, ``proved``, ``compliant``, ``certified``, ``safe``, ``cleared``) given as a value: an
    assignment, a return or yield, a keyword argument, a dict value or a default, also picked out of a vocabulary by a
    fixed index (``STATES[0]``); and a number given to a name, key or keyword containing "confidence", as a literal or
    through a name bound once in the file to one (``_HIGH = 0.95``). Comparisons, membership tests, annotations,
    ``Literal`` types and vocabulary tuples or sets are not values given, so they are not flagged."""
    out: list[Problem] = []
    for path in t.files:
        tree, lines = _parsed(path)
        seen: set[tuple[int, str]] = set()
        once = _bound_once(tree)

        def constant(node: ast.expr) -> ast.expr | None:
            """What a name bound once stands for, or the element ``NAME[<int>]`` picks out of one."""
            if isinstance(node, ast.Name):
                return once.get(node.id)
            if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                    and isinstance(seq := once.get(node.value.id), (ast.Tuple, ast.List))
                    and isinstance(node.slice, ast.Constant) and type(node.slice.value) is int
                    and -len(seq.elts) <= node.slice.value < len(seq.elts)):
                return seq.elts[node.slice.value]
            return None

        def label(value: ast.expr, how: str) -> None:
            for node in _flows(value):
                picked = constant(node) if isinstance(node, ast.Subscript) else None
                word = picked if picked is not None else node
                if isinstance(word, ast.Constant) and isinstance(word.value, str) and word.value in LABELS:
                    if (node.lineno, word.value) not in seen:
                        seen.add((node.lineno, word.value))
                        source = f", from {ast.unparse(node)}" if picked is not None else ""
                        out.append(_at(path, lines, node.lineno, "G1", f"{word.value!r} given as {how}{source}"))

        def confidence(name: str, value: ast.expr, how: str) -> None:
            if "confidence" in name.lower():
                for node in _flows(value):
                    # a name containing "confidence" is flagged where it is bound, so it is not followed again here
                    follow = not (isinstance(node, ast.Name) and "confidence" in node.id.lower())
                    fixed = _number(node) or (follow and (c := constant(node)) is not None and _number(c))
                    if fixed and (node.lineno, name) not in seen:
                        seen.add((node.lineno, name))
                        out.append(_at(path, lines, node.lineno, "G1", f"a fixed confidence given to {how} {name!r}"))

        def assigned(targets: list[ast.expr], value: ast.expr) -> None:
            for tgt in targets:
                if isinstance(tgt, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
                    for sub_t, sub_v in zip(tgt.elts, value.elts):
                        assigned([sub_t], sub_v)
                    continue
                label(value, "an assignment value")
                confidence(_target_name(tgt), value, "the name")

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                assigned(node.targets, node.value)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None:
                assigned([node.target], node.value)
            elif isinstance(node, (ast.Return, ast.Yield)) and node.value is not None:
                values = node.value.elts if isinstance(node.value, ast.Tuple) else [node.value]
                for value in values:
                    label(value, "a return value")
            elif isinstance(node, ast.keyword) and node.arg:
                label(node.value, f"keyword {node.arg}=")
                confidence(node.arg, node.value, "the keyword")
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if key is None:
                        continue
                    label(value, "a dict value")
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        confidence(key.value, value, "the key")
            elif isinstance(node, ast.arguments):
                for value in [*node.defaults, *(d for d in node.kw_defaults if d is not None)]:
                    label(value, "a default")
    return out


# --- G2 / G9: untrusted code and the network --------------------------------------------------------------------------

#: Imports that reach the network (G9) or a terminal (G2); components reach the network only through g6.core.http.
NETWORK = ("socket", "urllib.request", "http.client", "requests", "httpx", "aiohttp", "smtplib", "ftplib",
           "urllib3", "websockets", "websocket", "paramiko", "xmlrpc.client", "imaplib", "poplib", "telnetlib")
TERMINAL = ("pty",)
#: subprocess calls that bound a child when they pass both timeout= and env= (neither None).
BOUNDED = frozenset({"run", "check_output", "call", "check_call"})
#: subprocess names that start nothing (constants, exceptions, the result type).
INERT = frozenset({"PIPE", "DEVNULL", "STDOUT", "TimeoutExpired", "CalledProcessError", "CompletedProcess",
                   "SubprocessError"})
#: asyncio's own functions that open or accept a connection.
ASYNC_NETWORK = frozenset({"open_connection", "open_unix_connection", "start_server", "start_unix_server"})
#: Event-loop methods that open a connection or start a program, whatever the loop object is called.
LOOP_NETWORK = frozenset({"create_connection", "create_datagram_endpoint", "create_unix_connection", "create_server",
                          "create_unix_server", "sock_connect"})
LOOP_PROCESS = frozenset({"subprocess_exec", "subprocess_shell"})


def _os_spawn(attr: str) -> bool:
    return attr in ("system", "popen") or attr.startswith(("exec", "spawn", "posix_spawn"))


class _Scopes:
    """Where each name of a file is bound, scope by scope (each function and the module): its plain assignments, its
    ``.update(...)`` and ``|=`` calls, and, for a parameter, its default. ``env=`` follows a name through these, from
    the function that makes the call out to the module; never through attributes, other calls or other files."""

    def __init__(self, tree: ast.Module) -> None:
        self.module = tree
        self.parent = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        self.bound: dict[tuple[int, str], list[ast.expr]] = {}
        self.params: set[tuple[int, str]] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = node.args
                positional = [*args.posonlyargs, *args.args]
                defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
                for arg, default in [*zip(positional, defaults), *zip(args.kwonlyargs, args.kw_defaults),
                                     *((a, None) for a in (args.vararg, args.kwarg) if a)]:
                    self.params.add((id(node), arg.arg))
                    if default is not None:
                        self.bound.setdefault((id(node), arg.arg), []).append(default)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                for tgt in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(tgt, ast.Name):
                        self.bind(node, tgt.id, node.value)
            elif (isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr)
                  and isinstance(node.target, ast.Name)):
                self.bind(node, node.target.id, node.value)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update"
                  and isinstance(node.func.value, ast.Name)):
                for value in [*node.args, *(k.value for k in node.keywords if k.arg is None)]:
                    self.bind(node, node.func.value.id, value)

    def scope(self, node: ast.AST) -> ast.AST:
        node = self.parent.get(id(node), self.module)
        while not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.Module)):
            node = self.parent.get(id(node), self.module)
        return node

    def bind(self, at: ast.AST, name: str, value: ast.expr) -> None:
        self.bound.setdefault((id(self.scope(at)), name), []).append(value)

    def values(self, name: str, at: ast.AST) -> list[ast.expr]:
        scope = self.scope(at)
        while True:
            key = (id(scope), name)
            if key in self.bound or key in self.params:
                return self.bound.get(key, [])
            if scope is self.module:
                return []
            scope = self.scope(scope)


def _chosen(test: ast.expr) -> bool:
    """A comprehension filter that keeps only chosen keys (``if k in KEEP``, alone or and-ed), not one that drops a
    few (``if k not in DROP``)."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(_chosen(v) for v in test.values)
    return isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.In)


def _inherited(value: ast.expr, scopes: _Scopes | None = None, names: Mapping[str, str] | None = None) -> bool:
    """``os.environ`` itself, a copy of it (``.copy()``, ``dict(...)``, ``copy.copy``, ``{**...}``, ``... | {...}``),
    a comprehension over it that does not keep only chosen keys, or a name bound to any of these where it is passed
    (:class:`_Scopes`): the parent's whole environment, not a clean one. A dict built from chosen keys is clean, and so
    is what any other call returns."""
    def environ(node: ast.expr) -> bool:
        return _dotted(node).endswith("environ") or (names is not None and _absolute(node, names) == "os.environ")

    def comprehension(node: ast.DictComp | ast.GeneratorExp) -> bool:
        for gen in node.generators:
            source = gen.iter
            if (isinstance(source, ast.Call) and isinstance(source.func, ast.Attribute)
                    and source.func.attr in ("items", "keys", "values")):
                source = source.func.value
            if inherited(source, frozenset()) and not any(_chosen(test) for test in gen.ifs):
                return True
        return False

    def inherited(node: ast.expr, followed: frozenset[str]) -> bool:
        for node in _flows(node):
            if environ(node):
                return True
            if isinstance(node, ast.Name) and scopes is not None and node.id not in followed:
                if any(inherited(v, followed | {node.id}) for v in scopes.values(node.id, node)):
                    return True
            elif isinstance(node, ast.Call):
                fn = _dotted(node.func)
                if fn == "dict" and node.args and isinstance(node.args[0], ast.GeneratorExp):
                    if comprehension(node.args[0]):
                        return True
                elif fn == "dict" and (node.args and inherited(node.args[0], followed)
                                       or any(k.arg is None and inherited(k.value, followed) for k in node.keywords)):
                    return True
                elif (isinstance(node.func, ast.Attribute) and node.func.attr == "copy"
                      and inherited(node.func.value, followed)):
                    return True
                elif fn.rpartition(".")[2] in ("copy", "deepcopy") and node.args and inherited(node.args[0], followed):
                    return True
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                if inherited(node.left, followed) or inherited(node.right, followed):
                    return True
            elif isinstance(node, ast.Dict):
                if any(k is None and inherited(v, followed) for k, v in zip(node.keys, node.values)):
                    return True
            elif isinstance(node, ast.DictComp) and comprehension(node):
                return True
        return False

    return inherited(value, frozenset())


def _bounded(call: ast.Call, scopes: _Scopes | None = None, names: Mapping[str, str] | None = None) -> bool:
    given = {k.arg: k.value for k in call.keywords if k.arg}
    return all(k in given and not (isinstance(given[k], ast.Constant) and given[k].value is None)
               for k in ("timeout", "env")) and not _inherited(given["env"], scopes, names)


def g2_g9_processes_and_network(t: Target) -> list[Problem]:
    """G9: imports of the network modules (``NETWORK``), by name too, and a use of one reached through another import
    (``urllib.request`` after ``import urllib.parse``); ``asyncio``'s connections and an event loop's
    ``create_connection`` and the like. G2: ``pty``; ``exec``/``eval``; ``os.system``, ``os.popen``, ``os.exec*``,
    ``os.spawn*``; ``asyncio.create_subprocess_*`` and a loop's ``subprocess_*``; any call with ``shell=`` other than
    ``False``; and every use of ``subprocess`` except a ``run``, ``check_output``, ``call`` or ``check_call`` that
    passes both ``timeout=`` and ``env=`` (a bounded child process; ``None`` for either, or an ``env=`` that is the
    parent's whole environment, :func:`_inherited`, is not bounded). Names are read through every import, alias,
    ``from`` import, ``getattr(module, "name")`` and module imported by name (:func:`_absolute`)."""
    out: list[Problem] = []
    for path in t.files:
        tree, lines = _parsed(path)
        names = _names(t, path, tree)
        scopes = _Scopes(tree)

        def flag(node: ast.AST, gate: str, message: str) -> None:
            out.append(_at(path, lines, node.lineno, gate, message))

        direct: set[str] = set()  # the network modules this file imports itself
        for node, name in _imports(t, path, tree, names):
            how = " by name" if isinstance(node, ast.Call) else ""
            if module := _under(name, NETWORK):
                direct.add(module)
                flag(node, "G9", f"imports {module}{how}: reach the network through g6.core.http")
            elif module := _under(name, TERMINAL):
                flag(node, "G2", f"imports {module}{how}")
            elif how and _under(name, ("subprocess",)):
                flag(node, "G2", f"imports {name} by name")
        handled: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            handled.add(id(node.func))
            if any(k.arg == "shell" and not (isinstance(k.value, ast.Constant) and not k.value.value)
                   for k in node.keywords):
                flag(node, "G2", "shell= runs a command line through the shell")
            full = _absolute(node.func, names)
            head, _, attr = full.rpartition(".")
            method = node.func.attr if isinstance(node.func, ast.Attribute) and not full else ""
            if head == "subprocess":
                if attr not in INERT and not (attr in BOUNDED and _bounded(node, scopes, names)):
                    flag(node, "G2", f"subprocess.{attr} without both timeout= and a clean env=: an unbounded child "
                                     "process")
            elif head == "os" and _os_spawn(attr):
                flag(node, "G2", f"os.{attr}() starts a program")
            elif full in ("builtins.exec", "builtins.eval"):
                flag(node, "G2", f"{'' if isinstance(node.func, ast.Name) else 'builtins.'}{attr}() runs code in G6's "
                                 "process")
            elif head in ("asyncio", "asyncio.subprocess") and attr.startswith("create_subprocess"):
                flag(node, "G2", f"asyncio.{attr}() starts an unbounded program")
            elif head == "asyncio" and attr in ASYNC_NETWORK:
                flag(node, "G9", f"asyncio.{attr}() opens a connection: reach the network through g6.core.http")
            elif method in LOOP_PROCESS:
                flag(node, "G2", f"{method}() starts an unbounded program")
            elif method in LOOP_NETWORK:
                flag(node, "G9", f"{method}() opens a connection: reach the network through g6.core.http")
        seen: set[tuple[int, str]] = set()
        for node in ast.walk(tree):  # a callable passed around instead of called, and a network module not imported
            if id(node) in handled or not isinstance(node, (ast.Attribute, ast.Name, ast.Call)):
                continue
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                continue
            full = _absolute(node, names)
            head, _, attr = full.rpartition(".")
            if head == "subprocess" and attr not in INERT:
                flag(node, "G2", f"subprocess.{attr} used outside a bounded call")
            elif head == "os" and _os_spawn(attr):
                flag(node, "G2", f"os.{attr} used to start a program")
            elif full in ("builtins.exec", "builtins.eval"):
                flag(node, "G2", f"{attr} passed around: it runs code in G6's process")
            elif ((module := _under(full, NETWORK)) and module not in direct and not isinstance(node, ast.Name)
                  and (node.lineno, module) not in seen):
                seen.add((node.lineno, module))
                flag(node, "G9", f"uses {module} through import {full.partition('.')[0]}, without importing it: "
                                 "reach the network through g6.core.http")
    return out


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


# --- G6: model calls through one injected caller ----------------------------------------------------------------------

PROVIDERS = ("anthropic", "openai", "litellm", "google.generativeai", "google.genai", "cohere", "mistralai", "groq",
             "ollama")
PROVIDER_PREFIXES = ("langchain", "llama_index")
SECRET_NAME = re.compile(r".*(_KEY|_TOKEN|_SECRET)$", re.IGNORECASE)
#: A key's name as an environment variable spells it: read so from anything (a parameter, a copy), it is a key read.
ENV_SECRET = re.compile(r"[A-Z][A-Z0-9_]*(_KEY|_TOKEN|_SECRET)")
API_KEY = re.compile(r"[A-Z0-9_]*API_KEY")


def g6_models_and_keys(t: Target) -> list[Problem]:
    """Imports of a provider SDK, litellm, LangChain, LlamaIndex or the ollama library, by name too
    (``importlib.import_module("openai")``, ``__import__("litellm")``); environment reads (or writes) of a name ending
    ``_KEY``, ``_TOKEN`` or ``_SECRET`` (``os.environ[...]``, ``.get``/``.pop``/``.setdefault``, ``getenv``, ``in
    os.environ``); the same reads of an upper-case name so ending (``HF_TOKEN``) from anything else (a parameter that
    defaults to ``os.environ``, a copy of it); the name given as a literal or through any name assigned it in the file,
    module constant or local; and any string constant that is an ``API_KEY`` name, except as the name of a
    ``g6.core.keys.Key`` declaration. Model calls belong to ``g6.core.llm``; keys come only from the person's key file,
    through ``g6.core.keys`` (owner ruling, 2026-10-21)."""
    out: list[Problem] = []
    from_file = "keys come only from the person's key file (g6.core.keys)"
    for path in t.files:
        tree, lines = _parsed(path)
        names = _names(t, path, tree)
        declared = {id(arg) for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and _absolute(node.func, names) == "g6.core.keys.Key"
                    for arg in [*node.args[:1], *(k.value for k in node.keywords if k.arg == "name")]}
        for node, name in _imports(t, path, tree, names):
            how = " by name" if isinstance(node, ast.Call) else ""
            if (module := _under(name, PROVIDERS)) or name.split(".")[0].startswith(PROVIDER_PREFIXES):
                out.append(_at(path, lines, node.lineno, "G6", f"imports {module or name.split('.')[0]}{how}: model "
                                                               "calls go through g6.core.llm"))
        strings = _strings(tree)

        def environ(node: ast.expr) -> bool:
            return _absolute(node, names) == "os.environ"

        def resolved(node: ast.expr) -> list[str]:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return [node.value]
            return sorted(strings.get(node.id, ())) if isinstance(node, ast.Name) else []

        seen: set[tuple[int, str]] = set()

        def secret(node: ast.AST, arg: ast.expr | None, from_environ: bool) -> None:
            for name in resolved(arg) if arg is not None else []:
                if (node.lineno, name) in seen:
                    continue
                if from_environ and SECRET_NAME.match(name):
                    seen.add((node.lineno, name))
                    out.append(_at(path, lines, node.lineno, "G6", f"reads the environment for {name}: {from_file}"))
                elif ENV_SECRET.fullmatch(name):
                    seen.add((node.lineno, name))
                    out.append(_at(path, lines, node.lineno, "G6", f"reads {name}, a key's name: {from_file}"))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                arg = node.args[0] if node.args else None
                if (isinstance(fn, ast.Name) and fn.id == "getenv") or (isinstance(fn, ast.Attribute)
                                                                        and fn.attr == "getenv"):
                    secret(node, arg, True)
                elif isinstance(fn, ast.Attribute) and fn.attr in ("get", "pop", "setdefault"):
                    secret(node, arg, environ(fn.value))
            elif isinstance(node, ast.Subscript):
                secret(node, node.slice, environ(node.value))
            elif isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                if any(environ(c) for c in node.comparators):
                    secret(node, node.left, True)
            elif (isinstance(node, ast.Constant) and isinstance(node.value, str) and API_KEY.fullmatch(node.value)
                  and id(node) not in declared):
                if (node.lineno, f"named {node.value}") not in seen:
                    seen.add((node.lineno, f"named {node.value}"))
                    out.append(_at(path, lines, node.lineno, "G6", f"names the key {node.value} outside a "
                                                                   f"g6.core.keys.Key declaration: {from_file}"))
    return out


# --- G7: no test double leaves the block as a real result -------------------------------------------------------------

DOUBLE = re.compile(r"mock|fake|simulat", re.IGNORECASE)


def g7_doubles(t: Target) -> list[Problem]:
    """Identifiers and strings (docstrings too) matching mock, fake or simulat in the package outside its tests: one
    hit per line."""
    out: list[Problem] = []
    for path in t.files:
        if _is_tests(t, path):
            continue
        tree, lines = _parsed(path)
        found: dict[int, set[str]] = {}
        for node in ast.walk(tree):
            words: list[str] = []
            if isinstance(node, ast.Name):
                words = [node.id]
            elif isinstance(node, ast.Attribute):
                words = [node.attr]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                words = [node.name]
            elif isinstance(node, ast.arg):
                words = [node.arg]
            elif isinstance(node, ast.keyword) and node.arg:
                words = [node.arg]
            elif isinstance(node, ast.alias):
                words = [node.name, node.asname or ""]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                words = [node.value]
            for word in words:
                for match in DOUBLE.finditer(word):
                    found.setdefault(getattr(node, "lineno", 0), set()).add(match.group(0).lower())
        for lineno, matched in sorted(found.items()):
            out.append(_at(path, lines, lineno, "G7", f"names {', '.join(sorted(matched))} outside tests"))
    return out


# --- G8: nothing that must never ship ---------------------------------------------------------------------------------

HEADER = "# SPDX-License-Identifier: Apache-2.0"
VENDOR_DIRS = frozenset({"vendor", "vendored", "_vendor", "third_party", "thirdparty"})
_LICENCE_FILE = re.compile(r"^(LICEN[CS]E|COPYING)", re.IGNORECASE)


def g8_headers(t: Target) -> list[Problem]:
    """Every ``.py`` file starts with the project's licence header line."""
    out = []
    for path in t.files:
        _, lines = _parsed(path)
        if not lines or lines[0] != HEADER:
            out.append(_at(path, lines, 1, "G8", f"does not start with {HEADER!r}"))
    return out


def g8_vendored(t: Target) -> list[Problem]:
    """Each vendored project (a folder under a ``vendor``-like folder, or a folder holding ``VENDORED.md``) carries its
    licence file."""
    out: list[Problem] = []
    if not (t.root / "__init__.py").is_file():  # a single-module extension has no folder of its own to scan
        return out
    for folder in sorted(p for p in t.root.rglob("*") if p.is_dir() and "__pycache__" not in p.parts
                         and not any(p.is_relative_to(s) for s in t.skip)):
        projects: list[Path] = []
        if folder.name in VENDOR_DIRS:
            subdirs = [p for p in folder.iterdir() if p.is_dir() and p.name != "__pycache__"]
            projects = subdirs or [folder]
        elif (folder / "VENDORED.md").is_file():
            projects = [folder]
        for project in projects:
            if not any(_LICENCE_FILE.match(p.name) for p in project.iterdir() if p.is_file()):
                out.append(_runtime(project, "G8", f"vendored {project.name} carries no LICENSE file"))
    return out


def pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def closures() -> dict[str, Any]:
    return json.loads(CLOSURES.read_text(encoding="utf-8")) if CLOSURES.is_file() else {}


def g8_extras(t: Target) -> list[Problem]:
    """``__g6_extras__`` is ``{extra: (import names)}``, and each extra it names is one of ``pyproject.toml``'s and has
    a resolved closure in ``tools/data/extra_closures.json``."""
    from g6.core.registry import get_registry

    mod = get_registry().get_module(t.name)
    extras = getattr(mod, "__g6_extras__", {})
    if not isinstance(extras, Mapping) or not all(
            isinstance(k, str) and isinstance(v, (tuple, list)) and all(isinstance(n, str) for n in v)
            for k, v in extras.items()):
        return [_runtime(t.init, "G8", "__g6_extras__ is not {extra: (import names)}")]
    declared = pyproject()["project"].get("optional-dependencies", {})
    resolved = closures().get("extras", {})
    out = []
    for extra in extras:
        if extra not in declared:
            out.append(_runtime(t.init, "G8", f"extra {extra!r} is not in pyproject.toml"))
        elif extra not in resolved:
            out.append(_runtime(t.init, "G8", f"extra {extra!r} has no closure: run python tools/gen_extra_closures.py"))
    return out


def closure_problems(project: Mapping[str, Any] | None = None, data: Mapping[str, Any] | None = None) -> list[Problem]:
    """G8 (c): the closure file covers the core and every extra of ``pyproject.toml``, and was generated from each one's
    current requirement strings (so an edited extra fails offline until it is regenerated)."""
    project = pyproject()["project"] if project is None else project
    data = closures() if data is None else data
    where = rel(CLOSURES)
    if not data:
        return [Problem(where, 0, "G8", "there is no closure file: run python tools/gen_extra_closures.py",
                        "no closure file")]
    out = []
    if data.get("core", {}).get("requires") != list(project.get("dependencies", [])):
        out.append(Problem(where, 0, "G8", "core: the dependencies changed since the closures were generated",
                           "core: requirements changed"))
    declared = project.get("optional-dependencies", {})
    resolved = data.get("extras", {})
    for extra, requires in declared.items():
        if extra not in resolved:
            out.append(Problem(where, 0, "G8", f"{extra}: no closure (run python tools/gen_extra_closures.py)",
                               f"{extra}: no closure"))
        elif resolved[extra].get("requires") != list(requires):
            out.append(Problem(where, 0, "G8", f"{extra}: its requirements changed since the closures were generated "
                                               "(run python tools/gen_extra_closures.py)",
                               f"{extra}: requirements changed"))
    for extra in sorted(set(resolved) - set(declared)):
        out.append(Problem(where, 0, "G8", f"{extra}: a closure for an extra pyproject.toml does not declare",
                           f"{extra}: not declared"))
    return out


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def licence_table() -> dict[str, Any]:
    return json.loads(LICENCE_TABLE.read_text(encoding="utf-8"))


_TOKEN = re.compile(r"\(|\)|[^\s()]+")


def _leaf(ident: str, table: Mapping[str, Any]) -> str:
    ident = ident.rstrip("+")
    if any(re.fullmatch(p, ident, re.IGNORECASE) for p in table["banned_licences"]):
        return "banned"
    return "allowed" if ident.lower() in {a.lower() for a in table["allowed"]} else "unknown"


def _spdx_shaped(text: str, table: Mapping[str, Any]) -> bool:
    ids = [tok for tok in _TOKEN.findall(text) if tok not in ("(", ")") and tok.upper() not in ("AND", "OR", "WITH")]
    return bool(ids) and all(re.fullmatch(r"[A-Za-z0-9.+-]+", i) and (
        _leaf(i, table) != "unknown" or i.startswith("LicenseRef-")) for i in ids)


def expression_verdict(expression: str, table: Mapping[str, Any]) -> str:
    """``allowed``, ``banned`` or ``unknown`` for an SPDX expression: OR is allowed if any side is, AND only if every
    side is; an exception (``WITH``) adds permissions, so the licence it qualifies decides."""
    tokens, pos = _TOKEN.findall(expression), 0

    def peek() -> str:
        return tokens[pos].upper() if pos < len(tokens) else ""

    def atom() -> str:
        nonlocal pos
        if peek() == "(":
            pos += 1
            verdict = either()
            if peek() != ")":
                raise ValueError("unbalanced")
            pos += 1
            return verdict
        if pos >= len(tokens) or peek() in ("AND", "OR", "WITH", ")"):
            raise ValueError("an identifier is missing")
        verdict = _leaf(tokens[pos], table)
        pos += 1
        if peek() == "WITH":
            pos += 2
        return verdict

    def both() -> str:
        nonlocal pos
        verdicts = [atom()]
        while peek() == "AND":
            pos += 1
            verdicts.append(atom())
        return "banned" if "banned" in verdicts else "unknown" if "unknown" in verdicts else "allowed"

    def either() -> str:
        nonlocal pos
        verdicts = [both()]
        while peek() == "OR":
            pos += 1
            verdicts.append(both())
        if "allowed" in verdicts:
            return "allowed"
        return "banned" if all(v == "banned" for v in verdicts) else "unknown"

    try:
        verdict = either()
        return verdict if pos == len(tokens) else "unknown"
    except (ValueError, IndexError):
        return "unknown"


_BANNED_TEXT = re.compile(r"general public licen[cs]e|\bA?GPL|\bSSPL|server side public|non-?commercial|\bBUSL|"
                          r"business source", re.IGNORECASE)


def licence_verdict(name: str, meta: Mapping[str, Any], table: Mapping[str, Any],
                    scope: str | None = None) -> tuple[str, str]:
    """``(allowed | banned | unknown, why)`` for one pinned package, from its PyPI metadata and the licence table. A
    banned package name is banned whatever its licence; a reviewed override in the table decides a package whose
    metadata says nothing usable; otherwise every source of licence information present (the SPDX expression, the
    licence field when the table recognises it, the licence classifiers) must say allowed, and one must be present.
    An override's banned licence is allowed only by the owner's ruling it records, and only in the closures (``scope``:
    ``core`` or an extra) it names in ``only_in``."""
    key = _norm(name)
    if key in table["banned_packages"]:
        return "banned", f"a banned package ({table['banned_packages'][key]})"
    override = table["packages"].get(key)
    if override:
        verdict = expression_verdict(override["licence"], table)
        why = f"licence {override['licence']} (reviewed: {override['source']})"
        if verdict == "banned" and override.get("owner_ruling"):
            only = override.get("only_in", [])
            if scope in only:
                return "allowed", f"{why}; allowed in {scope} by the owner's ruling: {override['owner_ruling']}"
            return "banned", f"{why}; the owner allowed it only in {', '.join(only) or 'no closure'}"
        return verdict, why
    sources: list[tuple[str, str]] = []
    if expression := (meta.get("license_expression") or "").strip():
        sources.append(("license_expression", expression))
    if licence := (meta.get("license") or "").strip():
        alias = table["aliases"].get(licence.lower())
        if alias or _spdx_shaped(licence, table):
            sources.append(("license", alias or licence))
        elif _BANNED_TEXT.search(licence):
            sources.append(("license", "LicenseRef-Banned-Wording"))
    classifiers = [c for c in meta.get("classifiers", []) if c.startswith("License ::")]
    if classifiers:
        mapped = [table["classifiers"].get(c, "LicenseRef-Unrecognised-Classifier") for c in classifiers]
        sources.append(("classifiers", " AND ".join(mapped)))
    if not sources:
        return "unknown", "no licence metadata (add a reviewed override to the licence table)"
    verdicts = [(source, text, expression_verdict(text, table)) for source, text in sources]
    for wanted in ("banned", "unknown"):
        hit = [f"{source} {text!r}" for source, text, v in verdicts if v == wanted]
        if hit:
            return wanted, f"{wanted}: {'; '.join(hit)}"
    return "allowed", "; ".join(f"{source} {text!r}" for source, text, _ in verdicts)


def licence_problems(data: Mapping[str, Any] | None = None, table: Mapping[str, Any] | None = None) -> list[Problem]:
    """G8 (d): every package in every closure has a licence the table allows (banned names and licences fail, and so
    does anything the table cannot place)."""
    data = closures() if data is None else data
    table = licence_table() if table is None else table
    where = rel(CLOSURES)
    out = []
    scopes = [("core", data.get("core", {}))] + sorted(data.get("extras", {}).items())
    for scope, entry in scopes:
        for pin in entry.get("closure", []):
            name = pin.split("==", 1)[0]
            meta = data.get("packages", {}).get(pin)
            if meta is None:
                verdict, why = "unknown", "no metadata in the closure file"
            else:
                verdict, why = licence_verdict(name, meta, table, scope)
            if verdict != "allowed":
                line = f"{scope}: {pin} {why}"
                out.append(Problem(where, 0, "G8", line, line))
    return out


# --- G5: sensitive ops are refused on an untrusted surface ------------------------------------------------------------

def _ops_of(registry: Any, name: str) -> list[str]:
    result = registry.invoke(name, "ops")
    if result.__class__.__name__ != "Success":
        return []
    value = result.unwrap()
    listed = value.get("ops") if isinstance(value, dict) else None
    return [str(o) for o in listed] if isinstance(listed, list) else []


def g5_sensitive_ops(t: Target, registry: Any = None) -> list[Problem]:
    """``__g6_sensitive_ops__`` is a collection of op names, each one of the block's ops, and the registry refuses each
    on an untrusted surface (``block_sensitive=True``) with ``G6_E_SENSITIVE_COMPONENT_DENIED``."""
    from g6.core.registry import get_registry

    reg = registry or get_registry()
    mod = reg.get_module(t.name)
    declared = getattr(mod, "__g6_sensitive_ops__", ())
    if not isinstance(declared, (tuple, list, set, frozenset)) or not all(isinstance(o, str) for o in declared):
        return [_runtime(t.init, "G5", f"__g6_sensitive_ops__ is {declared!r}, not a collection of op names")]
    out = []
    ops = _ops_of(reg, t.name) if declared else []
    for op in sorted(declared):
        if op not in ops:
            out.append(_runtime(t.init, "G5", f"sensitive op {op!r} is not one of the block's ops"))
        result = reg.invoke(t.name, op, {}, block_sensitive=True)
        said = "" if result.__class__.__name__ == "Success" else str(result.failure())
        if not said.startswith("[G6_E_SENSITIVE_COMPONENT_DENIED]"):
            out.append(_runtime(t.init, "G5", f"sensitive op {op!r} is not refused on an untrusted surface"))
    return out


# --- D2: failure modes declared in code -------------------------------------------------------------------------------

def d2_failure_modes(t: Target) -> list[Problem]:
    """The FW-03 check ``tests/core`` runs (``g6.core.failure_modes.declaration_problems``), per component."""
    from g6.core.failure_modes import declaration_problems

    return [_runtime(t.init, "D2", problem) for problem in declaration_problems(t.package)]


# --- D4: stands alone -------------------------------------------------------------------------------------------------

def _block(t: Target, registry: Any = None) -> Any:
    from g6.core.registry import get_registry

    return (registry or get_registry()).get_class(t.name)


def d4_has_block(t: Target) -> list[Problem]:
    """``__g6_block__`` names a class the package defines (a core library may instead list a Python API in its skill,
    which ``tests/core/test_component_skills.py`` checks)."""
    from g6.core.registry import get_registry

    mod = get_registry().get_module(t.name)
    named = getattr(mod, "__g6_block__", None)
    if not named:
        return [_runtime(t.init, "D4", "declares no __g6_block__")]
    if not inspect.isclass(getattr(mod, named, None)):
        return [_runtime(t.init, "D4", f"__g6_block__ names {named!r}, which the package does not define")]
    return []


def d4_block(t: Target) -> list[Problem]:
    """Where there is a block: its ``infer`` takes a pydantic model that forbids extra fields, and it answers its
    ``ops`` op (a list of op names) and its ``help`` op."""
    from pydantic import BaseModel

    from g6.core.registry import get_registry

    cls = _block(t)
    if cls is None:
        return []
    out = []
    params = [p for p in inspect.signature(cls.infer).parameters.values() if p.name != "self"]
    model = typing.get_type_hints(cls.infer).get(params[0].name) if params else None
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        out.append(_runtime(Path(inspect.getsourcefile(cls) or t.init), "D4",
                            f"{cls.__name__}.infer does not take a pydantic model"))
    elif model.model_config.get("extra") != "forbid":
        path = Path(inspect.getsourcefile(model) or t.init)
        lineno = inspect.getsourcelines(model)[1]
        out.append(_at(path, _parsed(path)[1], lineno, "D4",
                       f"the input model {model.__name__} does not forbid extra fields (extra='forbid')"))
    reg = get_registry()
    for op in ("ops", "help"):
        try:
            answer = reg.invoke(t.name, op)
        except Exception as exc:  # noqa: BLE001 - a block that raises is what the check reports
            out.append(_runtime(t.init, "D4", f"the {op} op raised {type(exc).__name__}: {exc}"[:300]))
            continue
        if answer.__class__.__name__ != "Success":
            out.append(_runtime(t.init, "D4", f"the {op} op does not answer: {str(answer.failure())[:160]}"))
        elif op == "ops" and not _ops_of(reg, t.name):
            out.append(_runtime(t.init, "D4", "the ops op does not answer a list of op names"))
    return out


#: The ``g6.core`` helpers through which a non-sensitive op may start a program (G5): bounded, owned child processes.
LAUNCHERS = frozenset({"g6.core.component_process", "g6.core.outside"})
#: The ``os`` functions that start a program without ``subprocess``; ``offline`` records each call.
_OS_LAUNCH = ("system", "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn", "posix_spawnp", "startfile", "fork")


def _stem(program: object) -> str:
    name = Path(os.fsdecode(program) if isinstance(program, (str, bytes, os.PathLike)) else str(program)).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        name = name.removesuffix(suffix)
    return name


def _program(args: object, executable: object) -> str:
    """The program a ``Popen`` starts: its ``executable``, else the first word of ``args``."""
    if executable:
        return os.fsdecode(executable)
    if isinstance(args, (str, bytes)):
        text = os.fsdecode(args)
        with contextlib.suppress(ValueError):
            return (shlex.split(text, posix=os.name != "nt") or [""])[0].strip('"')
        return text.split(" ", 1)[0]
    if isinstance(args, os.PathLike):
        return os.fsdecode(args)
    listed = list(args) if isinstance(args, Iterable) else []
    return os.fsdecode(listed[0]) if listed else ""


def _launched_by_core() -> bool:
    frame = sys._getframe(2)
    while frame is not None:
        if frame.f_globals.get("__name__") in LAUNCHERS:
            return True
        frame = frame.f_back
    return False


def _python(program: str) -> bool:
    """G6's own interpreter (this one, or the one ``g6.core.sandbox`` runs scripts with)."""
    from g6.core.sandbox import interpreter

    def same(a: str | None, b: str) -> bool:
        return bool(a) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    return same(sys.executable, program) or same(interpreter(), program)


def _declared(program: str, outside: Iterable[Any]) -> bool:
    """``program`` is one ``outside`` (a component's ``__g6_outside__``) names: its command, or the program its
    environment variable names."""
    names = set()
    for declared in outside:
        if getattr(declared, "command", ""):
            names.add(_stem(declared.command))
            if getattr(declared, "env", "") and os.environ.get(declared.env, "").strip():
                names.add(_stem(os.environ[declared.env].strip()))
    return _stem(program) in names


@contextlib.contextmanager
def offline(outside: Iterable[Any] = ()) -> Iterator[list[str]]:
    """No model (``G6_DISABLE_LLM=1``) and no socket connection while the block runs, and a record of what it reached
    for outside G6's process (yielded, one line each): every socket connection (refused), ``g6.core.http`` fetch (a
    download's hops too) and
    ``g6.core.outside.call_service`` call, and every program started (``subprocess``, ``os.system`` and the like),
    except one a ``LAUNCHERS`` helper starts for G6's own interpreter or for a program ``outside`` (the component's
    ``__g6_outside__``) declares: bounded local computation, such as a solver run through ``g6.core``."""
    from g6.core import http, outside as outside_mod

    reached: list[str] = []
    programs = tuple(outside)
    saved = (socket.socket.connect, socket.socket.connect_ex, http.Client._send, outside_mod.call_service,
             subprocess.Popen._execute_child, os.environ.get("G6_DISABLE_LLM"))
    saved_os = {name: getattr(os, name) for name in _OS_LAUNCH if hasattr(os, name)}
    saved_open = http.Client._open

    def refuse(sock: object, address: object, *args: object, **kwargs: object) -> None:
        reached.append(f"connected to {address!r}")
        raise OSError("the conformance kit runs offline")

    def fetch(client: object, method: str, url: str, *args: object, **kwargs: object) -> Any:
        reached.append(f"fetched {method} {url}")
        return saved[2](client, method, url, *args, **kwargs)

    def stream(client: object, method: str, url: str, *args: object, **kwargs: object) -> Any:
        reached.append(f"downloaded {method} {url}")
        return saved_open(client, method, url, *args, **kwargs)

    def service(program: object, method: str, path: str, *args: object, **kwargs: object) -> Any:
        reached.append(f"called the local service {getattr(program, 'name', program)!r} ({method} {path})")
        return saved[3](program, method, path, *args, **kwargs)

    def launch(popen: object, args: object, executable: object, *rest: object, **kwargs: object) -> Any:
        program = _program(args, executable)
        if not _launched_by_core():
            reached.append(f"started {_stem(program) or program!r} itself, not through g6.core")
        elif not (_python(program) or _declared(program, programs)):
            reached.append(f"started {_stem(program) or program!r}, which its __g6_outside__ does not declare")
        return saved[4](popen, args, executable, *rest, **kwargs)

    def os_launch(name: str) -> Callable[..., Any]:
        def launched(*args: object, **kwargs: object) -> Any:
            frame = sys._getframe(1)
            while frame is not None and frame.f_globals.get("__name__") != "subprocess":
                frame = frame.f_back
            if frame is None:  # subprocess's own use of it is recorded once, by launch
                reached.append(f"started a program through os.{name}")
            return saved_os[name](*args, **kwargs)
        return launched

    socket.socket.connect, socket.socket.connect_ex = refuse, refuse
    http.Client._send, outside_mod.call_service, subprocess.Popen._execute_child = fetch, service, launch
    http.Client._open = stream
    for name in saved_os:
        setattr(os, name, os_launch(name))
    os.environ["G6_DISABLE_LLM"] = "1"
    try:
        yield reached
    finally:
        socket.socket.connect, socket.socket.connect_ex = saved[0], saved[1]
        http.Client._send, outside_mod.call_service, subprocess.Popen._execute_child = saved[2], saved[3], saved[4]
        http.Client._open = saved_open
        for name, real in saved_os.items():
            setattr(os, name, real)
        if saved[5] is None:
            os.environ.pop("G6_DISABLE_LLM", None)
        else:
            os.environ["G6_DISABLE_LLM"] = saved[5]


def _outside(t: Target) -> tuple[Any, ...]:
    from g6.core.registry import get_registry

    declared = getattr(get_registry().get_module(t.name), "__g6_outside__", ())
    return tuple(declared) if isinstance(declared, (tuple, list)) else ()


def _reach_problems(path: Path, op: str, reached: list[str]) -> list[Problem]:
    """G5: a non-sensitive op that reached outside G6's process (``offline``'s record) should be a sensitive one."""
    if not reached:
        return []
    said = "; ".join(list(dict.fromkeys(reached))[:3])
    return [_runtime(path, "G5", f"{op}: not a sensitive op, yet it {said}: declare it in __g6_sensitive_ops__")]


def _own_code(result: Any) -> bool:
    if not isinstance(result, Mapping):
        return False
    card = result.get("warning_card")
    codes = [card.get("code") if isinstance(card, Mapping) else None, result.get("code"), result.get("error_code")]
    return any(isinstance(c, str) and _G6_CODE.match(c) for c in codes)


def _reply_problems(path: Path, gate: str, op: str, reply: Mapping[str, Any]) -> list[Problem]:
    """What is wrong with one door reply: a state outside the three; a refusal with no stable code of its own."""
    out = []
    if reply.get("completion_state") not in STATES:
        out.append(_runtime(path, gate, f"{op}: the reply's completion_state is {reply.get('completion_state')!r}"))
    if not reply.get("ok"):
        if not OWN_CODE.match(str(reply.get("error", ""))):
            out.append(_runtime(path, gate, f"{op}: refusal carries no stable G6_E_ code of its own: "
                                            f"{str(reply.get('error', ''))[:160]}"))
        return out
    result = reply.get("result")
    said = result.get("completion_state") if isinstance(result, Mapping) else None
    if said is not None and said not in STATES:
        out.append(_runtime(path, gate, f"{op}: reports completion_state {said!r}, not one of {', '.join(STATES)}"))
    if said == "blocked-escalated" and not _own_code(result):
        out.append(_runtime(path, gate, f"{op}: blocked-escalated with no G6_E_ code of its own"))
    return out


def _sensitive(t: Target) -> set[str]:
    from g6.core.registry import get_registry

    declared = getattr(get_registry().get_module(t.name), "__g6_sensitive_ops__", ())
    return set(declared) if isinstance(declared, (tuple, list, set, frozenset)) else set()


def d4_refusals(t: Target) -> list[Problem]:
    """Every non-sensitive op sent no parameters through ``g6_invoke``: a refusal is ``blocked-escalated`` with a
    stable ``G6_E_`` code of its own (not the door's catch-all), a result's ``completion_state`` is one of the three,
    and nothing raises. G5 from the other side: it reaches nothing outside G6's process (``offline``'s record)."""
    from g6.mcp import invoke_reply

    cls = _block(t)
    if cls is None:
        return []
    from g6.core.registry import get_registry

    where = Path(inspect.getsourcefile(cls) or t.init)
    out = []
    for op in _ops_of(get_registry(), t.name):
        if op in _sensitive(t):
            continue
        reached: list[str] = []
        try:
            with offline(_outside(t)) as reached:
                reply = invoke_reply(t.name, op, "{}")
        except Exception as exc:  # noqa: BLE001 - a block that raises is what the check reports
            out.append(_runtime(where, "D4", f"{op}: with no parameters it raised {type(exc).__name__}: {exc}"[:300]))
            out += _reach_problems(where, op, reached)
            continue
        out += _reply_problems(where, "D4", op, reply) + _reach_problems(where, op, reached)
    return out


def d4_isolation(t: Target) -> list[Problem]:
    """No import of another family's member, nor of a sibling member (``g6.<family>.<other>``), by name too; a
    family's ``_``-prefixed shared code is allowed. A family's shared code imports none of the family's members."""
    from g6.core.registry import FAMILIES

    own = t.package.split(".")
    own_family, own_member = (own[1], own[2]) if len(own) > 2 and own[0] == "g6" else (t.family, None) if t.shared \
        else (None, None)
    out = []
    for path in t.files:
        tree, lines = _parsed(path)
        for node, name in _imports(t, path, tree):
            parts = name.split(".")
            if len(parts) > 2 and parts[0] == "g6" and parts[1] in FAMILIES and not parts[2].startswith("_"):
                if (parts[1], parts[2]) != (own_family, own_member):
                    kind = ("another family's member" if parts[1] != own_family else "a sibling member" if own_member
                            else "a member of its own family")
                    out.append(_at(path, lines, node.lineno, "D4", f"imports {kind}, g6.{parts[1]}.{parts[2]}"))
    return out


def d4_home(t: Target) -> list[Problem]:
    """No ``Path.home()`` or ``expanduser`` state: durable state lives under ``g6.core.storage.state_path``."""
    out = []
    for path in t.files:
        tree, lines = _parsed(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr, owner = node.func.attr, _dotted(node.func.value)
                if attr == "home" and owner.endswith("Path") or attr == "expanduser":
                    out.append(_at(path, lines, node.lineno, "D4", f"{owner + '.' if owner else ''}{attr}() state: "
                                                                   "use g6.core.storage.state_path"))
    return out


# --- D6: reachable over MCP -------------------------------------------------------------------------------------------

def d8_page_truth(t: Target, catalogue: Mapping[str, Any] | None = None) -> list[Problem]:
    """D8, the page tells the truth: an extension has a row on the Components page (the catalogue made from the
    extension tracker) under the package the registry finds it in, so the page reads its lines from that package; and
    one in a vetted area (the row's ``vetting`` kinds) declares its review state, so the page shows it as awaiting its
    certificate until that certificate is current (``g6.core.vetting``; a missing declaration fails closed). No
    qualified person's signature is awaited (DECISIONS #39)."""
    from g6.core import vetting

    data = catalogue if catalogue is not None else json.loads(CATALOGUE.read_text(encoding="utf-8"))
    row = next((r for r in data["components"] if r["name"] == t.name), None)
    out = []
    if row is None or row.get("hidden"):
        out.append(_runtime(t.init, "D8", f"{t.name}: no row on the Components page; add it to the extension tracker "
                                          "and run web/data/from_tracker.py"))
    elif row["package"] != t.package:
        out.append(_runtime(t.init, "D8", f"{t.name}: the Components page lists it as {row['package']}, but the "
                                          f"registry finds it in {t.package}"))
    required = list(row.get("vetting", ())) if row and not row.get("hidden") else []
    return out + [_runtime(t.init, "D8", problem) for problem in vetting.problems(t.package, required)]


def d6_find(t: Target) -> list[Problem]:
    """``g6_find``, asked each declared mode's title, ranks the component within ``FIND_LIMIT``, or, where more
    components guard that mode, among them."""
    from g6.core import discovery
    from g6.core.failure_modes import FailureModeRegistry, declared
    from g6.core.registry import get_registry

    reg = get_registry()
    mitigates = declared(t.package).get("__g6_mitigates__", ())
    out = []
    for code in mitigates:
        if not FailureModeRegistry.is_registered(code):
            out.append(_runtime(t.init, "D6", f"{code!r} is not a registered failure mode, so g6_find cannot find it "
                                              "by it"))
            continue
        title = FailureModeRegistry.title(code)
        guards = sum(code in declared(reg.refs[n].module.split(":", 1)[0]).get("__g6_mitigates__", ())
                     for n in reg.names())
        allowed = max(FIND_LIMIT, guards)
        ranked = [r["component"] for r in discovery.find(title, limit=max(allowed, len(reg.names())))]
        rank = ranked.index(t.name) + 1 if t.name in ranked else None
        if rank is None or rank > allowed:
            out.append(_runtime(t.init, "D6", f"g6_find({title!r}) ranks it {rank or 'nowhere'}, not within "
                                              f"{allowed} ({code})"))
    return out


def d6_describe(t: Target) -> list[Problem]:
    """``g6_describe`` returns the component's own skill."""
    from g6.core.skills import front_matter, skill_name
    from g6.mcp import describe_reply

    skill = describe_reply(t.name).get("skill")
    if not skill:
        return [_runtime(t.init, "D6", "g6_describe returns no skill")]
    named = front_matter(skill).get("name")
    if named != skill_name(t.name):
        return [_runtime(t.init, "D6", f"g6_describe returns a skill named {named!r}, not {skill_name(t.name)!r}")]
    return []


def _skill_path(t: Target) -> Path:
    from g6.core.skills import skill_dir

    found = skill_dir(t.name)
    return found / "SKILL.md" if found else t.init


def d6_examples(t: Target) -> list[Problem]:
    """Every non-sensitive op has at least one ``g6-example`` in the skill, and each example round-trips through
    ``g6_invoke`` with no model and no socket: an envelope (ok, or a refusal with its own ``G6_E_`` code), never a
    traceback; and (G5) it reaches nothing outside G6's process (``offline``'s record)."""
    from g6.core import skills
    from g6.mcp import invoke_reply

    from g6.core.registry import get_registry

    ops = _ops_of(get_registry(), t.name)
    if not ops:
        return []
    where = _skill_path(t)
    try:
        examples = skills.skill_examples(skills.skill_text(t.name) or "")
    except ValueError as exc:
        return [_runtime(where, "D6", str(exc))]
    sensitive = _sensitive(t)
    out = [_runtime(where, "D6", f"op {op!r} has no g6-example in the skill")
           for op in ops if op not in sensitive and op not in {o for o, _ in examples}]
    outside = _outside(t)
    for op, params in examples:
        if op in sensitive:
            continue
        reached: list[str] = []
        try:
            with offline(outside) as reached:
                reply = invoke_reply(t.name, op, json.dumps(params))
        except Exception as exc:  # noqa: BLE001 - a block that raises is what the check reports
            out.append(_runtime(where, "D6", f"the {op} example raised {type(exc).__name__}: {exc}"[:300]))
            out += _reach_problems(where, f"the {op} example", reached)
            continue
        out += _reply_problems(where, "D6", f"the {op} example", reply)
        out += _reach_problems(where, f"the {op} example", reached)
    return out


# --- D9: heavy dependencies are optional ------------------------------------------------------------------------------

#: What an honest answer without an extra says (lower case), in a reply's message fields: its ``error``, and the
#: string values of its own and its result's ``warning_card`` and of the result's ``error`` and ``message``; never a
#: key or another field (``"fallback": false`` says nothing).
HONEST = ("not installed", "pip install", "g6[", "unavailable", "fallback", "fell back")
#: What a Python example's own values (or its output, or what it raised) must say once it reached for the missing extra.
INSTALL = ("not installed", "pip install", "g6[")

_CHILD = r'''
import contextlib, importlib.abc, importlib.util, io, json, os, socket, sys, types
args = json.loads(sys.argv[1])
sys.path.insert(0, args["src"])
BLOCKED = frozenset(args["blocked"])
fired = [0]
top = lambda name: (name or "").partition(".")[0]
class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if top(name) in BLOCKED:
            fired[0] += 1
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None
sys.meta_path.insert(0, Absent())
for loaded in [m for m in sys.modules if top(m) in BLOCKED]:
    del sys.modules[loaded]
real_find_spec = importlib.util.find_spec
def find_spec(name, package=None):
    if top(name) in BLOCKED:
        fired[0] += 1
        return None
    return real_find_spec(name, package)
importlib.util.find_spec = find_spec
def offline(*a, **k):
    raise OSError("the D9 check runs offline")
socket.socket.connect = socket.socket.connect_ex = offline
def strings(value, found):
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            strings(item, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            strings(item, found)
    return found
def said(reply):
    found = strings(reply.get("error"), [])
    strings(reply.get("warning_card"), found)
    result = reply.get("result")
    if isinstance(result, dict):
        for key in ("warning_card", "error", "message"):
            strings(result.get(key), found)
    return " ".join(found)[:20000].lower()
def values(namespace):
    found = []
    for key, value in namespace.items():
        if key.startswith("__") or isinstance(value, (types.ModuleType, type, types.FunctionType,
                                                      types.BuiltinFunctionType)):
            continue
        try:
            found.append(repr(value)[:4000])
        except Exception:
            pass
    return found
out = {"import": None, "ops": [], "python": []}
name = args["component"]
try:
    import g6
    g6.__path__.extend(p for p in args["g6_paths"] if p not in g6.__path__)
    from g6.core import registry, skills
    registry.FAMILIES = registry.FAMILIES | frozenset(args["families"])
    from g6.mcp import invoke_reply
    reg = registry.get_registry()
    mod = reg.get_module(name)
    reg.get_class(name)
except BaseException as exc:
    out["import"] = f"{type(exc).__name__}: {exc}"
else:
    text = skills.skill_text(name) or ""
    sensitive = set(getattr(mod, "__g6_sensitive_ops__", ()) or ())
    examples = []
    with contextlib.suppress(ValueError):
        examples = skills.skill_examples(text)
    for op, params in examples:
        if op in sensitive:
            continue
        before = fired[0]
        entry = {"op": op}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                reply = invoke_reply(name, op, json.dumps(params))
            entry["reply"] = {k: reply.get(k) for k in ("ok", "error", "completion_state")}
            entry["reply"]["result_state"] = (reply.get("result") or {}).get("completion_state") if isinstance(reply.get("result"), dict) else None
            card = (reply.get("result") or {}).get("warning_card") if isinstance(reply.get("result"), dict) else None
            entry["reply"]["own_code"] = isinstance(card, dict) and str(card.get("code", "")).startswith("G6_E_")
            entry["said"] = said(reply)
        except BaseException as exc:
            entry["raised"] = f"{type(exc).__name__}: {exc}"[:500]
        entry["fired"] = fired[0] - before
        out["ops"].append(entry)
    for index, code in enumerate(skills.skill_python_examples(text), 1):
        before = fired[0]
        label = f"<{name} skill example {index}>"
        entry = {"index": index, "raised": None, "own_assert": False}
        namespace = {"__name__": "__skill_example__"}
        printed = io.StringIO()
        message = ""
        try:
            with contextlib.redirect_stdout(printed):
                exec(compile(code, label, "exec"), namespace)
        except BaseException as exc:
            import traceback
            frames = traceback.extract_tb(exc.__traceback__)
            message = f"{type(exc).__name__}: {exc}"
            # the example's own assert failing: the component answered, differently, without the extra
            if isinstance(exc, AssertionError) and frames and frames[-1].filename == label:
                entry["own_assert"] = True
            else:
                entry["raised"] = message[:500]
        entry["said"] = " ".join([message, printed.getvalue()[-4000:], *values(namespace)])[:40000].lower()
        entry["fired"] = fired[0] - before
        out["python"].append(entry)
print("\nD9-RESULT " + json.dumps(out))
'''


def _extras_of(t: Target) -> dict[str, tuple[str, ...]]:
    from g6.core.registry import get_registry

    extras = getattr(get_registry().get_module(t.name), "__g6_extras__", {})
    return {k: tuple(v) for k, v in extras.items()} if isinstance(extras, Mapping) else {}


def _run_child(t: Target, extra: str, blocked: tuple[str, ...], families: list[str]) -> list[Problem]:
    where = _skill_path(t)
    home = Path(g6.__file__).resolve().parent
    args = {"component": t.name, "blocked": list(blocked), "src": str(home.parent), "families": families,
            "g6_paths": [str(Path(p).resolve()) for p in g6.__path__ if Path(p).resolve() != home]}
    with tempfile.TemporaryDirectory(prefix="g6-d9-") as scratch:
        env = {k: v for k, v in os.environ.items() if k != "G6_MCP_SENSITIVE"}
        env.update({"G6_HOME": str(Path(scratch) / "home"), "G6_DISABLE_LLM": "1", "HOME": scratch,
                    "USERPROFILE": scratch, "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            done = subprocess.run([sys.executable, "-I", "-c", _CHILD, json.dumps(args)], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", cwd=scratch, env=env,
                                  timeout=CHILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return [_runtime(where, "D9", f"without g6[{extra}] its examples did not finish in {CHILD_TIMEOUT_S}s")]
    marker = [ln for ln in done.stdout.splitlines() if ln.startswith("D9-RESULT ")]
    if not marker:
        return [_runtime(where, "D9", f"without g6[{extra}] the check's child died: {done.stderr[-300:]}")]
    found = json.loads(marker[-1][len("D9-RESULT "):])
    if found["import"]:
        return [_runtime(where, "D9", f"without g6[{extra}] the component does not import: {found['import']}")]
    out = []
    for entry in found["ops"]:
        op = entry["op"]
        if "raised" in entry:
            out.append(_runtime(where, "D9", f"without g6[{extra}], {op} raised {entry['raised']}"))
            continue
        reply = entry["reply"]
        honest = any(word in entry["said"] for word in HONEST)
        refused = not reply["ok"] or reply["result_state"] == "blocked-escalated"
        if refused:
            coded = OWN_CODE.match(str(reply["error"] or "")) if not reply["ok"] else reply["own_code"]
            if not coded:
                out.append(_runtime(where, "D9", f"without g6[{extra}], {op} refuses with no G6_E_ code of its own"))
            elif entry["fired"] and not honest:
                out.append(_runtime(where, "D9", f"without g6[{extra}], {op} refuses without saying the extra is "
                                                 "not installed"))
        elif entry["fired"] and not honest:
            out.append(_runtime(where, "D9", f"without g6[{extra}], {op} reached for {', '.join(blocked)} and answered "
                                             "as if nothing were missing (a silent fallback)"))
    for entry in found["python"]:
        raised = entry["raised"]
        if raised and not any(word in raised.lower() for word in ("pip install", "g6[")):
            out.append(_runtime(where, "D9", f"without g6[{extra}], Python example {entry['index']} raised {raised}"
                                             [:400]))
        elif not raised and entry["fired"] and not any(word in entry["said"] for word in INSTALL):
            how = "its own assertion failed" if entry["own_assert"] else "it ran to the end"
            out.append(_runtime(where, "D9", f"without g6[{extra}], Python example {entry['index']} reached for "
                                             f"{', '.join(blocked)} and {how}, and nothing it holds, printed or raised "
                                             "says the extra is not installed (a silent fallback)"))
    return out


def d9_extras_many(targets: Iterable[Target]) -> dict[str, list[Problem]]:
    """D9 for many components at once: one child process per component and extra, run side by side."""
    from g6.core.registry import FAMILIES

    families = sorted(FAMILIES)
    targets = list(targets)
    jobs = [(t, extra, names) for t in targets for extra, names in _extras_of(t).items()]
    out: dict[str, list[Problem]] = {t.name: [] for t in targets}
    with ThreadPoolExecutor(max_workers=max(1, min(8, os.cpu_count() or 2))) as pool:
        futures = [(t, pool.submit(_run_child, t, extra, names, families)) for t, extra, names in jobs]
        for t, future in futures:
            out[t.name] += future.result()
    return out


def d9_extras(t: Target) -> list[Problem]:
    """For each ``__g6_extras__`` entry, the skill's examples run in a child process with that extra's import names
    blocked (a meta-path finder raising ``ModuleNotFoundError``; ``find_spec`` answers ``None``): every op answers,
    and a refusal names its own ``G6_E_`` code and says the extra is not installed; an op that reached for the missing
    package and answered without saying so in its message fields (``HONEST``) is a silent fallback; a Python example
    may raise only to say what to install, and one that reached for the missing package and then failed its own
    ``assert`` or ran to the end is a silent fallback unless what it holds (the values it bound), printed or raised
    says the extra is not installed (``INSTALL``). Sensitive ops are not run."""
    return d9_extras_many([t])[t.name]


@functools.cache
def _core_imports() -> frozenset[str]:
    """The import names of the core's own dependencies (``pyproject.toml`` ``[project].dependencies``), from the
    installed distributions' metadata."""
    wanted = {_norm(m.group(0)) for req in pyproject()["project"].get("dependencies", [])
              if (m := re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", req))}
    return frozenset(top for top, dists in importlib.metadata.packages_distributions().items()
                     if any(_norm(d) in wanted for d in dists))


def _declared_extras(t: Target) -> dict[str, tuple[str, ...]]:
    """A component's ``__g6_extras__``; for a family's shared code, every extra the family package or one of its
    members declares."""
    from g6.core.registry import get_registry

    if not t.shared:
        return _extras_of(t)
    reg = get_registry()
    modules = [importlib.import_module(t.package), *(reg.get_module(n) for n, ref in reg.refs.items()
                                                    if ref.family == t.family)]
    found: dict[str, tuple[str, ...]] = {}
    for module in modules:
        extras = getattr(module, "__g6_extras__", {})
        for extra, names in (extras.items() if isinstance(extras, Mapping) else ()):
            found[extra] = (*found.get(extra, ()), *(n for n in names if isinstance(n, str)))
    return found


def d9_imports(t: Target) -> list[Problem]:
    """Every import outside the package's tests (inside functions and by name too, not under ``if TYPE_CHECKING:``)
    is the standard library (``sys.stdlib_module_names``), ``g6``, one of the core's own dependencies, or an import
    name of an extra the component declares in ``__g6_extras__`` (:func:`_declared_extras`). Anything else is an
    extra D9 never takes away, so its absence is never tried. A component's ``certificate/`` folder (the one the
    certificate framework defines, directly in the component's folder; no other folder of that name) is not its code:
    nothing the component runs imports it, the issuing tool runs it only in its own sandbox child, and C1 holds it to
    its own import rule (a second route and a falsifier import only the standard library and the reference libraries,
    an adapter only those and g6; without that, the certificate cannot be issued or rechecked), so D9 skips it."""
    declared = {n.partition(".")[0] for names in _declared_extras(t).values() for n in names}
    allowed = {"g6", *sys.stdlib_module_names, *_core_imports(), *declared}
    out = []
    for path in t.files:
        if _is_tests(t, path) or path.parent == t.root / "certificate":
            continue
        tree, lines = _parsed(path)
        skip = _type_checking(tree)
        seen: set[tuple[int, str]] = set()
        for node, name in _imports(t, path, tree):
            top = name.partition(".")[0]
            if id(node) in skip or not top or top in allowed or (node.lineno, top) in seen:
                continue
            seen.add((node.lineno, top))
            out.append(_at(path, lines, node.lineno, "D9", f"imports {top}, which is not the standard library, the "
                                                           "core's dependencies or an extra it declares in "
                                                           "__g6_extras__"))
    return out


# --- C1: a committed component certificate --------------------------------------------------------------------------

def _certificate_history_positions(bundle):
    """Use the issuance tool's one authoritative resolver; repository history is required in CI."""
    spec = importlib.util.spec_from_file_location("g6_certificate_history_tool", OSS / "tools/issue_certificate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.history_positions(bundle)


def c1_certificate(t: Target, *, today=None) -> list[Problem]:
    """Every vetted component ships a certificate whose exact observations rerun offline."""
    from g6.core import component_certificate as records
    from g6.core import vetting
    from g6.workforce import component_certificate as certificates

    if vetting.declared(t.package) is None:
        return []
    path = t.root / "certificate/certificate.json"
    if not path.is_file():
        return [_runtime(t.init, "C1", "missing certificate")]
    try:
        bundle = certificates.load(t.package)
        body = records.read_json(path)
        try:
            positions = _certificate_history_positions(bundle)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return [_runtime(t.init, "C1", f"certificate history unavailable or recorded commit unreachable: {exc}")]
        if asdict(positions) != body.get("positions"):
            return [_runtime(t.init, "C1", "certificate history positions differ or a recorded commit is unreachable")]
        problems = certificates.verify(bundle, body, today=today)
        if path.read_bytes() != records.canonical(body):
            problems.append("certificate JSON bytes differ from canonical output")
        md = t.root / "certificate/CERTIFICATE.md"
        if not md.is_file() or md.read_bytes() != certificates.markdown(body):
            problems.append("generated certificate Markdown differs")
        return [_runtime(t.init, "C1", problem) for problem in problems]
    except (OSError, ValueError, TypeError, KeyError, SyntaxError) as exc:
        return [_runtime(t.init, "C1", f"certificate cannot be checked: {exc}")]

# --- the checks -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    name: str
    gates: str
    run: Callable[[Target], list[Problem]]
    extensions_only: bool = False
    shared: bool = False  # it reads only source, so it also runs over each family's shared code

    def required(self, t: Target) -> bool:
        return t.extension or not self.extensions_only


CHECKS: tuple[Check, ...] = (
    Check("c1_certificate", "C1", c1_certificate),
    Check("g1_labels", "G1", g1_labels, extensions_only=True, shared=True),
    Check("g2_g9_processes_and_network", "G2/G9", g2_g9_processes_and_network, shared=True),
    Check("g5_sensitive_ops", "G5", g5_sensitive_ops),
    Check("g6_models_and_keys", "G6", g6_models_and_keys, shared=True),
    Check("g7_doubles", "G7", g7_doubles, extensions_only=True, shared=True),
    Check("g8_headers", "G8", g8_headers, shared=True),
    Check("g8_vendored", "G8", g8_vendored, shared=True),
    Check("g8_extras", "G8", g8_extras),
    Check("d2_failure_modes", "D2", d2_failure_modes),
    Check("d4_has_block", "D4", d4_has_block, extensions_only=True),
    Check("d4_block", "D4", d4_block),
    Check("d4_refusals", "D4/G5", d4_refusals),
    Check("d4_isolation", "D4", d4_isolation, extensions_only=True, shared=True),
    Check("d4_home", "D4", d4_home, extensions_only=True, shared=True),
    Check("d6_find", "D6", d6_find),
    Check("d6_describe", "D6", d6_describe),
    Check("d6_examples", "D6/G5", d6_examples, extensions_only=True),
    Check("d8_page_truth", "D8", d8_page_truth, extensions_only=True),
    Check("d9_extras", "D9", d9_extras),
    Check("d9_imports", "D9", d9_imports, extensions_only=True, shared=True),
)


# --- waivers ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Waiver:
    component: str
    gate: str
    file: str      # relative to oss/; a folder ending in "/" covers the files under it
    match: str     # a substring of the flagged line (a runtime hit's line is its message)
    reason: str
    finding: str   # where it is tracked: a tracker row, a ledger letter, a DECISIONS number, or "permanent: <why>"

    def covers(self, component: str, problem: Problem) -> bool:
        same_file = problem.file == self.file or (self.file.endswith("/") and problem.file.startswith(self.file))
        return (component == self.component and problem.gate == self.gate and same_file and bool(self.match)
                and self.match in problem.text)


def load_waivers(path: Path = WAIVERS) -> list[Waiver]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [Waiver(**entry) for entry in data.get("waiver", [])]
