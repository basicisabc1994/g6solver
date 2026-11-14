# SPDX-License-Identifier: Apache-2.0
"""Inventory actual provider constructors, subclasses, factories and host sends from Python ASTs."""
import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW = {'g6.core.llm', 'g6.core.host_models'}
SAFE = 'g6.core.consent_callers'
GUARDED_REGISTRY = 'g6.core.consent_registry.GuardedRegistry'


def door_inventory(root=ROOT, extra=()):
    """Discover door calls, including import aliases and local factory aliases.

    Every call of the door, the Python API ``g6.invoke`` in certificate-bound
    ``g6/__init__.py`` included since the certificate issuance window before
    0.3.0, must hand it the shared guarded registry directly. The one exact
    site left is the door's own read of the person's model, which takes no
    registry: it is covered only while every door call passes the guard.
    A new person_model call cannot take a registry and is therefore a gap.
    """
    rows = []
    for path in [*(root / 'src').rglob('*.py'), *extra]:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        relative = path.relative_to(root).as_posix() if root in path.parents else path.name
        module = '.'.join(path.relative_to(root / 'src').with_suffix('').parts) if root / 'src' in path.parents else 'new'
        package = module.rpartition('.')[0]
        if module.endswith('.__init__'):
            package = module.removesuffix('.__init__')
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    base = importlib.util.resolve_name('.' * node.level + base, package)
                aliases.update({a.asname or a.name: f'{base}.{a.name}' for a in node.names})
            elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                aliases[node.name] = f'{module}.{node.name}'

        def qualified(node):
            if isinstance(node, ast.Name):
                return aliases.get(node.id, node.id)
            if isinstance(node, ast.Attribute):
                return qualified(node.value) + '.' + node.attr
            return ''

        assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
        for _ in range(len(assignments) + 1):
            previous = dict(aliases)
            for node in assignments:
                if isinstance(node.value, ast.Name | ast.Attribute):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and not any(
                                isinstance(n, ast.Name) and n.id == target.id for n in ast.walk(node.value)):
                            aliases[target.id] = qualified(node.value)
            if aliases == previous:
                break
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            symbol = qualified(call.func)
            name = symbol.rpartition('.')[2]
            if name not in ('invoke_checked', 'person_model'):
                continue
            owner = call
            while owner in parents and not isinstance(owner, ast.FunctionDef | ast.AsyncFunctionDef):
                owner = parents[owner]
            owner_name = getattr(owner, 'name', '')
            issuance = (relative, owner_name, name) in {
                ('src/g6/core/door.py', 'invoke_checked', 'person_model'),
            }
            registry = next((kw.value for kw in call.keywords if kw.arg == 'registry'), None)
            guarded = (name == 'invoke_checked' and isinstance(registry, ast.Call)
                       and qualified(registry.func) == GUARDED_REGISTRY)
            rows.append((relative, call.lineno, symbol, guarded, issuance))
    return rows


def test_every_unbound_door_call_passes_the_guarded_registry():
    rows = door_inventory()
    assert rows
    assert not [r[:3] for r in rows if not r[3] and not r[4]], rows
    assert sorted((r[0], r[2].rpartition('.')[2]) for r in rows if r[4]) == [('src/g6/core/door.py', 'person_model')]
    python_api = [r for r in rows if r[0] == 'src/g6/__init__.py']
    assert len(python_api) == 1 and python_api[0][3] and not python_api[0][4], (
        'g6.invoke hands the door the guarded registry; it is no exception', python_api)


def test_new_door_calls_and_aliases_cannot_bypass_registry_rule(tmp_path):
    fresh = tmp_path / 'new_door.py'
    fresh.write_text('from g6.core import door as d\n'
        'from g6.core.door import invoke_checked as checked, person_model as model\n'
        'from g6.core.consent_registry import GuardedRegistry as Guard\n'
        'again = checked\n'
        'def call():\n'
        '    d.invoke_checked("c", "op", {}, registry=raw)\n'
        '    checked("c", "op", {})\n'
        '    again("c", "op", {}, registry=Other())\n'
        '    model()\n'
        '    d.person_model()\n'
        '    checked("c", "op", {}, registry=Guard(raw))\n', encoding='utf-8')
    rows = [r for r in door_inventory(extra=(fresh,)) if Path(r[0]).name == fresh.name]
    assert len(rows) == 6
    assert len([r for r in rows if not r[3] and not r[4]]) == 5
    assert rows[-1][3] and not rows[-1][4]


