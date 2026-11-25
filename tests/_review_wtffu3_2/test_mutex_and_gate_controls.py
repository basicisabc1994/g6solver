"""Passing controls for repair 1's mutex and gate claims, across real processes on the real disk.

None of these is a finding. They are the checks that came back clean: the home mutex is one mutex
for a hook process and an MCP process even when one names the home through a junction; a killed
holder leaves no stale lock; another process's publication survives a failed attempt's rollback;
a junction put where a created folder was is refused before any write.
"""
import os
import subprocess
import sys
import time

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import _completion_storage as store
from g6.workspace import _coordination as coordination
from g6.workspace._envelope import BUSY, OUTSIDE_ROOT, WorkspaceRefused

windows_links = pytest.mark.skipif(os.name != 'nt', reason='junctions are the folder links this machine can make')


def _wait_for(path, child, seconds=10):
    deadline = time.monotonic() + seconds
    while not path.exists() and child.poll() is None and time.monotonic() < deadline:
        time.sleep(.01)
    return path.exists()


@pytest.mark.parametrize('alias', [False, pytest.param(True, marks=windows_links)], ids=['direct-home', 'junction-home'])
def test_real_process_mutex_is_shared_and_released_on_death(tmp_path, monkeypatch, alias):
    home = tmp_path / 'home'
    home.mkdir()
    target = home / 'completion_loop' / 'run' / 'task.json'
    target.parent.mkdir(parents=True)
    monkeypatch.setenv('G6_HOME', str(home))
    child_home = home
    if alias:
        import _winapi
        child_home = tmp_path / 'home-alias'
        _winapi.CreateJunction(str(home), str(child_home))
    ready = tmp_path / 'ready'
    script = (
        'import sys, time\nfrom pathlib import Path\n'
        'from g6.harness import _completion_storage as store\n'
        'with store._locked(Path(sys.argv[1])):\n'
        "    Path(sys.argv[2]).write_text('locked')\n"
        '    time.sleep(30)\n'
    )
    child = subprocess.Popen(
        [sys.executable, '-c', script, str(child_home / 'completion_loop' / 'run' / 'task.json'), str(ready)],
        env={**os.environ, 'G6_HOME': str(child_home)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert _wait_for(ready, child), 'child failed to acquire the real mutex'
        monkeypatch.setattr(coordination, 'MUTEX_WAIT', .05)
        with pytest.raises(WorkspaceRefused) as refused:
            store.write_bytes(target, b'must wait')
        assert refused.value.code == BUSY
    finally:
        child.terminate()
        child.communicate(timeout=5)
        if alias:
            os.rmdir(child_home)
    assert (home / 'mutex').is_file()
    store.write_bytes(target, b'after process death')
    assert target.read_bytes() == b'after process death'


def test_another_processes_publication_survives_a_failed_attempts_rollback(tmp_path, monkeypatch):
    """Review 1's finding, across two real processes: the failed attempt's rollback compares before it restores."""
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('G6_HOME', str(home))
    target = home / 'completion_loop' / 'run' / 'task.json'
    store.mkdir(target.parent, parents=True, exist_ok=True)
    store.write_retained(target, b'original')
    ready, go = tmp_path / 'ready', tmp_path / 'go'
    script = (
        'import sys, time\nfrom pathlib import Path\n'
        'from g6.harness import _completion_storage as store\n'
        'target, ready, go = map(Path, sys.argv[1:4])\n'
        'try:\n'
        '    with store.task_publication(target):\n'
        "        store.write_retained(target, b'older attempt')\n"
        "        ready.write_text('published')\n"
        '        while not go.exists():\n'
        '            time.sleep(.01)\n'
        "        raise OSError('older attempt failed')\n"
        'except OSError as exc:\n'
        '    print(exc)\n'
    )
    child = subprocess.Popen([sys.executable, '-c', script, str(target), str(ready), str(go)],
                             env={**os.environ, 'G6_HOME': str(home)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert _wait_for(ready, child), 'child failed to publish'
        store.write_retained(target, b'newer producer')
        go.write_text('fail now')
        out, err = child.communicate(timeout=20)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    assert out.decode().strip() == 'older attempt failed', err.decode()
    assert cloud.read_retained(target, target.with_suffix('.sha256').read_text()) == b'newer producer'
    assert sorted(item.name for item in target.parent.iterdir()) == ['task.json', 'task.sha256']


@windows_links
def test_junction_inserted_after_folder_creation_is_refused(tmp_path, monkeypatch):
    import _winapi

    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('G6_HOME', str(home))
    folder = home / 'run'
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'task.json'
    sentinel.write_bytes(b'keep')
    store.mkdir(folder)
    folder.rmdir()
    _winapi.CreateJunction(str(outside), str(folder))
    try:
        with pytest.raises(WorkspaceRefused) as refused:
            store.write_bytes(folder / 'task.json', b'overwrite')
        assert refused.value.code == OUTSIDE_ROOT
        with pytest.raises(WorkspaceRefused):
            with store.task_publication(folder / 'task.json'):
                store.write_retained(folder / 'task.json', b'overwrite')
        assert sentinel.read_bytes() == b'keep'
        assert sorted(item.name for item in outside.iterdir()) == ['task.json']
    finally:
        os.rmdir(folder)
