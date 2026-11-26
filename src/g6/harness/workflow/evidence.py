# SPDX-License-Identifier: Apache-2.0
"""Small observed counts and task-local citations shared by the card and export.

Readers never mint events. A citation hashes the event's canonical body, so it
can be resolved from the inspection file without trusting report wording.
"""
from __future__ import annotations

import ast
import builtins
from collections.abc import Iterable, Mapping
from pathlib import Path
import re

from g6.core.cloud_consent import sha256
from g6.core.ledger import digest
from g6.harness.workflow import policy
from g6.harness.workflow.record import Event, ProjectRecord


def _count(value) -> int:
    return len(value) if isinstance(value, (list, tuple, dict)) else 0


def source_digests(root, checks: Iterable[str]) -> dict[str, str]:
    """Hash regular test files inside the named tree; never retain their text."""
    top = Path(root).resolve()
    found, files = {}, {}
    for check in checks:
        file = check.partition("::")[0]
        path = top / file
        try:
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(top):
                if file not in files:
                    files[file] = sha256(path.read_bytes())
                found[check] = files[file]
        except OSError:
            continue
    return found


def capture_sources(root, checks: Iterable[str]) -> dict[str, str]:
    """Bind test machinery before execution, without importing repository code.

    R3'/R5: follow collection code and every referenced in-repo callable body;
    recognised fixtures, collected tests and returned test wrappers run at test time.
    R5' keeps recognised fixture bodies at setup even when referenced at collection.
    R6 includes constructed-class protocols, not body-only named methods.
    R3'-u: unused imports count only in pytest's attribute-collecting modules.
    Whole files are hashed; unresolved machinery withholds identity. Oracle calls
    hidden in product bodies remain outside scope.
    """
    top = Path(root).resolve()
    checks = list(checks)
    cache = {}

    def bindings_in(body, *, static_conditions=False):
        bindings = {}
        for node in body:
            if (static_conditions and isinstance(node, ast.If) and isinstance(node.test, ast.Constant)
                    and isinstance(node.test.value, bool)):
                branch = node.body if node.test.value else node.orelse
                pairs = list(bindings_in(branch, static_conditions=True).items())
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                pairs = [(node.name, ("definition", node))]
            elif isinstance(node, ast.ImportFrom):
                pairs = [(a.asname or a.name, ("from", (node, a.name))) for a in node.names]
            elif isinstance(node, ast.Import):
                pairs = [(a.asname or a.name.split(".")[0], ("import", a.name if a.asname
                          else a.name.split(".")[0])) for a in node.names]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                pairs = [(t.id, ("value", node.value)) for t in targets if isinstance(t, ast.Name)]
                pairs += [(n.id, ("unknown", None)) for t in targets if not isinstance(t, ast.Name)
                          for n in ast.walk(t) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
            else:
                # Conditional/runtime bindings are deliberately not evaluated.
                pairs = [(n.id, ("unknown", None)) for n in ast.walk(node)
                         if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))]
                pairs += [(a.asname or a.name.split(".")[0], ("unknown", None))
                          for n in ast.walk(node) if isinstance(n, (ast.Import, ast.ImportFrom))
                          for a in n.names]
                pairs += [(n.name, ("unknown", None)) for n in ast.walk(node)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            for name, binding in pairs:
                bindings[name] = ("unknown", None) if name in bindings else binding
        return bindings

    def read(path):
        if isinstance(path, tuple):
            # A class has its own registrations, with module globals for expressions.
            source, node = path
            raw_sha, _, globals_ = read(source)
            return raw_sha, node, {**globals_, **bindings_in(node.body)}
        if path not in cache:
            if len(cache) >= 128:
                raise ValueError("source limit")
            if not path.resolve().is_relative_to(top) or any(
                    p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(top)):
                raise ValueError("source outside regular repository files")
            raw = path.read_bytes()
            tree = ast.parse(raw)
            cache[path] = sha256(raw), tree, bindings_in(tree.body)
        return cache[path]

    def module(path, name, level=0):
        if isinstance(path, tuple):
            path = path[0]
        if level:
            base = path.parent
            # importlib level 1 is the containing package; each extra level goes up once.
            for _ in range(level - 1):
                if base == top or not (base / "__init__.py").is_file():
                    raise ValueError("relative import above package")
                base = base.parent
            if not base.is_relative_to(top) or not (base / "__init__.py").is_file():
                raise ValueError("relative import above repository package")
            bases = [base]
        else:
            bases = [top, top / "src", path.parent]
        found = set()
        for base in bases:
            stem = base.joinpath(*name.split(".")) if name else base
            # Python prefers a regular package over a same-named module.
            for candidate in (stem / "__init__.py", stem.with_suffix(".py")):
                if candidate.is_file():
                    found.add(candidate)
                    break
        if len(found) > 1:
            raise ValueError("ambiguous repository import roots")
        return next(iter(found), None)

    def identity(file):
        files, scanned, active, done = {}, set(), set(), set()
        dependency_scanned = set()
        function_active = set()
        local_active = set()
        local_class_scopes = {}
        receivers = []
        classes = []
        collected_classes = set()
        fixture_audited = set()
        fixture_rebound = False
        plugins, plugin_scanned = [], set()
        collector = top / file
        conftests = []
        for folder in reversed((collector.parent, *collector.parent.parents)):
            if folder.is_relative_to(top) and (folder / "conftest.py").is_file():
                conftests.append(folder / "conftest.py")

        def collection_statements(path, tree, bindings):
            if "*" in bindings or "__getattr__" in bindings or any(
                    name.startswith("pytest_") and name not in {"pytestmark", "pytest_plugins"} for name in bindings):
                raise ValueError("runtime collection machinery")
            for statement in tree.body:
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue  # body-only imports do not establish machinery dependencies
                for node in ast.walk(statement):
                    if isinstance(node, ast.Call) and (
                            isinstance(node.func, ast.Name) and node.func.id in {"exec", "eval", "__import__"}
                            or isinstance(node.func, ast.Attribute) and node.func.attr in {"import_module", "exec_module"}
                            or qualified(path, node.func) in {"importlib.import_module", "builtins.exec",
                                                              "builtins.eval", "builtins.__import__"}):
                        raise ValueError("dynamic collection machinery")

        def load_plugins(path):
            if path in plugin_scanned:
                return
            plugin_scanned.add(path)
            binding = read(path)[2].get("pytest_plugins")
            if binding:
                if binding[0] != "value":
                    raise ValueError("runtime plugin declaration")
                for name in strings(binding[1]):
                    imported = module(path, name)
                    if imported is None:
                        raise ValueError("unresolved plugin source")
                    if imported not in plugins:
                        plugins.append(imported)
                    load_plugins(imported)

        def include(path):
            parsed = read(path)
            files[path] = parsed[0]
            if path in scanned:
                return
            scanned.add(path)
            collection_statements(path, parsed[1], parsed[2])
            load_plugins(path)
            # Package initializers execute on the import chain too.
            for folder in path.parents:
                if folder == top or not folder.is_relative_to(top):
                    break
                initializer = folder / "__init__.py"
                if initializer.is_file() and initializer != path:
                    include(initializer)
            # Module marks apply before function/class arguments are resolved.
            if "pytestmark" in parsed[2]:
                resolve(path, ("pytestmark",), "mark")
            for name in parsed[2]:
                if name.startswith("test_") or name.startswith("Test"):
                    if path in (collector, *conftests, *plugins):
                        resolve(path, (name,), "test")
                elif name == "pytestmark":
                    continue  # fixture names resolve in each test's collecting scope
                elif fixture_options(path, name).get("autouse"):
                    resolve(path, (name,), "fixture")

        def external(name):
            # Known pytest machinery needs no in-repository defining module.
            if name == "pytest" or name.startswith("pytest."):
                return
            raise ValueError("unresolved machinery import")

        def parts_of(node):
            if isinstance(node, ast.Name):
                return (node.id,)
            if isinstance(node, ast.Attribute):
                return (*parts_of(node.value), node.attr)
            raise ValueError("runtime machinery attribute")

        def definition_source(path, parts, seen=frozenset()):
            """Locate a registered object's definition without executing imports."""
            key = path, parts
            if key in seen:
                return None
            seen = seen | {key}
            binding = read(path)[2].get(parts[0])
            if not binding:
                return None
            kind, value = binding
            if kind == "definition" and len(parts) == 1:
                return path, value
            if kind in {"from", "import"}:
                if kind == "from":
                    source, symbol = value
                    imported = module(path, source.module or "", source.level)
                    tail = (symbol, *parts[1:])
                else:
                    imported, tail = module(path, value), parts[1:]
                if imported and tail:
                    if tail[0] not in read(imported)[2] and imported.name == "__init__.py":
                        child = module(imported, tail[0], 1)
                        return definition_source(child, tail[1:], seen) if child and len(tail) > 1 else None
                    return definition_source(imported, tail, seen)
            if kind == "value" and isinstance(value, (ast.Name, ast.Attribute)):
                return definition_source(path, (*parts_of(value), *parts[1:]), seen)
            return None

        def builtin_base(path, node):
            return (isinstance(node, ast.Name) and node.id in {"object", "type"}
                    and node.id not in read(path)[2])

        def class_order(scope, seen=frozenset()):
            # Python's C3 order is static when every base names a definite class.
            if scope in seen:
                raise ValueError("cyclic class machinery")
            source, node = scope
            bases = []
            for base in node.bases:
                if builtin_base(source, base):
                    continue
                target = definition_source(source, parts_of(base))
                if target is None or not isinstance(target[1], ast.ClassDef):
                    raise ValueError("unresolved class base")
                base_path = target[0][0] if isinstance(target[0], tuple) else target[0]
                bases.append((base_path, target[1]))
            pending = [class_order(base, seen | {scope}) for base in bases] + [bases[:]]
            ordered = [scope]
            while any(pending):
                candidate = next((seq[0] for seq in pending if seq
                                  and not any(seq[0] in other[1:] for other in pending)), None)
                if candidate is None:
                    raise ValueError("inconsistent class resolution order")
                ordered.append(candidate)
                pending = [seq[1:] if seq and seq[0] == candidate else seq for seq in pending]
            return ordered

        def class_attribute(scope, parts, role):
            # Look up the class MRO before module globals. A missing/dynamic
            # attribute has no definite source and cannot borrow a global name.
            for current in class_order(scope):
                if parts[0] in bindings_in(current[1].body):
                    resolve(current, parts, role)
                    return
            raise ValueError("unresolved class attribute")

        def fixture(path, name):
            # Union the fixture pytest executes with A'' defining-module chains.
            # Class registrations precede the collector, then nearest conftest first.
            found = False
            class_scopes = [scope for current in classes for scope in class_order(current)]
            for scopes in ((*class_scopes, collector, *reversed(conftests), *reversed(plugins)), (path,)):
                for scope in dict.fromkeys(scopes):
                    bindings = bindings_in(scope[1].body) if isinstance(scope, tuple) else read(scope)[2]
                    matches = [binding for binding in bindings
                               if (options := fixture_options(scope, binding)).get("fixture")
                               and options.get("name", binding) == name]
                    resolved = False
                    for binding in matches:
                        key = scope, (binding,), "fixture", tuple(classes)
                        if key in active:
                            continue  # an overriding fixture can request the next outer fixture
                        resolve(scope, (binding,), "fixture")
                        resolved = True
                    if resolved:
                        found = True
                        break
            if found:
                return
            if name not in {"request", "tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory",
                            "monkeypatch", "capsys", "capsysbinary", "capfd", "capfdbinary", "caplog",
                            "cache", "pytestconfig", "record_property", "record_testsuite_property",
                            "record_xml_attribute", "doctest_namespace", "recwarn"}:
                raise ValueError("unresolved fixture")

        def expression(path, node, role="value", locals_=frozenset()):
            if node is None:
                raise ValueError("runtime machinery binding")
            if isinstance(node, ast.Name):
                if node.id not in locals_:
                    if node.id in read(path)[2]:
                        resolve(path, (node.id,), role)
                    elif node.id not in {"True", "False", "None", "range", "list", "tuple", "dict", "set",
                                         "str", "int", "float", "bool", "len", "enumerate", "zip",
                                         "type", "object", "super", "isinstance"}:
                        raise ValueError("unresolved machinery name")
                return
            if isinstance(node, ast.Attribute):
                parts, current = [], node
                while isinstance(current, ast.Attribute):
                    parts.insert(0, current.attr)
                    current = current.value
                if not isinstance(current, ast.Name):
                    raise ValueError("runtime machinery attribute")
                resolve(path, (current.id, *parts), role)
                return
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in {"getattr", "__import__", "eval", "exec"}:
                    raise ValueError("dynamic machinery")
                if isinstance(node.func, ast.Attribute) and node.func.attr in {"import_module", "exec_module"}:
                    raise ValueError("dynamic machinery import")
                if (role != "mark" or classes) and qualified(path, node.func) == "pytest.mark.usefixtures":
                    # Module marks are first read before collecting classes exist.
                    # Bind their fixtures in each test/class scope; inherited class
                    # marks can resolve immediately with the active collecting MRO.
                    for argument in node.args:
                        for name in strings(argument):
                            fixture(path, name)
                expression(path, node.func, "decorator_factory" if role == "decorator" else "call", locals_)
                for argument in (*node.args, *(k.value for k in node.keywords)):
                    expression(path, argument, "value", locals_)
                return
            if isinstance(node, ast.Lambda):
                for default in (*node.args.defaults, *node.args.kw_defaults):
                    if default is not None:
                        expression(path, default, locals_=locals_)
                names = {a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
                expression(path, node.body, locals_=locals_ | names)
                return
            for child in ast.iter_child_nodes(node):
                expression(path, child, role, locals_=locals_)

        def strings(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return [part.strip() for part in node.value.split(",") if part.strip()]
            if isinstance(node, (ast.List, ast.Tuple)):
                return [s for item in node.elts for s in strings(item)]
            raise ValueError("runtime fixture or parameter names")

        def qualified(path, node, seen=frozenset()):
            """Recognize pytest decorator aliases without evaluating them."""
            try:
                parts = parts_of(node)
            except ValueError:
                return ""
            key = path, parts
            if key in seen:
                return ""
            seen = seen | {key}
            binding = read(path)[2].get(parts[0])
            if not binding:
                return ""
            kind, value = binding
            if kind in {"import", "from"}:
                if kind == "from":
                    source, name = value
                    imported = module(path, source.module or "", source.level)
                    tail = (name, *parts[1:])
                    prefix = source.module or ""
                    if imported is None and source.level:
                        return ""
                else:
                    imported, tail, prefix = module(path, value), parts[1:], value
                if imported is None:
                    return ".".join((prefix, *tail))
                if tail:
                    if tail[0] not in read(imported)[2] and imported.name == "__init__.py":
                        imported = module(imported, tail[0], 1)
                        tail = tail[1:]
                    if imported is not None and tail:
                        target = ast.Name(id=tail[0])
                        for part in tail[1:]:
                            target = ast.Attribute(value=target, attr=part)
                        return qualified(imported, target, seen)
            if kind == "value" and isinstance(value, (ast.Name, ast.Attribute)):
                target = value
                for part in parts[1:]:
                    target = ast.Attribute(value=target, attr=part)
                return qualified(path, target, seen)
            return ""

        def dependencies(path, node, locals_=frozenset(), nested=None, role="call", wrapper_return=False):
            """Read collection-time dependencies, never execute candidate code.

            Callable references promote bodies, except recognised fixtures and
            inner test wrappers returned by an applied decorator (R3'/R5').
            """
            nested = nested or {}
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Constant)
                    and isinstance(node.test.value, bool)):
                for statement in node.body if node.test.value else node.orelse:
                    dependencies(path, statement, locals_, nested, role)
                return
            if isinstance(node, ast.Return):
                if node.value is not None:
                    dependencies(path, node.value, locals_, nested, role, role == "decorator")
                return
            if isinstance(node, ast.Lambda):
                for default in (*node.args.defaults, *node.args.kw_defaults):
                    if default is not None:
                        dependencies(path, default, locals_, nested, role)
                if wrapper_return:
                    return  # the returned lambda is the test wrapper; defaults still run now
                names = {a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
                names |= {a.arg for a in (node.args.vararg, node.args.kwarg) if a}
                dependencies(path, node.body, locals_ | names, nested, role)
                return
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                raise ValueError("unresolved collection scope mutation")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
                    raise ValueError("unresolved star import")
                # Function-local imports need lexical binding resolution that the
                # module table cannot provide. Refuse rather than omit a source.
                if locals_:
                    raise ValueError("unresolved collection-local import")
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    expression(path, decorator, "decorator", locals_)
                annotations = [a.annotation for a in (*node.args.posonlyargs, *node.args.args,
                                                       *node.args.kwonlyargs)]
                annotations += [a.annotation for a in (node.args.vararg, node.args.kwarg) if a]
                for header in (*node.args.defaults, *node.args.kw_defaults, *annotations, node.returns):
                    if header is not None:
                        dependencies(path, header, locals_, nested, role)
                return
            if isinstance(node, ast.ClassDef):
                scope = (path[0] if isinstance(path, tuple) else path, node)
                collecting = scope in collected_classes
                if collecting:
                    classes.append(scope)
                try:
                    for decorator in node.decorator_list:
                        expression(path, decorator, "decorator", locals_)
                    for base in node.bases:
                        expression(path, base, "base", locals_)
                    for keyword in node.keywords:
                        expression(path, keyword.value, "metaclass" if keyword.arg == "metaclass" else "value")
                    for statement in node.body:
                        dependencies(scope, statement, locals_, nested, role)
                    unused_imports(scope, node, bindings_in(node.body))
                finally:
                    if collecting:
                        classes.pop()
                return
            if isinstance(node, ast.Call):
                target = node.func
                try:
                    parts = parts_of(target)
                except ValueError:
                    parts = ()
                receiver = next((scope for name, scope in reversed(receivers) if len(parts) > 1 and parts[0] == name), None)
                if receiver is not None:
                    class_attribute(receiver, parts[1:], "call")
                elif isinstance(target, ast.Name) and target.id in nested:
                    local_reference(path, target.id, locals_, nested, "call")
                else:
                    if parts and parts[0] not in locals_ and parts[0] in read(path)[2]:
                        resolve(path, parts, "call")
                    elif parts and parts[0] in locals_:
                        raise ValueError("unresolved collection callable")
                if (isinstance(target, ast.Name) and target.id in {
                        "exec", "eval", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars", "compile"}
                        or isinstance(target, ast.Attribute) and target.attr in {"import_module", "exec_module"}):
                    raise ValueError("dynamic collection machinery")
            if isinstance(node, (ast.Name, ast.Attribute)):
                try:
                    parts = parts_of(node)
                except ValueError:
                    # The inherited protocol walk covers super's implicit
                    # receiver. Other computed receivers have no definite type.
                    if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Call)
                            and isinstance(node.value.func, ast.Name) and node.value.func.id == "super"
                            and node.attr.startswith("__") and node.attr.endswith("__")):
                        raise ValueError("unresolved collection attribute receiver") from None
                    parts = ()
                receiver = next((scope for name, scope in reversed(receivers) if parts and parts[0] == name), None)
                if receiver is not None and len(parts) > 1 and isinstance(node.ctx, ast.Load):
                    class_attribute(receiver, parts[1:], "value")
                    return
                if isinstance(node, ast.Attribute) and parts and parts[0] in locals_:
                    if isinstance(node.ctx, ast.Load):
                        raise ValueError("unresolved collection-local attribute")
                    return
                if parts and parts[0] in nested and isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if not (wrapper_return and isinstance(nested[parts[0]], (ast.FunctionDef, ast.AsyncFunctionDef))):
                        local_reference(path, parts[0], locals_, nested,
                                        "decorator" if role == "decorator_factory" else "call")
                    return
                if parts and parts[0] not in locals_ and parts[0] in read(path)[2]:
                    if not isinstance(node, ast.Name) or isinstance(node.ctx, ast.Load):
                        resolve(path, parts, "value")
                    return
                if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                        and node.id not in locals_ and not hasattr(builtins, node.id)):
                    raise ValueError("unresolved collection reference")
            for child in ast.iter_child_nodes(node):
                # A call in a returned expression still executes at collection;
                # only references to the returned wrapper itself are deferred.
                dependencies(path, child, locals_, nested, role, wrapper_return and not isinstance(node, ast.Call))

        def local_reference(path, name, locals_, nested, role):
            key = path, name, nested[name]
            if key in local_active:
                raise ValueError("cyclic collection-local reference")
            local_active.add(key)
            try:
                value = nested[name]
                if value is None:
                    raise ValueError("unresolved collection-local reference")
                if isinstance(value, ast.ClassDef):
                    # A closure base needs a lexical class scope we cannot yet
                    # resolve. Never mistake a local object/type for a builtin.
                    headers = [*value.bases, *value.decorator_list, *(k.value for k in value.keywords)]
                    for member in value.body:
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            headers.extend((*member.decorator_list, *member.args.defaults,
                                            *(d for d in member.args.kw_defaults if d is not None)))
                    if any(name_loads(header) & locals_ for header in headers):
                        raise ValueError("unresolved collection-local class header")
                    source = path[0] if isinstance(path, tuple) else path
                    scope = source, value
                    local_class_scopes[scope] = locals_, nested
                    try:
                        definition(path, value, role)
                    finally:
                        local_class_scopes.pop(scope)
                elif isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not recognised_fixture(path, value, locals_):
                        collection_function(path, value, role, locals_, nested)
                else:
                    dependencies(path, value, locals_, nested, role)
            finally:
                local_active.remove(key)

        def collection_function(path, node, role, outer=frozenset(), nested=None):
            key = path, node
            if key in function_active:
                raise ValueError("cyclic collection call")
            function_active.add(key)
            arguments = [*node.args.posonlyargs, *node.args.args]
            receiver = isinstance(path, tuple) and node in path[1].body and bool(arguments)
            if receiver:
                receivers.append((arguments[0].arg, path))
            try:
                if path in local_class_scopes:
                    closure, captured = local_class_scopes[path]
                    outer = outer | closure
                    nested = {**captured, **(nested or {})}
                local = bindings_in(node.body, static_conditions=True)
                # Retain unknown locals as explicit gaps, also shadowing outer
                # bindings. Missing/conditional bodies must never vanish (F3').
                nested = {**(nested or {}), **{name: value if kind in {"definition", "value"} else None
                                              for name, (kind, value) in local.items()}}
                names = outer | set(local) | {a.arg for a in (*node.args.posonlyargs, *node.args.args,
                                                            *node.args.kwonlyargs)}
                names |= {a.arg for a in (node.args.vararg, node.args.kwarg) if a}
                for statement in node.body:
                    dependencies(path, statement, names, nested, role)
            finally:
                if receiver:
                    receivers.pop()
                function_active.remove(key)

        def name_loads(node, bound=frozenset()):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                body = [node.body] if isinstance(node, ast.Lambda) else node.body
                headers = [*node.args.defaults, *node.args.kw_defaults]
                if not isinstance(node, ast.Lambda):
                    headers += node.decorator_list
                used = set().union(*(name_loads(h, bound) for h in headers if h is not None))
                names = {a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
                names |= {a.arg for a in (node.args.vararg, node.args.kwarg) if a}
                names |= set(bindings_in(body))
                names -= {name for n in body if isinstance(n, ast.Global) for name in n.names}
                return used | set().union(*(name_loads(n, bound | names) for n in body))
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                return {node.id} - bound
            return set().union(*(name_loads(child, bound) for child in ast.iter_child_nodes(node)))

        def unused_imports(path, tree, bindings):
            owner = path[0] if isinstance(path, tuple) else path
            if owner in (collector, *conftests, *plugins) or path in collected_classes:
                # Pytest inspects these module attributes even without a lexical
                # use, including inherited class registrations (F2). Defining
                # modules themselves do not get this exception (R3'-u).
                used = set().union(*(name_loads(n) for n in tree.body))
                for name, (kind, value) in bindings.items():
                    if kind not in {"from", "import"} or name in used:
                        continue
                    source = module(path, value[0].module or "", value[0].level) if kind == "from" else module(path, value)
                    if source is not None:
                        resolve(path, (name,), "test" if name.startswith(("test_", "Test")) else "value")

        def scan_dependencies(path):
            _, tree, bindings = read(path)
            for statement in tree.body:
                dependencies(path, statement)
            unused_imports(path, tree, bindings)

        def fixture_decorator(path, node, locals_=frozenset()):
            if qualified(path, node) != "pytest.fixture" or parts_of(node)[0] in locals_:
                return False
            # Imports can mutate pytest before a decorator is applied, including
            # unused imports in defining modules. Audit their source without
            # treating test-time product bodies as identity dependencies. Any
            # possible fixture-attribute write defeats the exemption; do not
            # infer execution order or try to prove a mutation harmless.
            nonlocal fixture_rebound
            owner = path[0] if isinstance(path, tuple) else path
            pending = [collector, *conftests, *plugins, owner]
            while pending:
                source = pending.pop()
                if source in fixture_audited:
                    continue
                fixture_audited.add(source)
                tree = read(source)[1]
                if any(isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del))
                       and (n.attr == "fixture" or qualified(source, n).startswith("pytest.fixture."))
                       or isinstance(n, ast.Attribute) and n.attr == "__dict__"
                       and qualified(source, n) == "pytest.__dict__"
                       or isinstance(n, ast.Subscript) and isinstance(n.ctx, (ast.Store, ast.Del))
                       and isinstance(n.slice, ast.Constant) and n.slice.value == "fixture"
                       for n in ast.walk(tree)):
                    fixture_rebound = True
                statements = list(tree.body)
                while statements:
                    statement = statements.pop()
                    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        continue
                    if isinstance(statement, ast.ImportFrom):
                        imported = module(source, statement.module or "", statement.level)
                        if imported:
                            pending.append(imported)
                            if imported.name == "__init__.py":
                                for alias in statement.names:
                                    child = module(imported, alias.name, 1)
                                    if child:
                                        pending.append(child)
                    elif isinstance(statement, ast.Import):
                        for alias in statement.names:
                            imported = module(source, alias.name)
                            if imported:
                                pending.append(imported)
                    statements.extend(ast.iter_child_nodes(statement))
                # Package initializers execute before the imported module too.
                for folder in source.parents:
                    if folder == top or not folder.is_relative_to(top):
                        break
                    initializer = folder / "__init__.py"
                    if initializer.is_file():
                        pending.append(initializer)
            return not fixture_rebound

        def recognised_fixture(path, node, locals_=frozenset()):
            return any(fixture_decorator(path, d.func if isinstance(d, ast.Call) else d, locals_)
                       for d in node.decorator_list)

        def fixture_options(path, name):
            """Discover explicit fixture registration through static re-exports."""
            target = definition_source(path, (name,))
            if target and isinstance(target[1], (ast.FunctionDef, ast.AsyncFunctionDef)):
                path, value = target
                for decorator in value.decorator_list:
                    target = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if fixture_decorator(path, target):
                        options = {"fixture": True}
                        for keyword in decorator.keywords if isinstance(decorator, ast.Call) else ():
                            if keyword.arg in {"name", "autouse"}:
                                if not isinstance(keyword.value, ast.Constant):
                                    raise ValueError("runtime fixture registration")
                                options[keyword.arg] = keyword.value.value
                        return options
            return {}

        def mark_names(path, node, seen=frozenset()):
            if isinstance(node, ast.Name) and (path, node.id) not in seen:
                binding = read(path)[2].get(node.id)
                seen = seen | {(path, node.id)}
                if binding and binding[0] == "value":
                    return mark_names(path, binding[1], seen)
                if binding and binding[0] == "from":
                    source, symbol = binding[1]
                    imported = module(path, source.module or "", source.level)
                    if imported:
                        return mark_names(imported, ast.Name(id=symbol), seen)
            direct = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and qualified(path, child.func) == "pytest.mark.parametrize":
                    keywords = {k.arg: k.value for k in child.keywords}
                    argnames = child.args[0] if child.args else keywords.get("argnames")
                    argvalues = child.args[1] if len(child.args) > 1 else keywords.get("argvalues")
                    if argnames is None or argvalues is None:
                        raise ValueError("unresolved parametrization")
                    names = set(strings(argnames))
                    indirect = next((k.value for k in child.keywords if k.arg == "indirect"), ast.Constant(False))
                    if isinstance(indirect, ast.Constant) and isinstance(indirect.value, bool):
                        requested = names if indirect.value else set()
                    else:
                        requested = set(strings(indirect))
                    direct.update(names - requested)
                    for name in requested:
                        fixture(path, name)
            return direct

        def body_fixtures(path, node):
            # Inspect fixture access only; ordinary body imports remain product code.
            parents = {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) and child.value.id == "request":
                    if child.attr == "param":
                        continue
                    parent = parents.get(child)
                    if child.attr != "getfixturevalue" or not isinstance(parent, ast.Call) or parent.func is not child:
                        raise ValueError("unresolved fixture access")
                    if (len(parent.args) != 1 or parent.keywords or not isinstance(parent.args[0], ast.Constant)
                            or not isinstance(parent.args[0].value, str)):
                        raise ValueError("dynamic fixture access")
                    fixture(path, parent.args[0].value)
                elif (isinstance(child, ast.Name) and child.id == "request" and isinstance(child.ctx, ast.Load)
                      and not isinstance(parents.get(child), ast.Attribute)):
                    raise ValueError("escaped fixture request")
                elif isinstance(child, ast.Attribute) and child.attr == "getfixturevalue":
                    if not isinstance(child.value, ast.Name) or child.value.id != "request":
                        raise ValueError("unresolved fixture request receiver")

        def definition(path, node, role, inherited=frozenset()):
            if isinstance(node, ast.ClassDef):
                scope = (path[0] if isinstance(path, tuple) else path, node)
                if role == "test":
                    collected_classes.add(scope)
                classes.append(scope)
                try:
                    inspect_definition(path, node, role, inherited)
                finally:
                    classes.pop()
            else:
                inspect_definition(path, node, role, inherited)

        def inspect_definition(path, node, role, inherited):
            if isinstance(node, ast.ClassDef) and role in {"value", "mark", "decorator", "decorator_factory"}:
                role = "call"  # R5 class references may be invoked; apply R6's protocol boundary.
            direct = set(inherited)
            mark = read(path)[2].get("pytestmark") if role == "test" else None
            if mark:
                expression(path, ast.Name(id="pytestmark"))
                direct.update(mark_names(path, ast.Name(id="pytestmark")))
            for decorator in node.decorator_list:
                expression(path, decorator, "decorator")
                direct.update(mark_names(path, decorator))
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if not builtin_base(path, base):
                        expression(path, base, "test" if role == "test" else
                                   role if role in {"call", "metaclass", "base"} else "value")
                        if role == "test":
                            expression(path, base, "base")
                for keyword in node.keywords:
                    expression(path, keyword.value, "metaclass" if keyword.arg == "metaclass" else "value")
                scope = classes[-1]
                local = bindings_in(node.body)
                collection_statements(scope, node, local)
                if "pytest_plugins" in local:
                    raise ValueError("runtime class machinery")
                if "pytestmark" in local:
                    resolve(scope, ("pytestmark",), "mark")
                    mark = local["pytestmark"]
                    if mark[0] == "value":
                        direct.update(mark_names(scope, mark[1]))
                for name in local:
                    if (role == "metaclass" or role == "call" and name.startswith("__") and name.endswith("__")
                            or role == "base" and name == "__init_subclass__"
                            ) and local[name][0] == "definition":
                        definition(scope, local[name][1], "call")
                    elif fixture_options(scope, name).get("autouse"):
                        resolve(scope, (name,), "fixture")
                    elif name.startswith("test_") or name.startswith("Test"):
                        binding = local[name]
                        if binding[0] == "definition":
                            definition(scope, binding[1], "test", direct)
                        else:
                            resolve(scope, (name,), "test")
                return
            for default in (*node.args.defaults, *node.args.kw_defaults):
                if default is not None:
                    expression(path, default)
            if role not in {"test", "fixture"} and not recognised_fixture(path, node):
                collection_function(path, node, role)
            if role in {"test", "fixture"}:
                for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                    if arg.arg not in direct | {"self", "cls"}:
                        fixture(path, arg.arg)
                body_fixtures(path, node)

        def resolve(path, parts, role):
            key = path, parts, role, tuple(classes)
            if key in active:
                raise ValueError("cyclic machinery binding")
            if key in done:
                return
            active.add(key)
            binding = read(path)[2].get(parts[0])
            if binding is None:
                raise ValueError("missing machinery definition")
            kind, value = binding
            if kind in {"from", "import"}:
                if kind == "from":
                    node, symbol = value
                    name, level = node.module or "", node.level
                    tail = (symbol, *parts[1:])
                else:
                    name, level, tail = value, 0, parts[1:]
                imported = module(path, name, level)
                if imported is None:
                    if level:
                        raise ValueError("missing relative machinery")
                    external(name)
                else:
                    include(imported)
                    if tail:
                        if tail[0] not in read(imported)[2] and imported.name == "__init__.py":
                            child = module(imported, tail[0], 1)
                            if child is None:
                                raise ValueError("missing package machinery")
                            include(child)
                            if len(tail) > 1:
                                resolve(child, tail[1:], role)
                        else:
                            resolve(imported, tail, role)
                    elif role in {"test", "fixture"}:
                        raise ValueError("module is not a test or fixture definition")
            elif kind == "definition":
                if len(parts) == 1:
                    definition(path, value, role)
                elif isinstance(value, ast.ClassDef):
                    source = path[0] if isinstance(path, tuple) else path
                    class_attribute((source, value), parts[1:], role)
                else:
                    raise ValueError("unresolved function attribute")
            elif kind == "value":
                if len(parts) > 1:
                    if isinstance(value, (ast.Name, ast.Attribute)):
                        resolve(path, (*parts_of(value), *parts[1:]), role)
                    elif isinstance(value, ast.Call):
                        target = definition_source(path, parts_of(value.func))
                        if target is None or not isinstance(target[1], ast.ClassDef):
                            raise ValueError("unresolved constructed instance")
                        expression(path, value)
                        source = target[0][0] if isinstance(target[0], tuple) else target[0]
                        class_attribute((source, target[1]), parts[1:], role)
                    else:
                        raise ValueError("unresolved value attribute")
                    active.remove(key)
                    done.add(key)
                    return
                if value is None or any(
                        isinstance(n, (ast.Await, ast.NamedExpr)) or isinstance(n, ast.Call)
                        and not (parts[0] == "pytestmark" and qualified(path, n.func).startswith("pytest.mark."))
                        for n in ast.walk(value)):
                    raise ValueError("runtime machinery value")
                if role in {"test", "fixture"} and not isinstance(value, (ast.Name, ast.Attribute)):
                    raise ValueError("runtime test or fixture")
                expression(path, value, role)
            else:
                raise ValueError("runtime machinery binding")
            active.remove(key)
            done.add(key)

        try:
            for path in (*conftests, collector):
                load_plugins(path)
            include(collector)
            for path in conftests:
                include(path)
            for path in plugins:
                include(path)
            # Resolve object roles first, then inspect all import/class-time code.
            # Newly reached files join the same pass until there is no new source.
            while pending := set(files) - dependency_scanned:
                for path in sorted(pending):
                    dependency_scanned.add(path)
                    scan_dependencies(path)
            if len(files) == 1:
                return next(iter(files.values()))
            return digest(sorted((p.relative_to(top).as_posix(), sha) for p, sha in files.items()))
        except (OSError, SyntaxError, ValueError, RecursionError):
            return ""

    by_file = {n.partition("::")[0]: None for n in checks}
    by_file = {file: identity(file) for file in by_file}
    return {n: by_file[n.partition("::")[0]] for n in checks}


