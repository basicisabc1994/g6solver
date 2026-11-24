import json
import os
import uuid
from pathlib import Path

import pytest

from g6.harness import completion_loop as loop
from g6.workspace._envelope import WorkspaceRefused


@pytest.fixture
def tmp_path():
    # Python's Windows 0o700 temp ACL excludes this sandbox. Inherit the
    # review folder's ACL instead, keeping all storage and locking real.
    scratch = Path(__file__).parent / 'temp' / uuid.uuid4().hex
    scratch.mkdir(parents=True)
    return scratch


def test_settle_refuses_a_run_junction_outside_g6_home(tmp_path, monkeypatch):
    home, outside = tmp_path / 'home', tmp_path / 'outside-run'
    outside.mkdir()
    monkeypatch.setenv('G6_HOME', str(home))
    name = 'linked-run'
    row = json.dumps({'session': name, 'cause': 'still-owed'})
    loop._save(outside, {'attempted': 1, 'row_due': row, 'row': 'keep retry'}, sources=())
    owing = loop.state_path(*loop.OWING)
    owing.mkdir(parents=True)
    alias = owing.parent / name
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(outside), str(alias))
    else:
        alias.symlink_to(outside, target_is_directory=True)
    (owing / name).touch()

    # The existing coordination gate correctly rejects this exact linked path.
    with pytest.raises(WorkspaceRefused):
        with loop._held(alias):
            pass
    loop.settle()
    retained = loop._load(outside)
    assert retained.get('row_due') == row, retained
