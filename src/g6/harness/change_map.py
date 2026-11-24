# SPDX-License-Identifier: Apache-2.0
"""Tier 0: AST-only leads, never review evidence or a decision.

Inputs are file texts and confined pytest collection results, never imported
modules in this process. ``exercised`` means a static named call is reachable
from a collected or explicitly stated body, not that the call ran. Expanding
file or class selections needs collection. Dynamic dispatch, reflection, fixtures,
external packages and arbitrary data flow are outside this map's view.
"""
from __future__ import annotations

import ast
import configparser
import fnmatch
import hashlib
import json
import time
import tomllib
import types
from bisect import bisect_left
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Mapping, Sequence

from g6.core import component_process
from g6.core.git_name import shown, shown_name

if TYPE_CHECKING:
    from g6.workforce.repo_tests import Change

# Run limits count distinct Python paths and distinct (path, source text) versions.
MAX_FILES = 512
MAX_FILE_CHARS = 100_000
MAX_FILE_BYTES = 100_000  # Held-file/blob I/O cap, checked before decoding.
MAX_TOTAL_CHARS = 2_000_000
# AST nodes and definitions are per source file, not cumulative across the index.
MAX_NODES = 100_000
MAX_SYMBOLS = 512
MAX_TARGETS = 512
MAX_CHECKS = 512
MAX_LITERAL_OUTPUT = 8192  # Characters in emitted, deduplicated literal source strings.
MAX_OUTPUT_CHARS = 2_000_000  # Compact, ASCII-escaped JSON characters.
MAX_WORK = 2_000_000
STATIC_LIMIT = ("Static named calls only: this cannot see dynamic dispatch, reflection, external code, fixture "
                "injection or arbitrary data flow; a named call may never execute. No execution coverage is claimed. "
                "Not checked against code written to deceive collection: repository code running inside the collection "
                "child can make a check look exercised, and the pre-launch read is not authority against it.")
_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)
PYTEST_CONFIGS = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")


def pytest_context_paths(paths, test_file=None):
    """Configuration inventory shared by the reader and static admission.

    Without a test path, retain every possible ancestor configuration. With
    one, only its directory and ancestors inside the repository apply.
    """
    directory = PurePosixPath(test_file).parent if test_file is not None else None
    return tuple(path for path in _each(paths)
                 if PurePosixPath(path).name in (*PYTEST_CONFIGS, "conftest.py") and
                 (directory is None or directory.is_relative_to(PurePosixPath(path).parent)))


class _Omitted(Exception):
    def __init__(self, check: str, reason: str):
        self.check, self.reason = check, reason


class _FileOmitted(Exception):
    """A file-local analysis failure; never a whole-run budget signal."""


class _FileFailed(Exception):
    def __init__(self, rel, reason, check=None):
        self.rel, self.reason = rel, reason
        self.check = check if check is not None else f"change map: {shown_name(rel)}"


@dataclass
class _Budget:
    deadline: float
    work: int = 0
    files: set = field(default_factory=set)
    sources: dict = field(default_factory=dict)
    total_chars: int = 0
    failed: dict = field(default_factory=dict)
    import_names: dict = field(default_factory=dict)


_BUDGET: ContextVar[_Budget | None] = ContextVar("change_map_budget", default=None)


@contextmanager
def _file(rel):
    """Attribute every local stage failure before rolling back its whole file."""
    try:
        yield
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure, _Omitted, _FileFailed):
        raise
    except Exception as exc:
        reason = exc.args[0] if isinstance(exc, _FileOmitted) else f"source analysis unavailable ({type(exc).__name__})"
        raise _FileFailed(rel, reason) from None


def _tick():
    budget = _BUDGET.get()
    if budget is not None:
        if time.monotonic() >= budget.deadline:
            raise _Omitted("change map", "the time budget is spent")
        component_process.checkpoint()
        budget.work += 1
        if budget.work > MAX_WORK:
            raise _Omitted("change map", "static analysis work limit reached")


def _each(items):
    for item in items:
        _tick()
        yield item


def _walk(node):
    pending = [node]
    while pending:
        _tick()
        item = pending.pop()
        yield item
        pending.extend(reversed(list(_each(ast.iter_child_nodes(item)))))


def empty() -> dict:
    """The shared neighbourhood record; later tiers own instances, cases and gates."""
    return {"targets": [], "instances": [], "cases": [], "gates": [], "skipped": []}


def skip(check: str, reason: str) -> dict:
    return {"tier": 0, "check": shown(check), "reason": shown(reason)}


def _name(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        _tick()
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id, *reversed(parts)])
    return ""


def _module(rel: str, roots: Sequence[str]) -> str:
    path = PurePosixPath(rel)
    for root in _each(sorted({"src", *roots} - {"", "."}, key=len, reverse=True)):
        if path.is_relative_to(root):
            path = path.relative_to(root)
            break
    parts = list(path.with_suffix("").parts)
    if parts[-1:] == ["__init__"]:
        parts.pop()
    return ".".join(shown_name(part) for part in _each(parts))


def _module_identities(paths, roots):
    """Import spellings are aliases; colliding files retain separate namespaces."""
    groups = {}
    budget = _BUDGET.get()
    for rel in _each(sorted(paths)):
        if not rel.endswith(".py"):
            continue
        key = (rel, tuple(roots))
        if budget is not None and rel in budget.failed and key not in budget.import_names:
            continue
        with _file(rel):
            name = budget.import_names.get(key) if budget is not None else None
            if name is None:
                name = _module(rel, roots)
                if budget is not None:
                    budget.import_names[key] = name
            groups.setdefault(name, []).append(rel)
    # Reserve a namespace root absent from every import spelling. A closing
    # marker prevents a file identity from equalling another file's symbol.
    marker = "@file"
    prefixes = {name.split(".")[0] for name in _each(groups)}
    while marker in prefixes:
        _tick()
        marker += "@"
    return {rel: name if len(files) == 1 else f"{marker}.{shown_name(rel)}>"
            for name, files in _each(groups.items()) for rel in _each(files)}


def _nodes(node: ast.AST, *, headers: bool = False, lambdas: bool = False):
    """One callable's code, excluding bodies of nested definitions."""
    if headers:
        pending = list(reversed(list(_each(ast.iter_child_nodes(node)))))
    else:
        pending = list(reversed(node.body)) if isinstance(node, (*_FUNCTION, ast.Module, ast.ClassDef)) else [node]
    while pending:
        _tick()
        item = pending.pop()
        yield item
        if not isinstance(item, (*_FUNCTION, ast.ClassDef)) and (lambdas or not isinstance(item, ast.Lambda)):
            pending.extend(reversed(list(_each(ast.iter_child_nodes(item)))))


def _added(before: str, after: str) -> list[int]:
    """Match equal line blocks, checking the deadline at every matching step.

    The position index avoids rescanning lines with no possible match. Repeated
    lines can exhaust the work budget; in that case the whole map is omitted.
    AST nodes and literal leaves are never part of this matching pass.
    """
    left, right = before.split("\n"), after.split("\n")
    positions = {}
    for j, line in _each(enumerate(right)):
        positions.setdefault(line, []).append(j)
    pending, added = [(0, len(left), 0, len(right))], []
    while pending:
        _tick()
        a, end_a, b, end_b = pending.pop()
        while a < end_a and b < end_b and left[a] == right[b]:
            _tick()
            a, b = a + 1, b + 1
        while a < end_a and b < end_b and left[end_a - 1] == right[end_b - 1]:
            _tick()
            end_a, end_b = end_a - 1, end_b - 1
        best_a, best_b, length, previous = a, b, 0, {}
        for i in _each(range(a, end_a)):
            current = {}
            matches = positions.get(left[i], [])
            start, stop = bisect_left(matches, b), bisect_left(matches, end_b)
            for offset in _each(range(start, stop)):
                j = matches[offset]
                size = previous.get(j - 1, 0) + 1
                current[j] = size
                if size > length:
                    best_a, best_b, length = i - size + 1, j - size + 1, size
            previous = current
        if length:
            pending.extend(((a, best_a, b, best_b), (best_a + length, end_a, best_b + length, end_b)))
        else:
            added.extend(_each(range(b + 1, end_b + 1)))
    return sorted(added)


def _touches(node: ast.AST, lines: Sequence[int]) -> bool:
    _tick()
    # _context supplies sorted lines, so a multiline expression costs one lookup.
    pos = bisect_left(lines, getattr(node, "lineno", 0))
    return pos < len(lines) and lines[pos] <= getattr(node, "end_lineno", 0)


def _is_test(rel: str) -> bool:
    path = PurePosixPath(rel)
    return (path.name.startswith("test_") or path.name.endswith("_test.py") or path.name == "conftest.py"
            or any(part in ("test", "tests", "testing") for part in _each(path.parts[:-1])))


@dataclass
class _Symbol:
    file: str
    module: str
    symbol: str
    owner: str
    node: ast.AST

    @property
    def binding(self) -> str:
        return f"{self.module}.{self.symbol}".lstrip(".")

    def record(self) -> dict:
        return {"file": self.file, "symbol": self.symbol, "binding": self.binding, "owner": self.owner,
                "line": self.node.lineno}


