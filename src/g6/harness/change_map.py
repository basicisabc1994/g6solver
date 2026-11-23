# SPDX-License-Identifier: Apache-2.0
"""Tier 0: AST-only leads, never review evidence or a decision.

Inputs are file texts, not importable modules. ``exercised`` means a static named
call was seen, not that the call ran. Dynamic dispatch, reflection, fixtures,
external packages and arbitrary data flow are outside this map's view.
"""
from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Mapping, Sequence

from g6.core.git_name import shown_name

if TYPE_CHECKING:
    from g6.workforce.repo_tests import Change

MAX_FILES = 512
MAX_FILE_CHARS = 100_000
MAX_TOTAL_CHARS = 2_000_000
MAX_NODES = 100_000
MAX_SYMBOLS = 512
MAX_TARGETS = 512
STATIC_LIMIT = ("Static named calls only: this cannot see dynamic dispatch, reflection, external code, fixture "
                "injection or arbitrary data flow; a named call may never execute. No execution coverage is claimed.")
_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)


def empty() -> dict:
    """The shared neighbourhood record; later tiers own instances, cases and gates."""
    return {"targets": [], "instances": [], "cases": [], "gates": [], "skipped": []}


def skip(check: str, reason: str) -> dict:
    return {"tier": 0, "check": check, "reason": reason}


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def _module(rel: str, roots: Sequence[str]) -> str:
    path = PurePosixPath(rel)
    for root in sorted({"src", *roots} - {"", "."}, key=len, reverse=True):
        if path.is_relative_to(root):
            path = path.relative_to(root)
            break
    parts = list(path.with_suffix("").parts)
    if parts[-1:] == ["__init__"]:
        parts.pop()
    return ".".join(shown_name(part) for part in parts)


def _nodes(node: ast.AST, *, headers: bool = False, lambdas: bool = False):
    """One callable's code, excluding bodies of nested definitions."""
    if headers:
        pending = list(reversed(list(ast.iter_child_nodes(node))))
    else:
        pending = list(reversed(node.body)) if isinstance(node, (*_FUNCTION, ast.Module, ast.ClassDef)) else [node]
    while pending:
        item = pending.pop()
        yield item
        if not isinstance(item, (*_FUNCTION, ast.ClassDef)) and (lambdas or not isinstance(item, ast.Lambda)):
            pending.extend(reversed(list(ast.iter_child_nodes(item))))


def _added(before: str, after: str) -> set[int]:
    matcher = difflib.SequenceMatcher(None, before.split("\n"), after.split("\n"))
    return {line for tag, _, _, start, end in matcher.get_opcodes() if tag in ("insert", "replace")
            for line in range(start + 1, end + 1)}


def _touches(node: ast.AST, lines: set[int]) -> bool:
    return any(line in lines for line in range(getattr(node, "lineno", 0), getattr(node, "end_lineno", 0) + 1))


def _is_test(rel: str) -> bool:
    path = PurePosixPath(rel)
    return (path.name.startswith("test_") or path.name.endswith("_test.py") or path.name == "conftest.py"
            or any(part in ("test", "tests", "testing") for part in path.parts[:-1]))


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