def test_a_shadowed_guard_factory_fails_the_door_inventory(tmp_path):
    for number, shadow in enumerate(('def Safe(value):\n    return value\n', 'class Safe:\n    pass\n')):
        fresh = tmp_path / f'shadow_{number}.py'
        fresh.write_text('from g6.core.door import invoke_checked\n'
            'from g6.core.consent_registry import GuardedRegistry as Safe\n' + shadow +
            'def send(registry):\n'
            '    return invoke_checked("c", "op", {}, registry=Safe(registry))\n', encoding='utf-8')
        rows = [r for r in door_inventory(extra=(fresh,)) if Path(r[0]).name == fresh.name]
        assert len(rows) == 1
        assert not rows[0][3] and not rows[0][4], 'a shadowed import was credited as the shared guard'


def provider_symbols(root):
    symbols = set()
    for module in RAW:
        path = root / 'src' / Path(*module.split('.')).with_suffix('.py')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(isinstance(n, ast.Name) and n.id == '_start_model_call'
                                                     for n in ast.walk(node)):
                symbols.add(f'{module}.{node.name}')
                # Include the transport helper actually called by a sending class.
                functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in functions:
                        helper = functions[call.func.id]
                        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'open'
                               for n in ast.walk(helper)):
                            symbols.add(f'{module}.{helper.name}')
        if module.endswith('llm'):
            symbols.add(f'{module}.provider_from_env')
        else:
            symbols |= {f'{module}.{n.name}' for n in tree.body if isinstance(n, ast.ClassDef)
                        and any(isinstance(b, ast.Name) and b.id == '_HostCaller' for b in n.bases)}
            symbols |= {f'{module}.CALLERS', f'{module}._run'}
    return symbols