def outcome_sources(statuses: Mapping, sources: Mapping) -> dict:
    """Flat bounded pairs fit the record's data-only scalar/list schema."""
    names = sorted(n for n, v in statuses.items() if v in ("passed", "failed"))[:50]
    return {"check_sources_sha256": [sha256(n) + ":" + sources[n] for n in names if sources.get(n)],
            "source_incomplete_sha256": [sha256(n) for n in names if n in sources and not sources[n]],
            "identities_incomplete": len(statuses) > len(names) or any(n in sources and not sources[n] for n in names),
            "identities_omitted": len(statuses) - len(names),
            "failed_identities_omitted": sum(statuses[n] == "failed" for n in sorted(statuses)[50:])}


def executed(run: Mapping) -> int:
    """Actual terminal outcomes, never the number of selected files or node ids."""
    if run.get("loaded_complete") is False:
        return 0
    if "executed_tests" in run:
        return max(0, int(run["executed_tests"]))
    if "passed" in run or "failed" in run:
        return max(0, int(run.get("passed") or 0)) + max(0, int(run.get("failed") or 0))
    # Legacy outcome maps, unlike a list of selections, describe observations.
    tests = run.get("tests")
    return sum(v in (True, False, "passed", "failed") for v in tests.values()) if isinstance(tests, Mapping) else 0