class _Index:
    def __init__(self, texts: Mapping[str, str], roots: Sequence[str], skipped: list[dict], side: str):
        self.trees: dict[str, ast.Module] = {}
        self.symbols: dict[str, _Symbol] = {}
        self.classes: dict[str, _Symbol] = {}
        self.modules: dict[str, str] = {}
        self.bindings: dict[str, str] = {}
        self.invalid: set[str] = set()
        self.skipped = skipped
        total = 0
        for rel in sorted(texts):
            if not rel.endswith(".py"):
                continue
            text = texts[rel]
            total += len(text)
            if len(self.trees) >= MAX_FILES or len(text) > MAX_FILE_CHARS or total > MAX_TOTAL_CHARS:
                self.invalid.add(rel)
                skipped.append(skip(f"change map: {side} {shown_name(rel)}", "static analysis text limit reached"))
                continue
            try:
                tree = ast.parse(text)
                if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
                    raise ValueError("AST node limit reached")
            except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
                self.invalid.add(rel)
                why = "syntax unavailable" if isinstance(exc, SyntaxError) else "AST analysis limit reached"
                skipped.append(skip(f"change map: {side} {shown_name(rel)}", why))
                continue
            self.trees[rel], self.modules[rel] = tree, _module(rel, roots)
            self._definitions(rel, tree.body)
            if len(self.symbols) + len(self.classes) >= MAX_SYMBOLS:
                self.invalid.update(p for p in texts if p not in self.trees or p == rel)
                self.symbols = {key: s for key, s in self.symbols.items() if s.file != rel}
                self.classes = {key: s for key, s in self.classes.items() if s.file != rel}
                del self.trees[rel], self.modules[rel]
                skipped.append(skip(f"change map: {side}", "symbol limit reached; import closure is partial"))
                break
        self._imports()

    def _definitions(self, rel: str, body: list, prefix: str = "", owner: str = "") -> None:
        for node in body:
            if len(self.symbols) + len(self.classes) >= MAX_SYMBOLS:
                return
            if isinstance(node, (*_FUNCTION, ast.ClassDef)):
                qual = f"{prefix}.{node.name}".lstrip(".")
                symbol = _Symbol(rel, self.modules[rel], qual, owner, node)
                if isinstance(node, ast.ClassDef):
                    self.classes[symbol.binding] = symbol
                    self._definitions(rel, node.body, qual, qual)
                else:
                    self.symbols[symbol.binding] = symbol
                    self._definitions(rel, node.body, qual, "")
            else:
                # Definitions under module/class conditionals are visible statically too.
                for field in ("body", "orelse", "finalbody"):
                    nested = getattr(node, field, None)
                    if isinstance(nested, list):
                        self._definitions(rel, nested, prefix, owner)

    def _imports(self) -> None:
        for rel, tree in self.trees.items():
            module = self.modules[rel]
            package = module if PurePosixPath(rel).name == "__init__.py" else module.rpartition(".")[0]
            for node in _nodes(tree):
                if len(self.bindings) > MAX_SYMBOLS:
                    self.skipped.append(skip("change map import closure", "binding limit reached; import closure is partial"))
                    return
                if isinstance(node, (*_FUNCTION, ast.ClassDef)):
                    self.bindings.pop(f"{module}.{node.name}", None)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.bindings[f"{module}.{alias.asname or alias.name.split('.')[0]}"] = (
                            alias.name if alias.asname else alias.name.split('.')[0])
                elif isinstance(node, ast.ImportFrom):
                    parts = package.split(".") if package else []
                    parent = ".".join(parts[:len(parts) - node.level + 1]) if node.level else ""
                    source = ".".join(p for p in (parent, node.module) if p)
                    for alias in node.names:
                        if alias.name != "*":
                            self.bindings[f"{module}.{alias.asname or alias.name}"] = f"{source}.{alias.name}".lstrip(".")
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    value = _name(node.value) if node.value is not None else ""
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            self.bindings[f"{module}.{target.id}"] = f"{module}.{value}" if value else ""

    def canonical(self, name: str) -> str:
        seen = set()
        while name not in seen and len(seen) < 64:
            seen.add(name)
            parts = name.split(".")
            for end in range(len(parts), 0, -1):
                prefix = ".".join(parts[:end])
                if prefix in self.bindings:
                    if not self.bindings[prefix]:
                        return ""
                    candidate = ".".join([self.bindings[prefix], *parts[end:]])
                    if candidate != name:
                        name = candidate
                        break
            else:
                return name
        # Cycles and very long alias chains have no dependable static binding.
        entry = skip("change map import closure", "cyclic or over-limit alias chain could not be resolved")
        if entry not in self.skipped:
            self.skipped.append(entry)
        return ""

    def resolve(self, node: ast.AST, symbol: _Symbol, locals_: dict[str, str] | None = None) -> str:
        if isinstance(node, ast.Call):
            return self.resolve(node.func, symbol, locals_)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Call):
            base = self.resolve(node.value.func, symbol, locals_)
            return f"{base}.{node.attr}" if base in self.classes else ""
        name = _name(node)
        if not name:
            return ""
        first, _, tail = name.partition(".")
        if first in ("self", "cls") and symbol.owner:
            return f"{symbol.module}.{symbol.owner}.{tail}" if tail else ""
        if locals_ is not None and first in locals_:
            return self.canonical(".".join(p for p in (locals_[first], tail) if p)) if locals_[first] else ""
        return self.canonical(f"{symbol.module}.{name}")

    def locals(self, symbol: _Symbol) -> dict[str, str]:
        found = {}
        args = getattr(symbol.node, "args", None)
        if args:
            found.update({a.arg: "" for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]})
            found.update({a.arg: "" for a in (args.vararg, args.kwarg) if a})
        for node in _nodes(symbol.node):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = self.resolve(node.value, symbol, found) if node.value is not None else ""
                for target in targets:
                    if isinstance(target, ast.Name):
                        found[target.id] = value
            elif isinstance(node, ast.ImportFrom) and not node.level:
                for alias in node.names:
                    found[alias.asname or alias.name] = self.canonical(f"{node.module}.{alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    found[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
        return found

    def calls(self, symbol: _Symbol) -> set[str]:
        locals_ = self.locals(symbol)
        return {self.resolve(n.func, symbol, locals_) for n in _nodes(symbol.node) if isinstance(n, ast.Call)} - {""}


def _context(change: Change, base: Mapping[str, str], patch: Mapping[str, str], roots: Sequence[str]):
    skipped: list[dict] = []
    old, new = _Index(base, roots, skipped, "base"), _Index(patch, roots, skipped, "patch")
    added = {p: _added(base.get(p, ""), patch.get(p, "")) for p in change.files
             if p.endswith(".py") and not _is_test(p) and p not in old.invalid | new.invalid
             and len(base.get(p, "")) <= MAX_FILE_CHARS and len(patch.get(p, "")) <= MAX_FILE_CHARS}
    changed = []
    for key in sorted(old.symbols.keys() | new.symbols.keys()):
        symbol = new.symbols.get(key) or old.symbols[key]
        if symbol.file not in change.files or _is_test(symbol.file) or symbol.file in old.invalid | new.invalid:
            continue
        left, right = old.symbols.get(key), new.symbols.get(key)
        if left is None or right is None or _body(left.node) != _body(right.node):
            changed.append({**symbol.record(), "change": "added" if left is None else "deleted" if right is None else "changed"})
    return old, new, added, changed, skipped


def _body(node: ast.AST) -> str:
    return ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)