def inventory(root=ROOT, extra=()):
    providers = provider_symbols(root)
    doors = door_inventory(root, extra)
    guarded_door_rule = bool(doors) and all(row[3] or row[4] for row in doors)
    rows = []
    paths = [*(root / 'src/g6').rglob('*.py'), *extra]
    parsed = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        module = '.'.join(path.relative_to(root / 'src').with_suffix('').parts) if root / 'src' in path.parents else 'new'
        package = module.rpartition('.')[0]
        if module.endswith('.__init__'):
            module = module.removesuffix('.__init__')
            package = module
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases.update({a.asname or a.name: a.name for a in node.names})
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    try:
                        base = importlib.util.resolve_name('.' * node.level + base, package)
                    except (ImportError, ValueError):
                        base = '<unresolved-relative-import>'
                aliases.update({a.asname or a.name: f'{base}.{a.name}' for a in node.names})
            elif isinstance(node, ast.ClassDef):
                aliases[node.name] = f'{module}.{node.name}'
        def qualified(node, aliases=aliases):
            if isinstance(node, ast.Name):
                return aliases.get(node.id, node.id)
            if isinstance(node, ast.Attribute):
                return qualified(node.value, aliases) + '.' + node.attr
            if isinstance(node, ast.Subscript):
                return qualified(node.value, aliases)
            return ''
        for _ in range(sum(isinstance(n, ast.Assign) for n in ast.walk(tree)) + 1):
            previous = dict(aliases)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name | ast.Attribute | ast.Subscript):
                    resolved = qualified(node.value)
                    for target in node.targets:
                        self_reference = isinstance(target, ast.Name) and any(
                            isinstance(n, ast.Name) and n.id == target.id for n in ast.walk(node.value))
                        if isinstance(target, ast.Name) and resolved and not self_reference:
                            aliases[target.id] = resolved
            if aliases == previous:
                break
        parsed[path] = tree, qualified
        if module == SAFE:
            providers.update(f'{module}.{n.name}' for n in tree.body if isinstance(n, ast.FunctionDef)
                             and n.name == 'provider_from_env')
    # Resolve subclasses to a fixed point, rather than relying on a manually maintained class-name list.
    changed = True
    safe = set()
    while changed:
        changed = False
        for path, (tree, qualified) in parsed.items():
            module = '.'.join(path.relative_to(root / 'src').with_suffix('').parts) if root / 'src' in path.parents else 'new'
            for node in ast.walk(tree):
                name = f'{module}.{node.name}' if isinstance(node, ast.ClassDef) else ''
                if name and any(qualified(b) in providers for b in node.bases) and name not in providers:
                    providers.add(name)
                    changed = True
                if name and name in providers:
                    bases = [qualified(b) for b in node.bases if qualified(b) in providers]
                    overrides = [m for m in node.body if isinstance(m, ast.FunctionDef) and
                                 m.name in ('complete', 'complete_with_images', 'run')]
                    guarded_override = all(any(isinstance(n, ast.With) and any(isinstance(i.context_expr, ast.Call)
                        and qualified(i.context_expr.func) == 'g6.core.cloud_consent.admission' for i in n.items)
                        for n in ast.walk(m)) for m in overrides)
                    owns_guard = module == SAFE and any(qualified(b) == SAFE + '._Consent' for b in node.bases)
                    if ((owns_guard or bases and bases[0] in safe) and guarded_override) and name not in safe:
                        safe.add(name)
                        changed = True
    for path, (tree, qualified) in parsed.items():
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            symbols = [qualified(node.func)] if isinstance(node, ast.Call) else (
                [qualified(b) for b in node.bases] if isinstance(node, ast.ClassDef) else [])
            for symbol in symbols:
                base, _, method = symbol.rpartition('.')
                if symbol not in providers and not (base in providers and method in ('complete', 'complete_with_images')):
                    continue
                module = '.'.join(path.relative_to(root / 'src').with_suffix('').parts) if root / 'src' in path.parents else 'new'
                # Raw definitions/construction inside bound provider modules are reached only through outer wrappers.
                covered = module in RAW or symbol in safe or symbol.startswith(SAFE + '.')
                if isinstance(node, ast.ClassDef) and f'{module}.{node.name}' in safe:
                    covered = True
                ancestor = node
                while ancestor in parents:
                    ancestor = parents[ancestor]
                    if isinstance(ancestor, ast.Call):
                        helper = qualified(ancestor.func)
                        local_helper = module == SAFE and isinstance(ancestor.func, ast.Name) and \
                            ancestor.func.id in ('guard', 'destination')
                        if helper == SAFE + '.guard' or local_helper:
                            covered = True
                if isinstance(node, ast.ClassDef) and module not in RAW | {SAFE}:
                    for method in (m for m in node.body if isinstance(m, ast.FunctionDef) and
                                   m.name in ('complete', 'complete_with_images', 'run')):
                        if not any(isinstance(n, ast.With) and any(isinstance(i.context_expr, ast.Call) and
                            qualified(i.context_expr.func) == 'g6.core.cloud_consent.admission' for i in n.items)
                            for n in ast.walk(method)):
                            covered = False
                parent = parents.get(node)
                if isinstance(parent, ast.Call) and qualified(parent.func) == SAFE + '.guard':
                    covered = True
                # doctor constructs only to inspect locate(), which cannot call a model.
                if isinstance(parent, ast.Attribute) and parent.attr == 'locate':
                    covered = True
                # Host sessions explicitly take admission across the host process send.
                current = node
                while current in parents:
                    current = parents[current]
                    if isinstance(current, ast.With) and any(isinstance(i.context_expr, ast.Call)
                        and qualified(i.context_expr.func) == 'g6.core.cloud_consent.admission' for i in current.items):
                        covered = True
                # This bound factory is covered only while every unbound door
                # call passes the shared adapter. The Python API's exact bound
                # call remains a certificate issuance-window obligation above.
                enclosing = node
                while enclosing in parents and not isinstance(enclosing, ast.FunctionDef):
                    enclosing = parents[enclosing]
                if (module == 'g6.core.door' and symbol == 'g6.core.llm.provider_from_env'
                        and isinstance(enclosing, ast.FunctionDef) and enclosing.name == 'person_model'):
                    covered = guarded_door_rule
                rows.append((path.relative_to(root).as_posix() if root in path.parents else path.name,
                             node.lineno, symbol, covered, False))
    # Discover native prompt-process classes too: the Claude/Codex agent adapters
    # do not instantiate a core.llm or core.host_models provider.
    runners = set()
    native_calls = {}
    for path, (tree, qualified) in parsed.items():
        native_calls[path] = set()
        module = '.'.join(path.relative_to(root / 'src').with_suffix('').parts) if root / 'src' in path.parents else 'new'
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)]
            sends = {n for n in ast.walk(cls) if isinstance(n, ast.Call)
                     and qualified(n.func) == 'g6.core.component_process.run_process'}
            if any('prompt' in {a.arg for a in (*m.args.args, *m.args.kwonlyargs)} for m in methods) and sends:
                runners.add(f'{module}.{cls.name}')
                native_calls[path].update(sends)
    for path, (tree, qualified) in parsed.items():
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        holder_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(n, ast.Call) and qualified(n.func) in runners
                                                  for n in ast.walk(node.value)):
                holder_names.update(ast.unparse(t) for t in node.targets)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            symbol = qualified(node.func)
            native = node in native_calls[path]
            constructor = symbol in runners
            direct = (isinstance(node.func, ast.Attribute) and node.func.attr == 'run'
                      and ast.unparse(node.func.value) in holder_names)
            if not (native or constructor or direct):
                continue
            covered = False
            current = node
            while current in parents:
                current = parents[current]
                if isinstance(current, ast.With) and any(isinstance(i.context_expr, ast.Call) and
                    qualified(i.context_expr.func) in ('g6.core.cloud_consent.admission',
                        'g6.agents._runtime.consent.process_admission') for i in current.items):
                    covered = True
            if constructor:
                owner = node
                while owner in parents and not isinstance(owner, ast.Assign):
                    owner = parents[owner]
                if isinstance(owner, ast.Assign):
                    slots = {ast.unparse(t) for t in owner.targets}
                    covered = any(isinstance(n, ast.Call) and n.args and qualified(n.func) ==
                        'g6.agents._runtime.consent.consent_run' and ast.unparse(n.args[0]) in slots for n in ast.walk(tree))
            rows.append((path.relative_to(root).as_posix() if root in path.parents else path.name,
                         node.lineno, 'host-process:' + symbol, covered, False))
    return rows