def repair_blocked(assessment: Mapping) -> bool:
    """An integrity problem or a weakening detection cannot supply a repair pass."""
    return bool(assessment.get("outcome") == "test_integrity_compromised" or assessment.get("integrity_reasons")
                or assessment.get("loaded_complete") is False
                or any(r.get("stage") == "integrity" or "weaken" in str(r.get("code", ""))
                       for r in assessment.get("reasons") or []))


def review_data(assessment: Mapping, repo=None) -> dict:
    """Counts, model names and check digests only; no repository text."""
    scope = assessment.get("scope") or {}
    completion = assessment.get("completion") or {}
    defects = completion.get("defects_reproduced") or {}
    runs = [r for r in scope.get("checks_run") or [] if r.get("check") != "stated checks at the base commit"]
    requirements = assessment.get("requirements") or []
    from g6.workforce.repo_tests import report_covers

    loading_rejected = {str(check["id"]) for req in requirements for check in req.get("checks") or []
                        if isinstance(check, dict) and check.get("id") and check.get("verdict") == "changed_code_not_loaded"}
    loading_unknown = any(r.get("loaded_complete") is False for r in runs) or any(
        r.get("code") == "changed_code_not_loaded" for r in assessment.get("reasons") or [])
    # A file's withheld loading evidence also withholds its expanded node passes.
    # Work on detached run copies: the producer's decided reply stays untouched.
    if loading_rejected or loading_unknown:
        kept = []
        for run in runs:
            outcomes = {n: v for n, v in (run.get("executed_outcomes") or {}).items()
                        if v != "passed" or not (loading_unknown or any(report_covers(c, n) for c in loading_rejected))}
            withheld = loading_unknown or any(report_covers(c, n) or report_covers(n, c)
                                              for c in loading_rejected for n in run.get("tests") or [])
            kept.append({**run, "executed_outcomes": outcomes,
                         "executed_tests": len(outcomes) if "executed_outcomes" in run else 0 if withheld else executed(run)})
        runs = kept
    # Only the stated checks' own terminal status counts. Base runs and a skipped,
    # absent or renamed check cannot supply the pass for an earlier failure.
    statuses = {}
    for requirement in requirements:
        for check in requirement.get("checks") or []:
            if isinstance(check, dict) and check.get("id") and check.get("observation") is None:
                verdict = check.get("verdict")
                if verdict == "passed" and (loading_unknown or any(
                        report_covers(c, str(check["id"])) for c in loading_rejected)):
                    verdict = "changed_code_not_loaded"
                statuses.setdefault(str(check["id"]), []).append(verdict)
    sources, executed_names, expanded_selections = {}, set(), set()
    withheld_sources = set()
    has_capture = False
    for run in runs:
        for name, source in (run.get("check_sources") or {}).items():
            if not source or name in sources and sources[name] != source:
                withheld_sources.add(name)
            sources[name] = source
        if "check_sources" in run:
            has_capture = True
        if run.get("executed_outcomes"):
            expanded_selections.update(run.get("tests") or [])
        for name, verdict in (run.get("executed_outcomes") or {}).items():
            statuses.setdefault(name, []).append(verdict)
            executed_names.add(name)
            if "check_sources" in run and name not in (run.get("check_sources") or {}):
                if any(report_covers(c, name) for c in run.get("tests") or []):
                    # Preserve the existing refusal of malformed captures within
                    # their declared scope; do not manufacture bytes after a run.
                    raise ValueError("executed check source identity was not captured")
                withheld_sources.add(name)
    # A run selection is not an additional test beside its observed nodes, even
    # when collection remaps those nodes outside the selection's textual scope.
    replaced_selections = (set(statuses) & expanded_selections) - executed_names
    statuses = {name: values for name, values in statuses.items() if name in executed_names
                or name not in replaced_selections and not any(report_covers(name, node) for node in executed_names)}
    terminal = {n: "failed" if "failed" in v else "passed" if v and all(x == "passed" for x in v) else "unknown"
                for n, v in statuses.items()}
    terminal = {n: terminal[n] for n in sorted(terminal) if terminal[n] in ("passed", "failed")}
    if any("check_sources" in run and not run.get("executed_outcomes")
           and n not in (run.get("check_sources") or {})
           and any(report_covers(c, n) for c in run.get("tests") or []) for run in runs for n in terminal):
        raise ValueError("executed check source identity was not captured")
    if has_capture:
        withheld_sources.update(n for n in terminal if n not in sources)
    sources.update(dict.fromkeys(withheld_sources, ""))
    retained = dict(list(terminal.items())[:50])
    failed = sorted(sha256(n) for n, v in retained.items() if v == "failed")
    passed = sorted(sha256(n) for n, v in retained.items() if v == "passed")
    activities = completion.get("activities") or []
    proposer = next((str(a["model"])[:100] for a in activities if a.get("model") and a.get("model_calls")), "")
    data = {"requirements": len(requirements),
            "not_established": _count(assessment.get("requirements_without_adequate_check")),
            # Legacy aggregate-only observations keep their documented lower bound.
            # Once outcomes name the checks, selections are no longer a counter.
            "reproduced": 0 if terminal else _count(defects.get("stated_and_repository_checks")),
            "unconfirmed": _count(defects.get("proposed_checks")),
            "integrity_reasons": sum(r.get("stage") == "integrity" for r in assessment.get("reasons") or []),
            "checks_run": sum(executed(r) > 0 for r in runs), "checks_skipped": _count(scope.get("checks_skipped")),
            "failed_sha256": failed[:50], "passed_sha256": passed[:50],
            "questions": _count(completion.get("open_questions")), "unknowns": _count(assessment.get("unknowns")),
            "model_calls": int((scope.get("search") or {}).get("model_calls") or 0),
            "proposer": proposer,
            "person_checks": _count((assessment.get("confirmed_expectations") or {}).get("expectations")),
            "isolation": str(((scope.get("environment") or {}).get("isolation") or {}).get("backend") or "")[:100]}
    data.update(outcome_sources(terminal, sources))
    # Empty selection captures still explain the warning, not another finding.
    selection_gaps = {sha256(n) for n in replaced_selections if n in sources and not sources[n]}
    if selection_gaps:
        data["source_incomplete_sha256"] = sorted(set(data["source_incomplete_sha256"]) | selection_gaps)[:50]
        data["identities_incomplete"] = True
    data["reproduced"] = _reproduced_count(failed, [data])
    data["repair_blocked"] = repair_blocked(assessment)
    data["review_returned"] = any(a.get("model_calls") and a.get("result") == "replied" for a in activities)
    if not data["checks_run"] and not data["review_returned"]:
        data["unchecked_reason"] = (
            "changed_code_not_loaded: loading of changed code was not established"
            if loading_unknown or loading_rejected else
            next((str(r.get("verdict")) for r in runs if r.get("verdict")), "check_not_recorded")[:100])
    for tier, name in ((1, "stated_checks"), (2, "repository_checks"), (3, "deeper_checks")):
        data[name] = sum(executed(r) for r in runs if r.get("tier") == tier)
    return data


