"""Inventory receipt consumers from producer code, including persistent readers."""
import ast
from pathlib import Path

from g6.core import cloud_consent as cloud


def receipt_consumers(paths):
    found = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        def visit(node, owners=()):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                owners = (*owners, node.name)
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                    node.func.id if isinstance(node.func, ast.Name) else '')
                if name in ('freeze_output', 'output_sources', 'output_metadata', 'read_retained', 'write_retained'):
                    found.setdefault(path.name + ':' + '.'.join(owners), set()).add(name)
            for child in ast.iter_child_nodes(node):
                visit(child, owners)
        visit(tree)
    return found


def test_all_receipt_consumers_render_snapshots_and_no_live_receipt_checks_remain(tmp_path):
    root = Path(cloud.__file__).resolve().parents[1]
    found = receipt_consumers(root.rglob('*.py'))
    live = {site: calls for site, calls in found.items() if calls & {'output_sources', 'output_metadata'}}
    assert not live, ('Live receipt checks must be replaced by a checked snapshot', live)
    assert {site for site, calls in found.items() if 'freeze_output' in calls} == {
        'cloud_consent.py:prompt_state', 'cloud_consent.py:_source_function.invoke',
        'cloud_consent.py:protect_reply', 'completion_loop.py:_read', 'completion_loop.py:_snapshot_probe',
        'consent_controls.py:host_controls', 'jobs.py:Jobs._run', 'mission.py:reply',
        'review_loop.py:step', 'review_reply.py:finish'}
    assert {site for site, calls in found.items() if 'read_retained' in calls} == {
        'completion_loop.py:_load', 'completion_loop.py:read_report', 'review_loop.py:_asked', 'digest.py:read',
        'completion_hook.py:_task'}
    assert {site for site, calls in found.items() if 'write_retained' in calls} == {
        'completion_loop.py:_save', 'review_loop.py:_asked', 'digest.py:write',
        'completion_hook.py:_record', 'completion_hook.py:_again', 'completion_hook.py:on_prompt'}
    planted = tmp_path / 'new.py'
    planted.write_text('def late(value):\n    refs = alias.output_sources(value)\n    return str(value), refs\n')
    assert receipt_consumers([planted]) == {'new.py:late': {'output_sources'}}


def test_retained_producers_and_handoffs_are_inventoried():
    root = Path(cloud.__file__).resolve().parents[1]
    # The in-memory result stores come from the assignments, not poll names.
    tree = ast.parse((root / 'metaharness/jobs.py').read_text(encoding='utf-8'))
    stores = {target.attr for node in ast.walk(tree) if isinstance(node, ast.Assign)
              and isinstance(node.value, ast.Dict) for target in node.targets
              if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self'}
    assert stores == {'_jobs', '_compact', '_provenance'}
    # All direct disk reads on the MCP surface are either checked retained
    # readers or the repository input in submit; delegated readers are above.
    readers = {}
    for path in (root / 'mcp').glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods = {n.func.attr for n in ast.walk(node) if isinstance(n, ast.Call)
                           and isinstance(n.func, ast.Attribute) and n.func.attr in ('read_bytes', 'read_text')}
                if methods:
                    readers[path.name + ':' + node.name] = methods
    assert readers == {'review_loop.py:_asked': {'read_text'}, 'mission.py:submit': {'read_bytes'},
                       'mission.py:work': {'read_bytes'},
                       # Re-derived on the 0.3 integration line, each classified: G6's own saved review reply, read
                       # only when bound to the task (g6_confirm_behaviour declares source:jobs) and its digest check;
                       'behaviour.py:_review_candidates': {'read_bytes'}, 'review_binding.py:matches': {'read_bytes'},
                       # the loop folder's own stop records, which decide a stop and are never returned as text;
                       'improve.py:_stop_before': {'read_bytes'}, 'improve.py:_stop_entry_on_record': {'read_bytes'},
                       # G6's installed program text (its servers, skills and guidance), which holds no person's data.
                       'registrations.py:server_sources': {'read_text'}, 'registrations.py:guidance': {'read_text'},
                       'registrations.py:inputs': {'read_text'},
                       # db20c4f (section 14): the names in G6's own package .py files under each component a routed
                       # skill file names (paths from G6's own routing table), also G6's installed program text.
                       'registrations.py:identifiers': {'read_text'}}
