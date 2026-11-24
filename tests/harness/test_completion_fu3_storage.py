# SPDX-License-Identifier: Apache-2.0
"""Retained publications recover without granting authority to unreadable bytes."""
import errno
import json
from contextlib import contextmanager

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from tests._review_wtffu2b_3.rev3_world import TASK, hook_session, quiet_scouts
from tests._review_wtffu2b4.rev4_world import clean_critic, rows_of, torn
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def test_prompt_preserves_torn_task_and_arms_a_distinct_run(disk_run, monkeypatch):
    repo, base, _ = disk_run
    quiet_scouts(monkeypatch)
    event, old_run = hook_session(monkeypatch, repo, "fu3-binding")
    binding = hook.folder(event["session_id"]) / "task.json"
    torn(binding, binding.read_bytes() + b" ")
    before = binding.read_bytes(), binding.with_suffix(".sha256").read_bytes()
    hook.on_prompt({**event, "prompt": "new task"})
    asked = hook._task(binding.parent)
    assert asked["task"] == "new task"
    assert hook.current(event["session_id"], asked) != old_run
    assert (binding.read_bytes(), binding.with_suffix(".sha256").read_bytes()) == before


@pytest.mark.parametrize("held", ["content", "receipt"])
def test_sharing_violation_retries_the_whole_publication(tmp_path, monkeypatch, held):
    target = tmp_path / "state.json"
    store.write_retained(target, b"old")
    original = store.os.replace
    failures = 2

    def replace(source, destination):
        nonlocal failures
        blocked = target if held == "content" else target.with_suffix(".sha256")
        if destination == blocked and failures:
            failures -= 1
            raise PermissionError(errno.EACCES, "sharing violation")
        return original(source, destination)

    monkeypatch.setattr(store.os, "replace", replace)
    store.write_retained(target, b"new")
    assert loop.cloud.read_retained(target, target.with_suffix(".sha256").read_text()) == b"new"


def test_exhausted_receipt_retry_restores_the_previous_publication(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    store.write_retained(target, b"old")
    original = store.os.replace

    def replace(source, destination):
        if destination == target.with_suffix(".sha256"):
            raise PermissionError(errno.EACCES, "sharing violation")
        return original(source, destination)

    monkeypatch.setattr(store.os, "replace", replace)
    with pytest.raises(OSError):
        store.write_retained(target, b"new")
    assert loop.cloud.read_retained(target, target.with_suffix(".sha256").read_text()) == b"old"


def test_unsaved_reservation_remains_retryable_without_a_failed_review(disk_run, key_file, monkeypatch):
    repo, base, session = disk_run
    key_file("G6_LOOP_MODE=review\n")
    caller, sent = clean_critic()
    loop._save(session, {"attempted": 0}, sources=())

    def unavailable(*args, **kwargs):
        raise PermissionError(errno.EACCES, "sharing violation")

    monkeypatch.setattr(loop, "_save_reservation", unavailable)
    step = loop.advance(repo, TASK, base, session, critic=caller)
    state = loop._load(session)
    assert step.code == "G6_E_COMPLETION_RESERVATION_UNSAVED"
    assert sent == [] and loop.attempted(state) == 0
    assert not state.get("done") and not state.get("reviews")
    assert rows_of(session) == []


def test_token_alone_does_not_make_a_started_publication(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / ".write-version").write_text("a" * 32)
    assert loop._load(run) == {}


def test_content_spare_still_makes_an_interrupted_publication(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "state.json.new").write_text(json.dumps({"attempted": 1}))
    assert not loop._load(run).readable


def test_prompt_preserves_an_interrupted_recovery_binding(disk_run, monkeypatch):
    repo, base, _ = disk_run
    quiet_scouts(monkeypatch)
    event, _ = hook_session(monkeypatch, repo, 'fu3-recovery-spare')
    session = hook.folder(event['session_id'])
    spare = session / 'task-1.json.new'
    spare.write_bytes(b'partial binding')
    hook.on_prompt({**event, 'prompt': 'fresh task'})
    assert spare.read_bytes() == b'partial binding'
    assert hook._task(session)['task'] == 'fresh task'
    assert (session / 'task-2.json').is_file()


def test_unclassified_permission_failure_is_not_retried(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    store.write_retained(target, b'old')

    def denied(*args):
        raise PermissionError('unclassified failure')

    monkeypatch.setattr(store.os, 'replace', denied)
    with pytest.raises(PermissionError, match='unclassified'):
        store.write_retained(target, b'new')


def test_malformed_write_token_remains_unreadable(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    (run / '.write-version').write_text('not a write token')
    assert not loop._load(run).readable


def test_waiting_prompt_uses_the_binding_selected_under_its_lock(disk_run, monkeypatch):
    repo, base, _ = disk_run
    quiet_scouts(monkeypatch)
    event, _ = hook_session(monkeypatch, repo, 'fu3-waiting-prompt')
    session = hook.folder(event['session_id'])
    binding = session / 'task.json'
    torn(binding, binding.read_bytes() + b' ')
    before = binding.read_bytes(), binding.with_suffix('.sha256').read_bytes()
    real = hook._one_prompt

    @contextmanager
    def after_another_prompt(run):
        with real(run):
            hook._recover_task(event, 'recovered task')
            yield

    monkeypatch.setattr(hook, '_one_prompt', after_another_prompt)
    hook.on_prompt({**event, 'prompt': 'additional task'})
    assert (binding.read_bytes(), binding.with_suffix('.sha256').read_bytes()) == before
    assert hook._task(session)['task'] == 'recovered task\n\nadditional task'


def test_before_fix_unsaved_reservation_is_its_own_refusal(disk_run, monkeypatch):
    from g6.core.model_calls import note_model_call

    repo, base, session = disk_run

    def scouts(*args):
        note_model_call()

    def unavailable(*args, **kwargs):
        raise PermissionError(errno.EACCES, 'reservation unavailable')

    monkeypatch.setattr(loop, '_scouts', scouts)
    monkeypatch.setattr(loop, '_save_reservation', unavailable)
    with loop._writing(session):
        text = loop._prepare_locked(repo, TASK, base, session, object())
    assert 'G6_E_COMPLETION_RESERVATION_UNSAVED' in text
    assert not loop._load(session).get('done') and rows_of(session) == []
