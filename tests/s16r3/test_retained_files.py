"""Persistent producers supply completion digests to every result reader."""
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.mcp import review_loop
from g6.self_maintenance import digest


def write_completed_state(path, state):
    """A synthetic completed-state producer, usable against the old product too."""
    body = json.dumps(state).encode('utf-8')
    writer = getattr(cloud, 'write_retained', None)
    if writer is None:
        path.write_bytes(body)
    else:
        writer(path, body)


@pytest.mark.parametrize('damage', ['changed', 'missing_digest'])
def test_repeated_request_checks_its_retained_task_binding(tmp_path, monkeypatch, damage):
    repo = tmp_path / 'repo'
    repo.mkdir()
    folder = tmp_path / 'session'
    folder.mkdir()
    from g6.workforce import repo_tests
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda *a: repo)
    monkeypatch.setattr(repo_tests, 'change', lambda *a: SimpleNamespace(base_commit='original-base'))
    original = review_loop._asked(str(repo), 'task', 'HEAD', folder)
    assert original['base'] == 'original-base'
    if damage == 'changed':
        original['base'] = 'PRIVATE_CHANGED_BINDING'
        (folder / 'task.json').write_text(json.dumps(original), encoding='utf-8')
    else:
        receipt = folder / 'task.sha256'
        assert receipt.is_file(), 'retained task bindings have no completion digest'
        receipt.unlink()
    with cloud.collect_sources() as capture:
        with pytest.raises(repo_tests.RepoRefused) as refused:
            review_loop._asked(str(repo), 'task', 'HEAD', folder)
        assert refused.value.code == cloud.REQUIRED
        assert cloud.UNKNOWN in capture.sources


@pytest.mark.parametrize('damage', ['changed', 'missing_digest', 'unreadable', 'after_read'])
def test_daily_digest_reader_uses_its_producers_completed_bytes(tmp_path, monkeypatch, damage):
    reader = getattr(digest, 'read', None)
    assert reader is not None, 'retained daily reports have no completion-bound reader'
    path = digest.write(tmp_path, date(2026, 1, 1), 'shared')
    if damage == 'changed':
        path.write_text('PRIVATE_DIGEST_REPLACEMENT', encoding='utf-8')
    elif damage == 'missing_digest':
        path.with_suffix('.sha256').unlink()
    else:
        original = Path.read_bytes
        def read(value):
            if value == path:
                if damage == 'unreadable':
                    raise OSError('unreadable report')
                body = original(value)
                path.write_text('PRIVATE_DIGEST_REPLACEMENT', encoding='utf-8')
                return body
            return original(value)
        monkeypatch.setattr(Path, 'read_bytes', read)
    with cloud.collect_sources() as capture:
        text = reader(path)
        assert 'PRIVATE_DIGEST_REPLACEMENT' not in text
        if damage == 'after_read':
            assert text == 'shared'
        else:
            assert cloud.UNKNOWN in capture.sources


@pytest.mark.parametrize('stage', ['content_spare', 'digest_spare', 'content_replace', 'digest_replace'])
def test_interrupted_persistence_never_blesses_changed_completed_bytes(tmp_path, monkeypatch, stage):
    writer = getattr(cloud, 'write_retained', None)
    assert writer is not None, 'retained producers do not share a completion writer'
    path = tmp_path / 'result.json'
    writer(path, b'"shared"')
    original_write, original_replace = Path.write_bytes, cloud.os.replace
    def write(value, body):
        if (stage == 'content_spare' and value.name == 'result.json.new' or
                stage == 'digest_spare' and value.name == 'result.sha256.new'):
            raise OSError('interrupted persistence')
        return original_write(value, body)
    def replace(source, target):
        if (stage == 'content_replace' and Path(target) == path or
                stage == 'digest_replace' and Path(target) == path.with_suffix('.sha256')):
            raise OSError('interrupted persistence')
        return original_replace(source, target)
    monkeypatch.setattr(Path, 'write_bytes', write)
    monkeypatch.setattr(cloud.os, 'replace', replace)
    with pytest.raises(OSError, match='interrupted persistence'):
        writer(path, b'"PRIVATE_INTERRUPTED"')
    try:
        expected = path.with_suffix('.sha256').read_text(encoding='ascii')
        result = cloud.read_retained(path, expected)
    except ValueError:
        result = b'withheld'
    assert b'PRIVATE_INTERRUPTED' not in result