def bundle(events: Iterable[Event], *, status: str = "kept") -> dict:
    rows = list(events) if status == "kept" else []
    return {"status": status, "events": [e.to_body() for e in rows],
            "justification": [{"event_id": e.event_id, "kind": e.kind, "sha256": digest(e.to_body())} for e in rows]}


def read(repo, task: str, *, note: str = "", origin_kind: str = "", origin_id: str = "") -> dict:
    """Read only this task's stored events; off and failed writes cite nothing."""
    if not policy.enabled()[0]:
        return bundle([], status="off")
    if note:
        return bundle([], status="write_failed")
    try:
        from g6.core import cloud_consent as cloud
        from g6.harness.workflow.adapters import identities
        from g6.harness.workflow.record import project_id, workflow_source

        # A local record being on grants no disclosure. Jobs and nested loop
        # captures retain the enclosing client's identity through their parent.
        capture = cloud.current_capture()
        while capture is not None and capture.client is None:
            capture = capture.parent
        if capture is not None and not cloud.is_granted(workflow_source(project_id(repo)), capture.client[0]):
            return bundle([], status="withheld")

        project, task_id = identities(repo, task)
        record = ProjectRecord(project)
        rows = record.events(task=task_id)
        held = bundle([*rows, *record.annotations(task_id)])
        if origin_kind or origin_id:
            runs = {e.run for e in rows if e.kind == "run_started" and e.data.get("origin_kind") == origin_kind
                    and e.data.get("origin_sha256") == sha256(origin_id)}
            held["selected_run"] = next(iter(runs)) if len(runs) == 1 else ""
        return held
    except Exception:  # noqa: BLE001 - missing custody never manufactures evidence
        return bundle([], status="unavailable")


