# SPDX-License-Identifier: Apache-2.0
"""Python's scopes, read from the syntax tree: every identifier, where it is in the text, and which binding it means.

:func:`read` walks a module once with Python's own rules (the language reference, "Naming and binding"): a module,
each function and lambda, each class body and each comprehension is a scope; a name bound anywhere in a scope is local
to the whole of it unless the scope declares it ``global`` or ``nonlocal``; a name a scope does not bind is looked up
in the enclosing function scopes, never in a class body, and then in the module; a walrus in a comprehension binds in
the nearest scope that is not a comprehension; decorators, defaults, annotations, base classes and a comprehension's
first iterable belong to the enclosing scope.

Every identifier the text spells is an :class:`Occurrence` with its exact span (line, and character columns, worked
out from the parser's UTF-8 byte offsets, and checked against the text): names read and written, function and class
names, parameters, import bindings, ``except ... as`` names and the names a ``global`` or ``nonlocal`` statement lists.
A binding the text spells with no span the parser gives (a ``match`` capture, a type parameter) is an occurrence with
no span, so an edit that would need it refuses instead of guessing. :meth:`Scopes.resolve` says which scope's binding
an occurrence means (``None`` for a builtin or a name from elsewhere).
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

_NEWLINE = re.compile(r"\r\n|\r|\n")
_DEF = re.compile(r"(?:async[ \t]+)?(?:def|class)[ \t]+")
FUNCTION_LIKE = frozenset({"module", "function", "lambda", "comprehension"})


class Text:
    """The source's lines, split as Python's tokenizer splits them, and offsets from (line, UTF-8 byte column)."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.starts = [0] + [m.end() for m in _NEWLINE.finditer(source)]

    def line(self, lineno: int) -> str:
        start = self.starts[lineno - 1]
        end = self.starts[lineno] if lineno < len(self.starts) else len(self.source)
        return self.source[start:end].rstrip("\r\n")

    def offset(self, lineno: int, byte_col: int) -> int:
        """The character offset in the whole source of a parser position."""
        text = self.line(lineno)
        return self.starts[lineno - 1] + len(text.encode("utf-8")[:byte_col].decode("utf-8", "replace"))

    def column(self, offset: int) -> tuple[int, int]:
        """(line, character column) of an offset."""
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, offset - self.starts[lo]


@dataclass(eq=False)
class Scope:
    kind: str  # module | function | lambda | class | comprehension
    name: str
    node: ast.AST
    parent: Scope | None
    bindings: set[str] = field(default_factory=set)
    declared_global: set[str] = field(default_factory=set)
    declared_nonlocal: set[str] = field(default_factory=set)
    star_import: bool = False
    children: list[Scope] = field(default_factory=list)

    @property
    def line(self) -> int:
        return getattr(self.node, "lineno", 1)

    def describe(self) -> str:
        return "the module" if self.kind == "module" else f"{self.kind} {self.name} at line {self.line}"

    def within(self, other: Scope) -> bool:
        """Whether this scope is ``other`` or nested anywhere inside it."""
        found: Scope | None = self
        while found is not None:
            if found is other:
                return True
            found = found.parent
        return False


@dataclass(eq=False)
class Occurrence:
    name: str
    scope: Scope
    kind: str  # load | store | del | def | class | param | import | global | nonlocal | except | capture
    node: ast.AST
    start: int | None = None  # character offsets in the source, or None when the text gives no span
    end: int | None = None
    #: For an import binding: "asname" (the span is the alias's own name after "as"), "bare" (``import x`` or
    #: ``from m import x``: renaming adds "as"), "dotted" (``import a.b``: the binding is ``a``, which no span renames).
    import_form: str = ""


