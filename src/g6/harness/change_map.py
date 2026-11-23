# SPDX-License-Identifier: Apache-2.0
"""Tier 0: AST-only leads, never review evidence or a decision.

Inputs are file texts, not importable modules. ``exercised`` means a static named
call was seen, not that the call ran. Dynamic dispatch, reflection, fixtures,
external packages and arbitrary data flow are outside this map's view.
"""
from __future__ import annotations

import ast
import hashlib
import json
import time
from bisect import bisect_left
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Mapping, Sequence

from g6.core import component_process
from g6.core.git_name import shown_name

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
                "injection or arbitrary data flow; a named call may never execute. No execution coverage is claimed.")
_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)


class _Omitted(Exception):
    def __init__(self, check: str, reason: str):
        self.check, self.reason = check, reason


class _FileOmitted(Exception):
    """A file-local analysis failure; never a whole-run budget signal."""


@dataclass
class _Budget:
    deadline: float
    work: int = 0
    files: set = field(default_factory=set)
    sources: dict = field(default_factory=dict)
    total_chars: int = 0


_BUDGET: ContextVar[_Budget | None] = ContextVar("change_map_budget", default=None)


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
    return {"tier": 0, "check": check, "reason": reason}


def _name(node: ast.AST) -> str:
    _tick()
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
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
    def __init__(self, texts: Mapping[str, str], roots: Sequence[str], skipped: list[dict], side: str):
        self._reset(skipped)
        budget = _BUDGET.get() or _Budget(time.monotonic() + 2.0)
        budget.files.update(rel for rel in _each(texts) if rel.endswith(".py"))
        if len(budget.files) > MAX_FILES:
            raise _Omitted("change map", "static analysis file limit reached")
        for rel in _each(sorted(texts)):
            if not rel.endswith(".py"):
                continue
            text = texts[rel]
            if len(text) > MAX_FILE_CHARS:
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
            try:
                staged._load_file(rel, text, roots)
            except (component_process.ExecutionInterrupted, component_process.ContainmentFailure, _Omitted):
                raise
            except Exception as exc:
                self.invalid.add(rel)
                reason = exc.args[0] if isinstance(exc, _FileOmitted) else f"source analysis unavailable ({type(exc).__name__})"
                skipped.append(skip(f"change map: {side} {shown_name(rel)}", reason))
                continue
            for name, values in _each(vars(staged).items()):
                if name == "skipped":
                    skipped.extend(values)
                else:
                    getattr(self, name).update(values)
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
        self.bindings, self.invalid, self.ambiguous = {}, set(), set()
        self.skipped = skipped
        self.scopes, self.node_scopes, self.symbol_scopes = {}, {}, {}
        self.namespaces, self.resolved, self.call_cache, self.mros = {}, {}, {}, {}
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
        self.lines[rel] = self.sources[rel].split(b"\n")
        for line in _each(self.lines[rel]):
            pos += len(line) + 1
            offsets.append(pos)
        self.offsets[rel] = offsets
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
        self.literal_nodes[rel] = literals
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
        self.trees[rel], self.modules[rel] = tree, _module(rel, roots)
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

    def _target(self, node, scope, value=None):
        self.node_scopes[id(node)] = scope
        if isinstance(node, ast.Name):
            self._bind(scope, node.id, node, value)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in _each(node.elts):
                self._target(child, scope)
        elif isinstance(node, ast.Starred):
            self._target(node.value, scope)
        else:
            self._scan(node, scope)

    def _scan(self, node, scope):
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
                        self._scan(default, scope)
            for field_name, value in _each(ast.iter_fields(node)):
                if field_name in ("body", "args"):
                    continue
                for item in _each(value if isinstance(value, list) else [value]):
                    if isinstance(item, ast.AST):
                        self._scan(item, scope)
            for item in _each([node.body] if is_lambda else node.body):
                self._scan(item, child)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            child = _Scope(f"{scope.binding}.<comprehension:{node.lineno}:{node.col_offset}>",
                           "comprehension", scope, scope.file)
            self.scopes[child.binding] = child
            for i, generator in _each(enumerate(node.generators)):
                self._scan(generator.iter, scope if i == 0 else child)
                self._target(generator.target, child)
                for condition in _each(generator.ifs):
                    self._scan(condition, child)
            for name in _each(("key", "value") if isinstance(node, ast.DictComp) else ("elt",)):
                self._scan(getattr(node, name), child)
            return
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            if scope.kind == "module" and isinstance(node, ast.Global):
                return
            (scope.globals if isinstance(node, ast.Global) else scope.nonlocals).update(node.names)
            return
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            self._scan(node.value, scope) if node.value is not None else None
            destination = scope
            if isinstance(node, ast.NamedExpr):
                while destination.kind == "comprehension":
                    _tick()
                    destination = destination.parent
            for target in _each(node.targets if isinstance(node, ast.Assign) else [node.target]):
                self._target(target, destination, node.value if not isinstance(node, ast.AugAssign) else None)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            package = self.modules[scope.file]
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
            self._scan(child, scope)

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

    def _lookup(self, scope, name, node, seen):
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
                return self._value(entries[0], seen)
            parent = scope.parent
            while parent is not None and parent.kind == "class":
                _tick()
                parent = parent.parent
            scope = parent
        # An absent module name may refer to a deleted changed definition.
        return f"{self.modules[origin.file]}.{name}"

    def _value(self, entry, seen):
        key = id(entry)
        if key in seen or len(seen) >= 64:
            self.skipped.append(skip("change map import closure", "cyclic or over-limit alias chain could not be resolved"))
            return ""
        seen = seen | {key}
        if (isinstance(entry.node, ast.arg) and entry.scope.receiver == entry.node.arg and
                entry.scope.parent is not None and entry.scope.parent.kind == "class"):
            return entry.scope.parent.binding
        if isinstance(entry.value, str):
            return self.canonical(entry.value, seen)
        if isinstance(entry.value, ast.AST):
            return self._resolve(entry.value, entry.scope, seen, constructor=True)
        return ""

    def canonical(self, name, seen=None):
        seen = seen or set()
        parts = name.split(".")
        if any(".".join(parts[:end]) in self.ambiguous for end in _each(range(1, len(parts) + 1))):
            return ""
        for end in _each(range(len(parts), 0, -1)):
            prefix = ".".join(parts[:end])
            module, _, leaf = prefix.rpartition(".")
            scope = self.namespaces.get(module)
            if scope is None:
                continue
            entries = scope.names.get(leaf, [])
            if len(entries) > 1:
                return ""
            if entries and entries[0].kind != "definition":
                if (scope.kind == "class" and entries[0].kind == "local" and
                        not isinstance(entries[0].value, (ast.Name, ast.Attribute))):
                    # Class constants and enum members retain their symbolic
                    # attribute names; their runtime values are never evaluated.
                    return name
                value = self._value(entries[0], seen)
                return ".".join([value, *parts[end:]]) if value else ""
        return name

    def _resolve(self, node, scope, seen, constructor=False):
        key = (id(node), constructor)
        if key not in self.resolved:
            self.resolved[key] = self._resolve_uncached(node, scope, seen, constructor)
        return self.resolved[key]

    def _resolve_uncached(self, node, scope, seen, constructor=False):
        _tick()
        scope = self.node_scopes.get(id(node), scope)
        if isinstance(node, ast.Call):
            value = self._resolve(node.func, scope, seen)
            return value if not constructor or value in self.classes else ""
        if isinstance(node, ast.Attribute):
            value = self._resolve(node.value, scope, seen, constructor=True)
            return self.canonical(f"{value}.{node.attr}", seen) if value else ""
        if isinstance(node, ast.Name):
            return self._lookup(scope, node.id, node, seen)
        return ""

    def resolve(self, node, symbol, locals_=None):
        scope = self.symbol_scopes.get(id(symbol.node)) or self.scopes[self.modules[symbol.file]]
        # Bases execute in the enclosing scope, never in the class being defined.
        if isinstance(symbol.node, ast.ClassDef):
            scope = scope.parent
        return self._resolve(node, scope, set())

    def calls(self, symbol):
        if not self.canonical(symbol.binding):
            return set()
        if id(symbol.node) not in self.call_cache:
            self.call_cache[id(symbol.node)] = {
                self.resolve(n.func, symbol) for n in _each(_nodes(symbol.node, lambdas=True))
                if isinstance(n, ast.Call)} - {""}
        return self.call_cache[id(symbol.node)]

    def body(self, node):
        return tuple(self.fingerprints[id(child)] for child in _each(node.body))

    def source(self, rel, node, limit=None):
        offsets = self.offsets[rel]
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        if limit is not None:
            end = min(end, start + limit)
        return self.sources[rel][start:end].decode("utf-8", "replace")

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
    old, new = _Index(base, roots, skipped, "base"), _Index(patch, roots, skipped, "patch")
    added = {p: _added(base.get(p, ""), patch.get(p, "")) for p in _each(change.files)
             if p.endswith(".py") and not _is_test(p) and p not in old.invalid | new.invalid
             and len(base.get(p, "")) <= MAX_FILE_CHARS and len(patch.get(p, "")) <= MAX_FILE_CHARS}
    changed = []
    for key in _each(sorted(old.symbols.keys() | new.symbols.keys())):
        symbol = new.symbols.get(key) or old.symbols[key]
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

    calls = {key: new.calls(s) for key, s in _each(new.symbols.items())}
    writes_by_key, reads_by_key, helpers_by_key = {}, {}, {}
    for key, symbol in _each(new.symbols.items()):
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
        if _public(symbol):
            for changed_key in _each(sorted(calls[key] & changed_keys)):
                offer(symbol, 10, "calls a changed symbol", changed_key)
    for changed_record in _each(changed):
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
        canonical = new.canonical(binding)
        if canonical in changed_keys and binding != canonical and not binding.split(".")[-1].startswith("_"):
            module, _, name = binding.rpartition(".")
            rel = next((p for p, m in _each(new.modules.items()) if m == module), "")
            offer(_Symbol(rel, module, name, "", ast.Name(lineno=1)), 40,
                  "is another name bound to the changed function", canonical)
    # Overrides can keep a distinct implementation behind the same public entry point.
    for child in _each(new.classes.values()):
        ancestors, pending = set(), [(child, base) for base in _each(child.node.bases)]
        while pending:
            _tick()
            scope, node = pending.pop()
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
            if name not in added_members:
                offer(symbol, 30, "is a token or enumeration family member this patch did not add", sources[family])


