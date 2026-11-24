# SPDX-License-Identifier: Apache-2.0
"""Bounded source-only reading of ordinary import-time collection effects.

This is advisory analysis within the collection child's same-process caveat.
Nothing here imports, evaluates or calls repository objects.
"""
from __future__ import annotations

import ast
from collections import deque
from pathlib import PurePosixPath

from g6.core import component_process


MAX_CALL_DEPTH = 24
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
# These operations build values; the binding must resolve to the builtin first.
_VALUE_BUILTINS = frozenset("bool bytes complex dict float frozenset int len list range set str tuple".split())


def module_names(file, roots=()):
    from g6.workforce.repo_tests import _import_names

    return _import_names(file, (*roots, ".", "src"))


def _attributes(binding, attributes):
    """Append identifier parts, keeping a local file identity opaque."""
    if isinstance(binding, tuple):
        kind, file, line, held = binding
        return kind, file, line, (*held, *attributes)
    return ".".join((binding, *attributes))


def _internal(binding):
    return isinstance(binding, tuple) or binding.startswith("?")


def _external(binding):
    return isinstance(binding, str) and binding.startswith("?external")


def _owned_container(binding, file, kinds=("?list", "?dict")):
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
    def __init__(self, reader, file, body, parent=None, arguments=(), instance=None):
        self.reader, self.file, self.parent = reader, file, parent
        self.bindings = {name: "?value" for name in arguments}
        if instance:
            self.bindings[instance] = "?instance"
        self.nodes = list(_nodes(body))
        self.globals = {name for node in self.nodes if isinstance(node, ast.Global) for name in node.names}
        assignments = []
        for node in self.nodes:
            if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
                key = ("@", file, node.lineno, ())
                self.bindings[node.name] = key
                reader.definitions[key] = (self, node)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.bindings[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                module = reader.import_module(file, node)
                for alias in node.names:
                    self.bindings[alias.asname or alias.name] = f"{module}.{alias.name}"
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        assignments.append((target.id, node.value))
                        self.bindings[target.id] = "?unresolved"
        # Resolve aliases using bindings, not the spelling of a callable. A
        # bounded fixed point leaves cycles and unavailable assignments unknown.
        for _ in range(MAX_CALL_DEPTH):
            changed = False
            for name, value in assignments:
                component_process.checkpoint()
                if isinstance(value, ast.List):
                    resolved = ("?list", file, value.lineno, ())
                elif isinstance(value, ast.Dict):
                    resolved = ("?dict", file, value.lineno, ())
                elif isinstance(value, (ast.Name, ast.Attribute, ast.Call)):
                    resolved = self.resolve(value)
                else:
                    resolved = "?value"
                if self.bindings[name] != resolved:
                    self.bindings[name], changed = resolved, True
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
            for part in ast.walk(node):
                component_process.checkpoint()
                if isinstance(part, ast.Name):
                    name = self.resolve(part)
                    if _external(name) or not (_internal(name) or name.startswith("builtins.")):
                        return "?external"
            return "?value"
        return "?unresolved"


class CollectionEffects:
    @classmethod
    def from_trees(cls, trees, *, roots=(), modules=()):
        """Read a mapping of opaque file identities to parsed source trees."""
        return cls(trees, roots=roots, modules=modules)

    def __init__(self, trees, *, roots=(), modules=()):
        self.trees, self.modules = trees, set(modules)
        self.paths, self.definitions, self.scopes = {}, {}, {}
        self.names = {file: module_names(file, roots) for file in trees}
        self.function_scopes, self.checked, self.active = {}, {}, set()
        self.module_nodes, self.mark_aliases = {}, {}
        for file in trees:
            for name in self.names[file]:
                self.paths.setdefault(name, []).append(file)
                self.modules.add(name.split(".")[0])
        for file, tree in trees.items():
            self.scopes[file] = _Scope(self, file, tree.body)

    def import_module(self, file, node):
        if not node.level:
            return node.module or ""
        names = self.names[file]
        module = min(names, key=lambda name: (len(name.split(".")), name))
        parts = module.split(".") if PurePosixPath(file).name == "__init__.py" else module.split(".")[:-1]
        if node.level > len(parts):
            return "?unresolved"
        return ".".join([*parts[:len(parts) - node.level + 1], *([node.module] if node.module else [])])

    def follow(self, name, seen=()):
        component_process.checkpoint()
        if name in seen or len(seen) >= MAX_CALL_DEPTH:
            return "?unresolved"
        if name in self.definitions:
            return name
        if isinstance(name, tuple):
            kind, file, line, attributes = name
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
            scope = self.scopes[files[0]]
            resolved = scope.bindings.get(parts[end], "?unresolved")
            return self.follow(_attributes(resolved, parts[end + 1:]), (*seen, name))
        return name

    def _environment(self, node, scope):
        """None means another object; bool is the result for an environ write."""
        if (isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) and
                self.follow(scope.resolve(node.value)) == "os.environ"):
            return (isinstance(node.ctx, ast.Store) and self._environment_key(node.slice))
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            return None
        if self.follow(scope.resolve(node.func.value)) != "os.environ":
            return None
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
        return isinstance(node, ast.Constant) and isinstance(node.value, str) and not node.value.upper().startswith(("PYTEST", "PYTHON"))

    def _call(self, function, scope, depth, strict):
        if not isinstance(function, (ast.Name, ast.Attribute)):
            return False
        name = self.follow(scope.resolve(function))
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

    def _callee(self, name, depth):
        if depth > MAX_CALL_DEPTH or name in self.active:
            return False
        if name in self.checked:
            return self.checked[name]
        self.active.add(name)
        try:
            parent, node = self.definitions[name]
            if isinstance(node, ast.AsyncFunctionDef):
                result = False
            elif isinstance(node, ast.ClassDef):
                result = self._class(parent, node, depth, instantiate=True)
            else:
                scope = self._function_scope(parent, node)
                result = self._scan(node.body, scope, depth, strict=True)
            self.checked[name] = result
            return result
        finally:
            self.active.remove(name)

    def _function_scope(self, parent, node, *, method=False):
        key = (parent.file, node.lineno)
        if key not in self.function_scopes:
            args = []
            if isinstance(node, _FUNCTIONS):
                args = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
                args.extend(arg.arg for arg in (node.args.vararg, node.args.kwarg) if arg)
            self.function_scopes[key] = _Scope(self, parent.file, node.body, parent, args,
                                                args[0] if method and args else None)
        return self.function_scopes[key]

    def _decorator_result(self, name, depth):
        """A factory's returned callable also runs during decoration."""
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

    def _class(self, parent, node, depth, *, instantiate=False, subclass=False, metaclass=False):
        if depth > MAX_CALL_DEPTH:
            return False
        scope = self._function_scope(parent, node)
        if not self._scan(node.body, scope, depth, strict=False):
            return False
        for keyword in node.keywords:
            if keyword.arg != "metaclass" or not self._call(keyword.value, parent, depth, True):
                return False
            meta = self.follow(parent.resolve(keyword.value))
            if meta in self.definitions:
                owner, definition = self.definitions[meta]
                if not isinstance(definition, ast.ClassDef) or not self._class(
                        owner, definition, depth + 1, instantiate=True, metaclass=instantiate):
                    return False
        for base in node.bases:
            name = self.follow(parent.resolve(base))
            if name in self.definitions:
                owner, definition = self.definitions[name]
                if not isinstance(definition, ast.ClassDef) or not self._class(
                        owner, definition, depth + 1, instantiate=instantiate, subclass=True, metaclass=metaclass):
                    return False
            elif _internal(name) or name.split(".")[0] in self.modules:
                return False
        for method in node.body:
            if isinstance(method, _FUNCTIONS) and (subclass and method.name == "__init_subclass__" or
                                                  instantiate and method.name in ("__init__", "__new__") or
                                                  metaclass and method.name == "__call__"):
                if not self._scan(method.body, self._function_scope(parent, method, method=True), depth + 1, strict=True):
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
                        if callee in self.definitions and not self._decorator_result(callee, depth + 1):
                            return False
                        if self.follow(scope.resolve(function)) == "pytest.fixture" and any(
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
                name = scope.resolve(node.func)
                if name in ("builtins.setattr", "builtins.delattr") and node.args:
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
