# SPDX-License-Identifier: Apache-2.0
"""Bounded source-only reading of ordinary import-time collection effects.

This is advisory analysis within the collection child's same-process caveat.
Nothing here imports, evaluates or calls repository objects.
"""
from __future__ import annotations

import ast
import fnmatch
import sys
import sysconfig
from collections import deque
from importlib.util import resolve_name
from pathlib import Path, PurePosixPath

from g6.core import component_process


MAX_CALL_DEPTH = 24
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
# These operations build values; the binding must resolve to the builtin first.
_VALUE_BUILTINS = frozenset("bool bytes complex dict float frozenset int len list range set str tuple".split())
_LITERALS = {ast.List: "?list", ast.Dict: "?dict", ast.Tuple: "?tuple", ast.Set: "?set"}
_LITERALS.update({ast.ListComp: "?list", ast.SetComp: "?set", ast.DictComp: "?dict", ast.GeneratorExp: "?generator"})

# Exact external callees and their argument policy. No package-wide fallback:
# an unlisted dependency may call a callback, load a module or consume a protocol.
MODELLED_EXTERNAL_CALLS = {
    **dict.fromkeys(("builtins." + name for name in _VALUE_BUILTINS),
                    "plain: conversion/iteration only over modeled builtin values"),
    "builtins.sorted": "sorted: plain iterable and a modeled builtin key only",
    "builtins.type": "type: one-argument type inspection runs no object hook",
    "builtins.object": "allocate: bare builtin object allocation runs no caller code",
    "builtins.object.__new__": "allocate: native allocation; audit the allocated class's later protocols",
    "logging.getLogger": "logger: literal/module name only; no caller-supplied code",
    "contextlib.nullcontext": "hold: stores its argument without invoking it",
    "dataclasses.dataclass": "dataclass: class creation/defaults and generated hooks followed by _class",
    "dataclasses.field": "field: stores defaults; forwards default set_name even outside dataclasses; factories followed by _class",
    "functools.wraps": "wraps: resolvable function metadata only, checked by _decoration",
    "pytest.fixture": "fixture: defer body; autouse and declarations checked by _scan/passive",
    "builtins.staticmethod": "hold: stores function without invoking it",
    "builtins.classmethod": "hold: stores function without invoking it",
    "builtins.property": "hold: stores accessors; descriptor access is unavailable",
    **dict.fromkeys(("os.path." + name for name in ("dirname", "join", "abspath", "basename", "exists")),
                    "constant: path operations on literal strings run no repository path protocol"),
    "re.compile": "constant: literal pattern/flags run no repository conversion or callback",
    "typing.TypeVar": "constant: literal name and installed type constraints run no repository code",
    "collections.namedtuple": "constant: literal names/fields/defaults contain no repository callbacks",
    "typing.NamedTuple": "constant: literal fields with installed types contain no repository annotations",
    **dict.fromkeys(("datetime." + name for name in ("datetime", "date", "timedelta")),
                    "constant: literal constructor arguments run no repository numeric hooks"),
}
_PLAIN_EXTERNAL_VALUES = frozenset(("sys.platform", "sys.version", "sys.version_info", "sys.maxsize", "os.name",
    *("re." + name for name in ("ASCII", "IGNORECASE", "LOCALE", "MULTILINE", "DOTALL", "UNICODE", "VERBOSE",
                                "A", "I", "L", "M", "S", "U", "X", "NOFLAG"))))
_INSTALLED_TYPES = frozenset("builtins." + name for name in (*_VALUE_BUILTINS, "object", "type"))
_TYPING_GENERICS = frozenset("typing." + name for name in (
    "Optional", "List", "Dict", "Union", "Tuple", "Set", "FrozenSet", "Sequence", "Mapping", "Iterable",
    "Iterator", "Callable", "Type", "ClassVar", "Final", "Literal", "Annotated"))
# These bodies have explicit dispatch below. Every other repository protocol is
# unavailable, even if a particular spelling happens to have an inert body.
MODELLED_CLASS_PROTOCOLS = frozenset((
    "__init__", "__new__", "__init_subclass__", "__set_name__", "__post_init__",
    "__setattr__", "__getattribute__", "__getattr__", "__prepare__",
))
# The existing external-base set, with explicit native/generated/source policies.
# Source policies follow installed lifecycle bodies, including receiver dispatch.
MODELLED_EXTERNAL_BASES = {
    "builtins.object": "native: allocation and subclass creation run no caller code",
    "builtins.type": "native: repository metaclasses are followed separately",
    "typing.NamedTuple": "fields: generated record/default protocols are followed by _class_fields",
    "unittest.TestCase": "source: read installed lifecycle bodies on the repository receiver",
}

# Executable admission table: absence means unavailable, including future AST
# kinds. Conditional handlers below constrain the operations, not just the kind.
MODELLED_OPERATIONS = {
    **dict.fromkeys((ast.Expr, ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.Return),
                    "Follow evaluated values/targets; postponed annotations are names/strings, not executed."),
    **dict.fromkeys((ast.Pass, ast.Break, ast.Continue, ast.Load, ast.Store, ast.Del),
                    "Syntax or control transfer; no repository call."),
    ast.Constant: "Literal value; no repository call.",
    ast.Name: "Resolve all binding candidates; reads alone do not call them.",
    **dict.fromkeys((ast.List, ast.Tuple, ast.Set, ast.Dict),
                    "Build values; elements are followed and hash operands must be plain."),
    **dict.fromkeys((ast.FunctionDef, ast.AsyncFunctionDef),
                    "Follow defaults and decorator results; follow annotations only without the future import; bind modeled defaults on calls."),
    ast.ClassDef: "Follow bases/metaclasses and hooks on real receivers; audit implicit protocols, native new identity and inherited builder fields. Admitted installed bases use native/generated policies or source-read lifecycle routes with repository dispatch followed.",
    **dict.fromkeys((ast.arguments, ast.arg, ast.keyword, ast.alias),
                    "Syntax wrapper; evaluated children are followed."),
    ast.Lambda: "Defer body; follow defaults and called/factory/decorator bodies in lexical scopes that skip classes.",
    ast.Call: "Follow repository callees with arguments/defaults; external calls only by MODELLED_EXTERNAL_CALLS, environment/list policies and owner mark rules.",
    ast.Attribute: "Follow module missing-name hooks and class/instance method lookup on checked receivers/metaclasses; unknown descriptors/results unavailable.",
    ast.Subscript: "Plain builtin values, installed typing annotations with installed arguments, or classified environment writes; repository hooks unavailable.",
    ast.Import: "Repository modules are read separately; namespace portions must be unique and inside roots.",
    ast.ImportFrom: "Resolve relative levels; refuse stars, lazy module lookup and ambiguous namespace portions.",
    **dict.fromkeys((ast.Global, ast.Nonlocal),
                    "Redirect assignments to every candidate in the named lexical scope."),
    ast.If: "Follow both branches and a plain predicate; omit the guarded main body.",
    **dict.fromkeys((ast.For, ast.While), "Plain iteration/predicate; re-read until bindings and mutated-container sets stop changing."),
    **dict.fromkeys((ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension),
                    "Plain iterables and operands; comprehension names skip enclosing classes; re-read mutations."),
    ast.With: "Installed contextlib.nullcontext; follow body/arguments and require plain values for tuple/list targets.",
    ast.withitem: "Follow the modeled context expression and binding target.",
    **dict.fromkeys((ast.Try, ast.ExceptHandler), "Follow every body and exception expression."),
    ast.Match: "Plain subject and literal/name patterns only; follow every case.",
    **dict.fromkeys((ast.match_case, ast.MatchValue, ast.MatchSingleton, ast.MatchAs, ast.MatchStar, ast.MatchOr),
                    "Literal or binding pattern; evaluated children are followed."),
    **dict.fromkeys((ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.AugAssign),
                    "Only plain operands; repository operator and truth hooks unavailable."),
    **dict.fromkeys((ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
                     ast.LShift, ast.RShift, ast.BitOr, ast.BitXor, ast.BitAnd, ast.Invert,
                     ast.Not, ast.UAdd, ast.USub, ast.And, ast.Or, ast.Eq, ast.NotEq,
                     ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn),
                    "Operator token; enclosing operation checks operands."),
}


def module_names(file, roots=(), *, full=False):
    from g6.workforce.repo_tests import _import_names

    return _import_names(file, (".",) if full else (*roots, ".", "src"))


def _candidates(binding):
    return binding if isinstance(binding, frozenset) else (binding,)


def _merge(bindings):
    held = set()
    for binding in bindings:
        component_process.checkpoint()
        held.update(_candidates(binding))
    candidates = frozenset(held)
    return next(iter(candidates)) if len(candidates) == 1 else candidates or "?unresolved"


def _attributes(binding, attributes):
    """Append identifier parts, keeping a local file identity opaque."""
    if isinstance(binding, frozenset):
        return _merge(_attributes(candidate, attributes) for candidate in binding)
    if isinstance(binding, tuple):
        kind, file, line, held = binding
        return kind, file, line, (*held, *attributes)
    return ".".join((binding, *attributes))


def _internal(binding):
    if isinstance(binding, frozenset):
        return all(_internal(candidate) for candidate in binding)
    return isinstance(binding, tuple) or binding.startswith("?")


def _external(binding):
    if isinstance(binding, frozenset):
        return any(_external(candidate) for candidate in binding)
    return (isinstance(binding, str) and binding.startswith("?external") or
            isinstance(binding, tuple) and binding[0] == "?external-call")


def _owned_container(binding, file, kinds=("?list", "?dict")):
    if isinstance(binding, frozenset):
        return all(_owned_container(candidate, file, kinds) for candidate in binding)
    return isinstance(binding, tuple) and binding[0] in kinds and binding[1] == file