@dataclass(eq=False)
class _Scope:
    binding: str
    kind: str
    parent: _Scope | None
    file: str
    names: dict = field(default_factory=dict)
    globals: set = field(default_factory=set)
    nonlocals: set = field(default_factory=set)
    receiver: str = ""


@dataclass
class _Binding:
    node: ast.AST
    scope: _Scope
    value: object = None
    kind: str = "local"


class _Index:
    def __init__(self, texts: Mapping[str, str], roots: Sequence[str], skipped: list[dict], side: str,
                 identities=None):
        self._reset(skipped)
        identities = identities if identities is not None else _module_identities(texts, roots)
        imports = {}
        budget = _BUDGET.get() or _Budget(time.monotonic() + 2.0)
        for rel in _each(identities):
            with _file(rel):
                name = budget.import_names.get((rel, tuple(roots)))
                if name is None:
                    name = _module(rel, roots)
                imports.setdefault(name, []).append(rel)
        for name, paths in _each(imports.items()):
            if len(paths) > 1:
                self.ambiguous.add(name)
                for rel in _each(paths):
                    skipped.append(skip(f"change map: {side} {shown_name(rel)}",
                                        f"ambiguous import name {name}; file analysis uses its own path identity"))
        budget.files.update(rel for rel in _each(texts) if rel.endswith(".py"))
        if len(budget.files) > MAX_FILES:
            raise _Omitted("change map", "static analysis file limit reached")
        for rel in _each(sorted(texts)):
            if not rel.endswith(".py"):
                continue
            if rel in budget.failed:
                self.invalid.add(rel)
                continue
            text = texts[rel]
            if len(text) > MAX_FILE_CHARS:
                if _BUDGET.get() is not None:
                    raise _FileFailed(rel, "static analysis text limit reached", f"change map: {side} {shown_name(rel)}")
                self.invalid.add(rel)
                skipped.append(skip(f"change map: {side} {shown_name(rel)}", "static analysis text limit reached"))
                continue
            versions = budget.sources.setdefault(rel, set())
            if text not in versions:
                versions.add(text)
                budget.total_chars += len(text)
                if budget.total_chars > MAX_TOTAL_CHARS:
                    raise _Omitted("change map", "total text limit reached")
            # Stage a file independently. A parse/index failure cannot leave half
            # its bindings in the shared index or discard another file's leads.
            staged = object.__new__(_Index)
            staged._reset([])
            staged.modules[rel] = identities[rel]
            try:
                staged._load_file(rel, text, roots)
                staged._finish_file()
            except (component_process.ExecutionInterrupted, component_process.ContainmentFailure, _Omitted, _FileFailed):
                raise
            except Exception as exc:
                reason = exc.args[0] if isinstance(exc, _FileOmitted) else f"source analysis unavailable ({type(exc).__name__})"
                if _BUDGET.get() is not None:
                    raise _FileFailed(rel, reason, f"change map: {side} {shown_name(rel)}") from None
                self.invalid.add(rel)
                skipped.append(skip(f"change map: {side} {shown_name(rel)}", reason))
                continue
            for name, values in _each(vars(staged).items()):
                if name == "skipped":
                    skipped.extend(values)
                else:
                    getattr(self, name).update(values)
        # Only indexed namespace prefixes can bind attributes. Long chains of
        # unknown external attributes must not repeatedly scan every suffix.
        self.prefix_depth = max((len(name.split(".")) + 1 for name in _each(self.namespaces)), default=1)

    def _finish_file(self):
        # All enclosing locals exist before resolving global/nonlocal writes.
        for scope in _each(list(self.scopes.values())):
            for name in _each(scope.globals | scope.nonlocals):
                entries = scope.names.pop(name, [])
                destination = self._declared(scope, name)
                if destination is not None:
                    destination.names.setdefault(name, []).extend(entries)
        for scope in _each(self.scopes.values()):
            for name, entries in _each(scope.names.items()):
                binding = f"{scope.binding}.{name}".lstrip(".")
                if len(entries) > 1:
                    self.ambiguous.add(binding)
                    self.skipped.append({**skip(binding, "ambiguous binding; candidates are listed"),
                                         "candidates": [self._candidate(e) for e in _each(entries)]})
                if scope.kind == "module" and len(entries) == 1 and entries[0].kind != "definition":
                    self.bindings[binding] = entries[0]

    def _reset(self, skipped):
        self.trees, self.symbols, self.classes, self.modules = {}, {}, {}, {}
        self.import_names = {}
        self.bindings, self.invalid, self.ambiguous = {}, set(), set()
        self.skipped = skipped
        self.scopes, self.node_scopes, self.symbol_scopes = {}, {}, {}
        self.namespaces, self.resolved, self.call_cache, self.mros = {}, {}, {}, {}
        self.lineages = {}
        self.compiled = {}
        self.static_scans = {}
        self.collection_names = {}
        self.sources, self.offsets, self.fingerprints, self.lines = {}, {}, {}, {}
        self.literal_nodes, self.literal_ranges = {}, {}

    def _load_file(self, rel, text, roots):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            raise _FileOmitted("syntax unavailable") from None
        _tick()
        nodes, definitions = [], 0
        for node in _walk(tree):
            nodes.append(node)
            if len(nodes) > MAX_NODES:
                raise _FileOmitted("AST node limit reached")
            if isinstance(node, (*_FUNCTION, ast.ClassDef)):
                definitions += 1
                if definitions > MAX_SYMBOLS:
                    raise _FileOmitted("symbol limit reached")
        self.sources[rel] = text.encode("utf-8")
        offsets, pos = [0], 0
        self.lines[rel] = tuple(self.sources[rel].split(b"\n"))
        for line in _each(self.lines[rel]):
            pos += len(line) + 1
            offsets.append(pos)
        self.offsets[rel] = tuple(offsets)
        signed = {id(n.operand) for n in _each(nodes) if isinstance(n, ast.UnaryOp)
                  and isinstance(n.op, (ast.USub, ast.UAdd)) and isinstance(n.operand, ast.Constant)}
        literals, pending, starts = [], [(tree, False)], {}
        while pending:
            _tick()
            node, closing = pending.pop()
            if closing:
                self.literal_ranges[id(node)] = (starts[id(node)], len(literals))
                continue
            starts[id(node)] = len(literals)
            if (isinstance(node, ast.Constant) and id(node) not in signed or
                    isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd))
                    and isinstance(node.operand, ast.Constant)):
                literals.append(node)
            pending.append((node, True))
            pending.extend((child, False) for child in _each(reversed(list(_each(ast.iter_child_nodes(node))))))
        self.literal_nodes[rel] = tuple(literals)
        # Hash every AST node once, bottom up. Nested functions do not trigger
        # repeated dumps of their entire enclosing bodies.
        for node in _each(reversed(nodes)):
            digest = hashlib.sha256(type(node).__name__.encode())
            for name, value in _each(ast.iter_fields(node)):
                digest.update(name.encode())
                values = value if isinstance(value, list) else [value]
                for item in _each(values):
                    digest.update(self.fingerprints[id(item)] if isinstance(item, ast.AST)
                                  else repr(item).encode("utf-8"))
            self.fingerprints[id(node)] = digest.digest()
        self.trees[rel] = tree
        self.modules.setdefault(rel, _module(rel, roots))
        self.import_names[rel] = _module(rel, roots)
        scope = _Scope(self.modules[rel], "module", None, rel)
        self.scopes[scope.binding] = scope
        self.namespaces[scope.binding] = scope
        self._scan(tree, scope)

    def _candidate(self, entry):
        value = entry.value if isinstance(entry.value, str) else _name(entry.value) if entry.value else ""
        return {"file": entry.scope.file, "line": entry.node.lineno,
                "kind": entry.kind, "binding": value or "unknown local"}

    def _bind(self, scope, name, node, value=None, kind="local"):
        _tick()
        scope.names.setdefault(name, []).append(_Binding(node, scope, value, kind))

    def _target_step(self, node, scope, value=None):
        self.node_scopes[id(node)] = scope
        if isinstance(node, ast.Name):
            self._bind(scope, node.id, node, value)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in _each(node.elts):
                yield child, scope, True, None
        elif isinstance(node, ast.Starred):
            yield node.value, scope, True, None
        else:
            yield node, scope, False, None

    def _scan(self, node, scope):
        # Suspend each scope walk on an explicit iterator stack. AST depth does
        # not consume Python frames; binding order remains source order.
        pending = [iter(self._scan_step(node, scope))]
        while pending:
            _tick()
            task = next(pending[-1], None)
            if task is None:
                pending.pop()
                continue
            child, destination, target, value = task
            pending.append(iter(self._target_step(child, destination, value) if target
                                else self._scan_step(child, destination)))

    def _scan_step(self, node, scope):
        _tick()
        self.node_scopes[id(node)] = scope
        if isinstance(node, (*_FUNCTION, ast.ClassDef, ast.Lambda)):
            is_lambda = isinstance(node, ast.Lambda)
            name = f"<lambda:{node.lineno}:{node.col_offset}>" if is_lambda else node.name
            qual = f"{scope.binding}.{name}".lstrip(".")
            if not is_lambda:
                self._bind(scope, name, node, qual, "definition")
                owner = scope.binding.removeprefix(self.modules[scope.file] + ".") if scope.kind == "class" else ""
                symbol = _Symbol(scope.file, self.modules[scope.file],
                                 qual.removeprefix(self.modules[scope.file] + "."), owner, node)
                collection = self.classes if isinstance(node, ast.ClassDef) else self.symbols
                # Keep the first candidate for identity only; lookup rejects all
                # ambiguous candidates and their bodies still get indexed.
                collection.setdefault(qual, symbol)
            child = _Scope(qual, "class" if isinstance(node, ast.ClassDef) else "function", scope, scope.file)
            self.scopes[f"{qual}@{node.lineno}"] = child
            self.namespaces.setdefault(qual, child)
            self.symbol_scopes[id(node)] = child
            if isinstance(node, (*_FUNCTION, ast.Lambda)):
                args = node.args
                positional = [*args.posonlyargs, *args.args]
                if (scope.kind == "class" and not is_lambda and positional and
                        not any(_name(d).split(".")[-1] == "staticmethod" for d in _each(node.decorator_list))):
                    child.receiver = positional[0].arg
                for arg in _each([*args.posonlyargs, *args.args, *args.kwonlyargs,
                                  *([args.vararg] if args.vararg else []), *([args.kwarg] if args.kwarg else [])]):
                    self._bind(child, arg.arg, arg)
                for default in _each([*args.defaults, *args.kw_defaults]):
                    if default is not None:
                        yield default, scope, False, None
            for field_name, value in _each(ast.iter_fields(node)):
                if field_name in ("body", "args"):
                    continue
                for item in _each(value if isinstance(value, list) else [value]):
                    if isinstance(item, ast.AST):
                        yield item, scope, False, None
            for item in _each([node.body] if is_lambda else node.body):
                yield item, child, False, None
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            child = _Scope(f"{scope.binding}.<comprehension:{node.lineno}:{node.col_offset}>",
                           "comprehension", scope, scope.file)
            self.scopes[child.binding] = child
            for i, generator in _each(enumerate(node.generators)):
                yield generator.iter, scope if i == 0 else child, False, None
                yield generator.target, child, True, None
                for condition in _each(generator.ifs):
                    yield condition, child, False, None
            for name in _each(("key", "value") if isinstance(node, ast.DictComp) else ("elt",)):
                yield getattr(node, name), child, False, None
            return
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            if scope.kind == "module" and isinstance(node, ast.Global):
                return
            (scope.globals if isinstance(node, ast.Global) else scope.nonlocals).update(node.names)
            return
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            if node.value is not None:
                yield node.value, scope, False, None
            destination = scope
            if isinstance(node, ast.NamedExpr):
                while destination.kind == "comprehension":
                    _tick()
                    destination = destination.parent
            for target in _each(node.targets if isinstance(node, ast.Assign) else [node.target]):
                yield target, destination, True, node.value if not isinstance(node, ast.AugAssign) else None
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            package = self.import_names[scope.file]
            if PurePosixPath(scope.file).name != "__init__.py":
                package = package.rpartition(".")[0]
            for alias in _each(node.names):
                if alias.name == "*":
                    raise _FileOmitted("wildcard import binding is ambiguous")
                if isinstance(node, ast.Import):
                    value = alias.name if alias.asname else alias.name.split(".")[0]
                    name = alias.asname or alias.name.split(".")[0]
                else:
                    parts = package.split(".") if package else []
                    parent = ".".join(parts[:len(parts) - node.level + 1]) if node.level else ""
                    source = ".".join(p for p in _each((parent, node.module)) if p)
                    value, name = f"{source}.{alias.name}".lstrip("."), alias.asname or alias.name
                self._bind(scope, name, node, value, "import")
            return
        if isinstance(node, ast.ExceptHandler) and node.name:
            self._bind(scope, node.name, node)
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            self._bind(scope, node.name, node)
        if isinstance(node, ast.MatchMapping) and node.rest:
            self._bind(scope, node.rest, node)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(scope, node.id, node)
            return
        for child in _each(ast.iter_child_nodes(node)):
            yield child, scope, False, None

    def _declared(self, scope, name):
        parent = scope.parent
        if name in scope.globals:
            while parent is not None and parent.parent is not None:
                _tick()
                parent = parent.parent
            return parent
        while parent is not None:
            _tick()
            if parent.kind == "function" and name in parent.names:
                return parent
            parent = parent.parent
        return None

    def _lookup(self, scope, name, node):
        origin = scope
        while scope is not None:
            _tick()
            if name in scope.globals or name in scope.nonlocals:
                scope = self._declared(scope, name)
                if scope is None:
                    return ""
            entries = scope.names.get(name, [])
            if scope.kind == "class":
                if len(entries) > 1:
                    return ""
                entries = [e for e in _each(entries) if (e.node.lineno, e.node.col_offset) <
                           (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))]
            if entries:
                if len(entries) != 1:
                    return ""
                return entries[0]
            parent = scope.parent
            while parent is not None and parent.kind == "class":
                _tick()
                parent = parent.parent
            scope = parent
        # An absent module name may refer to a deleted changed definition.
        if name == "object":
            return "builtins.object"
        return f"{self.modules[origin.file]}.{name}"

    def _canonical_binding(self, name):
        """Return a binding and its remaining attributes without evaluating it."""
        parts = name.split(".")
        depth = min(len(parts), self.prefix_depth)
        if any(".".join(parts[:end]) in self.ambiguous for end in _each(range(1, depth + 1))):
            return "", ()
        for end in _each(range(depth, 0, -1)):
            prefix = ".".join(parts[:end])
            module, _, leaf = prefix.rpartition(".")
            scope = self.namespaces.get(module)
            if scope is None:
                continue
            with _file(scope.file):
                entries = scope.names.get(leaf, [])
                if len(entries) > 1:
                    return "", ()
                if entries and entries[0].kind != "definition":
                    if (scope.kind == "class" and entries[0].kind == "local" and
                            not isinstance(entries[0].value, (ast.Name, ast.Attribute))):
                        # Class constants and enum members retain their symbolic
                        # attribute names; runtime values are never evaluated.
                        return name, ()
                    return entries[0], tuple(parts[end:])
        return name, ()

    def canonical(self, name, seen=None):
        return self._evaluate(("canonical", name, frozenset(seen or ())))

    def _resolve(self, node, scope, seen, constructor=False):
        return self._evaluate(("resolve", node, scope, frozenset(seen), constructor))

    def _evaluate(self, task):
        """Resolve attributes, calls and alias chains without input-depth frames."""
        pending, value = [task], ""
        while pending:
            _tick()
            operation, *args = pending.pop()
            if operation == "cache":
                self.resolved[args[0]] = value
            elif operation == "call":
                if args[0] and value not in self.classes:
                    value = ""
            elif operation == "attribute":
                attr, seen = args
                if value:
                    pending.append(("canonical", f"{value}.{attr}", seen))
            elif operation == "suffix":
                value = ".".join([value, *args[0]]) if value else ""
            elif operation == "canonical":
                name, seen = args
                entry, suffix = self._canonical_binding(name)
                if isinstance(entry, _Binding):
                    pending.extend((("suffix", suffix), ("binding", entry, seen)))
                else:
                    value = entry
            elif operation == "binding":
                entry, seen = args
                with _file(entry.scope.file):
                    key = id(entry)
                    if key in seen or len(seen) >= 64:
                        self.skipped.append(skip("change map import closure",
                                                 "cyclic or over-limit alias chain could not be resolved"))
                        value = ""
                    elif (isinstance(entry.node, ast.arg) and entry.scope.receiver == entry.node.arg and
                          entry.scope.parent is not None and entry.scope.parent.kind == "class"):
                        value = entry.scope.parent.binding
                    elif isinstance(entry.value, str):
                        pending.append(("canonical", entry.value, seen | {key}))
                    elif isinstance(entry.value, ast.AST):
                        pending.append(("resolve", entry.value, entry.scope, seen | {key}, True))
                    else:
                        value = ""
            else:
                node, scope, seen, constructor = args
                with _file(scope.file):
                    # Cycle context is part of the key, so one alias chain
                    # cannot poison a later independent resolution.
                    key = (id(node), constructor, seen)
                    if key in self.resolved:
                        value = self.resolved[key]
                        continue
                    scope = self.node_scopes.get(id(node), scope)
                    pending.append(("cache", key))
                    if isinstance(node, ast.Call):
                        pending.extend((("call", constructor), ("resolve", node.func, scope, seen, False)))
                    elif isinstance(node, ast.Attribute):
                        pending.extend((("attribute", node.attr, seen), ("resolve", node.value, scope, seen, True)))
                    elif isinstance(node, ast.Name):
                        entry = self._lookup(scope, node.id, node)
                        if isinstance(entry, _Binding):
                            pending.append(("binding", entry, seen))
                        else:
                            value = entry
                    else:
                        value = ""
        return value

    def resolve(self, node, symbol, locals_=None):
        scope = self.symbol_scopes.get(id(symbol.node)) or self.scopes[self.modules[symbol.file]]
        # Bases execute in the enclosing scope, never in the class being defined.
        if isinstance(symbol.node, ast.ClassDef):
            scope = scope.parent
        return self._resolve(node, scope, set())

    def calls(self, symbol):
        if not self.canonical(symbol.binding):
            return frozenset()
        if id(symbol.node) not in self.call_cache:
            self.call_cache[id(symbol.node)] = frozenset({
                self.resolve(n.func, symbol) for n in _each(_nodes(symbol.node, lambdas=True))
                if isinstance(n, ast.Call)} - {""})
        return self.call_cache[id(symbol.node)]

    def body(self, node):
        return tuple(self.fingerprints[id(child)] for child in _each(node.body))

    def source(self, rel, node, limit=None):
        offsets = self.offsets[rel]
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        if limit is not None:
            end = min(end, start + limit)
            # A display-only byte cap can split a multibyte character. Back up
            # to its boundary, then decode strictly, as for full literal slices.
            while end > start and end < len(self.sources[rel]) and self.sources[rel][end] & 0xC0 == 0x80:
                end -= 1
        return self.sources[rel][start:end].decode("utf-8")

    def branch(self, rel, node, branch, kind):
        # The keyword can be the only added line. Locate its own source range,
        # rather than attributing the branch to its first (possibly old) statement.
        bodies = [getattr(node, "body", [])]
        bodies.extend(handler.body for handler in _each(getattr(node, "handlers", [])))
        if kind == "finally":
            bodies.append(getattr(node, "orelse", []))
        start = max([node.lineno, *(body[-1].end_lineno for body in _each(bodies) if body)])
        for line in _each(range(start, branch[0].lineno + 1)):
            text = self.lines[rel][line - 1]
            stripped = text.lstrip()
            if (len(text) - len(stripped) == node.col_offset and stripped.startswith(kind.encode())
                    and stripped[len(kind):].lstrip().startswith(b":")):
                marker = ast.Name(lineno=line, end_lineno=line, col_offset=node.col_offset,
                                  end_col_offset=node.col_offset + len(kind))
                self.literal_ranges[id(marker)] = (0, 0)
                return marker
        return None

    def literals(self, rel, node):
        start, stop = self.literal_ranges[id(node)]
        found, seen = [], set()
        for i in _each(range(start, stop)):
            value = self.source(rel, self.literal_nodes[rel][i])
            if value not in seen:
                seen.add(value)
                found.append(value)
        return found


