"""All producers of shared completion files, including hook re-arming, are covered."""
from dataclasses import asdict
import json

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import completion_hook as hook, completion_loop as loop


@pytest.mark.parametrize('field', ['task', 'base', 'repo'])
def test_hook_task_reader_cannot_reuse_replaced_task_fields(tmp_path, monkeypatch, field):
    reader = getattr(hook, '_task', None)
    assert reader is not None, 'the hook has an unchecked independent task reader'
    monkeypatch.setattr(hook, '_here', lambda *a: (tmp_path, 'base'))
    hook._record(tmp_path, str(tmp_path), 'task')
    data = json.loads((tmp_path / 'task.json').read_text(encoding='utf-8'))
    data[field] = 'PRIVATE_CHANGED_TASK'
    (tmp_path / 'task.json').write_text(json.dumps(data), encoding='utf-8')
    with cloud.collect_sources() as capture:
        with pytest.raises(ValueError, match=cloud.REQUIRED):
            reader(tmp_path)
        assert cloud.UNKNOWN in capture.sources


@pytest.mark.parametrize('unknown', [False, True], ids=['known-origin', 'unknown-origin'])
def test_hook_rearm_preserves_retained_origins_and_pins_both_files(tmp_path, monkeypatch, unknown):
    source = cloud.UNKNOWN if unknown else cloud.Source.text('private', 'old-result', 'PRIVATE_OLD_RESULT')
    state = {'done': True, 'reviews': [{'patch': 'old-patch'}], 'consent_sources': [asdict(source)]}
    monkeypatch.setattr(hook, 'folder', lambda name: tmp_path / name.replace(':', '_'))
    monkeypatch.setattr(hook, '_here', lambda *a: (tmp_path, 'base'))
    asked = {'repo': str(tmp_path), 'task': 'old task', 'base': 'base'}
    hook.folder('session').mkdir()
    hook.current('session', {**asked, 'run': 1}).mkdir()
    hook._again({'session_id': 'session', 'cwd': str(tmp_path)}, asked, state, 'new task')
    session = hook.folder('session')
    task = session / 'task.json'
    assert task.with_suffix('.sha256').is_file(), 'hook task updates have no completion digest'
    saved_task = hook._task(session)
    run = hook.current('session', saved_task)
    assert (run / 'state.sha256').is_file(), 'hook state resets have no completion digest'
    saved = loop._load(run)
    assert source in loop.source_refs(saved), 're-arming dropped the old run origin'


def test_every_literal_completion_file_producer_is_in_the_inventory():
    import ast
    from pathlib import Path
    root = Path(cloud.__file__).resolve().parents[1]
    found = {}
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        names = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                 and isinstance(node.value, str) and node.value in ('task.json', 'state.json', 'report.md')}
        if names:
            found[path.relative_to(root).as_posix()] = names
    assert found == {'harness/completion_hook.py': {'task.json'},
                     'harness/completion_loop.py': {'state.json', 'report.md'},
                     'mcp/review_loop.py': {'task.json'},
                     'integrations/hat_orchestrator/state.py': {'state.json'}}