def siblings(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, roots: Sequence[str] = ()) -> list[dict]:
    """Ranked named callers, shared-state paths, families, aliases and overrides."""
    def analyse():
        old, new, added, changed, _ = _context(change, base, patch, roots)
        return _siblings(old, new, added, changed, _points(new, added, changed))
    return _bounded(analyse, listing=True)


def _mro(index, cls, active=None):
    if cls.binding in index.mros:
        return index.mros[cls.binding]
    active = active or set()
    if cls.binding in active:
        raise _FileOmitted("ambiguous cyclic class hierarchy")
    bases = []
    for node in _each(cls.node.bases):
        name = index.resolve(node, cls)
        if name in ("object", f"{cls.module}.object"):
            continue
        base = index.classes.get(name)
        if base is None:
            index.skipped.append(skip(cls.binding, "class selection has an unavailable or ambiguous base"))
            return [cls]
        bases.append(base)
    sequences = [_mro(index, base, active | {cls.binding}) for base in _each(bases)] + [bases[:]]
    result = [cls]
    while any(sequences):
        _tick()
        sequences = [s for s in _each(sequences) if s]
        candidate = next((s[0] for s in _each(sequences)
                          if all(s[0] not in other[1:] for other in _each(sequences))), None)
        if candidate is None:
            raise _FileOmitted("ambiguous inconsistent class hierarchy")
        result.append(candidate)
        for sequence in _each(sequences):
            if sequence and sequence[0] is candidate:
                sequence.pop(0)
    index.mros[cls.binding] = result
    return result