def _context(change: Change, base: Mapping[str, str], patch: Mapping[str, str], roots: Sequence[str]):
    skipped: list[dict] = []
    identities = _module_identities(base.keys() | patch.keys(), roots)
    old = _Index(base, roots, skipped, "base", identities)
    new = _Index(patch, roots, skipped, "patch", identities)
    added = {}
    for p in _each(change.files):
        with _file(p):
            if (p.endswith(".py") and not _is_test(p) and p not in old.invalid | new.invalid
                    and len(base.get(p, "")) <= MAX_FILE_CHARS and len(patch.get(p, "")) <= MAX_FILE_CHARS):
                added[p] = _added(base.get(p, ""), patch.get(p, ""))
    changed = []
    for key in _each(sorted(old.symbols.keys() | new.symbols.keys())):
        symbol = new.symbols.get(key) or old.symbols[key]
        with _file(symbol.file):
            if symbol.file not in change.files or _is_test(symbol.file) or symbol.file in old.invalid | new.invalid:
                continue
            left, right = old.symbols.get(key), new.symbols.get(key)
            if left is None or right is None or old.body(left.node) != new.body(right.node):
                changed.append({**symbol.record(), "change": "added" if left is None else "deleted" if right is None else "changed"})
    return old, new, added, changed, skipped