class Scopes:
    """Every scope and identifier occurrence in one module."""

    def __init__(self, tree: ast.Module, text: Text) -> None:
        self.tree, self.text = tree, text
        self.module = Scope("module", "", tree, None)
        self.occurrences: list[Occurrence] = []
        self.unsupported: list[tuple[str, int]] = []
        self._visit_body(tree.body, self.module)

    # --- building ---------------------------------------------------------------------------------------------------

    def _add(self, name: str, scope: Scope, kind: str, node: ast.AST, start: int | None = None,
             end: int | None = None, import_form: str = "") -> None:
        self.occurrences.append(Occurrence(name, scope, kind, node, start, end, import_form))

    def _span(self, node: ast.AST, name: str) -> tuple[int, int] | tuple[None, None]:
        start = self.text.offset(node.lineno, node.col_offset)
        end = start + len(name)
        return (start, end) if self.text.source[start:end] == name else (None, None)

    def _binding_scope(self, scope: Scope) -> Scope:
        """Where a walrus binds: the nearest scope that is not a comprehension."""
        while scope.kind == "comprehension" and scope.parent is not None:
            scope = scope.parent
        return scope

    def _child(self, kind: str, name: str, node: ast.AST, parent: Scope) -> Scope:
        child = Scope(kind, name, node, parent)
        parent.children.append(child)
        return child

    def _visit_body(self, body: list[ast.stmt], scope: Scope) -> None:
        for stmt in body:
            self._visit(stmt, scope)

    def _visit_args(self, args: ast.arguments, outer: Scope, inner: Scope) -> None:
        for default in [*args.defaults, *(d for d in args.kw_defaults if d is not None)]:
            self._visit(default, outer)
        for arg in [*args.posonlyargs, *args.args, *([args.vararg] if args.vararg else []), *args.kwonlyargs,
                    *([args.kwarg] if args.kwarg else [])]:
            if arg.annotation is not None:
                self._visit(arg.annotation, outer)
            inner.bindings.add(arg.arg)
            self._add(arg.arg, inner, "param", arg, *self._span(arg, arg.arg))

    def _def_name_span(self, node: ast.AST, name: str) -> tuple[int, int] | tuple[None, None]:
        at = self.text.offset(node.lineno, node.col_offset)
        found = _DEF.match(self.text.source, at)
        if not found:
            return None, None
        start = found.end()
        return (start, start + len(name)) if self.text.source[start:start + len(name)] == name else (None, None)

    def _visit(self, node: ast.AST, scope: Scope) -> None:  # noqa: C901 - one case per syntax form
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                self._visit(decorator, scope)
            if getattr(node, "type_params", None):
                self.unsupported.append(("type parameters", node.lineno))
            inner = self._child("function", node.name, node, scope)
            self._visit_args(node.args, scope, inner)
            if node.returns is not None:
                self._visit(node.returns, scope)
            scope.bindings.add(node.name)
            self._add(node.name, scope, "def", node, *self._def_name_span(node, node.name))
            self._visit_body(node.body, inner)
        elif isinstance(node, ast.ClassDef):
            for part in [*node.decorator_list, *node.bases, *(k.value for k in node.keywords)]:
                self._visit(part, scope)
            if getattr(node, "type_params", None):
                self.unsupported.append(("type parameters", node.lineno))
            inner = self._child("class", node.name, node, scope)
            scope.bindings.add(node.name)
            self._add(node.name, scope, "class", node, *self._def_name_span(node, node.name))
            self._visit_body(node.body, inner)
        elif isinstance(node, ast.Lambda):
            inner = self._child("lambda", "lambda", node, scope)
            self._visit_args(node.args, scope, inner)
            self._visit(node.body, inner)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            first, *rest = node.generators
            self._visit(first.iter, scope)
            inner = self._child("comprehension", type(node).__name__, node, scope)
            for index, generator in enumerate(node.generators):
                if index:
                    self._visit(generator.iter, inner)
                self._visit(generator.target, inner)
                for condition in generator.ifs:
                    self._visit(condition, inner)
            for part in ((node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)):
                self._visit(part, inner)
        elif isinstance(node, ast.Name):
            kind = {ast.Load: "load", ast.Store: "store", ast.Del: "del"}[type(node.ctx)]
            if kind != "load":
                scope.bindings.add(node.id)
            self._add(node.id, scope, kind, node, *self._span(node, node.id))
        elif isinstance(node, ast.NamedExpr):
            self._visit(node.value, scope)
            target = self._binding_scope(scope)
            target.bindings.add(node.target.id)
            self._add(node.target.id, target, "store", node.target, *self._span(node.target, node.target.id))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                self._visit_alias(alias, node, scope)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            kind = "global" if isinstance(node, ast.Global) else "nonlocal"
            (scope.declared_global if kind == "global" else scope.declared_nonlocal).update(node.names)
            start = self.text.offset(node.lineno, node.col_offset)
            end = self.text.offset(node.end_lineno, node.end_col_offset)
            for name in node.names:
                found = re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", self.text.source[start + len(kind):end])
                if found:
                    at = start + len(kind) + found.start()
                    self._add(name, scope, kind, node, at, at + len(name))
                else:
                    self._add(name, scope, kind, node)
        elif isinstance(node, ast.ExceptHandler):
            if node.type is not None:
                self._visit(node.type, scope)
            if node.name:
                scope.bindings.add(node.name)
                start = self.text.offset(node.lineno, node.col_offset)
                body = self.text.offset(node.body[0].lineno, node.body[0].col_offset)
                header = self.text.source[start:body]
                found = list(re.finditer(rf"\bas[ \t]+({re.escape(node.name)})(?![\w])", header))
                if found:
                    at = start + found[-1].start(1)
                    self._add(node.name, scope, "except", node, at, at + len(node.name))
                else:
                    self._add(node.name, scope, "except", node)
            self._visit_body(node.body, scope)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            if node.name:
                scope.bindings.add(node.name)
                self._add(node.name, scope, "capture", node)
            if isinstance(node, ast.MatchAs) and node.pattern is not None:
                self._visit(node.pattern, scope)
        elif isinstance(node, ast.MatchMapping):
            for part in [*node.keys, *node.patterns]:
                self._visit(part, scope)
            if node.rest:
                scope.bindings.add(node.rest)
                self._add(node.rest, scope, "capture", node)
        elif getattr(ast, "TypeAlias", None) is not None and isinstance(node, ast.TypeAlias):
            self.unsupported.append(("a type alias statement", node.lineno))
            self._visit(node.name, scope)
            self._visit(node.value, scope)
        else:
            for child in ast.iter_child_nodes(node):
                self._visit(child, scope)

    def _visit_alias(self, alias: ast.alias, node: ast.AST, scope: Scope) -> None:
        if alias.name == "*":
            scope.star_import = True
            return
        if alias.asname:
            bound = alias.asname
            start = self.text.offset(alias.lineno, alias.col_offset)
            end = self.text.offset(alias.end_lineno, alias.end_col_offset)
            text = self.text.source[start:end]
            found = re.search(rf"\bas[ \t]+({re.escape(bound)})$", text)
            scope.bindings.add(bound)
            if found:
                self._add(bound, scope, "import", alias, start + found.start(1), start + found.end(1), "asname")
            else:
                self._add(bound, scope, "import", alias)
            return
        bound = alias.name.split(".", 1)[0]
        scope.bindings.add(bound)
        start = self.text.offset(alias.lineno, alias.col_offset)
        if "." in alias.name:
            self._add(bound, scope, "import", alias, import_form="dotted")
        elif self.text.source[start:start + len(bound)] == bound:
            self._add(bound, scope, "import", alias, start, start + len(bound), "bare")
        else:
            self._add(bound, scope, "import", alias)

    # --- resolving --------------------------------------------------------------------------------------------------

    def resolve(self, occurrence: Occurrence) -> Scope | None:
        """The scope whose binding ``occurrence`` means, or ``None`` (a builtin, or a name from elsewhere)."""
        return self.lookup(occurrence.scope, occurrence.name)

    def lookup(self, scope: Scope, name: str) -> Scope | None:
        """The scope whose binding of ``name`` a use in ``scope`` means."""
        if name in scope.declared_global:
            return self.module if name in self.module.bindings else None
        if name in scope.declared_nonlocal:
            return self._enclosing_function(scope.parent, name)
        if name in scope.bindings:
            return scope
        return self._enclosing_function(scope.parent, name)

    def _enclosing_function(self, scope: Scope | None, name: str) -> Scope | None:
        while scope is not None:
            if scope.kind != "class":
                if name in scope.declared_global:
                    return self.module if name in self.module.bindings else None
                if name in scope.declared_nonlocal:
                    return self._enclosing_function(scope.parent, name)
                if name in scope.bindings:
                    return scope
            scope = scope.parent
        return None

    def scopes(self) -> list[Scope]:
        found, todo = [], [self.module]
        while todo:
            scope = todo.pop()
            found.append(scope)
            todo.extend(scope.children)
        return found


def read(source: str) -> Scopes:
    """The scopes of ``source``; raises ``SyntaxError`` or ``ValueError`` when it does not parse."""
    return Scopes(ast.parse(source), Text(source))


__all__ = ["FUNCTION_LIKE", "Occurrence", "Scope", "Scopes", "Text", "read"]