def matched(held: Mapping) -> list[dict]:
    """Only bodies whose ids, kinds and digests match the supplied citations."""
    refs = {(r.get("event_id"), r.get("kind"), r.get("sha256")) for r in held.get("justification") or []}
    return [body for body in held.get("events") or []
            if (body.get("event_id"), body.get("kind"), digest(body)) in refs]


def check_ran(review: Mapping) -> bool:
    """A test result or a returned reviewer, never an attempted pre-call refusal."""
    return (any(value in ("passed", "failed") for value in (review.get("results") or {}).values())
            or bool(review.get("review_returned"))
            or "review_returned" not in review and review.get("verdict") in ("none", "defect", "clean"))


def checked(events: Iterable[Event]) -> bool:
    """The common coverage rule; aggregate review attempts alone establish nothing."""
    return any((e.kind == "loop_round" and bool(e.data.get("checked")))
               or (e.kind == "loop_ended" and e.data.get("probes", 0) > 0)
               or (e.kind == "review_finished" and (e.data.get("checks_run", 0) > 0
                                                   or bool(e.data.get("review_returned")))) for e in events)


def check_state(events: Iterable[Event]) -> dict[str, set[tuple[str, str]]]:
    """Same task/check, failed then passed on different bytes. No report-ending inference.

    Explicit loop round numbers establish order even within one wall-clock second.
    Separate review jobs need strictly ordered finish times; run-start order is irrelevant.
    """
    rows = [e for e in events if e.kind in ("loop_round", "review_finished")]
    failures: dict[tuple[str, str], list[Event]] = {}
    passes: dict[tuple[str, str], list[Event]] = {}
    for e in rows:
        for name in e.data.get("failed_sha256", e.data.get("open_sha256")) or []:
            failures.setdefault((e.task, name), []).append(e)
        for name in e.data.get("passed_sha256") or []:
            passes.setdefault((e.task, name), []).append(e)

    def later(failed: Event, passed: Event) -> bool:
        if failed.kind == passed.kind == "loop_round" and failed.run == passed.run:
            return 0 < failed.data.get("round", 0) < passed.data.get("round", 0)
        return failed.at < passed.at

    def source(event: Event, check: str) -> str:
        return next((p.partition(":")[2] for p in event.data.get("check_sources_sha256") or []
                     if p.partition(":")[0] == check), "")

    def resolved(failed: Event, passed: Event, check: str) -> bool:
        before, after = source(failed, check), source(passed, check)
        same_source = before == after and (not before or re.fullmatch(r"[0-9a-f]{64}", before) is not None)
        return bool(failed.artifact_sha256 and passed.artifact_sha256
                    and failed.artifact_sha256 != passed.artifact_sha256 and later(failed, passed)
                    and same_source and not passed.data.get("repair_blocked")
                    and check not in (failed.data.get("source_incomplete_sha256") or [])
                    and check not in (passed.data.get("source_incomplete_sha256") or [])
                    and passed.data.get("outcome") != "test_integrity_compromised"
                    and not passed.data.get("integrity_reasons"))

    repaired = {key for key, failed in failures.items()
                if any(resolved(f, p, key[1]) for f in failed for p in passes.get(key, ()))}
    still = {key for key, failed in failures.items()
             if any(not any(resolved(f, p, key[1]) for p in passes.get(key, ())) for f in failed)}
    return {"reproduced": set(failures), "repaired": repaired, "open": still}


