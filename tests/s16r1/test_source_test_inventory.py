# SPDX-License-Identifier: Apache-2.0
"""Every source-bearing test call gets a reviewed consent disposition.

The scan includes all test files (a superset of the changed-module importers),
follows file-local helpers and fixtures, and reads pointer kinds from the tool
schema inventory. New cases cannot silently inherit a business-test exemption.
"""
import ast
import hashlib
import json
from pathlib import Path

from g6.core.consent_declarations import TOOL_ARGUMENTS

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name('source_test_inventory.json')
MODEL_CALLS = {'run_critic', 'repository_context', 'review_patch', 'advance', 'prepare',
               'process_admission', 'consent_run'}
GRANT_FIXTURES = {'declared_mcp_grants', 'consented_provider_fakes', 'accepted_cloud_dialog'}


def leaf(node):
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ''


def named_fixtures(nodes):
    return {n.value for node in nodes for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in GRANT_FIXTURES}


def scan(files=None):
    found = {}
    for path in files or sorted((ROOT / 'tests').rglob('*.py')):
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source)
        functions = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                functions.setdefault(node.name, []).append(node)
            elif isinstance(node, ast.ImportFrom) and (node.module or '').startswith('tests.'):
                helper = ROOT / (node.module.replace('.', '/') + '.py')
                if helper.is_file():
                    imported = ast.parse(helper.read_text(encoding='utf-8'))
                    for alias in node.names:
                        for definition in imported.body:
                            if isinstance(definition, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                                    and definition.name == alias.name:
                                functions.setdefault(alias.asname or alias.name, []).append(definition)
        common = named_fixtures(node for node in tree.body if isinstance(node, ast.Assign))
        autouse = {name for name, nodes in functions.items() if any(
            isinstance(d, ast.Call) and leaf(d.func) == 'fixture' and any(
                k.arg == 'autouse' and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in d.keywords) for node in nodes for d in getattr(node, 'decorator_list', []))}
        for name, nodes in functions.items():
            if not name.startswith('test_'):
                continue
            visited, todo, calls, fixtures = set(), [name, *autouse], set(), set(common)
            while todo:
                current = todo.pop()
                if current in visited:
                    continue
                visited.add(current)
                if current in GRANT_FIXTURES:
                    fixtures.add(current)
                for node in functions.get(current, []):
                    fixtures.update(named_fixtures([node]))
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        todo.extend(a.arg for a in (*node.args.args, *node.args.kwonlyargs))
                    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
                        called = leaf(call.func)
                        module_method = (isinstance(call.func, ast.Attribute)
                                         and isinstance(call.func.value, ast.Name)
                                         and call.func.value.id in {
                                             'asyncio', 'pytest', 'json', 'time', 'subprocess', 'runpy'})
                        if called in functions and not module_method:
                            todo.append(called)
                        if called == 'call_tool':
                            names = {n.value for n in ast.walk(call) if isinstance(n, ast.Constant)
                                     and isinstance(n.value, str) and n.value in TOOL_ARGUMENTS
                                     and 'pointer' in TOOL_ARGUMENTS[n.value].values()
                                     and n.value not in ('g6_cloud_grant', 'g6_cloud_revoke')}
                            calls.update('mcp:' + tool for tool in names)
                            if not names:
                                # Variable tool names, including helper methods, require review too.
                                calls.add('mcp:dynamic')
                        elif called == 'create' and isinstance(call.func, ast.Attribute) and any(
                                isinstance(n, ast.Attribute) and n.attr in ('messages', 'completions')
                                for n in ast.walk(call.func)):
                            calls.add('model:sdk-create')
                        elif called in MODEL_CALLS | {'complete', 'complete_with_images'} and any(
                                isinstance(n, ast.Name) and any(p in n.id.lower() for p in (
                                    'repo', 'path', 'change', 'workspace', 'folder', 'directory', 'root', '_id'))
                                or isinstance(n, ast.Attribute) and n.attr in ('repo', 'cwd', 'workspace')
                                for n in ast.walk(node if called.startswith('complete') else call)):
                            calls.add('model:' + called)
            if calls:
                rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT / 'tests') else path.name
                reviewed_code = source + '\n'.join(ast.dump(n) for helper in sorted(visited & functions.keys())
                                                    for n in functions[helper])
                if fixtures:
                    reviewed_code += (ROOT / 'tests/conftest.py').read_text(encoding='utf-8')
                found[rel + '::' + name] = {'calls': sorted(calls), 'fixtures': sorted(fixtures),
                                           'helpers': sorted(visited & functions.keys()),
                                           'fingerprint': hashlib.sha256(reviewed_code.encode()).hexdigest()}
    return found


def test_source_bearing_tests_have_explicit_consent_dispositions():
    actual = scan()
    recorded = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {}
    assert actual.keys() == recorded.keys(), (
        'Source-bearing tests need a granted or intentional-withheld review',
        sorted(actual.keys() - recorded.keys()), sorted(recorded.keys() - actual.keys()))
    for node, case in actual.items():
        row = recorded[node]
        assert row['calls'] == case['calls'], node
        assert row['fingerprint'] == case['fingerprint'], ('Changed test/helper needs renewed consent review', node)
        assert row['disposition'] in ('granted', 'withheld', 'granted-and-withheld', 'local-or-no-send'), node
        assert row['reason'], node
        if row['disposition'] in ('granted', 'granted-and-withheld'):
            assert case['fixtures'] or row.get('grant_helper'), node


def test_a_new_pointer_test_is_not_in_the_reviewed_inventory(tmp_path):
    new = tmp_path / 'test_new.py'
    new.write_text("async def test_private(client):\n"
                   "    await client.call_tool('g6_review_patch', {'repo': 'synthetic'})\n")
    assert scan([new])['test_new.py::test_private']['calls'] == ['mcp:g6_review_patch']
    recorded = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {}
    assert 'test_new.py::test_private' not in recorded