def test_every_unbound_provider_entry_has_consent_check():
    rows = inventory()
    assert rows, 'no provider callers discovered'
    gaps = [r[:3] for r in rows if not r[3]]
    assert not gaps, f'provider callers bypass consent: {gaps}'
    assert not any(r[4] for r in rows), 'no provider factory may bypass the wrapper rule as debt'


def test_a_new_door_bypass_invalidates_factory_coverage(tmp_path):
    fresh = tmp_path / 'bypass.py'
    fresh.write_text('from g6.core.door import invoke_checked\n'
                     'def send():\n    return invoke_checked("c", "op", {})\n', encoding='utf-8')
    rows = inventory(extra=(fresh,))
    assert any(r[0] == 'src/g6/core/door.py' and r[2] == 'g6.core.llm.provider_from_env'
               and not r[3] and not r[4] for r in rows)


def test_new_alias_or_subclass_fails_inventory(tmp_path):
    fresh = tmp_path / 'new_caller.py'
    fresh.write_text('from g6.core import llm as engine\n'
                     'def send():\n    return engine.provider_from_env()\n'
                     'class Remote(engine.OllamaCaller):\n    pass\n', encoding='utf-8')
    gaps = [r for r in inventory(extra=(fresh,)) if Path(r[0]).name == fresh.name and not r[3]]
    assert len(gaps) == 2


def test_relative_import_subclass_and_factory_reassignment_fail_inventory(tmp_path):
    core = tmp_path / 'src/g6/core'
    core.mkdir(parents=True)
    for name in ('llm.py', 'host_models.py'):
        (core / name).write_text((ROOT / 'src/g6/core' / name).read_text(encoding='utf-8'), encoding='utf-8')
    (core / 'rogue.py').write_text('from .llm import OllamaCaller as Parent\n'
        'class A(Parent):\n    pass\nclass B(A):\n    pass\n'
        'factory = Parent\ndef send():\n    return factory()\n', encoding='utf-8')
    gaps = [r for r in inventory(tmp_path) if r[0].endswith('rogue.py') and not r[3]]
    assert len(gaps) == 3


