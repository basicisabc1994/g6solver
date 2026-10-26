# SPDX-License-Identifier: Apache-2.0
"""The import graph of a package given as a file map, its cycles, its layer breaches and its pass-through functions.

Nothing touches a disk: the caller supplies the files (path to source, paths relative to the folder that holds the
package, ``/``-separated), and each is parsed with :mod:`ast` and never run. A path ``shop/cart/model.py`` is the
module ``shop.cart.model``; ``shop/cart/__init__.py`` is the package ``shop.cart``. An import (``import x``, ``from x
import y``, relative or not, at module level or inside a function) of a module inside ``package`` is an edge, kept
with its file and line and whether it sits inside a function or under ``if TYPE_CHECKING``; any other import is
external and only counted. Modules are grouped by their first ``depth`` dotted parts, so ``depth=2`` makes
``shop.cart.model`` a node ``shop.cart``.

* :func:`cycles`: the strongly connected components with more than one node, by Tarjan's algorithm (the archive's,
  made iterative so a deep graph needs no change to the interpreter's recursion limit), each with one cycle through it.
* :func:`layer_violations`: a contract's layers, highest first (each a dotted module prefix): a module of a lower layer
  that imports one of a higher layer is a breach; and its forbidden pairs (a source prefix that must not import a
  forbidden prefix). A layer or pair that matches no module is refused, so a misspelt contract never passes.
* :func:`pass_through`: functions whose one statement returns a call that forwards their parameters unchanged and in
  order (the archive's detector): an abstraction that adds nothing.
* :func:`diff_cycles`: the cycles a change introduced and the ones it removed, between two file maps.
"""
from __future__ import annotations

import ast
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from g6.code._types import CodeRefused

NO_EDGES = "G6_E_DESIGN_NO_EDGES"
CONTRACT_UNMATCHED = "G6_E_DESIGN_CONTRACT_UNMATCHED"
BAD_PATH = "G6_E_DESIGN_BAD_PATH"


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    file: str
    line: int
    lazy: bool
    type_checking: bool


@dataclass
class Graph:
    """The modules found, the edges between them inside the package, and what was left out."""

    package: str
    modules: dict[str, str]  # module -> file
    edges: list[Edge] = field(default_factory=list)
    external: int = 0
    unparsed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def grouped(self, depth: int, *, lazy: bool = True,
                type_checking: bool = True) -> dict[str, dict[str, list[Edge]]]:
        """Group -> group -> the edges between them (a group's edges to itself left out)."""
        found: dict[str, dict[str, list[Edge]]] = {group(m, depth): {} for m in self.modules}
        for edge in self.edges:
            if (edge.lazy and not lazy) or (edge.type_checking and not type_checking):
                continue
            a, b = group(edge.source, depth), group(edge.target, depth)
            if a != b:
                found.setdefault(a, {}).setdefault(b, []).append(edge)
        return found


def group(module: str, depth: int) -> str:
    return ".".join(module.split(".")[:depth])


def module_of(path: str) -> str | None:
    """The module a relative ``.py`` path names, or ``None`` for a path that is not one."""
    parts = path.replace("\\", "/").split("/")
    if not path.endswith(".py") or any(p in ("", ".", "..") for p in parts) or path.startswith(("/", "~")) \
            or ":" in parts[0]:
        return None
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts and all(p.isidentifier() for p in parts) else None