def _selected(index, rel, selection):
    selected = selection.split("[")[0].replace("::", ".")
    result = []
    if not selected:
        result.extend(s for s in _each(index.symbols.values()) if s.file == rel and not s.owner
                      and "." not in s.symbol and s.node.name.startswith("test_"))
    class_name, _, method = selected.partition(".")
    for cls in _each(list(index.classes.values())):
        if cls is None or cls.file != rel or not cls.node.name.startswith("Test"):
            continue
        if selected and cls.symbol != class_name:
            continue
        methods = {}
        collectible = True
        try:
            hierarchy = _mro(index, cls)
        except _FileOmitted as exc:
            index.skipped.append(skip(cls.binding, exc.args[0]))
            continue
        for owner in _each(hierarchy):
            scope = index.symbol_scopes[id(owner.node)]
            # Non-callable overrides also hide inherited methods.
            for name, entries in _each(scope.names.items()):
                if name in methods:
                    continue
                symbol = index.symbols.get(f"{owner.binding}.{name}")
                methods[name] = symbol if len(entries) == 1 and symbol is not None else None
                if name == "__test__" and len(entries) == 1:
                    value = entries[0].value
                    if isinstance(value, ast.Constant) and value.value is False:
                        collectible = False
        if not collectible or "__init__" in methods or "__new__" in methods:
            index.skipped.append(skip(cls.binding, "pytest does not collect this class"))
            continue
        result.extend(s for name, s in _each(methods.items()) if s is not None and name.startswith("test_")
                      and (not method or method == name))
    if selected and "." not in selected:
        symbol = index.symbols.get(f"{index.modules.get(rel, '')}.{selected}")
        if symbol and not symbol.owner and "." not in symbol.symbol and symbol.node.name.startswith("test_"):
            result.append(symbol)
    return result