def test_guarded_subclass_cannot_override_send_without_a_check(tmp_path):
    fresh = tmp_path / 'override.py'
    fresh.write_text('from g6.core.consent_callers import ConsentClaude\n'
        'class Unsafe(ConsentClaude):\n'
        '    def complete(self, system, user, **kwargs):\n        return unguarded_transport(user)\n'
        'def make():\n    return Unsafe()\n', encoding='utf-8')
    gaps = [r for r in inventory(extra=(fresh,)) if Path(r[0]).name == fresh.name and not r[3]]
    assert gaps, 'an overriding subclass inherited safety without inheriting the check'


def caller_classes(root=ROOT, extra=()):
    """Discover completion implementations and subclasses, including G6 wrappers."""
    classes, aliases = {}, {}
    for path in [*(root / 'src/g6').rglob('*.py'), *extra]:
        module = '.'.join(path.relative_to(root / 'src').with_suffix('').parts) if root / 'src' in path.parents else 'new'
        module = module.removesuffix('.__init__')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        local = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                local.update({a.asname or a.name: f'{node.module}.{a.name}' for a in node.names})
            elif isinstance(node, ast.Import):
                local.update({a.asname or a.name: a.name for a in node.names})
            elif isinstance(node, ast.ClassDef):
                local[node.name] = f'{module}.{node.name}'
        aliases.update({f'{module}.{k}': v for k, v in local.items()})
        def qualified(node):
            if isinstance(node, ast.Name):
                return local.get(node.id, f'{module}.{node.id}')
            if isinstance(node, ast.Attribute):
                return qualified(node.value) + '.' + node.attr
            return ''
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            own = any(isinstance(n, ast.FunctionDef) and n.name == 'complete' and
                      {'system', 'user'} <= {a.arg for a in n.args.args} for n in cls.body)
            classes[f'{module}.{cls.name}'] = own, [qualified(b) for b in cls.bases]
    def resolve(name):
        seen = set()
        while name in aliases and aliases[name] != name and name not in seen:
            seen.add(name)
            name = aliases[name]
        return name
    found = {name for name, (own, _) in classes.items() if own}
    while True:
        more = {name for name, (_, bases) in classes.items() if any(resolve(b) in found for b in bases)}
        if more <= found:
            return found
        found |= more


def test_every_g6_caller_and_wrapper_has_an_explicit_classification():
    from g6.core.consent_callers import CALLER_KINDS
    found = caller_classes()
    assert found
    assert not found - CALLER_KINDS.keys(), f'unclassified G6 callers: {sorted(found - CALLER_KINDS.keys())}'


def test_a_new_g6_wrapper_fails_the_classification_inventory(tmp_path):
    from g6.core.consent_callers import CALLER_KINDS
    fresh = tmp_path / 'wrapper.py'
    fresh.write_text('from g6.reasoning.budget import MeteredCaller\nclass NewWrapper(MeteredCaller):\n    pass\n',
                     encoding='utf-8')
    assert 'new.NewWrapper' in caller_classes(extra=(fresh,)) - CALLER_KINDS.keys()


def test_guard_module_cannot_add_a_raw_factory(tmp_path):
    core = tmp_path / 'src/g6/core'
    core.mkdir(parents=True)
    (core / 'llm.py').write_text('class Sender:\n    def complete(self, system, user):\n'
        '        _start_model_call()\ndef provider_from_env():\n    return Sender()\n', encoding='utf-8')
    (core / 'host_models.py').write_text('class _HostCaller:\n    pass\n', encoding='utf-8')
    (core / 'consent_callers.py').write_text('from g6.core.llm import Sender\n'
        'def accidental_factory():\n    return Sender()\n', encoding='utf-8')
    assert [row for row in inventory(tmp_path) if row[0].endswith('consent_callers.py') and not row[3]]