def _nodes(body):
    pending = deque(body)
    while pending:
        component_process.checkpoint()
        node = pending.popleft()
        yield node
        if not isinstance(node, (*_FUNCTIONS, ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(node))


class _Scope:
    def __init__(self, reader, file, body, parent=None, arguments=(), instance=None, *, class_body=False, values=()):
        self.reader, self.file, self.parent = reader, file, parent
        self.class_body = class_body
        self.bindings = {name: "?argument" for name in arguments}
        self.bindings.update(zip(arguments, values))
        if instance:
            self.bindings[instance] = "?instance"
        self.nodes = list(_nodes(body))
        self.globals = {name for node in self.nodes if isinstance(node, ast.Global) for name in node.names}
        self.nonlocals = {name for node in self.nodes if isinstance(node, ast.Nonlocal) for name in node.names}
        slots = {name: [value] for name, value in self.bindings.items()}

        def bind(name, value):
            held = slots.setdefault(name, [])
            held.append(value)
            self.bindings[name] = _merge(held)
            return len(held) - 1

        assignments = []

        def assignment(target, value):
            if isinstance(target, ast.Name):
                assignments.append((target.id, bind(target.id, "?unresolved"), value))
            elif isinstance(target, (ast.Tuple, ast.List)):
                if isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
                    for item, item_value in zip(target.elts, value.elts):
                        assignment(item, item_value)
                else:
                    for part in ast.walk(target):
                        if isinstance(part, ast.Name):
                            bind(part.id, "?unresolved")

        for node in self.nodes:
            if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
                key = ("@", file, (node.lineno, self), ())
                bind(node.name, key)
                reader.definitions[key] = (self, node)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    bind(alias.asname or alias.name.split(".")[0], alias.name if alias.asname else alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                module = reader.import_module(file, node)
                for alias in node.names:
                    binding = ".".join(part for part in (module, alias.name) if part)
                    if node.level and not module:
                        root_files = reader.paths.get("", ())
                        binding = ("?module", root_files[0], 0, (alias.name,)) if len(root_files) == 1 else "?unresolved"
                    bind(alias.asname or alias.name, binding)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if node.value is not None:
                    for target in targets:
                        assignment(target, node.value)
            elif isinstance(node, ast.NamedExpr):
                assignment(node.target, node.value)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                bind(node.target.id, "?unresolved")
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.withitem)):
                target = node.optional_vars if isinstance(node, ast.withitem) else node.target
                for part in ast.walk(target) if target is not None else ():
                    if isinstance(part, ast.Name):
                        bind(part.id, "?unresolved")
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bind(node.name, "?unresolved")
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                bind(node.name, "?unresolved")
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bind(node.rest, "?unresolved")
        # Resolve aliases using bindings, not the spelling of a callable. A
        # bounded fixed point leaves cycles and unavailable assignments unknown.
        for _ in range(MAX_CALL_DEPTH):
            changed = False
            for name, slot, value in assignments:
                component_process.checkpoint()
                if type(value) in _LITERALS:
                    resolved = self.literal(value)
                elif isinstance(value, (ast.Name, ast.Attribute, ast.Call, ast.Lambda)):
                    resolved = self.resolve(value)
                elif isinstance(value, ast.Constant):
                    resolved = "?value"
                else:
                    resolved = "?unresolved"
                if slots[name][slot] != resolved:
                    slots[name][slot], changed = resolved, True
                    self.bindings[name] = _merge(slots[name])
            if not changed:
                break
        for name in self.globals | self.nonlocals:
            target = self.redirect(name)
            if target is not None and name in self.bindings:
                value = _merge((target.bindings.get(name, self.bindings[name]), self.bindings[name]))
                if target.bindings.get(name) != value:
                    target.bindings[name] = value
                    reader.revision += 1

    def redirect(self, name):
        if not self.parent:
            return None
        if name in self.globals:
            return self.reader.scopes.get(self.file)
        if name in self.nonlocals:
            parent = self.parent
            while parent.parent:
                if not parent.class_body and name in parent.bindings:
                    return parent
                parent = parent.parent
        return None

    def assign(self, target, value):
        """Refresh aliases at an executed assignment after a followed installer."""
        if value is None:
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
                for item, held in zip(target.elts, value.elts):
                    self.assign(item, held)
            return
        if not isinstance(target, ast.Name):
            return
        owner = self.redirect(target.id) if self.parent and target.id in self.globals | self.nonlocals else self
        if owner is None:
            return
        if type(value) in _LITERALS:
            binding = self.literal(value)
        elif isinstance(value, (ast.Name, ast.Attribute, ast.Call, ast.Lambda)):
            binding = self.resolve(value)
        elif isinstance(value, ast.Constant):
            binding = "?value"
        else:
            binding = "?unresolved"
        merged = _merge((owner.bindings.get(target.id, binding), binding))
        if owner.bindings.get(target.id) != merged:
            owner.bindings[target.id] = merged
            self.reader.revision += 1

    def literal(self, node):
        key = (_LITERALS[type(node)], self.file, (node.lineno, node.col_offset, self), ())
        self.reader.literal_values[key] = (self, node)
        return key

    def resolve(self, node):
        if isinstance(node, ast.Name):
            if node.id in self.globals | self.nonlocals and self.parent:
                target = self.redirect(node.id)
                return target.resolve(node) if target else "?unresolved"
            if node.id in self.bindings:
                return self.bindings[node.id]
            if self.parent:
                return self.parent.resolve(node)
            return f"builtins.{node.id}" if node.id in _VALUE_BUILTINS | {
                "object", "type", "sorted", "staticmethod", "classmethod", "setattr", "delattr", "property"} else "?unresolved"
        if isinstance(node, ast.Lambda):
            parent = self
            while parent.class_body:
                parent = parent.parent
            key = ("?lambda", self.file, (node.lineno, node.col_offset, parent), ())
            self.reader.lambdas[key] = (parent, node)
            return key
        if isinstance(node, ast.Attribute):
            return _attributes(self.resolve(node.value), (node.attr,))
        if isinstance(node, ast.Call):
            external = False
            for part in ast.walk(node):
                component_process.checkpoint()
                if isinstance(part, ast.Name):
                    name = self.resolve(part)
                    if _external(name) or not all(_internal(candidate) or candidate.startswith("builtins.")
                                                  for candidate in _candidates(name)):
                        external = True
            key = ("?external-call" if external else "?call", self.file, (node.lineno, node.col_offset, self), ())
            self.reader.calls[key] = (self, node)
            return key
        if type(node) in _LITERALS:
            return self.literal(node)
        if isinstance(node, ast.Constant):
            return "?value"
        return "?unresolved"