def _type_checking(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _imports(tree: ast.Module) -> list[tuple[ast.AST, bool, bool]]:
    """Every import statement with whether it is inside a function and under ``if TYPE_CHECKING``."""
    found = []

    def visit(node: ast.AST, lazy: bool, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            inner_guard = guarded or (
                isinstance(node, ast.If) and _type_checking(node) and child in node.body)
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                found.append((child, lazy, inner_guard))
            inner_lazy = lazy or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            visit(child, inner_lazy, inner_guard)

    visit(tree, False, False)
    return found


def _resolve(importer: str, is_package: bool, node: ast.AST, modules: Mapping[str, str]) -> list[str]:
    """The module names an import statement reaches (inside or outside the package)."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    assert isinstance(node, ast.ImportFrom)
    if node.level:
        base = importer.split(".") if is_package else importer.split(".")[:-1]
        base = base[:len(base) - (node.level - 1)] if node.level > 1 else base
        stem = ".".join([*base, *([node.module] if node.module else [])])
    else:
        stem = node.module or ""
    submodules = [f"{stem}.{a.name}" if stem else a.name for a in node.names
                  if (f"{stem}.{a.name}" if stem else a.name) in modules]
    # from pkg import submodule reaches the submodule (which needs pkg anyway); a name that is not a submodule
    # reaches pkg itself
    names_left = len(submodules) < len(node.names)
    return [*([stem] if stem and names_left else []), *submodules]


def build(files: Mapping[str, str], package: str) -> Graph:
    """The graph of ``package`` in ``files``."""
    modules: dict[str, str] = {}
    graph = Graph(package, modules)
    for path in sorted(files):
        name = module_of(path)
        if name is None:
            if path.endswith(".py"):
                raise CodeRefused(BAD_PATH, f"{path!r} is not a relative path of a module (use a/b/c.py, with no "
                                            "drive, no leading / and no ..)")
            graph.skipped.append(path)
            continue
        modules[name] = path
    inside = {m for m in modules if m == package or m.startswith(package + ".")}
    for name, path in sorted(modules.items()):
        try:
            tree = ast.parse(files[path])
        except (SyntaxError, ValueError) as exc:
            graph.unparsed.append({"file": path, "line": getattr(exc, "lineno", None),
                                   "message": str(getattr(exc, "msg", exc))[:200]})
            continue
        is_package = path.endswith("__init__.py")
        for node, lazy, guarded in _imports(tree):
            for target in _resolve(name, is_package, node, modules):
                hit = _nearest(target, inside)
                if hit is None:
                    graph.external += 1
                elif name in inside and hit != name:
                    graph.edges.append(Edge(name, hit, path, node.lineno, lazy, guarded))
    graph.modules = {m: p for m, p in modules.items() if m in inside}
    return graph


def _nearest(target: str, inside: set[str]) -> str | None:
    """The module of the package an import of ``target`` reaches: itself, or the nearest package above it."""
    parts = target.split(".")
    while parts:
        name = ".".join(parts)
        if name in inside:
            return name
        parts.pop()
    return None


def tarjan(nodes: list[str], graph: Mapping[str, Mapping[str, Any]]) -> list[list[str]]:
    """The strongly connected components with more than one node, each sorted; iterative, so no recursion limit."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(graph.get(root, {}))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph.get(child, {})))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    found.append(sorted(component))
    return sorted(found)


def cycle_through(component: list[str], graph: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """One cycle inside a strongly connected component, as the nodes it passes, starting and ending at its first."""
    members, start = set(component), component[0]
    previous: dict[str, str] = {}
    queue, seen = deque([start]), {start}
    while queue:
        node = queue.popleft()
        for child in sorted(graph.get(node, {})):
            if child not in members:
                continue
            if child == start:
                path = [node]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                return [*reversed(path), start]
            if child not in seen:
                seen.add(child)
                previous[child] = node
                queue.append(child)
    return [start]


def cycles(graph: Graph, depth: int, *, lazy: bool = True, type_checking: bool = True) -> list[dict[str, Any]]:
    grouped = graph.grouped(depth, lazy=lazy, type_checking=type_checking)
    out = []
    for component in tarjan(sorted(grouped), grouped):
        path = cycle_through(component, grouped)
        closing = [grouped[a][b][0] for a, b in zip(path, path[1:]) if b in grouped.get(a, {})]
        out.append({"modules": component, "cycle": path,
                    "imports": [{"from": e.source, "to": e.target, "file": e.file, "line": e.line, "lazy": e.lazy}
                                for e in closing]})
    return out


def _in(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def layer_violations(graph: Graph, layers: tuple[str, ...],
                     forbidden: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    """Each import that breaks the layers (a lower layer importing a higher one) or a forbidden pair."""
    def rank(module: str) -> int | None:
        hits = [i for i, prefix in enumerate(layers) if _in(module, prefix)]
        return max(hits, key=lambda i: len(layers[i])) if hits else None

    assigned = {rank(m) for m in graph.modules}
    unmatched = [p for i, p in enumerate(layers) if i not in assigned]
    unmatched += [p for pair in forbidden for p in pair if not any(_in(m, p) for m in graph.modules)]
    if unmatched:
        raise CodeRefused(CONTRACT_UNMATCHED, f"the contract names {', '.join(sorted(set(unmatched)))}, which no "
                                              "module of the package is in: a contract that matches nothing would "
                                              "pass whatever the code does")


    out = []
    for edge in graph.edges:
        source, target = rank(edge.source), rank(edge.target)
        if source is not None and target is not None and target < source:
            out.append({"rule": "layers", "from": edge.source, "to": edge.target, "file": edge.file,
                        "line": edge.line, "lazy": edge.lazy,
                        "why": f"{layers[source]} is below {layers[target]}, so it may not import it"})
        for src, banned in forbidden:
            if _in(edge.source, src) and _in(edge.target, banned):
                out.append({"rule": "forbidden", "from": edge.source, "to": edge.target, "file": edge.file,
                            "line": edge.line, "lazy": edge.lazy, "why": f"{src} may not import {banned}"})
    return out


def _forwards(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """One statement (a docstring aside) returning a call that passes the parameters on unchanged and in order."""
    body = [s for s in func.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if len(body) != 1 or not isinstance(body[0], ast.Return) or not isinstance(body[0].value, ast.Call):
        return False
    params = [a.arg for a in [*func.args.posonlyargs, *func.args.args] if a.arg not in ("self", "cls")]
    if not params or func.args.vararg or func.args.kwarg or func.args.kwonlyargs:
        return False
    call = body[0].value
    positional = [a.id for a in call.args if isinstance(a, ast.Name)]
    keywords = [k.arg for k in call.keywords if isinstance(k.value, ast.Name) and k.arg == k.value.id]
    passed = positional + keywords
    return len(passed) == len(call.args) + len(call.keywords) and passed == params


def pass_through(files: Mapping[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    found, unparsed = [], []
    for path in sorted(files):
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(files[path])
        except (SyntaxError, ValueError) as exc:
            unparsed.append({"file": path, "line": getattr(exc, "lineno", None),
                             "message": str(getattr(exc, "msg", exc))[:200]})
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _forwards(node):
                returned = next(s for s in node.body if isinstance(s, ast.Return))
                call = returned.value
                if node.body[-1] is not returned:
                    raise CodeRefused("G6_E_DESIGN_INCOMPLETE",
                                      f"{path}:{node.lineno}: trailing statements after a forwarding return")
                found.append({"file": path, "line": node.lineno, "function": node.name,
                              "forwards_to": ast.unparse(call.func)[:120]})
    return found, unparsed


def diff_cycles(before: Graph, after: Graph, depth: int) -> dict[str, Any]:
    old, new = cycles(before, depth), cycles(after, depth)
    old_sets = [set(c["modules"]) for c in old]
    new_sets = [set(c["modules"]) for c in new]
    introduced = [c for c in new if not any(set(c["modules"]) <= s for s in old_sets)]
    removed = [c for c in old if not any(set(c["modules"]) <= s for s in new_sets)]
    before_edges = {(e.source, e.target) for e in before.edges}
    closing = []
    for c in introduced:
        members = set(c["modules"])
        closing += [{"from": e.source, "to": e.target, "file": e.file, "line": e.line} for e in after.edges
                    if group(e.source, depth) in members and group(e.target, depth) in members
                    and (e.source, e.target) not in before_edges]
    return {"introduced": introduced, "removed": removed, "new_imports_in_cycles": closing,
            "before": len(old), "after": len(new)}


__all__ = ["BAD_PATH", "CONTRACT_UNMATCHED", "NO_EDGES", "Edge", "Graph", "build", "cycle_through", "cycles",
           "diff_cycles", "group", "layer_violations", "module_of", "pass_through", "tarjan"]
