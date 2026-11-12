# SPDX-License-Identifier: Apache-2.0
"""Inventory actual provider constructors, subclasses, factories and host sends from Python ASTs."""
import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW = {'g6.core.llm', 'g6.core.host_models'}
SAFE = 'g6.core.consent_callers'


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
                # Bound door construction has no unbound seam for direct library/CLI use. Keep visible as issuance debt.
                enclosing = node
                while enclosing in parents and not isinstance(enclosing, ast.FunctionDef):
                    enclosing = parents[enclosing]
                debt = (module == 'g6.core.door' and symbol == 'g6.core.llm.provider_from_env'
                        and isinstance(enclosing, ast.FunctionDef) and enclosing.name == 'person_model')
                rows.append((path.relative_to(root).as_posix() if root in path.parents else path.name,
                             node.lineno, symbol, covered, debt))
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
    gaps = [r[:3] for r in rows if not r[3] and not r[4]]
    assert not gaps, f'provider callers bypass consent: {gaps}'
    assert len([r for r in rows if r[4]]) == 1, 'the existing bound door debt may not grow'


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
