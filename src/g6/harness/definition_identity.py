# SPDX-License-Identifier: Apache-2.0
"""Host-held definition custody for the two executable behaviour templates.

Read source without importing it. A static alias is readable; dynamic binding,
conditional binding, decorators and missing source supply no identity.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass


class ResolvedData(dict):
    """Bindings whose resolver is held in the compiler, never in their literal."""

    def __init__(self, data, tree):
        super().__init__(data)
        self.definitions = Definitions(tree)


@dataclass(frozen=True)
class Definitions:
    tree: object

    def same(self, spec, rejected):
        """Same definition on either execution side; None if either side is unreadable."""
        from g6.harness import verification_templates as vt

        if not isinstance(spec, list) or len(spec) < 3 or spec[0] != "module":
            return None
        readings = []
        for side in (vt.BASE, vt.PATCH):
            self.tree.in_time()
            located = vt._module_file(self.tree, spec[1].split("."), side)
            actual = (None if len(spec) > 3 and located != spec[3]
                      else self._identity(spec[1], spec[2], side))
            module, _, name = rejected.rpartition(".")
            wrong = self._identity(module, name, side)
            readings.append(None if actual is None or wrong is None else actual == wrong)
        return True if True in readings else None if None in readings else False

    def _body(self, module, side):
        from g6.harness import verification_templates as vt

        self.tree.in_time()
        rel = vt._module_file(self.tree, module.split("."), side)
        if not rel:
            return None
        body = ast.parse(self.tree.text(rel, side)).body
        # Top-level execution can change bindings beyond a syntactic assignment.
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if isinstance(node, ast.ClassDef) or node.decorator_list or node.name == '__getattr__':
                    return None
                defaults = [*node.args.defaults, *(value for value in node.args.kw_defaults if value is not None)]
                try:
                    for value in defaults:
                        ast.literal_eval(value)
                except (ValueError, TypeError):
                    return None
                if any(not isinstance(annotation, (ast.Name, ast.Constant))
                       for annotation in [node.returns, *(arg.annotation for arg in
                           [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                            *([node.args.vararg] if node.args.vararg else []),
                            *([node.args.kwarg] if node.args.kwarg else [])])]
                       if annotation is not None):
                    return None
            elif isinstance(node, ast.Import):
                if any((alias.asname or alias.name.split('.')[0]) == '__getattr__' for alias in node.names):
                    return None
                continue
            elif isinstance(node, ast.ImportFrom):
                if any(alias.name == "*" or (alias.asname or alias.name) == '__getattr__' for alias in node.names):
                    return None
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(not isinstance(target, ast.Name) or target.id == '__getattr__' for target in targets):
                    return None
                if isinstance(node, ast.AnnAssign) and not isinstance(node.annotation, (ast.Name, ast.Constant)):
                    return None
                if node.value is not None and not isinstance(node.value, (ast.Name, ast.Attribute)):
                    try:
                        ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        return None
            elif not (isinstance(node, ast.Pass) or isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                return None
        return rel, body

    def _safe_module(self, module, side, seen=()):
        """Imports and package initializers must also belong to the readable subset."""
        from g6.harness import verification_templates as vt

        if module in seen or len(seen) >= 64:
            return False
        read = self._body(module, side)
        if read is None:
            return False
        rel, body = read
        seen = (*seen, module)
        parent = module.rpartition(".")[0]
        if parent and parent not in seen and not self._safe_module(parent, side, seen):
            return False
        for node in body:
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = self._imported(module, rel, node, node.names[0]).rpartition(".")[0]
                imported = [prefix, *(name for alias in node.names
                    if (name := self._imported(module, rel, node, alias))
                    and vt._module_file(self.tree, name.split("."), side))]
            else:
                continue
            if any(not name or not self._safe_module(name, side, seen) for name in imported):
                return False
        return True

    def _binding(self, module, name, side, before=None):
        from g6.harness.verification_templates import _bound_names

        read = self._body(module, side)
        if read is None:
            return None
        rel, body = read
        found = None
        for index, node in enumerate(body[:before]):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
                found = rel, index, node
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if node.value is not None and any(name in _bound_names(target) for target in targets):
                    if not all(isinstance(target, ast.Name) for target in targets):
                        return None
                    found = rel, index, node
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                if any((alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name))
                       == name for alias in node.names):
                    found = rel, index, node
        return found

    @staticmethod
    def _imported(module, rel, node, alias):
        if isinstance(node, ast.Import):
            return alias.name if alias.asname else alias.name.split(".")[0]
        package = module.split(".") if rel.endswith("/__init__.py") else module.split(".")[:-1]
        if node.level:
            if node.level > len(package):
                return ""
            prefix = package[:len(package) - node.level + 1]
        else:
            prefix = []
        return ".".join([*prefix, *([node.module] if node.module else []), alias.name])

    def _identity(self, module, name, side, seen=(), before=None):
        key = module, name, before
        if key in seen or len(seen) >= 64:
            return None
        if not self._safe_module(module, side):
            return None
        bound = self._binding(module, name, side, before)
        if bound is None:
            return None
        rel, index, node = bound
        seen = (*seen, key)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return rel, node.lineno, node.name
        if isinstance(node, ast.ImportFrom):
            alias = next(alias for alias in node.names if (alias.asname or alias.name) == name)
            imported = self._imported(module, rel, node, alias)
            owner, _, attribute = imported.rpartition(".")
            return self._identity(owner, attribute, side, seen) if owner else None
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Name):
                return self._identity(module, value.id, side, seen, index)
            if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
                imported = self._binding(module, value.value.id, side, index)
                if imported is None:
                    return None
                file, _, statement = imported
                if isinstance(statement, (ast.Import, ast.ImportFrom)):
                    alias = next(a for a in statement.names if (a.asname or a.name.split(".")[0]) == value.value.id)
                    owner = self._imported(module, file, statement, alias)
                    return self._identity(owner, value.attr, side, seen)
        return None