def changed_symbols(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, roots: Sequence[str] = ()) -> list[dict]:
    """Functions and methods with AST body changes; names include enclosing classes."""
    return _context(change, base, patch, roots)[3]


def _literals(node: ast.AST) -> list:
    values = []
    signed = {id(n.operand) for n in ast.walk(node) if isinstance(n, ast.UnaryOp)
              and isinstance(n.op, (ast.USub, ast.UAdd)) and isinstance(n.operand, ast.Constant)
              and type(n.operand.value) in (int, float)}
    for item in ast.walk(node):
        if (isinstance(item, ast.UnaryOp) and isinstance(item.op, (ast.USub, ast.UAdd))
                and isinstance(item.operand, ast.Constant) and id(item.operand) in signed):
            value = -item.operand.value if isinstance(item.op, ast.USub) else item.operand.value
        elif isinstance(item, ast.Constant) and id(item) not in signed and type(item.value) in (str, int, float, bool, type(None)):
            value = item.value
        else:
            continue
        if not any(type(value) is type(v) and value == v for v in values):
            values.append(value)
    return values


def _decisions(scope: ast.AST) -> list[tuple[ast.AST, str]]:
    candidates = []
    for node in _nodes(scope, headers=True, lambdas=True):
        if isinstance(node, ast.Compare):
            candidates.append((node, "membership" if any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
                               else "comparison"))
        elif isinstance(node, ast.Call):
            name = _name(node.func).split(".")[-1]
            if name in ("isinstance", "startswith", "endswith"):
                candidates.append((node, name))
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            candidates.extend((leaf, "truthiness") for leaf in _truth_tests(node.test))
        elif isinstance(node, ast.comprehension):
            candidates.extend((leaf, "truthiness") for test in node.ifs for leaf in _truth_tests(test))
        elif isinstance(node, ast.BoolOp):
            candidates.extend((leaf, "truthiness") for value in node.values[:-1] for leaf in _truth_tests(value))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            candidates.extend((leaf, "truthiness") for leaf in _truth_tests(node.operand))
    return candidates