def _exercised(targets: Sequence[dict], checks: Mapping[str, str], roots: Sequence[str], inventory=None):
    result = {t["binding"]: [] for t in _each(targets)}
    skipped = []
    texts = dict(inventory or {})
    for check, source in _each(checks.items()):
        texts[check.partition("::")[0]] = source
    if not checks:
        return result, skipped
    index = _Index(texts, roots, skipped, "check")
    # Constructors are data here: resolving C().method never constructs C.
    for target in _each(targets):
        if target.get("owner"):
            owner = target["binding"].rpartition(".")[0]
            index.classes.setdefault(owner, None)
    for check in _each(sorted(checks)):
        rel, _, selection = check.partition("::")
        for symbol in _each(_selected(index, rel, selection)):
            for binding in _each(index.calls(symbol)):
                if binding in result and check not in result[binding]:
                    result[binding].append(check)
    return result, skipped


def exercised(targets: Sequence[dict], checks: Mapping[str, str], *, roots: Sequence[str] = ()) -> dict[str, list[str]]:
    """Named calls in selected check bodies; deliberately no execution/coverage claim."""
    result = _bounded(lambda: {"calls": _exercised(targets, checks, roots)[0]})
    return result["calls"] if "calls" in result else {str(t["binding"]): [] for t in targets}


def _build(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, stated_checks: Sequence[str] = (),
          proposed_checks: Sequence[Mapping[str, str]] = (), roots: Sequence[str] = ()) -> dict:
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
            # Folder selections are bounded by the supplied text inventory.
            # Split literally: case, backslashes, odd bytes and interior empty parts retain their meaning.
            parts = rel.rstrip("/").split("/")
            checks.update({p: text for p, text in _each(patch.items())
                           if len(p.split("/")) > len(parts) and p.split("/")[:len(parts)] == parts})
    for rel in _each(change.files):
        if rel in patch and _is_test(rel):
            checks[rel] = patch[rel]
    for proposed in _each(proposed_checks):
        node = proposed["file"] + ("::" + proposed["test"] if proposed.get("test") else "")
        checks[node] = proposed["source"]
    called, check_skips = _exercised(neighbours, checks, roots, patch)
    skipped.extend(check_skips)
    for sibling in _each(neighbours):
        sibling.update(exercised=bool(called[sibling["binding"]]), checks=called[sibling["binding"]])
        if not sibling["exercised"]:
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
        # Persisted path fields retain their exact bytes/spelling, including
        # GitName. Display text is constructed only through shown_name.
        return value
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
        result = _plain(call())
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
          proposed_checks: Sequence[Mapping[str, str]] = (), roots: Sequence[str] = (), deadline=None) -> dict:
    """Keep file-local skips; omit the advisory on whole-run limits or failures."""
    return _bounded(lambda: _build(change, base, patch, stated_checks=stated_checks,
                                  proposed_checks=proposed_checks, roots=roots), deadline)
