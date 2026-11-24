# SPDX-License-Identifier: Apache-2.0
"""Bounded source-only reading of ordinary import-time collection effects.

This is advisory analysis within the collection child's same-process caveat.
Nothing here imports, evaluates or calls repository objects.
"""
from __future__ import annotations

import ast
from collections import deque
from importlib.util import resolve_name
from pathlib import PurePosixPath

from g6.core import component_process


MAX_CALL_DEPTH = 24
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
# These operations build values; the binding must resolve to the builtin first.
_VALUE_BUILTINS = frozenset("bool bytes complex dict float frozenset int len list range set str tuple".split())


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
    def __init__(self, reader, file, body, parent=None, arguments=(), instance=None, *, class_body=False):
        self.reader, self.file, self.parent = reader, file, parent
        self.class_body = class_body
        self.bindings = {name: "?value" for name in arguments}
        if instance:
            self.bindings[instance] = "?instance"
        self.nodes = list(_nodes(body))
        self.globals = {name for node in self.nodes if isinstance(node, ast.Global) for name in node.names}
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
                key = ("@", file, node.lineno, ())
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
                if isinstance(value, ast.List):
                    resolved = ("?list", file, value.lineno, ())
                elif isinstance(value, ast.Dict):
                    resolved = ("?dict", file, value.lineno, ())
                elif isinstance(value, (ast.Name, ast.Attribute, ast.Call)):
                    resolved = self.resolve(value)
                else:
                    resolved = "?value"
                if slots[name][slot] != resolved:
                    slots[name][slot], changed = resolved, True
                    self.bindings[name] = _merge(slots[name])
            if not changed:
                break

    def resolve(self, node):
        if isinstance(node, ast.Name):
            if node.id in self.globals and self.parent:
                return self.reader.scopes[self.file].resolve(node)
            if node.id in self.bindings:
                return self.bindings[node.id]
            if self.parent:
                return self.parent.resolve(node)
            return f"builtins.{node.id}" if node.id in _VALUE_BUILTINS | {
                "object", "type", "staticmethod", "classmethod", "setattr", "delattr", "property"} else "?unresolved"
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
            key = ("?external-call" if external else "?call", self.file, (node.lineno, node.col_offset), ())
            self.reader.calls[key] = (self, node)
            return key
        return "?unresolved"