def _points(new: _Index, added: dict[str, set[int]], changed: list[dict]) -> list[dict]:
    points = []
    scopes = [*new.symbols.values(), *new.classes.values(),
              *(_Symbol(rel, new.modules[rel], "", "", tree) for rel, tree in new.trees.items())]
    for symbol in scopes:
        lines = added.get(symbol.file, set())
        if not lines:
            continue
        for node, kind in _decisions(symbol.node):
            if _touches(node, lines):
                points.append({"file": symbol.file, "symbol": symbol.symbol, "binding": symbol.binding,
                               "kind": "decision_point", "decision": kind,
                               "line": node.lineno, "expression": ast.unparse(node)[:500], "literals": _literals(node)})
                if len(points) > MAX_TARGETS:
                    return points
        if (isinstance(symbol.node, _FUNCTION) and symbol.owner and symbol.node.name.startswith("visit_") and
                any(c["binding"] == symbol.binding and c["change"] == "added" for c in changed)):
            points.append({**symbol.record(), "kind": "decision_point", "decision": "handler", "literals": [],
                           "expression": symbol.node.name})
    unique = {(p["file"], p["symbol"], p["line"], p["decision"], p["expression"]): p for p in points}
    return [unique[k] for k in sorted(unique)]


def _truth_tests(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BoolOp):
        return [leaf for value in node.values for leaf in _truth_tests(value)]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _truth_tests(node.operand)
    if isinstance(node, ast.Compare) or (isinstance(node, ast.Call) and
            _name(node.func).split(".")[-1] in ("isinstance", "startswith", "endswith")):
        return []
    return [node]


def decision_points(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, roots: Sequence[str] = ()) -> list[dict]:
    """Decisions intersecting added lines, with literal leaves and new visitor handlers."""
    _, new, added, changed, _ = _context(change, base, patch, roots)
    return _points(new, added, changed)


def _public(symbol: _Symbol) -> bool:
    return not _is_test(symbol.file) and all(not part.startswith("_") for part in symbol.symbol.split("."))


