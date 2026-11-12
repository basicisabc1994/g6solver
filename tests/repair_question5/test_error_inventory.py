"""Errors keep their own status through the real bound executor and review."""
import inspect
import subprocess

import pytest

from g6.core import component_process, confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.harness import behaviour_runner, patch_review, verification_search as vs
from g6.mcp import behaviour
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import REQUIREMENTS, TASK, Dialog, ask, repo, stated


@pytest.mark.parametrize('stage', ['manifest', 'copy', 'locate', 'startup', 'channel', 'after_read'])
@pytest.mark.parametrize('error', [OSError, ValueError, rt.CopyNotRun])
@pytest.mark.parametrize('when', ['patch', 'base'])
def test_executor_error_is_never_evidence_of_changed_bytes(tmp_path, monkeypatch, stage, error, when):
    root = repo(tmp_path)
    assert ask(root, dialog=Dialog({'choice': 'B', 'name': 'Maintainer'}))['status'] == 'confirmed'
    stated(monkeypatch)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('test-boundary', '', spec))
    def broken(*args, **kwargs):
        raise error('planted ' + stage)
    runs = []
    def failing():
        return len(runs) == (1 if when == 'patch' else 2)
    manifest = vs._copy_manifest
    def read_manifest(*args, **kwargs):
        if kwargs.get('artefact') is not None:
            runs.append(True)
            if stage == 'manifest' and failing():
                broken()
        return manifest(*args, **kwargs)
    monkeypatch.setattr(vs, '_copy_manifest', read_manifest)
    def execute(cmd, *, cwd, env, timeout_s, log, **kwargs):
        if stage == 'startup' and failing():
            broken()
        child = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=20)
        log.write_bytes(child.stdout + child.stderr)
        if stage == 'channel' and failing():
            __import__('pathlib').Path(cmd[-2]).unlink()
        return child.returncode
    monkeypatch.setattr(rt, '_bounded', execute)
    if stage == 'copy':
        original = rt.repository_copy
        monkeypatch.setattr(rt, 'repository_copy', lambda *a, **k: broken() if failing() else original(*a, **k))
    elif stage == 'locate':
        original = behaviour_runner.located
        monkeypatch.setattr(behaviour_runner, 'located', lambda *a, **k: broken() if failing() else original(*a, **k))
    elif stage == 'after_read':
        original = rt._copy_snapshot
        def snapshot(path, **kwargs):
            # The runner's read after the child ends is its one read of the folder it made for the run ("tree" in
            # its own run folder); it was the third or fourth read of the copy while the child ran in the copy.
            return broken() if path.name == 'tree' and failing() else original(path, **kwargs)
        monkeypatch.setattr(rt, '_copy_snapshot', snapshot)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    assessment = reply['assessment']
    assert not any(r['code'] == 'repository_changed_during_review' for r in assessment['reasons'])
    [held] = assessment['confirmed_expectations']['expectations']
    assert held['check']['result'] == ('failed' if when == 'base' else 'not_run'), held
    if when == 'base':
        assert held['check']['runs']['patch']['result'] == 'failed'
        assert held['check']['runs']['base']['result'] == 'error'
        assert assessment['outcome'] == patch_review.DEFECT
    assert not held['check'].get('tree_changed')


def test_inventory_integrity_flags_have_only_comparison_producers():
    # The exception handler is the reported class: it must never manufacture this flag.
    import ast
    tree = ast.parse(inspect.getsource(vs._run_check))
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    assert handlers
    assert all('refused' not in ast.unparse(n) for n in handlers)


@pytest.mark.parametrize('kind', ['isolation', 'interrupted', 'containment'])
def test_real_executor_preserves_boundary_refusals_and_operator_stops(tmp_path, monkeypatch, kind):
    root = repo(tmp_path)
    assert ask(root)['status'] == 'confirmed'
    stated(monkeypatch)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan(
        '' if kind == 'isolation' else 'test-boundary', '', spec))
    error = (component_process.ExecutionInterrupted('planted operator stop') if kind == 'interrupted'
             else component_process.ContainmentFailure('planted cleanup refusal'))
    def stopped(*args, **kwargs):
        raise error
    monkeypatch.setattr(rt, '_bounded', stopped)
    if kind == 'isolation':
        reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
        assert reply.get('ok') is False and ISOLATION_UNAVAILABLE in str(reply)
        assert 'repository_changed_during_review' not in str(reply)
    else:
        with pytest.raises(type(error)) as caught:
            behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
        assert caught.value is error