def changed_symbols(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, roots: Sequence[str] = ()) -> list[dict]:
    """Functions and methods with AST body changes; names include enclosing classes."""
    return _bounded(lambda: _context(change, base, patch, roots)[3], listing=True)


def _decisions(scope: ast.AST, index: _Index, rel: str) -> list[tuple[ast.AST, str]]:
    candidates = []
    for node in _each(_nodes(scope, headers=True, lambdas=True)):
        exits = {ast.Return: "return", ast.Raise: "raise", ast.Break: "break", ast.Continue: "continue",
                 ast.Assert: "assert", ast.If: "if", ast.IfExp: "conditional", ast.For: "for",
                 ast.AsyncFor: "for", ast.While: "while", ast.Try: "try", ast.TryStar: "try",
                 ast.ExceptHandler: "except", ast.Match: "match"}
        if type(node) in exits:
            kind = exits[type(node)]
            if isinstance(node, ast.If) and index.source(rel, node, 4) == "elif":
                kind = "elif"
            candidates.append((node, kind))
        if isinstance(node, ast.match_case):
            candidates.append((node.pattern, "case"))
            if node.guard is not None:
                candidates.append((node.guard, "case_guard"))
        for field_name, kind in _each((("orelse", "else"), ("finalbody", "finally"))):
            branch = getattr(node, field_name, None)
            if isinstance(branch, list) and branch:
                marker = index.branch(rel, node, branch, kind)
                if marker is not None:
                    candidates.append((marker, kind))
        if isinstance(node, ast.Compare):
            candidates.append((node, "membership" if any(isinstance(op, (ast.In, ast.NotIn)) for op in _each(node.ops))
                               else "comparison"))
        elif isinstance(node, ast.Call):
            name = _name(node.func).split(".")[-1]
            if name in ("isinstance", "startswith", "endswith"):
                candidates.append((node, name))
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            candidates.extend((leaf, "truthiness") for leaf in _each(_truth_tests(node.test)))
        elif isinstance(node, ast.comprehension):
            candidates.append((node.iter, "for"))
            candidates.extend((test, "if") for test in _each(node.ifs))
            candidates.extend((leaf, "truthiness") for test in _each(node.ifs) for leaf in _each(_truth_tests(test)))
        elif isinstance(node, ast.BoolOp):
            candidates.extend((leaf, "truthiness") for value in _each(node.values) for leaf in _each(_truth_tests(value)))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            candidates.extend((leaf, "truthiness") for leaf in _each(_truth_tests(node.operand)))
    return candidates


def _points(new: _Index, added: dict[str, list[int]], changed: list[dict]) -> list[dict]:
    points = []
    scopes = [*new.symbols.values(), *new.classes.values(),
              *(_Symbol(rel, new.modules[rel], "", "", tree) for rel, tree in _each(new.trees.items()))]
    for symbol in _each(scopes):
        with _file(symbol.file):
            lines = added.get(symbol.file, [])
            if not lines:
                continue
            for node, kind in _each(_decisions(symbol.node, new, symbol.file)):
                if _touches(node, lines):
                    points.append({"file": symbol.file, "symbol": symbol.symbol, "binding": symbol.binding,
                                   "kind": "decision_point", "decision": kind,
                                   "line": node.lineno, "column": node.col_offset,
                                   "expression": new.source(symbol.file, node, 500), "literals": new.literals(symbol.file, node)})
                    if len(points) > MAX_TARGETS:
                        return points
            if (isinstance(symbol.node, _FUNCTION) and symbol.owner and symbol.node.name.startswith("visit_") and
                    any(c["binding"] == symbol.binding and c["change"] == "added" for c in _each(changed))):
                points.append({**symbol.record(), "kind": "decision_point", "decision": "handler", "literals": [],
                               "expression": symbol.node.name})
    unique = {(p["file"], p["symbol"], p["line"], p["decision"], p["expression"]): p for p in _each(points)}
    return [unique[k] for k in _each(sorted(unique))]


def _truth_tests(node: ast.AST) -> list[ast.AST]:
    # Nested boolean nodes have their own visit in _decisions. Recursing from
    # every parent would repeatedly scan a skewed expression tree.
    if isinstance(node, ast.BoolOp) or isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return []
    if isinstance(node, ast.Compare) or (isinstance(node, ast.Call) and
            _name(node.func).split(".")[-1] in ("isinstance", "startswith", "endswith")):
        return []
    return [node]


def decision_points(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, roots: Sequence[str] = ()) -> list[dict]:
    """Decisions intersecting added lines, with literal leaves and new visitor handlers."""
    def analyse():
        _, new, added, changed, _ = _context(change, base, patch, roots)
        return _points(new, added, changed)
    return _bounded(analyse, listing=True)


def _public(symbol: _Symbol) -> bool:
    return not _is_test(symbol.file) and all(not part.startswith("_") for part in _each(symbol.symbol.split(".")))


