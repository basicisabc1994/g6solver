"""Review 2 of fu-3: a process that dies between staging the undo bytes and replacing the target leaves the staged
copy in the home for good.

``_restore`` now writes the undo bytes to ``.authority-undo-<random>`` beside the target and replaces the target's name.
Only the ``finally`` of the call that made a staging file removes it. A process that is killed after the staged file is
flushed and before ``os.replace`` (the window the repair opened; the old in-place write had no second file) leaves it
behind; the next reader's recovery stages a NEW random name, replaces the target, removes the intent, and never looks
at the old one. Each such death adds one full copy of the target's earlier bytes (a ledger, a library, the failure
records) under a name nothing reads, lists or removes.

Real disk, no injection in the recovering process: the dying process is a child that exits inside ``os.replace``.
The control is the same child without the death.
"""
import base64
import os
import subprocess
import sys

import pytest

from g6.core.ledger import canonical, digest
from g6.core.storage import state_path
from g6.distill import ledger, surface
from g6.mcp import distill

CHILD = r'''
import os, sys
from g6.distill import surface
from g6.mcp import distill

replace = os.replace
def dies_before_the_name_is_replaced(source, destination):
    if sys.argv[1] == 'dies' and os.path.basename(destination) == 'retained.dat':
        os._exit(7)
    return replace(source, destination)

os.replace = dies_before_the_name_is_replaced
reply = distill._call(surface.library)
sys.exit(0 if reply == {'entries': []} else 3)
'''


@pytest.mark.parametrize('child', ['dies', 'control_finishes'])
def test_recovery_after_a_death_between_staging_and_replace_leaves_no_copy(tmp_path, monkeypatch, child):
    home = tmp_path / 'home'
    monkeypatch.setenv('G6_HOME', str(home))
    (home / 'distill').mkdir(parents=True)
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'half-published')
    before = b'the earlier bytes of a ledger\n' * 100
    body = {'version': 1, 'committed': False, 'writes': [
        {'path': 'distill/retained.dat', 'operation': 'replace', 'after': None,
         'before': base64.b64encode(before).decode('ascii')}]}
    ledger._intent_path().write_text(canonical({'body': body, 'sha256': digest(body)}) + '\n', encoding='utf-8')

    done = subprocess.run([sys.executable, '-c', CHILD, child], env=dict(os.environ), capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == (7 if child == 'dies' else 0), done.stderr[-2000:]
    if child == 'dies':
        assert target.read_bytes() == b'half-published' and ledger._intent_path().exists()

    assert distill._call(surface.library) == {'entries': []}       # the next reader recovers
    assert target.read_bytes() == before and not ledger._intent_path().exists()
    left = sorted(path for path in target.parent.iterdir() if path.name.startswith('.authority-undo-'))
    assert not left, (
        f'recovery finished (target restored, intent removed) and the home still holds {len(left)} staged undo '
        f'cop{"y" if len(left) == 1 else "ies"} of {sum(path.stat().st_size for path in left)} bytes: '
        f'{[path.name for path in left]}; holds the earlier bytes: {[path.read_bytes() == before for path in left]}')