class CollectionEffects:
    @classmethod
    def from_trees(cls, trees, *, roots=(), modules=(), files=()):
        """Read a mapping of opaque file identities to parsed source trees."""
        return cls(trees, roots=roots, modules=modules, files=files)

    def __init__(self, trees, *, roots=(), modules=(), files=()):
        self.trees, self.modules = trees, set(modules)
        self.paths, self.definitions, self.scopes = {}, {}, {}
        files = set(trees) | set(files)
        self.names = {file: module_names(file, roots) for file in files}
        self.packages = {PurePosixPath(file).parent for file in files if PurePosixPath(file).name == "__init__.py"}
        self.calls = {}
        self.function_scopes, self.checked, self.active = {}, {}, set()
        self.module_nodes, self.mark_aliases = {}, {}
        for file in files:
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
            return "?unresolved"
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
        if any(len(self.paths.get(name, ())) != 1 for name in resolved):
            return "?unresolved"
        if len(resolved) > 1:
            identities = [self.paths.get(name, ()) for name in resolved]
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
            return name
        if isinstance(name, tuple):
            kind, file, line, attributes = name
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
            if kind != "@":
                return name
            definition = self.definitions.get((kind, file, line, ()))
            if not definition or not attributes or not isinstance(definition[1], ast.ClassDef):
                return "?unresolved"
            parent, node = definition
            scope = self._function_scope(parent, node)
            resolved = scope.bindings.get(attributes[0], "?unresolved")
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

    def _call(self, function, scope, depth, strict):
        if not isinstance(function, (ast.Name, ast.Attribute)):
            return False
        name = self.follow(scope.resolve(function))
        return all(self._call_binding(candidate, function, scope, depth, strict) for candidate in _candidates(name))

    def _call_binding(self, name, function, scope, depth, strict):
        # Preserve the owner's same-process exceptions; do not tighten the gate
        # against repository code deliberately shadowing these bindings.
        if (isinstance(name, str) and name.startswith("pytest.mark.") or self._legacy_mark(function, scope.file) or
                isinstance(function, ast.Name) and function.id in ("staticmethod", "classmethod")):
            return True
        if name in self.definitions:
            return self._callee(name, depth + 1)
        if _internal(name):
            if isinstance(function, ast.Attribute):
                owner = self.follow(scope.resolve(function.value))
                if _owned_container(owner, scope.file, ("?list",)) and function.attr in ("append", "extend", "insert"):
                    return True
            return not strict and name == "?unresolved" and isinstance(function, ast.Name) and function.id not in scope.bindings
        if name.split(".")[0] in self.modules:
            return False
        if strict:
            return name in {*("builtins." + item for item in _VALUE_BUILTINS), "builtins.object.__new__", "builtins.type"}
        return True   # installed dependencies retain the previous ordinary-code rule

    def _all_nodes(self, file):
        if file not in self.module_nodes:
            nodes, pending = [], deque([self.trees[file]])
            while pending:
                component_process.checkpoint()
                node = pending.popleft()
                nodes.append(node)
                pending.extend(ast.iter_child_nodes(node))
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

    def _callee(self, name, depth, *, method=False):
        if depth > MAX_CALL_DEPTH or name in self.active:
            return False
        cache_key = (name, method)
        if cache_key in self.checked:
            return self.checked[cache_key]
        self.active.add(name)
        try:
            parent, node = self.definitions[name]
            if isinstance(node, ast.AsyncFunctionDef):
                result = False
            elif isinstance(node, ast.ClassDef):
                result = self._class(parent, node, depth, instantiate=True)
            else:
                scope = self._function_scope(parent, node, method=method)
                result = self._scan(node.body, scope, depth, strict=True)
            self.checked[cache_key] = result
            return result
        finally:
            self.active.remove(name)

    def _function_scope(self, parent, node, *, method=False):
        if isinstance(node, _FUNCTIONS):
            while parent.class_body:
                parent = parent.parent
        key = (parent.file, node.lineno, method)
        if key not in self.function_scopes:
            args = []
            if isinstance(node, _FUNCTIONS):
                args = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
                args.extend(arg.arg for arg in (node.args.vararg, node.args.kwarg) if arg)
            self.function_scopes[key] = _Scope(self, parent.file, node.body, parent, args,
                                              args[0] if method and args else None,
                                              class_body=isinstance(node, ast.ClassDef))
        return self.function_scopes[key]

    def _decorator_result(self, name, depth):
        """A factory's returned callable also runs during decoration."""
        if isinstance(name, frozenset):
            return all(self._decorator_result(candidate, depth) for candidate in name)
        if depth > MAX_CALL_DEPTH or name not in self.definitions:
            return False
        parent, node = self.definitions[name]
        if not isinstance(node, ast.FunctionDef):
            return False
        scope = self._function_scope(parent, node)
        returns = [part for part in scope.nodes if isinstance(part, ast.Return)]
        if not returns:
            return False
        for part in returns:
            value = part.value
            if isinstance(value, ast.Lambda):
                args = [arg.arg for arg in (*value.args.posonlyargs, *value.args.args, *value.args.kwonlyargs)]
                inner = _Scope(self, scope.file, [value.body], scope, args)
                if not self._scan([value.body], inner, depth + 1, strict=True):
                    return False
            elif isinstance(value, ast.Call):
                if not self._decorator_result(self.follow(scope.resolve(value.func)), depth + 1):
                    return False
            elif not self._call(value, scope, depth, True):
                return False
        return True

    def _hook(self, scope, name, depth):
        """All local bindings of an implicitly invoked method must be read."""
        if name not in scope.bindings:
            return True
        for candidate in _candidates(self.follow(scope.bindings[name])):
            if candidate in self.definitions:
                if not self._callee(candidate, depth + 1, method=True):
                    return False
            elif not self._call_binding(candidate, ast.Name(id=name), scope, depth, True):
                return False
        return True

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

    def _descriptor(self, value, depth):
        if depth > MAX_CALL_DEPTH:
            return False
        for candidate in _candidates(self.follow(value)):
            call = self.calls.get(candidate)
            if call is None:
                if candidate == "?unresolved":
                    return False
                continue
            scope, expression = call
            for constructor in _candidates(self.follow(scope.resolve(expression.func))):
                if constructor in self.definitions:
                    owner, definition = self.definitions[constructor]
                    if not isinstance(definition, ast.ClassDef) or not self._class(
                            owner, definition, depth + 1, set_name=True):
                        return False
                elif _internal(constructor) or constructor.split(".")[0] in self.modules:
                    return False
        return True

    def _class(self, parent, node, depth, *, instantiate=False, subclass=False, metaclass=False,
               prepare=False, set_name=False, post_init=False):
        if depth > MAX_CALL_DEPTH:
            return False
        scope = self._function_scope(parent, node)
        if instantiate:
            for decorator in node.decorator_list:
                function = decorator.func if isinstance(decorator, ast.Call) else decorator
                if self.follow(parent.resolve(function)) != "dataclasses.dataclass":
                    return False
            post_init = post_init or self._dataclass(parent, node, depth)
        if not self._scan(node.body, scope, depth, strict=False):
            return False
        for binding in scope.bindings.values():
            component_process.checkpoint()
            if not self._descriptor(binding, depth):
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
                            metaclass=metaclass, prepare=prepare, set_name=set_name, post_init=post_init):
                        return False
                elif _internal(name) or name.split(".")[0] in self.modules:
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
            hooks.add("__post_init__")
            if not self._field_factories(scope, depth):
                return False
        return all(self._hook(scope, hook, depth) for hook in hooks)

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
        return self._scan(self.trees[file].body, self.scopes[file], 0, strict=False)

    def _scan(self, body, scope, depth, *, strict):
        pending = list(body)
        while pending:
            component_process.checkpoint()
            node = pending.pop()
            if isinstance(node, ast.If) and ast.unparse(node.test) in ("__name__ == '__main__'", "'__main__' == __name__"):
                pending.extend(node.orelse)
                continue
            if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
                for decorator in node.decorator_list:
                    function = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if not self._call(function, scope, depth, strict):
                        return False
                    if isinstance(decorator, ast.Call):
                        callee = self.follow(scope.resolve(function))
                        if any(candidate in self.definitions and not self._decorator_result(candidate, depth + 1)
                               for candidate in _candidates(callee)):
                            return False
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
                    pending.extend([node.args, *([node.returns] if node.returns else [])])
                continue
            if isinstance(node, ast.Lambda):
                pending.append(node.args)
                continue
            if isinstance(node, ast.ImportFrom) and self.import_module(scope.file, node) == "?unresolved":
                return False
            environment = self._environment(node, scope)
            if environment is False:
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
                names = _candidates(self.follow(scope.resolve(node.func)))
                if any(name in ("builtins.setattr", "builtins.delattr") for name in names) and node.args:
                    root = node.args[0]
                    while isinstance(root, (ast.Attribute, ast.Subscript)):
                        root = root.value
                    if not _internal(scope.resolve(root)) or strict:
                        return False
                if environment is not True and not self._call(node.func, scope, depth, strict):
                    return False
            if strict and isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom, ast.With, ast.AsyncWith)):
                return False
            pending.extend(ast.iter_child_nodes(node))
        return True