def _siblings(old: _Index, new: _Index, added: dict[str, list[int]], changed: list[dict], points: list[dict]) -> list[dict]:
    changed_keys = {c["binding"] for c in _each(changed)}
    found: dict[str, dict] = {}

    def offer(symbol: _Symbol, rank: int, reason: str, changed_key: str, **detail):
        if symbol.binding in changed_keys or not new.canonical(symbol.binding):
            return
        if symbol.binding not in found:
            found[symbol.binding] = {**symbol.record(), "kind": "sibling", "rank": rank,
                                     "reason": reason, "changed_symbols": [changed_key], **detail}
        else:
            entry = found[symbol.binding]
            entry["changed_symbols"] = sorted(set([*entry["changed_symbols"], changed_key]))
            if rank < entry["rank"]:
                entry.update(rank=rank, reason=reason, **detail)

    calls = {}
    for key, symbol in _each(new.symbols.items()):
        with _file(symbol.file):
            calls[key] = new.calls(symbol)
    writes_by_key, reads_by_key, helpers_by_key = {}, {}, {}
    for key, symbol in _each(new.symbols.items()):
        with _file(symbol.file):
            writes, reads, helpers = set(), set(), set()
            for node in _nodes(symbol.node):
                if (isinstance(node, ast.Attribute) and symbol.owner and
                        new.resolve(node.value, symbol) == f"{symbol.module}.{symbol.owner}"):
                    if isinstance(node.ctx, ast.Store):
                        writes.add(node.attr)
                    elif isinstance(node.ctx, ast.Load) and _touches(node, added.get(symbol.file, [])):
                        reads.add(node.attr)
                if isinstance(node, ast.Call) and _touches(node, added.get(symbol.file, [])):
                    helper = new.resolve(node.func, symbol)
                    if helper:
                        helpers.add(helper)
            writes_by_key[key], reads_by_key[key], helpers_by_key[key] = writes, reads, helpers
    for key, symbol in _each(new.symbols.items()):
        with _file(symbol.file):
            if _public(symbol):
                for changed_key in _each(sorted(calls[key] & changed_keys)):
                    offer(symbol, 10, "calls a changed symbol", changed_key)
    for changed_record in _each(changed):
        with _file(changed_record["file"]):
            key = changed_record["binding"]
            symbol = new.symbols.get(key)
            if symbol is None or not symbol.owner:
                continue
            reads, helpers = reads_by_key[key], helpers_by_key[key]
            for sibling in _each(new.symbols.values()):
                if sibling.file != symbol.file or sibling.owner != symbol.owner or not _public(sibling):
                    continue
                shared = sorted(reads & writes_by_key[sibling.binding])
                if shared:
                    offer(sibling, 20, "writes the state this patch reads", key, attributes=shared)
                elif calls[sibling.binding] & helpers:
                    offer(sibling, 20, "calls the same helper as the changed method", key,
                          helpers=sorted(calls[sibling.binding] & helpers))
                if changed_record["change"] == "added" and symbol.node.name.startswith("visit_") and sibling.node.name.startswith("visit_"):
                    offer(sibling, 30, "is a family handler this patch did not add", key)
    for binding in _each(sorted(new.bindings)):
        with _file(new.bindings[binding].scope.file):
            canonical = new.canonical(binding)
            if canonical in changed_keys and binding != canonical and not binding.split(".")[-1].startswith("_"):
                module, _, name = binding.rpartition(".")
                rel = next((p for p, m in _each(new.modules.items()) if m == module), "")
                offer(_Symbol(rel, module, name, "", ast.Name(lineno=1)), 40,
                      "is another name bound to the changed function", canonical)
    # Overrides can keep a distinct implementation behind the same public entry point.
    for child in _each(new.classes.values()):
        with _file(child.file):
            ancestors, pending = set(), [(child, base) for base in _each(child.node.bases)]
            while pending:
                _tick()
                scope, node = pending.pop()
                with _file(scope.file):
                    base = new.resolve(node, scope)
                if base in ancestors:
                    continue
                ancestors.add(base)
                if base in new.classes:
                    parent_class = new.classes[base]
                    pending.extend((parent_class, node) for node in _each(parent_class.node.bases))
            for record in _each(changed):
                parent = new.symbols.get(record["binding"]) or old.symbols.get(record["binding"])
                if parent and parent.owner and f"{parent.module}.{parent.owner}" in ancestors:
                    override = new.symbols.get(f"{child.binding}.{parent.node.name}")
                    if override and _public(override):
                        offer(override, 40, "overrides the changed method in a subclass", parent.binding)
    _token_siblings(old, new, points, offer)
    return sorted(found.values(), key=lambda s: (s["rank"], s["file"], s["symbol"]))


def _token_siblings(old: _Index, new: _Index, points: list[dict], offer) -> None:
    members = {}
    selected_attributes = []
    comparisons = {(p["file"], p["line"], p["column"]): p["binding"] for p in _each(points)
                   if p["decision"] in ("comparison", "membership")}
    for index in _each((old, new)):
        for cls in _each(index.classes.values()):
            with _file(cls.file):
                enumeration = any(index.resolve(base, cls).split(".")[-1] in
                                  ("Enum", "IntEnum", "StrEnum", "Flag", "IntFlag") for base in _each(cls.node.bases))
                for node in _each(cls.node.body):
                    if isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        for target in _each(targets):
                            if isinstance(target, ast.Name) and (target.id.isupper() or
                                    enumeration and not target.id.startswith("_")):
                                item = _Symbol(cls.file, cls.module, f"{cls.symbol}.{target.id}", cls.symbol, node)
                                members.setdefault(cls.binding, {})[target.id] = item
        for rel, tree in _each(index.trees.items()):
            with _file(rel):
                scope = _Symbol(rel, index.modules[rel], "", "", tree)
                pending = [(tree, "")]
                while pending:
                    _tick()
                    node, source = pending.pop()
                    if index is new and isinstance(node, ast.Compare):
                        source = comparisons.get((rel, node.lineno, node.col_offset), source)
                    if isinstance(node, ast.Attribute):
                        name = index.resolve(node, scope)
                        family, _, leaf = name.rpartition(".")
                        if name and node.attr.isupper():
                            members.setdefault(family, {}).setdefault(leaf, _Symbol(rel, "", name, "", node))
                        if index is new and source and name:
                            selected_attributes.append((family, leaf, source))
                    pending.extend((child, source) for child in _each(ast.iter_child_nodes(node)))
    selected, sources = {}, {}
    for family, leaf, source in _each(selected_attributes):
        if leaf in members.get(family, {}):
            selected.setdefault(family, set()).add(leaf)
            sources[family] = source
    for family, added_members in _each(selected.items()):
        for name, symbol in _each(members.get(family, {}).items()):
            with _file(symbol.file):
                if name not in added_members:
                    offer(symbol, 30, "is a token or enumeration family member this patch did not add", sources[family])


def siblings(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, roots: Sequence[str] = ()) -> list[dict]:
    """Ranked named callers, shared-state paths, families, aliases and overrides."""
    def analyse():
        old, new, added, changed, _ = _context(change, base, patch, roots)
        return _siblings(old, new, added, changed, _points(new, added, changed))
    return _bounded(analyse, listing=True)


_OBJECT = _Symbol("", "builtins", "object", "",
                  ast.ClassDef(name="object", bases=[], keywords=[], body=[], decorator_list=[]))


def _mro(index, cls):
    # Keep object in C3's constraints, but expose only source-backed ancestors
    # to consumers. Both caches are immutable; merge lists belong to this call.
    index.lineages.setdefault("builtins.object", (_OBJECT,))
    pending, active, parents = [(cls, False)], set(), {}
    while pending:
        _tick()
        current, closing = pending.pop()
        with _file(current.file):
            if current.binding in index.lineages:
                continue
            if closing:
                bases = parents[current.binding]
                if len(bases) <= 1:
                    inherited = index.lineages[bases[0].binding] if bases else (_OBJECT,)
                    order = (current, *inherited)
                    index.lineages[current.binding] = order
                    index.mros[current.binding] = tuple(c for c in _each(order) if c is not _OBJECT)
                    active.remove(current.binding)
                    continue
                # Consumers own their merge lists; cached orders are immutable.
                sequences = [list(index.lineages[base.binding]) for base in _each(bases)] + [list(bases)]
                result = [current]
                while any(sequences):
                    _tick()
                    sequences = [s for s in _each(sequences) if s]
                    candidate = next((s[0] for s in _each(sequences)
                                      if all(s[0] not in other[1:] for other in _each(sequences))), None)
                    if candidate is None:
                        raise _FileOmitted("source class has an inconsistent MRO hierarchy")
                    result.append(candidate)
                    for sequence in _each(sequences):
                        if sequence and sequence[0] is candidate:
                            sequence.pop(0)
                index.lineages[current.binding] = tuple(result)
                index.mros[current.binding] = tuple(c for c in _each(result) if c is not _OBJECT)
                active.remove(current.binding)
                continue
            if current.binding in active:
                raise _FileOmitted("ambiguous cyclic class hierarchy")
            bases = []
            for node in _each(current.node.bases):
                name = index.resolve(node, current)
                base = index.classes.get(name)
                if base is None and name == "builtins.object":
                    base = _OBJECT
                if base is None:
                    raise _FileOmitted("class selection has an unavailable or ambiguous base")
                bases.append(base)
            else:
                parents[current.binding] = tuple(bases)
                active.add(current.binding)
                pending.append((current, True))
                pending.extend((base, False) for base in reversed(bases))
    return index.mros[cls.binding]