class CollectionEffects:
    @classmethod
    def from_trees(cls, trees, *, roots=(), modules=(), files=(), collection_files=()):
        """Read a mapping of opaque file identities to parsed source trees."""
        return cls(trees, roots=roots, modules=modules, files=files, collection_files=collection_files)

    def __init__(self, trees, *, roots=(), modules=(), files=(), collection_files=()):
        from g6.workforce.repo_tests import _import_names

        self.trees, self.modules = trees, set(modules)
        self.collection_files = set(collection_files) | {file for file in trees if
            PurePosixPath(file).name == "conftest.py" or any(fnmatch.fnmatch(PurePosixPath(file).name, pattern)
                                                         for pattern in ("test_*.py", "*_test.py"))}
        self.paths, self.definitions, self.scopes = {}, {}, {}
        self.revision = 0
        self.roots = (*roots, ".", "src")
        self.namespace_portions = {}
        self.external_roots = any(PurePosixPath(root).is_absolute() or ".." in PurePosixPath(root).parts or
                                  ":" in root or "\\" in root for root in roots)
        files = set(trees) | set(files)
        self.names = {file: module_names(file, roots) for file in files}
        self.packages = {PurePosixPath(file).parent for file in files if PurePosixPath(file).name == "__init__.py"}
        self.calls = {}
        self.literal_values, self.dirty_containers = {}, set()
        self.decorating, self.returning, self.lambdas = set(), set(), {}
        self.function_scopes, self.active, self.protocol_active = {}, set(), set()
        self.module_nodes, self.mark_aliases = {}, {}
        self.installed_definitions = {}
        self.postponed_annotations = {file for file, tree in trees.items() if any(
            isinstance(node, ast.ImportFrom) and node.module == "__future__" and
            any(alias.name == "annotations" for alias in node.names) for node in tree.body)}
        for file in files:
            component_process.checkpoint()
            path = PurePosixPath(file)
            for root in self.roots:
                component_process.checkpoint()
                for module in _import_names(file, (root,)):
                    parts = module.split(".")
                    if path.name != "__init__.py":
                        parts = parts[:-1]
                    portion = path.parent
                    for end in range(len(parts), 0, -1):
                        module = ".".join(parts[:end])
                        self.namespace_portions.setdefault(module, set()).add(portion)
                        portion = portion.parent
            for name in self.names[file]:
                self.paths.setdefault(name, []).append(file)
                self.modules.add(name.split(".")[0])
            if PurePosixPath(file) == PurePosixPath("__init__.py"):
                self.paths.setdefault("", []).append(file)
        for file, tree in trees.items():
            self.scopes[file] = _Scope(self, file, tree.body)

    def import_module(self, file, node):
        if not node.level:
            return node.module or ""
        path, depth = PurePosixPath(file), 0
        directory = path.parent
        while directory in self.packages:
            component_process.checkpoint()
            depth += 1
            if directory == PurePosixPath("."):
                break
            directory = directory.parent
        if not depth:
            packages = {name.rpartition(".")[0] for name in self.names[file] if "." in name}
            if not packages or any(not self._namespace(package) for package in packages):
                return "?unresolved"
            resolved = set()
            for package in packages:
                try:
                    name = resolve_name("." * node.level + (node.module or ""), package)
                except (ImportError, ValueError):
                    return "?unresolved"
                if not self._import_path(name):
                    return "?unresolved"
                resolved.add(name)
            return self._same_import(resolved)
        root_package = directory == PurePosixPath(".") and directory in self.packages
        names = module_names(file, full=True) if root_package else self.names[file]
        packages = set()
        for module in names or ({""} if root_package and path.name == "__init__.py" else set()):
            parts = module.split(".") if module else []
            if path.name != "__init__.py":
                parts = parts[:-1]
            if root_package:
                # A private anchor represents the root's package level; it is
                # removed before lookup, never exposed as a repository name.
                parts.insert(0, "__g6_root__")
            if len(parts) >= depth:
                packages.add(".".join(parts))
        resolved = set()
        for package in packages:
            try:
                name = resolve_name("." * node.level + (node.module or ""), package)
            except (ImportError, ValueError):
                return "?unresolved"
            if root_package:
                name = "" if name == "__g6_root__" else name.removeprefix("__g6_root__.")
            resolved.add(name)
        if not resolved:
            return "?unresolved"
        if any(not self._import_path(name) for name in resolved):
            return "?unresolved"
        return self._same_import(resolved)

    def _namespace(self, name):
        portions = self.namespace_portions.get(name, ())
        return not self.external_roots and len(portions) == 1

    def _import_path(self, name):
        parts = name.split(".") if name else []
        for end in range(1, len(parts) + 1):
            prefix = ".".join(parts[:end])
            if prefix in self.paths:
                if len(self.paths[prefix]) != 1:
                    return False
            elif not self._namespace(prefix):
                return False
        return len(self.paths.get(name, ())) == 1 or self._namespace(name)

    def _same_import(self, resolved):
        if not resolved:
            return "?unresolved"
        if len(resolved) > 1:
            identities = [self.paths.get(name, ()) or tuple(self.namespace_portions.get(name, ())) for name in resolved]
            if any(len(files) != 1 for files in identities) or any(files != identities[0] for files in identities):
                return "?unresolved"
        return sorted(resolved)[0]

    def follow(self, name, seen=()):
        component_process.checkpoint()
        if isinstance(name, frozenset):
            return _merge(self.follow(candidate, seen) for candidate in name)
        if name in seen or len(seen) >= MAX_CALL_DEPTH:
            return "?unresolved"
        if name in self.definitions:
            if name[0] == "?original":
                return name
            return self._decorated(name, len(seen))
        if isinstance(name, tuple):
            kind, file, line, attributes = name
            if attributes and (kind, file, line, ()) in self.calls:
                call_scope, expression = self.calls[(kind, file, line, ())]
                members = []
                for constructor in _candidates(self.follow(call_scope.resolve(expression.func), (*seen, name))):
                    if constructor not in self.definitions or not isinstance(self.definitions[constructor][1], ast.ClassDef):
                        return "?unresolved"
                    owner, definition = self.definitions[constructor]
                    members.append(self._members(self._function_scope(owner, definition), attributes[0]))
                return self.follow(_attributes(_merge(members), attributes[1:]), (*seen, name))
            if kind == "?module":
                scope = self.scopes.get(file)
                if scope is None or not attributes:
                    return "?unresolved"
                reference = (kind, file, line, (attributes[0],))
                bindings = [candidate for candidate in _candidates(scope.bindings.get(attributes[0], "?unresolved"))
                            if candidate != reference and candidate != "?unresolved"]
                if attributes[0] in self.paths:
                    bindings.append(attributes[0])
                return self.follow(_attributes(_merge(bindings), attributes[1:]), (*seen, name))
            if kind not in ("@", "?original"):
                return name
            definition = self.definitions.get((kind, file, line, ()))
            if not definition or not attributes or not isinstance(definition[1], ast.ClassDef):
                return "?unresolved"
            parent, node = definition
            scope = self._function_scope(parent, node)
            resolved = self._members(scope, attributes[0])
            return self.follow(_attributes(resolved, attributes[1:]), (*seen, name))
        if name.startswith("?"):
            return name
        parts = name.split(".")
        for end in range(len(parts), 0, -1):
            files = self.paths.get(".".join(parts[:end]), ())
            if not files:
                continue
            if len(files) != 1:
                return "?unresolved"
            if end == len(parts):
                return name
            scope = self.scopes.get(files[0])
            if scope is None:
                return "?unresolved"
            resolved = scope.bindings.get(parts[end], "?unresolved")
            if parts[end] not in scope.bindings and "__getattr__" in scope.bindings:
                resolved = self._returned(self.follow(scope.bindings["__getattr__"], (*seen, name)),
                                          ("?value",), len(seen) + 1)
            return self.follow(_attributes(resolved, parts[end + 1:]), (*seen, name))
        return name

    def _environment(self, node, scope):
        """None means another object; bool is the result for an environ write."""
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            owners = _candidates(self.follow(scope.resolve(node.value)))
            if "os.environ" in owners:
                return (all(owner == "os.environ" for owner in owners) and
                        isinstance(node.ctx, ast.Store) and self._environment_key(node.slice))
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            return None
        owners = _candidates(self.follow(scope.resolve(node.func.value)))
        if "os.environ" not in owners:
            return None
        if any(owner != "os.environ" for owner in owners):
            return False
        if node.func.attr in ("get", "copy", "keys", "values", "items", "__getitem__", "__contains__", "__iter__", "__len__"):
            return True
        if node.func.attr == "setdefault":
            return bool(node.args and len(node.args) <= 2 and not node.keywords and self._environment_key(node.args[0]))
        if node.func.attr != "update" or len(node.args) > 1:
            return False
        if any(keyword.arg is None or not self._environment_key(ast.Constant(keyword.arg)) for keyword in node.keywords):
            return False
        return not node.args or (isinstance(node.args[0], ast.Dict) and all(
            self._environment_key(key) for key in node.args[0].keys))

    @staticmethod
    def _environment_key(node):
        from g6.workforce.repo_tests import _runner_name

        return isinstance(node, ast.Constant) and isinstance(node.value, str) and not _runner_name(node.value)

    def _call(self, function, scope, depth, strict, *, call=None):
        if not isinstance(function, (ast.Name, ast.Attribute)):
            return False
        if not self._callable_lookup(function, scope, depth):
            return False
        name = self.follow(scope.resolve(function))
        return all(self._call_binding(candidate, function, scope, depth, strict, call=call)
                   for candidate in _candidates(name))

    def _call_binding(self, name, function, scope, depth, strict, *, call=None):
        # Preserve the owner's same-process exceptions; do not tighten the gate
        # against repository code deliberately shadowing these bindings.
        if (isinstance(name, str) and name.startswith("pytest.mark.") or self._legacy_mark(function, scope.file) or
                isinstance(function, ast.Name) and function.id in ("staticmethod", "classmethod") and
                name not in ("builtins.staticmethod", "builtins.classmethod")):
            return True
        if name in self.definitions:
            receiver = self._bound_receiver(function, scope, name)
            return self._callee(name, depth + 1, call=call, caller=scope, receiver=receiver,
                                method=receiver is not None)
        if name in self.lambdas:
            parent, expression = self.lambdas[name]
            if call is not None and (call.args or call.keywords):
                return False
            inner = _Scope(self, parent.file, [expression.body], parent)
            return self._scan([expression.body], inner, depth + 1, strict=True)
        if _internal(name):
            if isinstance(function, ast.Attribute):
                owner = self.follow(scope.resolve(function.value))
                if _owned_container(owner, scope.file, ("?list",)) and function.attr in ("append", "extend", "insert"):
                    if call is None or call.keywords:
                        return False
                    if function.attr == "extend":
                        return len(call.args) == 1 and self._plain(call.args[0], scope)
                    if function.attr == "insert":
                        return len(call.args) == 2 and self._plain(call.args[0], scope)
                    return len(call.args) == 1
            return False
        if name.split(".")[0] in self.modules:
            return False
        policy = MODELLED_EXTERNAL_CALLS.get(name, "").partition(":")[0]
        if not policy:
            return False
        if call is None:
            return policy in ("plain", "type", "allocate", "hold", "dataclass", "fixture", "wraps")
        values = [*call.args, *(keyword.value for keyword in call.keywords)]
        if any(isinstance(arg, ast.Starred) for arg in call.args) or any(k.arg is None for k in call.keywords):
            return False
        if policy == "plain":
            return all(self._plain(value, scope) for value in values)
        if policy == "constant":
            return all(self._installed_argument(value, scope) for value in values)
        if policy == "sorted":
            if len(call.args) != 1 or not self._plain(call.args[0], scope):
                return False
            for keyword in call.keywords:
                if keyword.arg == "key":
                    if not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None):
                        keys = _candidates(self.follow(scope.resolve(keyword.value)))
                        if not all(key in {"builtins." + item for item in _VALUE_BUILTINS} for key in keys):
                            return False
                elif keyword.arg != "reverse" or not self._plain(keyword.value, scope):
                    return False
            return True
        if policy == "type":
            return len(call.args) == 1 and not call.keywords
        if policy == "allocate":
            if len(call.args) != (0 if name == "builtins.object" else 1) or call.keywords:
                return False
            if name == "builtins.object.__new__":
                for allocated in _candidates(self.follow(scope.resolve(call.args[0]))):
                    if allocated == "?instance":
                        owner = getattr(scope, "method_owner", None)
                        if owner is None or not self._protocols(owner, depth + 1):
                            return False
                    elif allocated in self.definitions and isinstance(self.definitions[allocated][1], ast.ClassDef):
                        owner, definition = self.definitions[allocated]
                        if not self._protocols(self._function_scope(owner, definition), depth + 1):
                            return False
                    elif allocated != "builtins.object":
                        return False
            return True
        if policy == "logger":
            return len(call.args) <= 1 and not call.keywords and all(
                isinstance(value, ast.Name) and value.id == "__name__" or self._plain(value, scope)
                for value in values)
        if policy == "hold":
            if name == "contextlib.nullcontext":
                return len(call.args) <= 1 and all(k.arg == "enter_result" for k in call.keywords)
            if name == "builtins.property":
                if len(call.args) > 4 or any(k.arg not in ("fget", "fset", "fdel", "doc") for k in call.keywords):
                    return False
                accessors = [*call.args[:3], *(k.value for k in call.keywords if k.arg != "doc")]
                docs = [*call.args[3:], *(k.value for k in call.keywords if k.arg == "doc")]
                return all(self._function_value(value, scope, allow_none=True) for value in accessors) and all(
                    self._plain(value, scope) for value in docs)
            return len(call.args) == 1 and not call.keywords and self._function_value(call.args[0], scope)
        if policy == "field":
            return not call.args and all(keyword.arg in {
                "default", "default_factory", "init", "repr", "hash", "compare", "metadata", "kw_only", "doc"}
                and (keyword.arg in ("default", "default_factory") or self._plain(keyword.value, scope))
                for keyword in call.keywords)
        # Decorator factories are handled only at their application site, where
        # both the input and the returned function/class are known.
        return False

    def _all_nodes(self, file):
        if file not in self.module_nodes:
            nodes, pending = [], deque([self.trees[file]])
            while pending:
                component_process.checkpoint()
                node = pending.popleft()
                nodes.append(node)
                children = ast.iter_child_nodes(node)
                if file in self.postponed_annotations and isinstance(node, (ast.arg, ast.AnnAssign)):
                    children = (child for child in children if child is not node.annotation)
                elif file in self.postponed_annotations and isinstance(node, _FUNCTIONS):
                    children = (child for child in children if child is not node.returns)
                pending.extend(children)
            self.module_nodes[file] = nodes
        return self.module_nodes[file]

    def _legacy_mark(self, function, file):
        # Keep the existing whole-file import-alias treatment of pytest marks,
        # including nested aliases. The owner explicitly leaves deception via
        # those aliases within the same-process caveat.
        if file not in self.mark_aliases:
            aliases = {}
            for node in self._all_nodes(file):
                if isinstance(node, ast.Import):
                    aliases.update({alias.asname or alias.name.split(".")[0]: alias.name for alias in node.names})
                elif isinstance(node, ast.ImportFrom):
                    aliases.update({alias.asname or alias.name: f"{node.module or ''}.{alias.name}" for alias in node.names})
            self.mark_aliases[file] = aliases
        name = ast.unparse(function)
        head = name.split(".")[0]
        return (self.mark_aliases[file].get(head, head) + name[len(head):]).startswith("pytest.mark.")

    def _callee(self, name, depth, *, method=False, call=None, caller=None, receiver=None, values=None):
        if depth > MAX_CALL_DEPTH or name in self.active:
            return False
        self.active.add(name)
        try:
            parent, node = self.definitions[name]
            if isinstance(node, ast.AsyncFunctionDef):
                result = False
            elif isinstance(node, ast.ClassDef):
                result = self._class(parent, node, depth, instantiate=True, call=call, caller=caller)
            else:
                if values is None:
                    values = self._arguments(parent, node, call, caller, bound=method) if call is not None else ()
                if values is None:
                    return False
                scope = self._function_scope(parent, node, method=method, values=values, receiver=receiver)
                result = self._scan(node.body, scope, depth, strict=True)
            return result
        finally:
            self.active.remove(name)

    def _arguments(self, parent, node, call, caller, *, bound=False):
        """Bind explicit arguments; unsupported unpacking stays unavailable."""
        if any(isinstance(arg, ast.Starred) for arg in call.args) or any(k.arg is None for k in call.keywords):
            return None
        positional = [*node.args.posonlyargs, *node.args.args][1 if bound else 0:]
        arguments = [*positional, *node.args.kwonlyargs]
        values = dict(zip((arg.arg for arg in positional), (self._value_binding(caller, arg) for arg in call.args)))
        if len(call.args) > len(positional) and node.args.vararg is None:
            return None
        for keyword in call.keywords:
            if keyword.arg in values or keyword.arg in {arg.arg for arg in node.args.posonlyargs}:
                return None
            if keyword.arg not in {arg.arg for arg in arguments}:
                if node.args.kwarg is None:
                    return None
                continue
            values[keyword.arg] = self._value_binding(caller, keyword.value)
        defaults = self._defaults(parent, node)
        return (("?instance",) if bound else ()) + tuple(
            values.get(arg.arg, defaults.get(arg.arg, "?argument")) for arg in arguments)

    def _value_binding(self, scope, value):
        binding = scope.resolve(value)
        return "?value" if binding == "?unresolved" and self._plain(value, scope) else binding

    def _defaults(self, parent, node):
        positional = [*node.args.posonlyargs, *node.args.args]
        pairs = [*zip(positional[len(positional) - len(node.args.defaults):], node.args.defaults),
                 *zip(node.args.kwonlyargs, node.args.kw_defaults)]
        return {arg.arg: self._value_binding(parent, value) for arg, value in pairs if value is not None}

    def _function_scope(self, parent, node, *, method=False, values=(), receiver=None):
        declaration = parent
        owner = parent if parent.class_body else None
        while parent.class_body:
            parent = parent.parent
        key = (parent, node.lineno, method, values, receiver)
        if key not in self.function_scopes:
            args = []
            if isinstance(node, _FUNCTIONS):
                args = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
                args.extend(arg.arg for arg in (node.args.vararg, node.args.kwarg) if arg)
                # Implicit class hooks receive protocol/constructor arguments
                # whose bindings are not modeled here. Do not mistake those
                # unknown supplied arguments for an omitted default.
                defaults = {} if method else self._defaults(owner or parent, node)
                values = (*values, *(defaults.get(arg, "?argument") for arg in args[len(values):]))
            self.function_scopes[key] = _Scope(self, parent.file, node.body, parent, args,
                                              args[0] if method and args else None,
                                              class_body=isinstance(node, ast.ClassDef), values=values)
            self.function_scopes[key].method_owner = (receiver or owner) if method else None
            self.function_scopes[key].class_receiver = method and isinstance(node, _FUNCTIONS) and (
                node.name == "__init_subclass__" or any("builtins.classmethod" in _candidates(self.follow(
                    (owner or parent).resolve(decorator))) for decorator in node.decorator_list))
            self.function_scopes[key].class_node = node if isinstance(node, ast.ClassDef) else None
            self.function_scopes[key].declaration_scope = declaration
        return self.function_scopes[key]

    def _returned(self, name, values, depth):
        """Follow a visible decorator call and determine its callable result."""
        if isinstance(name, frozenset):
            return _merge(self._returned(candidate, values, depth) for candidate in name)
        if depth > MAX_CALL_DEPTH or name in self.returning:
            return "?unresolved"
        if name in self.lambdas:
            parent, node = self.lambdas[name]
            args = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args)]
            scope = _Scope(self, parent.file, [node.body], parent, args, values=values)
            return self._return_value(node.body, scope, depth + 1) if self._scan(
                [node.body], scope, depth + 1, strict=True) else "?unresolved"
        if name not in self.definitions:
            return "?unresolved"
        parent, node = self.definitions[name]
        if not isinstance(node, ast.FunctionDef):
            return "?unresolved"
        self.returning.add(name)
        try:
            scope = self._function_scope(parent, node, values=values)
            if not self._scan(node.body, scope, depth + 1, strict=True):
                return "?unresolved"
            returns = [part for part in scope.nodes if isinstance(part, ast.Return)]
            if not returns:
                return "?value"
            # A fall-through path returns None. Only a final return (or two
            # complete returning branches) establishes a determined result.
            if not self._always_returns(node.body):
                return "?unresolved"
            return _merge(self._return_value(part.value, scope, depth + 1) for part in returns)
        finally:
            self.returning.remove(name)

    @staticmethod
    def _always_returns(body):
        if not body:
            return False
        last = body[-1]
        return isinstance(last, ast.Return) or isinstance(last, ast.If) and (
            CollectionEffects._always_returns(last.body) and CollectionEffects._always_returns(last.orelse))

    def _return_value(self, value, scope, depth):
        if depth > MAX_CALL_DEPTH:
            return "?unresolved"
        if value is None or isinstance(value, ast.Constant):
            return "?value"
        if type(value) in _LITERALS:
            return scope.literal(value)
        if isinstance(value, ast.Lambda):
            key = ("?lambda", scope.file, (value.lineno, value.col_offset, scope), ())
            self.lambdas[key] = (scope, value)
            return key
        if isinstance(value, ast.Call):
            if value.keywords or any(isinstance(arg, ast.Starred) for arg in value.args):
                return "?unresolved"
            return self._returned(self.follow(scope.resolve(value.func)),
                                  tuple(scope.resolve(arg) for arg in value.args), depth + 1)
        if isinstance(value, (ast.Name, ast.Attribute)):
            binding = scope.resolve(value)
            # A decorator returning its input must retain the undecorated
            # identity; following that same definition would recurse here.
            return _merge(candidate if candidate in self.decorating else self.follow(candidate)
                          for candidate in _candidates(binding))
        return "?unresolved"

    def _decoration(self, decorator, scope, value, depth):
        function = decorator.func if isinstance(decorator, ast.Call) else decorator
        if not self._callable_lookup(function, scope, depth):
            return "?unresolved"
        name = self.follow(scope.resolve(function))
        results = []
        is_function = value in self.definitions and isinstance(self.definitions[value][1], _FUNCTIONS)
        for candidate in _candidates(name):
            if candidate == "functools.wraps":
                originals = _candidates(self.follow(scope.resolve(decorator.args[0]))) if (
                    isinstance(decorator, ast.Call) and len(decorator.args) == 1 and not decorator.keywords) else ()
                if (originals and all(original in self.definitions and isinstance(
                        self.definitions[original][1], _FUNCTIONS) for original in originals) and
                        value in self.definitions and isinstance(self.definitions[value][1], _FUNCTIONS)):
                    results.append(value)
                else:
                    results.append("?unresolved")
            elif candidate in self.definitions or candidate in self.lambdas:
                if isinstance(decorator, ast.Call):
                    if decorator.keywords or any(isinstance(arg, ast.Starred) for arg in decorator.args):
                        results.append("?unresolved")
                        continue
                    callee = self._returned(candidate, tuple(scope.resolve(arg) for arg in decorator.args), depth + 1)
                else:
                    callee = candidate
                results.append(self._returned(callee, (value,), depth + 1))
            elif (candidate in ("dataclasses.dataclass", "pytest.fixture", "builtins.staticmethod",
                                "builtins.classmethod", "builtins.property") or
                  isinstance(candidate, str) and candidate.startswith("pytest.mark.") or
                  self._legacy_mark(function, scope.file) or
                  isinstance(function, ast.Name) and function.id in ("staticmethod", "classmethod")):
                # Only known decorator applications carry these deferred
                # callbacks. Unknown keyword protocols do not get a free pass.
                if isinstance(decorator, ast.Call) and candidate == "dataclasses.dataclass" and (
                        decorator.args or any(keyword.arg not in {
                            "init", "repr", "eq", "order", "unsafe_hash", "frozen", "match_args", "kw_only", "slots",
                            "weakref_slot"} or not self._plain(keyword.value, scope) for keyword in decorator.keywords)):
                    results.append("?unresolved")
                elif isinstance(decorator, ast.Call) and candidate == "pytest.fixture" and (
                        decorator.args or any(keyword.arg not in ("scope", "params", "autouse", "ids", "name") or
                                              not self._plain(keyword.value, scope) for keyword in decorator.keywords)):
                    results.append("?unresolved")
                elif candidate in ("builtins.property", "pytest.fixture") and not is_function:
                    results.append("?unresolved")
                elif candidate in ("builtins.staticmethod", "builtins.classmethod") and not is_function:
                    results.append("?unresolved")
                elif candidate == "dataclasses.dataclass" and not (
                        value in self.definitions and isinstance(self.definitions[value][1], ast.ClassDef)):
                    results.append("?unresolved")
                else:
                    results.append(value)
            else:
                results.append("?unresolved")
        return _merge(candidate if candidate in self.definitions or candidate in self.lambdas else "?unresolved"
                      for result in results for candidate in _candidates(result))

    def _function_value(self, value, scope, *, allow_none=False):
        if allow_none and isinstance(value, ast.Constant) and value.value is None:
            return True
        return all(candidate in self.definitions and isinstance(self.definitions[candidate][1], _FUNCTIONS)
                   for candidate in _candidates(self.follow(scope.resolve(value))))

    def _decorated(self, name, depth):
        if depth > MAX_CALL_DEPTH or name in self.decorating:
            return "?unresolved"
        parent, node = self.definitions[name]
        self.decorating.add(name)
        try:
            # Keep the undecorated input distinct in closures. A wrapper that
            # calls its parameter calls the original body, not itself again.
            value = ("?original", *name[1:])
            self.definitions[value] = (parent, node)
            for decorator in reversed(node.decorator_list):
                value = self._decoration(decorator, parent, value, depth + 1)
            return value
        finally:
            self.decorating.remove(name)

    def _members(self, scope, name, seen=()):
        """Keep all possible inherited members; cycles/unknown bases fail closed."""
        if scope in seen or len(seen) >= MAX_CALL_DEPTH:
            return "?unresolved"
        if name in scope.bindings:
            return scope.bindings[name]
        members = []
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in MODELLED_EXTERNAL_BASES:
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return "?unresolved"
                parent, node = self.definitions[candidate]
                inherited = self._members(self._function_scope(parent, node), name, (*seen, scope))
                if inherited != "?unresolved":
                    members.append(inherited)
        return _merge(members)

    def _bound_receiver(self, function, caller, binding):
        parent, node = self.definitions[binding]
        if not parent.class_body or not isinstance(node, _FUNCTIONS):
            return None
        decorators = {name for decorator in node.decorator_list for name in _candidates(self.follow(parent.resolve(
            decorator.func if isinstance(decorator, ast.Call) else decorator)))}
        if "builtins.staticmethod" in decorators:
            return None
        origin = caller.resolve(function.value) if isinstance(function, ast.Attribute) else caller.resolve(function)
        for candidate in _candidates(origin):
            if candidate in self.calls or isinstance(candidate, tuple) and candidate[0] in ("?call", "?external-call"):
                base = (*candidate[:3], ())
                if base not in self.calls:
                    continue
                owner, expression = self.calls[base]
                classes = _candidates(self.follow(owner.resolve(expression.func)))
                if len(classes) == 1 and classes[0] in self.definitions:
                    held, definition = self.definitions[classes[0]]
                    if isinstance(definition, ast.ClassDef):
                        return self._function_scope(held, definition)
            for resolved in _candidates(self.follow(candidate)):
                if "builtins.classmethod" in decorators and resolved in self.definitions:
                    held, definition = self.definitions[resolved]
                    if isinstance(definition, ast.ClassDef):
                        return self._function_scope(held, definition)
        return None

    def _hook(self, scope, name, depth, *, receiver=None, call=None, caller=None):
        """All local bindings of an implicitly invoked method must be read."""
        if name not in scope.bindings:
            return True
        for candidate in _candidates(self.follow(scope.bindings[name])):
            if candidate in self.definitions:
                method = scope.class_body
                values = ("?value",) if not method and name == "__getattr__" else None
                if not self._callee(candidate, depth + 1, method=method, receiver=receiver or scope,
                                    call=call, caller=caller, values=values):
                    return False
                if name in ("__getattribute__", "__getattr__", "__dir__", "__call__"):
                    parent, node = self.definitions[candidate]
                    held = self._function_scope(parent, node, method=method, receiver=receiver or scope, values=values or ())
                    if not all(part.value is None or self._plain(part.value, held) for part in held.nodes
                               if isinstance(part, ast.Return)):
                        # Lookup results may themselves carry protocols or be
                        # called by pytest. An undetermined result is not the
                        # statically declared member of the same name.
                        return False
            elif not self._call_binding(candidate, ast.Name(id=name), scope, depth, True):
                return False
        return True

    def _callable_lookup(self, function, scope, depth):
        """Do not call the declared member after a lookup replaced its value."""
        if depth > MAX_CALL_DEPTH:
            return False
        for raw in _candidates(scope.resolve(function)):
            if isinstance(raw, tuple) and raw[3]:
                owner = self.follow((*raw[:3], raw[3][:-1]))
            elif isinstance(raw, str) and "." in raw:
                owner = self.follow(raw.rpartition(".")[0])
            else:
                continue
            for candidate in _candidates(owner):
                if candidate in self.calls:
                    caller, expression = self.calls[candidate]
                    for constructor in _candidates(self.follow(caller.resolve(expression.func))):
                        if constructor in self.definitions and isinstance(self.definitions[constructor][1], ast.ClassDef):
                            parent, node = self.definitions[constructor]
                            if self._members(self._function_scope(parent, node), "__getattribute__") != "?unresolved":
                                return False
                elif candidate in self.definitions and isinstance(self.definitions[candidate][1], ast.ClassDef):
                    parent, node = self.definitions[candidate]
                    if self._meta_intercepts(self._function_scope(parent, node), depth + 1):
                        return False
        return True

    def _meta_intercepts(self, scope, depth, seen=()):
        if depth > MAX_CALL_DEPTH or scope in seen:
            return True
        for keyword in scope.class_node.keywords:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(keyword.value))):
                if candidate == "builtins.type":
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return True
                parent, node = self.definitions[candidate]
                if self._members(self._function_scope(parent, node), "__getattribute__") != "?unresolved":
                    return True
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in self.definitions and isinstance(self.definitions[candidate][1], ast.ClassDef):
                    parent, node = self.definitions[candidate]
                    if self._meta_intercepts(self._function_scope(parent, node), depth + 1, (*seen, scope)):
                        return True
        return False

    def _protocols(self, scope, depth, *, receiver=None, metaclass=False, seen=()):
        """Admission shared by constructors and every implicit value consumer.

        A body in the allowed set is read on the actual receiver. Unknown
        protocols are never inferred inert from their name or an empty body.
        """
        if depth > MAX_CALL_DEPTH or scope in seen:
            return False
        receiver = receiver or scope
        key = (scope, receiver, metaclass)
        if key in self.protocol_active:
            return False
        self.protocol_active.add(key)
        try:
            for name, binding in scope.bindings.items():
                if not (name.startswith("__") and name.endswith("__")):
                    for candidate in _candidates(self.follow(binding)):
                        if candidate in self.definitions:
                            parent, node = self.definitions[candidate]
                            if isinstance(node, _FUNCTIONS) and any("builtins.property" in _candidates(
                                    self.follow(parent.resolve(decorator))) for decorator in node.decorator_list):
                                return False
                    continue
                for candidate in _candidates(self.follow(binding)):
                    if candidate == "?value" or candidate in self.literal_values:
                        continue
                    if name == "__new__" and candidate == "builtins.object.__new__":
                        continue
                    if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], _FUNCTIONS):
                        return False
                    if name not in MODELLED_CLASS_PROTOCOLS and not (metaclass and name == "__call__"):
                        return False
                    if name == "__new__" and not self._native_new(*self.definitions[candidate]):
                        return False
                if name in ("__getattr__", "__getattribute__", "__setattr__") or metaclass and name == "__call__":
                    if not self._hook(scope, name, depth, receiver=receiver):
                        return False
            for base in scope.class_node.bases:
                for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                    if candidate in MODELLED_EXTERNAL_BASES:
                        continue
                    if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                        return False
                    parent, node = self.definitions[candidate]
                    if not self._protocols(self._function_scope(parent, node), depth + 1,
                                           receiver=receiver, metaclass=metaclass, seen=(*seen, scope)):
                        return False
            return True
        finally:
            self.protocol_active.remove(key)

    def _native_new(self, parent, node):
        """Only allocation of the supplied class has a determined identity."""
        positional = [*node.args.posonlyargs, *node.args.args]
        body = [part for part in node.body if not (isinstance(part, ast.Expr) and
                isinstance(part.value, ast.Constant) and isinstance(part.value.value, str))]
        if not positional or len(body) != 1 or not isinstance(body[0], ast.Return):
            return False
        value = body[0].value
        return (isinstance(value, ast.Call) and self.follow(parent.resolve(value.func)) == "builtins.object.__new__"
                and not value.keywords and len(value.args) == 1 and isinstance(value.args[0], ast.Name)
                and value.args[0].id == positional[0].arg)

    def _dataclass(self, parent, node, depth):
        if depth > MAX_CALL_DEPTH:
            return False
        for decorator in node.decorator_list:
            function = decorator.func if isinstance(decorator, ast.Call) else decorator
            if "dataclasses.dataclass" in _candidates(self.follow(parent.resolve(function))):
                return True
        for base in node.bases:
            for candidate in _candidates(self.follow(parent.resolve(base))):
                if candidate in self.definitions:
                    owner, definition = self.definitions[candidate]
                    if isinstance(definition, ast.ClassDef) and self._dataclass(owner, definition, depth + 1):
                        return True
        return False

    def _factory(self, value, scope, depth):
        if depth >= MAX_CALL_DEPTH:
            return False
        if isinstance(value, ast.Lambda):
            args = [arg.arg for arg in (*value.args.posonlyargs, *value.args.args, *value.args.kwonlyargs)]
            args.extend(arg.arg for arg in (value.args.vararg, value.args.kwarg) if arg)
            while scope.class_body:
                scope = scope.parent
            inner = _Scope(self, scope.file, [value.body], scope, args)
            return self._scan([value.body], inner, depth + 1, strict=True)
        return self._call(value, scope, depth, True)

    def _field_factories(self, scope, depth):
        for binding in scope.bindings.values():
            component_process.checkpoint()
            for candidate in _candidates(self.follow(binding)):
                call = self.calls.get(candidate)
                if call is None:
                    continue
                owner, value = call
                functions = _candidates(self.follow(owner.resolve(value.func)))
                if "dataclasses.field" not in functions:
                    continue
                if any(function != "dataclasses.field" for function in functions):
                    return False
                for keyword in value.keywords:
                    if (keyword.arg is None or keyword.arg == "default_factory" and
                            not self._factory(keyword.value, owner, depth)):
                        return False
        return True

    def _descriptor(self, value, depth, *, dataclass=False):
        if depth > MAX_CALL_DEPTH:
            return False
        for candidate in _candidates(self.follow(value)):
            call = self.calls.get(candidate)
            if call is None:
                if candidate == "?unresolved":
                    return False
                if candidate in self.definitions:
                    owner, definition = self.definitions[candidate]
                    if isinstance(definition, ast.ClassDef) and not self._class_value_descriptor(
                            owner, definition, depth + 1, dataclass=dataclass):
                        return False
                continue
            scope, expression = call
            for constructor in _candidates(self.follow(scope.resolve(expression.func))):
                if constructor == "dataclasses.field":
                    if any(keyword.arg == "default" and not self._descriptor(
                            scope.resolve(keyword.value), depth + 1, dataclass=dataclass) for keyword in expression.keywords):
                        return False
                if constructor == "builtins.object.__new__":
                    if len(expression.args) != 1 or expression.keywords:
                        return False
                    for allocated in _candidates(self.follow(scope.resolve(expression.args[0]))):
                        if allocated not in self.definitions or not isinstance(self.definitions[allocated][1], ast.ClassDef):
                            return False
                        owner, definition = self.definitions[allocated]
                        if not self._class(owner, definition, depth + 1, set_name=True):
                            return False
                        if dataclass and not self._plain_descriptor_class(owner, definition, depth + 1):
                            return False
                if constructor in self.definitions:
                    owner, definition = self.definitions[constructor]
                    if not isinstance(definition, ast.ClassDef) or not self._class(
                            owner, definition, depth + 1, set_name=True):
                        return False
                    if dataclass and not self._plain_descriptor_class(owner, definition, depth + 1):
                        return False
                elif _internal(constructor) or constructor.split(".")[0] in self.modules:
                    return False
        return True

    def _class_value_descriptor(self, parent, node, depth, *, dataclass):
        """A class used as a default is an instance of its effective metaclass."""
        if depth > MAX_CALL_DEPTH:
            return False
        for keyword in node.keywords:
            if keyword.arg != "metaclass":
                return False
            for candidate in _candidates(self.follow(parent.resolve(keyword.value))):
                if candidate == "builtins.type":
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._class(owner, definition, depth + 1, set_name=True):
                    return False
                if dataclass and not self._plain_descriptor_class(owner, definition, depth + 1):
                    return False
        for base in node.bases:
            for candidate in _candidates(self.follow(parent.resolve(base))):
                if candidate in MODELLED_EXTERNAL_BASES:
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._class_value_descriptor(
                        owner, definition, depth + 1, dataclass=dataclass):
                    return False
        return True

    def _plain_descriptor_class(self, parent, node, depth):
        """Dataclass default extraction can read descriptors before __init__."""
        if depth > MAX_CALL_DEPTH:
            return False
        scope = self._function_scope(parent, node)
        if not self._protocols(scope, depth):
            return False
        if any(method in scope.bindings for method in ("__get__", "__set__", "__delete__", "__getattribute__", "__getattr__")):
            return False
        if not self._metaclass_lookup(parent, node, "__hash__"):
            return False
        for base in node.bases:
            for candidate in _candidates(self.follow(parent.resolve(base))):
                if candidate == "builtins.object":
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._plain_descriptor_class(owner, definition, depth + 1):
                    return False
        return True

    def _installed_definition(self, name, depth):
        """Read installed stdlib source, never import a repository-selected module.

        This is only reached for an already admitted external base. Reexports
        are resolved in source; native/generated bases retain their own policy.
        Missing source, ambiguous bindings and nontrivial inheritance refuse.
        """
        if depth > MAX_CALL_DEPTH or not isinstance(name, str):
            return None
        if name in self.installed_definitions:
            return self.installed_definitions[name]
        self.installed_definitions[name] = None
        module, _, attribute = name.rpartition(".")
        if module.split(".")[0] not in sys.stdlib_module_names:
            return None
        root = Path(sysconfig.get_path("stdlib"))
        path = root.joinpath(*module.split("."))
        try:
            package = path.is_dir()
            path = path / "__init__.py" if package else path.with_suffix(".py")
            # Resolving installed paths cannot grant a repository path authority.
            if not path.resolve().is_relative_to(root.resolve()):
                return None
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError, ValueError):
            return None
        found = []
        for part in tree.body:
            component_process.checkpoint()
            if isinstance(part, (ast.ClassDef, *_FUNCTIONS)) and part.name == attribute:
                found.append(part)
            elif isinstance(part, ast.ImportFrom):
                for alias in part.names:
                    if (alias.asname or alias.name) == attribute:
                        origin = part.module or ""
                        if part.level:
                            try:
                                origin = resolve_name("." * part.level + origin,
                                                      module if package else module.rpartition(".")[0])
                            except (ImportError, ValueError):
                                return None
                        found.append(self._installed_definition(origin + "." + alias.name, depth + 1))
            elif isinstance(part, (ast.Assign, ast.AnnAssign)):
                targets = part.targets if isinstance(part, ast.Assign) else [part.target]
                if any(isinstance(target, ast.Name) and target.id == attribute for target in targets):
                    found.append(None)
        result = found[0] if len(found) == 1 else None
        self.installed_definitions[name] = result
        return result

    def _installed_class_routes(self, name, receiver, depth, *, instantiate):
        """Follow receiver dispatch in an unmodified installed lifecycle body.

        The installed implementation owns its local operations, as with exact
        installed-call entries. That trust never covers repository overrides:
        every receiver member it touches is resolved on the repository MRO and
        its exact body is read. Receiver escapes, indirect calls, unsupported
        inheritance and uncertain callback results remain unavailable.
        """
        node = self._installed_definition(name, depth)
        if (not isinstance(node, ast.ClassDef) or node.decorator_list or node.keywords or
                any(not isinstance(base, ast.Name) or base.id != "object" for base in node.bases)):
            return False
        methods = {part.name: part for part in node.body if isinstance(part, _FUNCTIONS)}
        if len(methods) != sum(isinstance(part, _FUNCTIONS) for part in node.body):
            return False

        def follow_method(method, seen):
            component_process.checkpoint()
            if method not in methods:
                return True  # Native object lifecycle, or installed instance data.
            if method in seen or depth + len(seen) > MAX_CALL_DEPTH:
                return False
            body = methods[method]
            args = [*body.args.posonlyargs, *body.args.args]
            if not args or isinstance(body, ast.AsyncFunctionDef):
                return False
            owner = args[0].arg
            nodes = list(_nodes(body.body))
            parents = {child: part for part in nodes for child in ast.iter_child_nodes(part)}
            # Only calls whose source callee is determined may be trusted.
            # A parameter/local could hold a callback, even without a receiver
            # alias. Collect all stores first so source order cannot hide one.
            indirect = {argument.arg for argument in args}
            indirect.update(part.id for part in nodes if isinstance(part, ast.Name) and
                            isinstance(part.ctx, ast.Store))
            for part in nodes:
                component_process.checkpoint()
                if isinstance(part, (*_FUNCTIONS, ast.ClassDef, ast.Lambda)):
                    return False
                if isinstance(part, ast.Call):
                    head = part.func
                    while isinstance(head, ast.Attribute):
                        head = head.value
                    if isinstance(head, ast.Name) and head.id in indirect and head.id != owner:
                        return False
                    if isinstance(part.func, ast.Name) and part.func.id == owner:
                        return False
                    if isinstance(head, ast.Call) and not (isinstance(head.func, ast.Name) and
                            head.func.id == "super" and isinstance(part.func, ast.Attribute) and
                            part.func.attr == "__init_subclass__"):
                        return False
                if isinstance(part, ast.Name) and part.id == owner:
                    parent = parents.get(part)
                    if isinstance(parent, ast.Attribute) and parent.value is part:
                        continue
                    if (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and
                            parent.func.id in ("getattr", "hasattr", "type") and parent.args and
                            parent.args[0] is part):
                        continue  # Attribute read/type inspection, never a callback call.
                    return False
                if not (isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name) and
                        part.value.id == owner):
                    continue
                member = self._members(receiver, part.attr)
                if member == "?unresolved":
                    if not follow_method(part.attr, (*seen, method)):
                        return False
                    continue
                for candidate in _candidates(self.follow(member)):
                    if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], _FUNCTIONS):
                        return False
                    if not self._callee(candidate, depth + len(seen) + 1, method=True, receiver=receiver):
                        return False
                    parent, definition = self.definitions[candidate]
                    held = self._function_scope(parent, definition, method=True, receiver=receiver)
                    if any(part.value is not None and not self._plain(part.value, held)
                           for part in held.nodes if isinstance(part, ast.Return)):
                        return False
            return True

        routes = ("__init_subclass__", "__new__", "__init__") if instantiate else ("__init_subclass__",)
        return all(follow_method(method, ()) for method in routes)

    def _class(self, parent, node, depth, *, instantiate=False, subclass=False, metaclass=False,
               prepare=False, set_name=False, post_init=False, receiver=None, call=None, caller=None):
        if depth > MAX_CALL_DEPTH:
            return False
        scope = self._function_scope(parent, node)
        receiver = receiver or scope
        if not self._class_metaprotocols(scope, depth):
            return False
        if instantiate and not self._protocols(scope, depth, receiver=receiver,
                                               metaclass=self._is_metaclass(scope)):
            return False
        dataclass = self._dataclass(parent, node, depth)
        if dataclass and not all(self._metaclass_lookup(parent, node, attribute) for attribute in
                                 ("__dict__", "__annotations__", "__mro__", "__hash__")):
            return False
        if instantiate:
            post_init = post_init or self._dataclass(parent, node, depth)
        if not self._scan(node.body, scope, depth, strict=False):
            return False
        if dataclass and not self._class_fields(scope, depth):
            return False
        for binding in scope.bindings.values():
            component_process.checkpoint()
            if not self._descriptor(binding, depth, dataclass=dataclass):
                return False
        for keyword in node.keywords:
            if keyword.arg != "metaclass" or not self._call(keyword.value, parent, depth, True):
                return False
            for meta in _candidates(self.follow(parent.resolve(keyword.value))):
                if meta in self.definitions:
                    owner, definition = self.definitions[meta]
                    if not isinstance(definition, ast.ClassDef) or not self._class(
                            owner, definition, depth + 1, instantiate=True, metaclass=instantiate, prepare=True):
                        return False
        for base in node.bases:
            for name in _candidates(self.follow(parent.resolve(base))):
                if name in self.definitions:
                    owner, definition = self.definitions[name]
                    if not isinstance(definition, ast.ClassDef) or not self._class(
                            owner, definition, depth + 1, instantiate=instantiate, subclass=True,
                            metaclass=metaclass, prepare=prepare, set_name=set_name, post_init=post_init,
                            receiver=receiver, call=call, caller=caller):
                        return False
                elif name not in MODELLED_EXTERNAL_BASES:
                    return False
                else:
                    # Use the table's canonical installed identifier, never a
                    # repository file identity, to locate installed source.
                    installed = next(base for base in MODELLED_EXTERNAL_BASES if base == name)
                    if MODELLED_EXTERNAL_BASES[installed].startswith("source:") and not (
                            self._installed_class_routes(installed, receiver, depth + 1, instantiate=instantiate)):
                        return False
        hooks = set()
        if subclass:
            hooks.add("__init_subclass__")
        if instantiate:
            hooks.update(("__init__", "__new__"))
        if metaclass:
            hooks.add("__call__")
        if prepare:
            hooks.add("__prepare__")
        if set_name:
            hooks.add("__set_name__")
        if post_init:
            hooks.update(("__post_init__", "__setattr__", "__getattribute__", "__getattr__"))
            if not self._field_factories(scope, depth):
                return False
        return all(self._hook(scope, hook, depth, receiver=receiver,
                              call=call if hook in ("__init__", "__new__") else None,
                              caller=caller) for hook in hooks)

    def _class_fields(self, scope, depth, seen=()):
        """Class builders read field defaults and annotations through the MRO."""
        if depth > MAX_CALL_DEPTH or scope in seen:
            return False
        for part in scope.nodes:
            if isinstance(part, ast.AnnAssign) and isinstance(part.target, ast.Name):
                default = self._members(scope, part.target.id)
                if default != "?unresolved" and not self._descriptor(default, depth + 1, dataclass=True):
                    return False
                if scope.file not in self.postponed_annotations and not self._pytest_value(scope.resolve(part.annotation), depth + 1):
                    return False
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in self.definitions and isinstance(self.definitions[candidate][1], ast.ClassDef):
                    parent, node = self.definitions[candidate]
                    if not self._class_fields(self._function_scope(parent, node), depth + 1, (*seen, scope)):
                        return False
        return True

    def _is_metaclass(self, scope, seen=()):
        if scope in seen or len(seen) >= MAX_CALL_DEPTH:
            return False
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate == "builtins.type":
                    return True
                if candidate in self.definitions and isinstance(self.definitions[candidate][1], ast.ClassDef):
                    parent, node = self.definitions[candidate]
                    if self._is_metaclass(self._function_scope(parent, node), (*seen, scope)):
                        return True
        return False

    def _class_metaprotocols(self, scope, depth, seen=()):
        if depth > MAX_CALL_DEPTH or scope in seen:
            return False
        for keyword in scope.class_node.keywords:
            if keyword.arg != "metaclass":
                return False
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(keyword.value))):
                if candidate == "builtins.type":
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return False
                parent, node = self.definitions[candidate]
                if not self._protocols(self._function_scope(parent, node), depth + 1, metaclass=True):
                    return False
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in MODELLED_EXTERNAL_BASES:
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return False
                parent, node = self.definitions[candidate]
                if not self._class_metaprotocols(self._function_scope(parent, node), depth + 1, (*seen, scope)):
                    return False
        return True

    def passive(self, file):
        # Hooks and declarations are refused even when nested, matching the
        # existing read. Function bodies otherwise matter only when called.
        for node in self._all_nodes(file):
            component_process.checkpoint()
            if (isinstance(node, _FUNCTIONS) and node.name.startswith("pytest_") or
                    isinstance(node, ast.Name) and (node.id in ("pytest_plugins", "collect_ignore", "collect_ignore_glob") or
                        isinstance(node.ctx, ast.Store) and node.id.startswith("pytest_")) or
                    isinstance(node, ast.ImportFrom) and any(
                        (alias.asname or alias.name).startswith("pytest_") for alias in node.names)):
                return False
        scope = self.scopes[file]
        if not self._scan(self.trees[file].body, scope, 0, strict=False):
            return False
        if file in self.collection_files:
            # Pytest/plugin discovery looks up attributes on the collected
            # module itself. Importing a helper does not do those lookups.
            if not all(self._hook(scope, name, 0) for name in ("__getattr__", "__dir__")):
                return False
            return all(self._pytest_value(binding, 0) for binding in list(scope.bindings.values()))
        return True

    def _pytest_value(self, binding, depth, seen=()):
        if depth > MAX_CALL_DEPTH:
            return False
        for candidate in _candidates(self.follow(binding)):
            if candidate in seen:
                continue
            if candidate in self.definitions:
                parent, node = self.definitions[candidate]
                if isinstance(node, ast.ClassDef):
                    scope = self._function_scope(parent, node)
                    if not self._class_metaprotocols(scope, depth):
                        return False
                    if self._test_class(scope, depth) and not self._class(parent, node, depth + 1, instantiate=True):
                        return False
            elif candidate in self.calls:
                owner, expression = self.calls[candidate]
                for constructor in _candidates(self.follow(owner.resolve(expression.func))):
                    if constructor in self.definitions:
                        parent, node = self.definitions[constructor]
                        if isinstance(node, ast.ClassDef):
                            if not self._protocols(self._function_scope(parent, node), depth + 1):
                                return False
                        elif isinstance(node, ast.FunctionDef):
                            values = self._arguments(parent, node, expression, owner)
                            if values is None:
                                return False
                            returned = self._returned(constructor, values, depth + 1)
                            if returned == "?unresolved" or not self._pytest_value(returned, depth + 1, (*seen, candidate)):
                                return False
                        else:
                            return False
                    elif constructor == "dataclasses.field":
                        if any(keyword.arg == "default" and not self._pytest_value(
                                owner.resolve(keyword.value), depth + 1, (*seen, candidate)) for keyword in expression.keywords):
                            return False
            elif candidate in self.literal_values:
                owner, value = self.literal_values[candidate]
                if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                    children = [value.elt]
                elif isinstance(value, ast.DictComp):
                    children = [value.key, value.value]
                else:
                    children = value.elts if isinstance(value, (ast.Tuple, ast.List, ast.Set)) else [*value.keys, *value.values]
                if not all(child is not None and self._pytest_value(owner.resolve(child), depth + 1, (*seen, candidate))
                           for child in children):
                    return False
        return True

    def _test_class(self, scope, depth, seen=()):
        if depth > MAX_CALL_DEPTH or scope in seen:
            return True
        if scope.class_node.name.startswith("Test"):
            return True
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate == "unittest.TestCase" and any(name.startswith("test") for name in scope.bindings):
                    return True
                if candidate in self.definitions and isinstance(self.definitions[candidate][1], ast.ClassDef):
                    parent, node = self.definitions[candidate]
                    if self._test_class(self._function_scope(parent, node), depth + 1, (*seen, scope)):
                        return True
        return False

    def _scan(self, body, scope, depth, *, strict):
        if depth > MAX_CALL_DEPTH:
            return False
        revision = self.revision
        dirty = set(self.dirty_containers)
        pending = list(reversed(body))
        while pending:
            component_process.checkpoint()
            node = pending.pop()
            if type(node) not in MODELLED_OPERATIONS:
                return False
            if isinstance(node, ast.If) and ast.unparse(node.test) in ("__name__ == '__main__'", "'__main__' == __name__"):
                pending.extend(reversed(node.orelse))
                continue
            if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
                binding = ("@", scope.file, (node.lineno, scope), ())
                if "?unresolved" in _candidates(self._decorated(binding, depth)):
                    return False
                for decorator in node.decorator_list:
                    function = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if isinstance(function, ast.Attribute) and not self._attribute_allowed(function, scope):
                        return False
                    # _decorated already followed repository decorators with
                    # the actual original binding. Re-entering without that
                    # binding would discard a nested wraps(function) input.
                    names = _candidates(self.follow(scope.resolve(function)))
                    if not all(name in self.definitions or name in self.lambdas or
                               self._call_binding(name, function, scope, depth, strict) for name in names):
                        return False
                    if isinstance(decorator, ast.Call):
                        callee = self.follow(scope.resolve(function))
                        if "pytest.fixture" in _candidates(callee) and any(
                                keyword.arg is None or keyword.arg == "autouse" and not (
                                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
                                for keyword in decorator.keywords):
                            return False
                        pending.extend([*decorator.args, *(keyword.value for keyword in decorator.keywords)])
                if isinstance(node, ast.ClassDef):
                    if not self._class(scope, node, depth):
                        return False
                    pending.extend([*node.bases, *(keyword.value for keyword in node.keywords)])
                else:
                    pending.append(node.args)
                    if node.returns and scope.file not in self.postponed_annotations:
                        pending.append(node.returns)
                continue
            if isinstance(node, ast.arg):
                if node.annotation and scope.file not in self.postponed_annotations:
                    pending.append(node.annotation)
                continue
            if isinstance(node, ast.Lambda):
                pending.append(node.args)
                continue
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                if not self._comprehension(node, scope, depth):
                    return False
                continue
            if isinstance(node, ast.For):
                if not self._plain(node.iter, scope):
                    return False
                for part in ast.walk(node.target):
                    if isinstance(part, ast.Name):
                        scope.bindings[part.id] = "?value"
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    scope.assign(target, node.value)
                    if self._environment(target, scope) is True and (node.value is None or not self._plain(node.value, scope)):
                        return False
            if isinstance(node, (ast.Import, ast.ImportFrom)) and not self._import_allowed(node, scope):
                return False
            environment = self._environment(node, scope)
            if environment is False:
                return False
            if isinstance(node, ast.Attribute) and not self._attribute_allowed(node, scope):
                return False
            if isinstance(node, ast.Subscript) and environment is not True:
                owner = self.follow(scope.resolve(node.value))
                typing = self._typing_subscript(node, scope)
                if not typing and (not (_owned_container(owner, scope.file) or self._plain(node.value, scope)) or
                                   not self._plain(node.slice, scope)):
                    return False
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    self.dirty_containers.update(_candidates(owner))
            if not self._operands_allowed(node, scope):
                return False
            if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
                root = node.value
                while isinstance(root, (ast.Attribute, ast.Subscript)):
                    root = root.value
                origin = scope.resolve(root)
                if environment is not True:
                    owned_container = _owned_container(origin, scope.file)
                    if (not _internal(origin) or _external(origin) or
                            strict and not (origin == "?instance" or owned_container)):
                        return False
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    owner = self.follow(scope.resolve(node.func.value))
                    if _owned_container(owner, scope.file) and not all(self._plain(value, scope) for value in
                            [*node.args, *(keyword.value for keyword in node.keywords)]):
                        self.dirty_containers.update(_candidates(owner))
                names = _candidates(self.follow(scope.resolve(node.func)))
                if any(isinstance(name, str) and name in {"builtins." + item for item in _VALUE_BUILTINS}
                       for name in names) and not all(self._plain(value, scope) for value in
                                                     [*node.args, *(keyword.value for keyword in node.keywords)]):
                    return False
                if "builtins.type" in names and len(node.args) != 1:
                    return False
                if any(name in ("builtins.setattr", "builtins.delattr") for name in names) and node.args:
                    root = node.args[0]
                    while isinstance(root, (ast.Attribute, ast.Subscript)):
                        root = root.value
                    origin = self.follow(scope.resolve(root))
                    if (not _internal(origin) or strict or any(candidate in self.definitions or candidate in self.calls
                                                              for candidate in _candidates(origin))):
                        return False
                if environment is True and not all(self._plain(value, scope) for value in
                                                   [*node.args, *(keyword.value for keyword in node.keywords)]):
                    return False
                if environment is not True and not self._call(node.func, scope, depth, strict, call=node):
                    return False
            if strict and isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom, ast.With, ast.AsyncWith)):
                return False
            children = list(ast.iter_child_nodes(node))
            if isinstance(node, ast.AnnAssign) and scope.file in self.postponed_annotations:
                # Dataclasses inspect field names/strings, without executing
                # calls embedded in postponed annotations. Defaults still run.
                children = [child for child in children if child is not node.annotation]
            pending.extend(reversed(children))
        # A followed call may add global/nonlocal candidates after an earlier
        # use in this body. Re-read against that enlarged candidate set.
        return (self.revision == revision and self.dirty_containers == dirty or
                self._scan(body, scope, depth + 1, strict=strict))

    def _import_allowed(self, node, scope):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            if any(alias.name == "*" for alias in node.names):
                return False
            module = self.import_module(scope.file, node)
            if module == "?unresolved":
                return False
            names = [module]
            for file in self.paths.get(module, ()):
                imported = self.scopes.get(file)
                if imported and "__getattr__" in imported.bindings:
                    return False
        for name in names:
            parts = name.split(".")
            for end in range(1, len(parts) + 1):
                prefix = ".".join(parts[:end])
                if prefix not in self.paths and prefix in self.namespace_portions and not self._namespace(prefix):
                    return False
        return True

    def _plain(self, node, scope, seen=()):
        """Values with no repository operator/iteration/subscription hooks."""
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            # Its body was followed, but its produced element graph is not a
            # literal graph. Consumers requiring plain values stay unavailable.
            return False
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(self._plain(item, scope, seen) for item in node.elts)
        if isinstance(node, ast.Dict):
            return all(key is not None and self._plain(key, scope, seen) and self._plain(value, scope, seen)
                       for key, value in zip(node.keys, node.values))
        if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp)):
            return all(self._plain(child, scope, seen) for child in ast.iter_child_nodes(node)
                       if isinstance(child, ast.expr))
        if isinstance(node, ast.Subscript):
            return self._plain(node.value, scope, seen) and self._plain(node.slice, scope, seen)
        if isinstance(node, ast.Call):
            names = _candidates(self.follow(scope.resolve(node.func)))
            return all(isinstance(name, str) and name in {"builtins." + item for item in _VALUE_BUILTINS}
                       for name in names) and all(self._plain(arg, scope, seen) for arg in
                                                 [*node.args, *(keyword.value for keyword in node.keywords)])
        binding = self.follow(scope.resolve(node))
        for candidate in _candidates(binding):
            if candidate in self.literal_values:
                if candidate in seen or candidate in self.dirty_containers or len(seen) >= MAX_CALL_DEPTH:
                    return False
                owner, literal = self.literal_values[candidate]
                if not self._plain(literal, owner, (*seen, candidate)):
                    return False
            elif candidate in self.calls:
                if candidate in seen or len(seen) >= MAX_CALL_DEPTH:
                    return False
                owner, expression = self.calls[candidate]
                if not self._plain(expression, owner, (*seen, candidate)):
                    return False
            elif not (candidate == "?value" or candidate in _PLAIN_EXTERNAL_VALUES or
                      candidate in {"builtins." + item for item in _VALUE_BUILTINS}):
                return False
        return True

    def _installed_argument(self, node, scope, seen=()):
        """Literal graphs and named installed constants/types, with no hooks."""
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(self._installed_argument(item, scope, seen) for item in node.elts)
        if isinstance(node, ast.Dict):
            return all(key is not None and self._installed_argument(key, scope, seen) and
                       self._installed_argument(value, scope, seen) for key, value in zip(node.keys, node.values))
        if isinstance(node, (ast.BinOp, ast.UnaryOp)):
            return all(self._installed_argument(child, scope, seen) for child in ast.iter_child_nodes(node)
                       if isinstance(child, ast.expr))
        if isinstance(node, ast.Subscript):
            return self._typing_subscript(node, scope)
        if isinstance(node, ast.Call):
            names = _candidates(self.follow(scope.resolve(node.func)))
            return (all(MODELLED_EXTERNAL_CALLS.get(name, "").startswith("constant:") for name in names) and
                    not any(isinstance(arg, ast.Starred) for arg in node.args) and
                    not any(keyword.arg is None for keyword in node.keywords) and
                    all(self._installed_argument(value, scope, seen) for value in
                        [*node.args, *(keyword.value for keyword in node.keywords)]))
        binding = self.follow(scope.resolve(node))
        if (isinstance(node, ast.Name) and node.id in ("__file__", "__name__") and binding == "?unresolved"):
            return True
        constants = _PLAIN_EXTERNAL_VALUES | _INSTALLED_TYPES | {
            "typing.Any", "typing.NoReturn", "typing.Never", "typing.LiteralString",
            "re.ASCII", "re.IGNORECASE", "re.LOCALE", "re.MULTILINE", "re.DOTALL", "re.UNICODE", "re.VERBOSE",
            "re.A", "re.I", "re.L", "re.M", "re.S", "re.U", "re.X", "re.NOFLAG",
        }
        for candidate in _candidates(binding):
            if candidate in seen or len(seen) >= MAX_CALL_DEPTH or candidate in self.dirty_containers:
                return False
            if candidate in self.literal_values:
                owner, value = self.literal_values[candidate]
                if not self._installed_argument(value, owner, (*seen, candidate)):
                    return False
            elif candidate in self.calls:
                owner, value = self.calls[candidate]
                if not self._installed_argument(value, owner, (*seen, candidate)):
                    return False
            elif candidate != "?value" and candidate not in constants:
                return False
        return True

    def _typing_subscript(self, node, scope):
        names = _candidates(self.follow(scope.resolve(node.value)))
        return isinstance(node.ctx, ast.Load) and all(name in _TYPING_GENERICS for name in names) and self._installed_argument(
            node.slice, scope)

    def _comprehension(self, node, scope, depth):
        if depth >= MAX_CALL_DEPTH or any(generator.is_async for generator in node.generators):
            return False
        parent = scope
        while parent.class_body:
            parent = parent.parent
        targets = [part.id for generator in node.generators for part in ast.walk(generator.target)
                   if isinstance(part, ast.Name)]
        inner = _Scope(self, scope.file, [], parent, targets, values=("?value",) * len(targets))
        expressions = []
        for index, generator in enumerate(node.generators):
            owner = scope if index == 0 else inner
            if not self._plain(generator.iter, owner) or not self._scan([generator.iter], owner, depth + 1, strict=True):
                return False
            expressions.extend(generator.ifs)
        expressions.extend([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt])
        return self._scan(expressions, inner, depth + 1, strict=True) and (
            not isinstance(node, (ast.SetComp, ast.DictComp)) or
            self._plain(node.key if isinstance(node, ast.DictComp) else node.elt, inner))

    def _attribute_allowed(self, node, scope):
        owners = _candidates(self.follow(scope.resolve(node.value)))
        for owner in owners:
            if owner == "?instance":
                if not isinstance(node.ctx, ast.Store) or not self._instance_store(scope, node.attr):
                    return False
            elif owner in self.calls:
                if not isinstance(node.ctx, ast.Load):
                    return False
                caller, expression = self.calls[owner]
                for constructor in _candidates(self.follow(caller.resolve(expression.func))):
                    if constructor not in self.definitions or not isinstance(self.definitions[constructor][1], ast.ClassDef):
                        return False
                    parent, definition = self.definitions[constructor]
                    held = self._function_scope(parent, definition)
                    if not self._protocols(held, 0):
                        return False
                    for value in _candidates(self.follow(self._members(held, node.attr))):
                        if value not in self.definitions or not isinstance(self.definitions[value][1], _FUNCTIONS):
                            return False
            elif owner in self.definitions:
                parent, definition = self.definitions[owner]
                if (not isinstance(definition, ast.ClassDef) or not isinstance(node.ctx, ast.Load) or
                        not self._metaclass_lookup(parent, definition, node.attr)):
                    return False
                held = self._function_scope(parent, definition)
                values = _candidates(self.follow(held.bindings.get(node.attr, "?unresolved")))
                for value in values:
                    if value not in self.definitions or not isinstance(self.definitions[value][1], _FUNCTIONS):
                        return False
                    function_parent, function = self.definitions[value]
                    if any("builtins.property" in _candidates(self.follow(function_parent.resolve(
                            decorator.func if isinstance(decorator, ast.Call) else decorator)))
                           for decorator in function.decorator_list):
                        return False
            elif _owned_container(owner, scope.file):
                if not isinstance(node.ctx, ast.Load):
                    return False
            elif _internal(owner):
                return False
            elif not isinstance(node.ctx, ast.Load):
                return False
            else:
                for file in self.paths.get(owner, ()):
                    imported = self.scopes.get(file)
                    if imported and node.attr not in imported.bindings and "__getattr__" in imported.bindings:
                        if not self._hook(imported, "__getattr__", 0):
                            return False
        return True

    def _metaclass_lookup(self, parent, node, attribute, seen=()):
        """Check metaclasses contributed by every base before class lookup."""
        if node in seen or len(seen) >= MAX_CALL_DEPTH:
            return False
        for keyword in node.keywords:
            if keyword.arg != "metaclass":
                return False
            for candidate in _candidates(self.follow(parent.resolve(keyword.value))):
                if candidate == "builtins.type":
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._metaclass_members(owner, definition, attribute):
                    return False
        for base in node.bases:
            for candidate in _candidates(self.follow(parent.resolve(base))):
                if candidate in ("builtins.object", "builtins.type"):
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._metaclass_lookup(
                        owner, definition, attribute, (*seen, node)):
                    return False
        return True

    def _metaclass_members(self, parent, node, attribute, seen=()):
        if node in seen or len(seen) >= MAX_CALL_DEPTH:
            return False
        scope = self._function_scope(parent, node)
        if not all(self._hook(scope, name, len(seen), receiver=scope) for name in ("__getattribute__", "__getattr__")):
            return False
        if attribute in scope.bindings:
            # A member of the same name may itself be a descriptor. Until its
            # lookup protocol is followed, the class lookup stays unavailable.
            return False
        for base in node.bases:
            for candidate in _candidates(self.follow(parent.resolve(base))):
                if candidate in ("builtins.type", "builtins.object"):
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._metaclass_members(
                        owner, definition, attribute, (*seen, node)):
                    return False
        return True

    def _instance_store(self, scope, attribute, seen=()):
        owner = getattr(scope, "method_owner", None)
        if getattr(scope, "class_receiver", False):
            return owner is not None and self._class_metaprotocols(owner, len(seen))
        return self._instance_store_owner(owner, attribute, seen)

    def _instance_store_owner(self, owner, attribute, seen):
        if owner is None or owner in seen or len(seen) >= MAX_CALL_DEPTH:
            return False
        if not all(self._hook(owner, name, len(seen), receiver=owner) for name in ("__setattr__",)):
            return False
        if attribute in owner.bindings and not all(candidate == "?value" for candidate in
                                                   _candidates(self.follow(owner.bindings[attribute]))):
            return False
        node = owner.class_node
        for base in node.bases:
            for candidate in _candidates(self.follow(owner.declaration_scope.resolve(base))):
                if candidate == "builtins.object":
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return False
                parent, definition = self.definitions[candidate]
                base_scope = self._function_scope(parent, definition)
                # Reuse the same store rule with the inherited owner.
                if not self._instance_store_owner(base_scope, attribute, (*seen, owner)):
                    return False
        return True

    def _operands_allowed(self, node, scope):
        operands = []
        if isinstance(node, ast.BinOp):
            operands = [node.left, node.right]
        elif isinstance(node, ast.UnaryOp):
            operands = [node.operand]
        elif isinstance(node, ast.BoolOp):
            operands = node.values
        elif isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
        elif isinstance(node, ast.AugAssign):
            operands = [node.target, node.value]
        elif isinstance(node, (ast.If, ast.While, ast.IfExp)):
            operands = [node.test]
        elif isinstance(node, ast.For):
            operands = [node.iter]
        elif isinstance(node, ast.Match):
            operands = [node.subject]
        elif isinstance(node, ast.Set):
            operands = node.elts
        elif isinstance(node, ast.Dict):
            operands = node.keys
        elif isinstance(node, ast.With):
            for item in node.items:
                expression = item.context_expr
                if not isinstance(expression, ast.Call) or self.follow(scope.resolve(expression.func)) != "contextlib.nullcontext":
                    return False
                values = [*expression.args, *(keyword.value for keyword in expression.keywords)]
                if isinstance(item.optional_vars, (ast.Tuple, ast.List)) and (len(values) != 1 or not self._plain(values[0], scope)):
                    return False
            return True
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, (ast.Tuple, ast.List)) for target in targets):
                operands = [node.value]
        return all(operand is not None and self._plain(operand, scope) for operand in operands)