def sdk_resources(root=ROOT, extra=()):
    from g6.core.consent_declarations import SDK_SENDS
    rows = []
    for path in [*(root / 'src/g6').rglob('*.py'), *extra]:
        module = '.'.join(path.relative_to(root / 'src').with_suffix('').parts) if root / 'src' in path.parents else 'new'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        aliases = {a.asname or a.name: f'{node.module}.{a.name}' for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module for a in node.names}
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == 'create'
                    and isinstance(call.func.value, ast.Attribute)
                    and call.func.value.attr in ('messages', '_messages', 'responses', 'completions')):
                continue
            current, owners, guarded = call, [], False
            while current in parents:
                current = parents[current]
                if isinstance(current, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                    owners.append(current.name)
                if isinstance(current, ast.With):
                    guarded |= any(isinstance(i.context_expr, ast.Call) and isinstance(i.context_expr.func, ast.Name)
                        and aliases.get(i.context_expr.func.id) == SAFE + '.sdk_send' for i in current.items)
            identity = module + '.' + '.'.join(reversed(owners))
            rows.append((identity, module in RAW or identity in SDK_SENDS, module in RAW or guarded))
    return rows


def test_every_sdk_resource_has_a_declaration_and_send_check():
    rows = sdk_resources()
    assert rows
    assert all(declared and guarded for _, declared, guarded in rows), rows


def test_new_sdk_resource_fails_inventory(tmp_path):
    path = tmp_path / 'new_sdk.py'
    path.write_text('class NewSDK:\n    def send(self):\n        return self.messages.create()\n', encoding='utf-8')
    assert ('new.NewSDK.send', False, False) in sdk_resources(extra=(path,))


def test_every_context_engine_tool_has_its_source_contract_and_argument_kinds():
    """The Context Engine's tools live in g6.mcp.knowledge: each one the server registers is in the closed tables,
    with exactly the arguments its schema has, and declares the client's request as a source."""
    import asyncio

    from g6.core.consent_declarations import TOOL_ARGUMENTS, TOOLS
    from g6.mcp import build_server

    tools = {name: tool for name, tool in asyncio.run(build_server().get_tools()).items()
             if name.startswith("g6_knowledge_")}
    assert len(tools) >= 5
    for name, tool in tools.items():
        assert name in TOOLS and TOOLS[name].classes == ('client_request',), name
        assert set(tool.parameters.get('properties', {})) == set(TOOL_ARGUMENTS[name]), name
        assert set(TOOL_ARGUMENTS[name].values()) <= {'content', 'pointer'}, name
    declared = {name for name in TOOLS if name.startswith("g6_knowledge_")}
    assert declared == set(tools), "a declared Context Engine tool is not registered, or the reverse"


def knowledge_model_calls(root=ROOT, extra=()):
    """Every place the Context Engine's shared modules ask a model: (file:function, the receiver is ``guard(...)``,
    the call lies inside ``with cloud_consent.request_sources(...)``). Read from the folder, so a new module is seen."""
    rows = []
    for path in [*sorted((root / 'src/g6/knowledge').glob('_*.py')), *sorted((root / 'src/g6/_context_engine').glob('_*.py')),
                 *extra]:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for function in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]:
            inside = {id(call) for block in ast.walk(function) if isinstance(block, ast.With) and any(
                isinstance(i.context_expr, ast.Call) and ast.unparse(i.context_expr.func) == 'cloud_consent.request_sources'
                for i in block.items) for call in ast.walk(block)}
            for call in ast.walk(function):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in (
                        'complete', 'complete_with_images'):
                    receiver = call.func.value
                    guarded = isinstance(receiver, ast.Call) and ast.unparse(receiver.func) == 'guard'
                    rows.append((f'{path.name}:{function.name}', guarded, id(call) in inside))
    return rows


def test_every_model_call_of_the_context_engine_is_guarded_and_names_its_sources(tmp_path):
    rows = knowledge_model_calls()
    assert {'_relations.py:extract', '_planner.py:run'} <= {name for name, _, _ in rows}
    assert all(guarded and inside for _, guarded, inside in rows), rows
    text = (ROOT / 'src/g6/_context_engine/_relations.py').read_text(encoding='utf-8')
    assert 'from g6.core.consent_callers import guard' in text and 'from g6.core import cloud_consent' in text
    # a new module that asks a model directly, or outside the sources of what it sends, fails the inventory
    fresh = tmp_path / '_fresh.py'
    fresh.write_text('def ask(caller, text):\n    return caller.complete("", text)\n\n\n'
                     'def half(caller, text):\n    return guard(caller).complete("", text)\n', encoding='utf-8')
    assert knowledge_model_calls(extra=(fresh,))[-2:] == [('_fresh.py:ask', False, False), ('_fresh.py:half', True, False)]