def _selected(index, rel, selection):
    """Resolve one node pytest collected to source, without collection rules.

    Inherited method lookup is static call-graph work. A dynamic or unavailable
    base leaves the body unresolved; it cannot establish absence of a call.
    """
    selected = selection.split("[", 1)[0].replace("::", ".")
    binding = f"{index.modules.get(rel, '')}.{selected}"
    canonical = index.canonical(binding)
    if not canonical:
        raise _FileOmitted("collected body has an ambiguous source binding")
    symbol = index.symbols.get(canonical)
    if symbol is not None:
        return [symbol]
    owner, _, name = binding.rpartition(".")
    cls = index.classes.get(owner)
    if cls is not None:
        for ancestor in _each(_mro(index, cls)):
            scope = index.symbol_scopes[id(ancestor.node)]
            entries = scope.names.get(name, ())
            if entries:
                symbol = index.symbols.get(f"{ancestor.binding}.{name}")
                if len(entries) == 1 and symbol is not None:
                    return [symbol]
                break
    raise _FileOmitted("collected body has an unavailable or ambiguous source")


def _pytest_names(texts, rel):
    """Read collection names without executing configuration or repository code.

    Matching a name never overrides the existing configuration/context guard.
    """
    options = {"python_files": ["test_*.py", "*_test.py"], "python_classes": ["Test"], "python_functions": ["test"]}
    for directory in (PurePosixPath(rel).parent, *PurePosixPath(rel).parent.parents):
        for name in PYTEST_CONFIGS:
            path = (directory / name).as_posix()
            if path not in texts:
                continue
            text = texts[path]
            try:
                if name.endswith(".toml"):
                    data = tomllib.loads(text)
                    data = data.get("tool", {}).get("pytest", {}) if name == "pyproject.toml" else data.get("pytest", {})
                    values = data.get("ini_options", data)
                    if not data and name == "pyproject.toml":
                        continue
                else:
                    parser = configparser.ConfigParser(interpolation=None)
                    parser.read_string(text)
                    section = "tool:pytest" if name == "setup.cfg" else "pytest"
                    if not parser.has_section(section) and name not in ("pytest.ini", ".pytest.ini"):
                        continue
                    values = dict(parser[section]) if parser.has_section(section) else {}
                for key in options:
                    if key in values:
                        value = values[key]
                        options[key] = value.split() if isinstance(value, str) else list(value)
                        if any(not isinstance(item, str) for item in options[key]):
                            raise ValueError("non-text collection name")
                break
            except (ValueError, TypeError, AttributeError, configparser.Error):
                raise _FileOmitted("pytest collection naming configuration unavailable") from None
        else:
            continue
        break
    return options


def _static_names(texts, rel, selection, cache):
    directory = PurePosixPath(rel).parent
    if directory not in cache:
        try:
            cache[directory] = _pytest_names(texts, rel)
        except _FileOmitted as exc:
            cache[directory] = exc.args[0]
    options = cache[directory]
    if isinstance(options, str):
        raise _FileOmitted(options)
    parts = selection.split("[", 1)[0].replace("::", ".").split(".")
    # This path serves only explicit file::body selections. pytest admits a
    # file named on its command line regardless of python_files.
    for name, key in [*( (part, "python_classes") for part in parts[:-1]), (parts[-1], "python_functions")]:
        if not any(name.startswith(pattern) or any(char in pattern for char in "?*[") and fnmatch.fnmatch(name, pattern)
                   for pattern in options[key]):
            raise _FileOmitted("body does not match pytest collection names")


def _static_module(index, rel, *, collection_only=False):
    """Cache success and failure once per module in this map's index."""
    if rel not in index.static_scans:
        try:
            if rel not in index.trees or rel in index.invalid:
                raise _FileOmitted("explicit body has an unreadable definition module")
            identity_reason = _static_test_writes(index.trees[rel])
            index.static_scans[rel] = (None, identity_reason)
        except _FileOmitted as exc:
            index.static_scans[rel] = (exc.args[0], None)
    collection_reason, identity_reason = index.static_scans[rel]
    reason = collection_reason or (None if collection_only else identity_reason)
    if reason is not None:
        raise _FileOmitted(reason)


def _static_selected(index, rel, selection):
    """Reject suppressed containing classes before admitting an explicit body.

    This is a conservative guard, not a replacement for collection. Configured
    class patterns and custom collectors need an available collection result.
    """
    _static_module(index, rel)
    parts = selection.split("[", 1)[0].replace("::", ".").split(".")
    module = index.modules[rel]
    for end in _each(range(1, len(parts))):
        cls = index.classes.get(index.canonical(".".join((module, *parts[:end]))))
        if cls is None or not cls.node.name.startswith("Test"):
            raise _FileOmitted("explicit body has an unavailable or non-test containing class")
        if cls.node.decorator_list or cls.node.keywords:
            raise _FileOmitted("explicit body has dynamic containing class creation")
        lineage = _mro(index, cls)
        attributes = {}
        for ancestor in _each(lineage):
            tree = index.trees.get(ancestor.file)
            if tree is None or ancestor.file in index.invalid:
                raise _FileOmitted("explicit body has an unreadable ancestor module")
            # Many ancestors can share one module. Its whole-tree scan covers
            # every class already; repeating it can exhaust the work budget.
            _static_module(index, ancestor.file)
            if ancestor.node.decorator_list or ancestor.node.keywords:
                raise _FileOmitted("explicit body has dynamic ancestor class creation")
            scope = index.symbol_scopes[id(ancestor.node)]
            for name in _each(("__test__", "__init__", "__new__")):
                if name in scope.names and name not in attributes:
                    entries = scope.names[name]
                    if len(entries) != 1:
                        raise _FileOmitted("explicit body has an ambiguous class collection attribute")
                    attributes[name] = (entries[0], ancestor)
        for name, (entry, ancestor) in _each(attributes.items()):
            if name == "__test__":
                try:
                    enabled = ast.literal_eval(entry.value)
                except (ValueError, TypeError):
                    raise _FileOmitted("explicit body has an unavailable class collection attribute") from None
                if not enabled:
                    raise _FileOmitted("explicit body has a suppressed containing class")
            elif not isinstance(entry.value, ast.AST) or index.resolve(entry.value, ancestor) != f"builtins.object.{name}":
                raise _FileOmitted("explicit body has a containing class with a constructor")
    # A module-level suppression also prevents every nested body collecting.
    entries = index.scopes[module].names.get("__test__", ())
    if entries:
        try:
            enabled = len(entries) == 1 and ast.literal_eval(entries[0].value)
        except (ValueError, TypeError):
            enabled = False
        if not enabled:
            raise _FileOmitted("explicit body has a suppressed or unavailable module")
    symbols = _selected(index, rel, selection)
    for symbol in _each(symbols):
        _static_module(index, symbol.file, collection_only=True)
        _same_function_decorators(index, symbol)
    return symbols


def _same_function_decorators(index, symbol):
    """Only pytest marks have a known same-function decorator result."""
    for decorator in _each(symbol.node.decorator_list):
        function = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = index.resolve(function, symbol)
        if not name.startswith("pytest.mark."):
            raise _FileOmitted("body has an unknown decorator result")


def code_identity(code):
    """Location-free code content, including constants and nested code.

    Keep this encoding identical to the small collect-only producer below.
    Code locations and __wrapped__ never substitute for these contents.
    """
    def pack(value):
        _tick()
        if isinstance(value, types.CodeType):
            return ["code", [pack(getattr(value, field, b"")) for field in (
                "co_code", "co_consts", "co_names", "co_varnames", "co_freevars", "co_cellvars",
                "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
                "co_stacksize", "co_flags", "co_name", "co_qualname", "co_exceptiontable")]]
        if isinstance(value, (tuple, frozenset)):
            items = [pack(item) for item in value]
            if isinstance(value, frozenset):
                items.sort(key=lambda item: json.dumps(item, ensure_ascii=True))
            return [type(value).__name__, items]
        return [type(value).__name__, repr(value)]

    return hashlib.sha256(json.dumps(pack(code), ensure_ascii=True).encode("utf-8")).hexdigest()


def compiled_identities(source, filename):
    """Compile, never execute, G6's own source read with both assertion modes."""
    found = {}

    def remember(code):
        pending = [code]
        while pending:
            _tick()
            current = pending.pop()
            found.setdefault((current.co_name, current.co_firstlineno), set()).add(code_identity(current))
            pending.extend(value for value in current.co_consts if isinstance(value, types.CodeType))

    remember(compile(source, filename, "exec", dont_inherit=True, optimize=0))
    # pytest rewrites assertions before compiling a test module. Use G6's
    # installed rewriter on our read, never a function or source from the child.
    from _pytest.assertion.rewrite import rewrite_asserts

    tree = ast.parse(source, filename=filename)
    rewrite_asserts(tree, source.encode("utf-8") if isinstance(source, str) else source, filename)
    remember(compile(tree, filename, "exec", dont_inherit=True, optimize=0))
    return found