def _reproduced_count(failures: Iterable, observations: Iterable[Mapping]) -> int:
    """One reproduction lower bound for producers and retained-event projections."""
    rows = list(observations)
    anonymous = max((d.get("reproduced", 0) for d in rows
                     if not (d.get("failed_sha256", d.get("open_sha256")) or [])
                     and not d.get("passed_sha256")), default=0)
    omitted = max((len(d.get("failed_sha256") or []) + d["failed_identities_omitted"] for d in rows
                   if d.get("failed_identities_omitted", 0)), default=0)
    return max(len(set(failures)), anonymous, omitted)


def check_counts(events: Iterable[Event]) -> dict:
    """Retained identities plus unresolved omitted failures; never invent a pass.

    Omitted identities cannot be matched across observations. Counts are lower
    bounds: a later retained identity may overlap an earlier omitted one.
    """
    rows = list(events)
    state = check_state(rows)
    totals = {key: len(state[key]) for key in ("reproduced", "repaired", "open")}
    for task in {e.task for e in rows}:
        # Legacy aggregate observations have no check to match with a later pass.
        # Retain their open lower bound here, so every projection uses this rule.
        anonymous = max((e.data.get("reproduced", 0) for e in rows if e.task == task
                         and e.kind in ("review_finished", "loop_round")
                         and not (e.data.get("failed_sha256", e.data.get("open_sha256")) or [])
                         and not e.data.get("passed_sha256")), default=0)
        known = {key: sum(t == task for t, _ in state[key]) for key in totals}
        observations = [e.data for e in rows if e.task == task and e.kind in ("review_finished", "loop_round")]
        totals["reproduced"] += _reproduced_count(
            (name for t, name in state["reproduced"] if t == task), observations) - known["reproduced"]
        totals["open"] += max(0, anonymous - known["open"])
        missing = [e for e in rows if e.task == task and e.kind in ("review_finished", "loop_round")
                   and e.data.get("failed_identities_omitted", 0)]
        if not missing:
            continue
        known = {key: max(known[key], anonymous) if key in ("reproduced", "open") else known[key]
                 for key in totals}
        observed = max(len(e.data.get("failed_sha256") or []) + e.data["failed_identities_omitted"] for e in missing)
        totals["open"] += max(known["open"], observed - known["repaired"], 1) - known["open"]
    return {**totals,
            "identities_incomplete": any(e.data.get("identities_incomplete") or e.data.get("source_incomplete_sha256")
                                         for e in rows)}


def finding_labels(events: Iterable[Event], targets: Iterable[str]) -> list[dict]:
    """Only retained, attributed annotations for the finding events in this projection."""
    wanted = set(targets)
    return [{"event_id": e.event_id, "target": e.data["target"], "by": e.data.get("by") or "not recorded",
             "note": e.data["note"]} for e in events if e.kind == "annotation" and e.data.get("target") in wanted
            and e.provenance in ("attributed_label", "person_confirmed") and e.data.get("note")]