def _siblings(old: _Index, new: _Index, added: dict[str, set[int]], changed: list[dict], points: list[dict]) -> list[dict]:
    changed_keys = {c["binding"] for c in changed}
    found: dict[str, dict] = {}

    def offer(symbol: _Symbol, rank: int, reason: str, changed_key: str, **detail):
        if symbol.binding in changed_keys:
            return
        if symbol.binding not in found:
            found[symbol.binding] = {**symbol.record(), "kind": "sibling", "rank": rank,
                                     "reason": reason, "changed_symbols": [changed_key], **detail}
        else:
            entry = found[symbol.binding]
            entry["changed_symbols"] = sorted(set([*entry["changed_symbols"], changed_key]))
            if rank < entry["rank"]:
                entry.update(rank=rank, reason=reason, **detail)

    calls = {key: new.calls(s) for key, s in new.symbols.items()}
    for key, symbol in new.symbols.items():
        if _public(symbol):
            for changed_key in sorted(calls[key] & changed_keys):
                offer(symbol, 10, "calls a changed symbol", changed_key)
    for changed_record in changed:
        key = changed_record["binding"]
        symbol = new.symbols.get(key)
        if symbol is None or not symbol.owner:
            continue
        reads = {n.attr for n in _nodes(symbol.node) if isinstance(n, ast.Attribute) and
                 isinstance(n.ctx, ast.Load) and _name(n.value) in ("self", "cls") and
                 _touches(n, added.get(symbol.file, set()))}
        locals_ = new.locals(symbol)
        helpers = {new.resolve(n.func, symbol, locals_) for n in _nodes(symbol.node) if isinstance(n, ast.Call)
                   and _touches(n, added.get(symbol.file, set()))} - {""}
        for sibling in new.symbols.values():
            if sibling.file != symbol.file or sibling.owner != symbol.owner or not _public(sibling):
                continue
            writes = {n.attr for n in _nodes(sibling.node) if isinstance(n, ast.Attribute) and
                      isinstance(n.ctx, ast.Store) and _name(n.value) in ("self", "cls")}
            shared = sorted(reads & writes)
            if shared:
                offer(sibling, 20, "writes the state this patch reads", key, attributes=shared)
            elif calls[sibling.binding] & helpers:
                offer(sibling, 20, "calls the same helper as the changed method", key,
                      helpers=sorted(calls[sibling.binding] & helpers))
            if changed_record["change"] == "added" and symbol.node.name.startswith("visit_") and sibling.node.name.startswith("visit_"):
                offer(sibling, 30, "is a family handler this patch did not add", key)
    for binding in sorted(new.bindings):
        canonical = new.canonical(binding)
        if canonical in changed_keys and binding != canonical and not binding.split(".")[-1].startswith("_"):
            module, _, name = binding.rpartition(".")
            rel = next((p for p, m in new.modules.items() if m == module), "")
            offer(_Symbol(rel, module, name, "", ast.Name(lineno=1)), 40,
                  "is another name bound to the changed function", canonical)
    # Overrides can keep a distinct implementation behind the same public entry point.
    for child in new.classes.values():
        ancestors, pending = set(), [(child, base) for base in child.node.bases]
        while pending:
            scope, node = pending.pop()
            base = new.resolve(node, scope)
            if base in ancestors:
                continue
            ancestors.add(base)
            if base in new.classes:
                parent_class = new.classes[base]
                pending.extend((parent_class, node) for node in parent_class.node.bases)
        for record in changed:
            parent = new.symbols.get(record["binding"]) or old.symbols.get(record["binding"])
            if parent and parent.owner and f"{parent.module}.{parent.owner}" in ancestors:
                override = new.symbols.get(f"{child.binding}.{parent.node.name}")
                if override and _public(override):
                    offer(override, 40, "overrides the changed method in a subclass", parent.binding)
    _token_siblings(old, new, points, offer)
    return sorted(found.values(), key=lambda s: (s["rank"], s["file"], s["symbol"]))


def _token_siblings(old: _Index, new: _Index, points: list[dict], offer) -> None:
    members: dict[str, dict[str, _Symbol]] = {}
    comparisons: dict[tuple[str, int], list[ast.Compare]] = {}
    for index in (old, new):
        for cls in index.classes.values():
            enumeration = any(index.resolve(base, cls).split(".")[-1] in ("Enum", "IntEnum", "StrEnum", "Flag", "IntFlag")
                              for base in cls.node.bases)
            for node in cls.node.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and (target.id.isupper() or (enumeration and not target.id.startswith("_"))):
                            item = _Symbol(cls.file, cls.module, f"{cls.symbol}.{target.id}", cls.symbol, node)
                            members.setdefault(cls.binding, {})[target.id] = item
        for rel, tree in index.trees.items():
            scope = _Symbol(rel, index.modules[rel], "", "", tree)
            for node in ast.walk(tree):
                if index is new and isinstance(node, ast.Compare):
                    comparisons.setdefault((rel, node.lineno), []).append(node)
                if isinstance(node, ast.Attribute):
                    name = index.resolve(node, scope)
                    family, _, leaf = name.rpartition(".")
                    if node.attr.isupper():
                        members.setdefault(family, {}).setdefault(leaf, _Symbol(rel, "", name, "", node))
    selected: dict[str, set[str]] = {}
    sources = {}
    for point in points:
        if point["decision"] not in ("comparison", "membership"):
            continue
        scope = new.symbols.get(point["binding"])
        if scope is None:
            scope = _Symbol(point["file"], new.modules[point["file"]], "", "", ast.Module(body=[]))
        for comparison in comparisons.get((point["file"], point["line"]), []):
            for node in ast.walk(comparison):
                if isinstance(node, ast.Attribute):
                    binding = new.resolve(node, scope)
                    family, _, leaf = binding.rpartition(".")
                    if node.attr.isupper() or leaf in members.get(family, {}):
                        selected.setdefault(family, set()).add(leaf)
                        sources[family] = point["binding"]
    for family, added_members in selected.items():
        for name, symbol in members.get(family, {}).items():
            if name not in added_members:
                offer(symbol, 30, "is a token or enumeration family member this patch did not add", sources[family])