def _static_test_writes(tree):
    """Only literal, direct class-local __test__ bindings are statically read.

    Scan every scope: a helper or conditional can mutate collection attributes.
    Computed attribute names and subscript stores cannot establish a safe target.
    """
    lexical = {id(statement) for node in _walk(tree) if isinstance(node, ast.ClassDef)
               for statement in _each(node.body)}
    local_names = {id(target) for node in _walk(tree) if id(node) in lexical
                   and isinstance(node, (ast.Assign, ast.AnnAssign))
                   for target in _each(node.targets if isinstance(node, ast.Assign) else [node.target])
                   if isinstance(target, ast.Name)}
    setters, aliases = {"setattr", "delattr", "builtins.setattr", "builtins.delattr"}, []
    identity_reason = None
    for node in _walk(tree):
        if (isinstance(node, ast.Name) and node.id == "__test__" and
                isinstance(node.ctx, (ast.Store, ast.Del)) and id(node) not in local_names):
            raise _FileOmitted("explicit body has a non-lexical collection attribute binding")
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            setters.update(alias.asname or alias.name for alias in _each(node.names)
                           if alias.name in ("setattr", "delattr"))
        elif isinstance(node, ast.Assign):
            aliases.extend((target.id, _name(node.value)) for target in _each(node.targets)
                           if isinstance(target, ast.Name))
    while True:
        added = {name for name, value in _each(aliases) if value in setters} - setters
        if not added:
            break
        setters.update(added)
    for node in _walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr in ("__code__", "__wrapped__") and
                isinstance(node.ctx, (ast.Store, ast.Del))):
            identity_reason = "explicit body has an external callable identity write"
        if isinstance(node, ast.Call) and _name(node.func).split(".")[-1] in ("exec", "compile"):
            identity_reason = "explicit body has dynamic code construction"
        if isinstance(node, (ast.Global, ast.Nonlocal)) and "__test__" in node.names:
            raise _FileOmitted("explicit body has a redirected collection attribute binding")
        if isinstance(node, ast.Call) and (
                any(keyword.arg == "__test__" for keyword in _each(node.keywords)) or
                any(isinstance(part, ast.Constant) and part.value == "__test__"
                    for argument in _each([*node.args, *(keyword.value for keyword in node.keywords)])
                    for part in _walk(argument))):
            raise _FileOmitted("explicit body passes a collection attribute to an unreadable call")
        if isinstance(node, ast.Call) and (_name(node.func) in setters or
                _name(node.func).split(".")[-1] in ("setattr", "delattr")):
            name = node.args[1] if len(node.args) > 1 else None
            if not isinstance(name, ast.Constant) or name.value == "__test__":
                raise _FileOmitted("explicit body has an external or dynamic collection attribute write")
        if (isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) and
                (not isinstance(node.slice, ast.Constant) or node.slice.value == "__test__")):
            raise _FileOmitted("explicit body has an unreadable subscript assignment")
        if isinstance(node, ast.Attribute) and node.attr == "__test__" and isinstance(node.ctx, (ast.Store, ast.Del)):
            raise _FileOmitted("explicit body has an external collection attribute write")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            writes = any(isinstance(part, ast.Name) and part.id == "__test__"
                         for target in _each(targets) for part in _walk(target))
            if writes:
                if (id(node) not in lexical or not isinstance(node, (ast.Assign, ast.AnnAssign)) or
                        any(not isinstance(target, ast.Name) for target in targets)):
                    raise _FileOmitted("explicit body has a non-lexical collection attribute write")
                try:
                    ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    raise _FileOmitted("explicit body has an unreadable collection attribute write") from None
    return identity_reason


def _collected_selected(index, rel, selection, body, identity=None, compilations=None):
    """Resolve a reader-validated identity to the corresponding AST body.

    The collection reader compares code contents against G6's compilation and
    marks rejected reports unresolved. Locations alone remain diagnostic data.
    """
    symbols = _selected(index, rel, selection)
    if not isinstance(body, dict) or type(body.get("line")) is not int:
        raise _FileOmitted("collected callable identity unavailable")
    for symbol in _each(symbols):
        first = min([symbol.node.lineno, *(node.lineno for node in _each(symbol.node.decorator_list))])
        if body.get("file") != symbol.file or body["line"] != first:
            raise _FileOmitted("collected callable does not match the source body")
        # A body with no call expressions cannot supply a positive. Retain its
        # location for negatives; every body we follow needs content identity.
        if any(isinstance(node, ast.Call) for node in _nodes(symbol.node, lambdas=True)):
            if not isinstance(identity, str):
                raise _FileOmitted("collected callable code identity unavailable")
            if symbol.file not in index.compiled:
                supplied = (compilations or {}).get(symbol.file, {})
                if supplied.get("source") == hashlib.sha256(index.sources[symbol.file]).hexdigest():
                    index.compiled[symbol.file] = {(name, line): codes for name, line, codes in supplied["codes"]}
                else:
                    index.compiled[symbol.file] = compiled_identities(index.sources[symbol.file].decode("utf-8"), symbol.file)
            expected = index.compiled[symbol.file].get((symbol.node.name, body["line"]), ())
            if identity not in expected:
                raise _FileOmitted("collected callable code differs from the source body")
    return symbols


def collect_checks(change, stated_checks, proposed_checks, roots, *, collector, deadline):
    """Expand selections through an injected collector, retaining each named failure.

    The review supplies the confined runner. Text-only callers can supply fixture
    node ids and callable locations without importing repository code here.
    """
    from g6.workforce import repo_tests

    selections = dict.fromkeys(stated_checks)
    selections.update({rel: None for rel in change.files if _is_test(rel)})
    for item in proposed_checks:
        node = item["file"] + ("::" + item["test"] if item.get("test") else "")
        selections[node] = {item["file"]: item["source"]}
    if len(selections) > MAX_CHECKS:
        return {}
    regular = [check for check, sources in selections.items() if sources is None]
    groups = [(regular, None)] if regular else []
    groups.extend(([check], sources) for check, sources in selections.items() if sources is not None)
    answers = {}
    for checks, sources in groups:
        left = deadline - time.monotonic()
        if left < 1:
            answers.update({check: {"status": "uncertain", "reason": "pytest collection time budget is spent"}
                            for check in checks})
            continue
        try:
            answer = collector(change.repo, checks, roots=roots, timeout_s=min(10, max(1, int(left))),
                               **({"sources": sources} if sources else {}))
            for check in checks:
                if answer.get("status") == "available" and type(answer.get("nodes")) is list:
                    path, separator, selection = check.partition("::")
                    scope = PurePosixPath(path.replace("\\", "/")).as_posix() + separator + selection
                    answers[check] = {"status": "available", "nodes": [
                        node for node in answer["nodes"] if len(checks) == 1 or scope == "." or
                        repo_tests.report_covers(scope, node)]}
                    if isinstance(answer.get("bodies"), dict):
                        answers[check]["bodies"] = {node: answer["bodies"][node]
                                                    for node in answers[check]["nodes"] if node in answer["bodies"]}
                    if isinstance(answer.get("unresolved"), list):
                        answers[check]["unresolved"] = [node for node in answers[check]["nodes"]
                                                        if node in answer["unresolved"]]
                    if isinstance(answer.get("codes"), dict):
                        answers[check]["codes"] = {node: answer["codes"][node]
                                                   for node in answers[check]["nodes"] if node in answer["codes"]}
                    if isinstance(answer.get("compilations"), dict):
                        answers[check]["compilations"] = answer["compilations"]
                else:
                    answers[check] = {"status": "uncertain", "reason": answer.get("reason") or
                                      "pytest collection result unavailable"}
        except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
            raise
        except Exception as exc:
            answers.update({check: {"status": "uncertain", "reason":
                                   f"pytest collection unavailable ({type(exc).__name__})"} for check in checks})
    return _plain(answers)


def _exercised(targets: Sequence[dict], checks: Mapping[str, str], roots: Sequence[str], inventory=None,
               collection=None, explicit=()):
    result = {t["binding"]: [] for t in _each(targets)}
    skipped, uncertain = [], set()
    texts = dict(inventory or {})
    for check, source in _each(checks.items()):
        rel = check.partition("::")[0]
        if rel.endswith(".py"):
            texts[rel] = source
    index = _Index(texts, roots, skipped, "check") if checks else None
    static = set()
    # External target owners can be resolved without loading their modules in
    # the small caller-list API, just as they can in the full inventory.
    if index is not None:
        for target in _each(targets):
            if target.get("owner"):
                index.classes.setdefault(target["binding"].rpartition(".")[0], None)
    for check in _each(sorted(checks)):
        answer = (collection or {}).get(check, (collection or {}).get(shown(check), {}))
        if answer.get("status") != "available" or type(answer.get("nodes")) is not list:
            rel, separator, selection = check.partition("::")
            bodies = []
            if (not pytest_context_paths(texts, rel) and check in explicit and separator and selection and
                    rel in index.modules and rel not in index.invalid):
                with _file(rel):
                    # A class/file selection is not a named body. Do not expand
                    # one using guessed pytest naming or collection rules.
                    try:
                        bodies = _static_selected(index, rel, selection)
                    except _FileOmitted:
                        pass
            # Resolve inside the file transaction before filtering names, so a
            # local stage failure still discards that file's partial advice.
            if check in explicit and separator and selection:
                try:
                    _static_names(texts, rel, selection, index.collection_names)
                except _FileOmitted as exc:
                    uncertain.add(check)
                    skipped.append(skip(check, exc.args[0]))
                    continue
            if not bodies:
                uncertain.add(check)
                skipped.append(skip(check, answer.get("reason") or "pytest collection unavailable (not supplied)"))
                continue
            static.add(check)
            nodes = [check]
        else:
            nodes = answer["nodes"]
        # A definite negative needs every collected entry point resolved.
        for node in _each(nodes):
            rel, separator, selection = node.partition("::")
            if not separator or rel in index.invalid or rel not in index.modules:
                uncertain.add(check)
                skipped.append(skip(check, "collected body source unavailable"))
                continue
            with _file(rel):
                try:
                    identities = answer.get("bodies")
                    body = (identities.get(node) if isinstance(identities, dict) and
                            node not in answer.get("unresolved", ()) else None)
                    codes = answer.get("codes", {})
                    bodies = (_selected(index, rel, selection) if check in static else
                              _collected_selected(index, rel, selection, body,
                                                  codes.get(node) if isinstance(codes, dict) else None,
                                                  answer.get("compilations")))
                except _FileOmitted as exc:
                    uncertain.add(check)
                    skipped.append(skip(check, exc.args[0]))
                    continue
                pending, seen = list(bodies), set()
                admitted = {symbol.binding for symbol in bodies}
            while pending:
                _tick()
                symbol = pending.pop()
                if symbol.binding in seen:
                    continue
                seen.add(symbol.binding)
                with _file(symbol.file):
                    if symbol.binding not in admitted:
                        try:
                            _same_function_decorators(index, symbol)
                        except _FileOmitted as exc:
                            uncertain.add(check)
                            skipped.append(skip(check, exc.args[0]))
                            continue
                    for binding in _each(index.calls(symbol)):
                        callee = index.symbols.get(binding)
                        if callee is not None and callee.binding not in admitted:
                            try:
                                _same_function_decorators(index, callee)
                            except _FileOmitted as exc:
                                uncertain.add(check)
                                skipped.append(skip(check, exc.args[0]))
                                continue
                        if binding in result and check not in result[binding]:
                            result[binding].append(check)
                        if callee is not None:
                            pending.append(callee)
    return result, skipped, uncertain, static


def exercised(targets: Sequence[dict], checks: Mapping[str, str], *, roots: Sequence[str] = (),
              collection=None) -> dict[str, list[str]]:
    """Static calls from collected or explicitly named bodies; no coverage claim.

    Unexpanded file/class selections supply no positive leads without collection.
    ``build`` also reports the uncertainty a bare list of callers cannot express.
    """
    result = _bounded(lambda: {"calls": _exercised(targets, checks, roots, collection=collection, explicit=checks)[0]})
    return result["calls"] if "calls" in result else {shown(t["binding"]): [] for t in targets}


def _build(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, stated_checks: Sequence[str] = (),
          proposed_checks: Sequence[Mapping[str, str]] = (), roots: Sequence[str] = (), collection=None) -> dict:
    """One advisory neighbourhood; an ordinary body edit alone creates no target."""
    record = empty()
    if len(stated_checks) + len(proposed_checks) > MAX_CHECKS:
        raise _Omitted("change map checks", "check selection limit reached")
    old, new, added, changed, skipped = _context(change, base, patch, roots)
    points = _points(new, added, changed)
    neighbours = _siblings(old, new, added, changed, points)
    checks = {}
    for node in _each(stated_checks):
        rel = node.partition("::")[0]
        if rel in patch:
            checks[node] = patch[rel]
        else:
            # Keep the original selection: pytest owns folder expansion too.
            checks[node] = ""
    for rel in _each(change.files):
        if rel in patch and _is_test(rel):
            checks[rel] = patch[rel]
    for proposed in _each(proposed_checks):
        node = proposed["file"] + ("::" + proposed["test"] if proposed.get("test") else "")
        checks[node] = proposed["source"]
    called, check_skips, uncertain, static = _exercised(neighbours, checks, roots, patch, collection, stated_checks)
    skipped.extend(check_skips)
    for sibling in _each(neighbours):
        calls = called[sibling["binding"]]
        # Missing collection cannot establish either presence or absence.
        state = "uncertain" if uncertain else "not_required" if static else "available"
        sibling.update(exercised=True if calls else "uncertain" if uncertain else False,
                       checks=calls, collection=state)
        if sibling["exercised"] is False:
            skipped.append(skip(sibling["symbol"], f"{sibling['symbol']} {sibling['reason']}; no check calls it"))
    targets = [*neighbours, *points]
    if len(targets) > MAX_TARGETS:
        raise _Omitted("change map targets", "target limit reached")
    record["targets"] = targets[:MAX_TARGETS]
    record["skipped"] = [*skipped, skip("change map static visibility", STATIC_LIMIT)]
    return record


def _plain(value):
    """Reject non-JSON values before the advisory can reach job serialization."""
    _tick()
    if isinstance(value, str):
        # Raw file identities stay inside analysis and git/disk operations.
        # Every exported key and text field crosses the same display boundary.
        text = shown(value)
        if type(text) is str:
            return text
        raise TypeError("map text must be a plain string")
    if type(value) in (int, bool):
        return value
    if type(value) is list:
        return [_plain(item) for item in _each(value)]
    if type(value) is dict:
        if not all(isinstance(key, str) for key in _each(value)):
            raise TypeError("map keys must be strings")
        return {_plain(key): _plain(item) for key, item in _each(value.items())}
    raise TypeError("map values must be plain JSON strings, integers, booleans, lists or dictionaries")


def _bounded(call, deadline=None, *, listing=False):
    token = _BUDGET.set(_Budget(min(deadline if deadline is not None else time.monotonic() + 2.0,
                                   time.monotonic() + 2.0)))
    try:
        _tick()
        budget = _BUDGET.get()
        while True:
            _tick()
            try:
                result = call()
                if isinstance(result, dict) and "skipped" in result:
                    result["skipped"] = [*budget.failed.values(), *result["skipped"]]
                result = _plain(result)
                break
            except _FileFailed as exc:
                if exc.rel in budget.failed:
                    raise _Omitted("change map", "file isolation could not finish") from None
                budget.failed[exc.rel] = skip(exc.check, exc.reason)
        targets = result.get("targets", []) if isinstance(result, dict) else result if isinstance(result, list) else []
        literal_chars = 0
        for target in _each(targets):
            for literal in _each(target.get("literals", [])):
                literal_chars += len(literal)
                if literal_chars > MAX_LITERAL_OUTPUT:
                    raise _Omitted("change map literals", "literal output limit reached")
        output_chars = 0
        encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        for chunk in _each(encoder.iterencode(result)):
            output_chars += len(chunk)
            if output_chars > MAX_OUTPUT_CHARS:
                raise _Omitted("change map", "JSON output size limit reached")
        _tick()
        return result
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise
    except Exception as exc:
        if listing:
            return []
        record = empty()
        record["skipped"].append(skip(exc.check, exc.reason) if isinstance(exc, _Omitted) else
                                 skip("change map", f"static analysis stopped ({type(exc).__name__})"))
        return record
    finally:
        _BUDGET.reset(token)


def json_record(record, *, deadline=None):
    """Final advisory boundary, also guarding substituted map producers."""
    def validate():
        if type(record) is not dict or set(record) != set(empty()):
            raise TypeError("invalid map record")
        if not all(type(value) is list for value in _each(record.values())):
            raise TypeError("invalid map lists")
        for entry in _each(record["skipped"]):
            if (type(entry) is not dict or type(entry.get("tier")) is not int or
                    not isinstance(entry.get("check"), str) or not isinstance(entry.get("reason"), str)):
                raise TypeError("invalid map skip")
        return record
    return _bounded(validate, deadline)


def build(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, stated_checks: Sequence[str] = (),
          proposed_checks: Sequence[Mapping[str, str]] = (), roots: Sequence[str] = (), deadline=None,
          collection=None, collector=None) -> dict:
    """Keep file-local skips; omit the advisory on whole-run limits or failures."""
    if collector is not None:
        collection = collect_checks(change, stated_checks, proposed_checks, roots, collector=collector,
                                    deadline=deadline if deadline is not None else time.monotonic() + 10)
    return _bounded(lambda: _build(change, base, patch, stated_checks=stated_checks,
                                  proposed_checks=proposed_checks, roots=roots,
                                  collection=collection), deadline)