def siblings(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, roots: Sequence[str] = ()) -> list[dict]:
    """Ranked named callers, shared-state paths, families, aliases and overrides."""
    old, new, added, changed, _ = _context(change, base, patch, roots)
    return _siblings(old, new, added, changed, _points(new, added, changed))


def _exercised(targets: Sequence[dict], checks: Mapping[str, str], roots: Sequence[str]):
    result = {t["binding"]: [] for t in targets}
    skipped = []
    for check, source in sorted(checks.items()):
        rel, _, selection = check.partition("::")
        index = _Index({rel: source}, roots, skipped, "check")
        # Constructors are data here: resolving C().method never constructs C.
        for target in targets:
            if target.get("owner"):
                owner = target["binding"].rpartition(".")[0]
                index.classes.setdefault(owner, None)
        selected = selection.split("[")[0].replace("::", ".")
        symbols = [s for s in index.symbols.values() if not selected or s.symbol == selected]
        for symbol in symbols:
            for binding in index.calls(symbol):
                if binding in result and check not in result[binding]:
                    result[binding].append(check)
    return result, skipped


def exercised(targets: Sequence[dict], checks: Mapping[str, str], *, roots: Sequence[str] = ()) -> dict[str, list[str]]:
    """Named calls in selected check bodies; deliberately no execution/coverage claim."""
    return _exercised(targets, checks, roots)[0]


def build(change: Change, base: Mapping[str, str], patch: Mapping[str, str], *, stated_checks: Sequence[str] = (),
          proposed_checks: Sequence[Mapping[str, str]] = (), roots: Sequence[str] = ()) -> dict:
    """One advisory neighbourhood; an ordinary body edit alone creates no target."""
    record = empty()
    old, new, added, changed, skipped = _context(change, base, patch, roots)
    points = _points(new, added, changed)
    neighbours = _siblings(old, new, added, changed, points)
    checks = {}
    for node in stated_checks:
        rel = node.partition("::")[0]
        if rel in patch:
            checks[node] = patch[rel]
        else:
            # Folder selections are bounded by the supplied text inventory.
            # Split literally: case, backslashes, odd bytes and interior empty parts retain their meaning.
            parts = rel.rstrip("/").split("/")
            checks.update({p: text for p, text in patch.items()
                           if len(p.split("/")) > len(parts) and p.split("/")[:len(parts)] == parts})
    for rel in change.files:
        if rel in patch and _is_test(rel):
            checks[rel] = patch[rel]
    for proposed in proposed_checks:
        node = proposed["file"] + ("::" + proposed["test"] if proposed.get("test") else "")
        checks[node] = proposed["source"]
    called, check_skips = _exercised(neighbours, checks, roots)
    skipped.extend(check_skips)
    for sibling in neighbours:
        sibling.update(exercised=bool(called[sibling["binding"]]), checks=called[sibling["binding"]])
        if not sibling["exercised"]:
            skipped.append(skip(sibling["symbol"], f"{sibling['symbol']} {sibling['reason']}; no check calls it"))
    targets = [*neighbours, *points]
    if len(targets) > MAX_TARGETS:
        skipped.append(skip("change map targets", f"target limit reached; {len(targets) - MAX_TARGETS} targets omitted"))
    record["targets"] = targets[:MAX_TARGETS]
    record["skipped"] = [*skipped, skip("change map static visibility", STATIC_LIMIT)]
    return record
