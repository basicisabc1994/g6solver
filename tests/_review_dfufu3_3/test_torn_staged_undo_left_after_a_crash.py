"""Review 3 of fu-3: a process that dies while it is writing a declared undo staging copy still leaves that copy in
the home for good.

Repair 2 declares the staging name in the intent before creating it, and the next recovery removes a declared copy
only when its bytes equal the undo bytes (``_remove_undo_staging``: ``if path.read_bytes() == content``). That covers
review 2's window (death after the copy is flushed, before ``os.replace``). A death one step earlier, after
``os.open(O_CREAT | O_EXCL)`` made the declared name and before the bytes are all on disk, leaves an empty or partial
copy: declared, contained, exact pattern, regular, one link, created by this home under an exclusive name. Its bytes
differ, so recovery skips it, stages a NEW name, restores the target, and removes the intent. With the intent goes the
only record that the file was G6's: nothing reads, lists or removes it afterwards. Each such death adds one.

Real disk, no injection in the recovering process: the dying process is a child that exits inside its own write.
The controls are review 2's window (the complete copy, which repair 2 removes) and the same child without a death.
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

mode = sys.argv[1]
real_open, real_fdopen, real_replace, staged = os.open, os.fdopen, os.replace, set()

def opens(path, flags, *args, **kwargs):
    descriptor = real_open(path, flags, *args, **kwargs)
    if os.path.basename(os.fspath(path)).startswith('.authority-undo-'):
        if mode == 'dies_after_creating_the_copy':
            os._exit(7)
        staged.add(descriptor)
    return descriptor

def fdopens(descriptor, *args, **kwargs):
    stream = real_fdopen(descriptor, *args, **kwargs)
    if descriptor not in staged or mode != 'dies_while_writing_the_copy':
        return stream

    class Dying:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def write(self, content):
            stream.write(content[:len(content) // 2])
            stream.flush()
            os.fsync(stream.fileno())
            os._exit(7)
    return Dying()

def replaces(source, destination):
    if mode == 'control_dies_before_the_name_is_replaced' and os.path.basename(destination) == 'retained.dat':
        os._exit(7)
    return real_replace(source, destination)

os.open, os.fdopen, os.replace = opens, fdopens, replaces
reply = distill._call(surface.library)
sys.exit(0 if reply == {'entries': []} else 3)
'''


@pytest.mark.parametrize('child', ['dies_after_creating_the_copy', 'dies_while_writing_the_copy',
                                   'control_dies_before_the_name_is_replaced', 'control_finishes'])
def test_recovery_after_a_death_inside_the_staged_write_leaves_no_copy(tmp_path, monkeypatch, child):
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
    dies = child != 'control_finishes'
    assert done.returncode == (7 if dies else 0), done.stderr[-2000:]
    after_death = sorted(path for path in target.parent.iterdir() if path.name.startswith('.authority-undo-'))
    if dies:
        assert target.read_bytes() == b'half-published' and ledger._intent_path().exists()
        declared = [row['path'] for row in
                    __import__('json').loads(ledger._intent_path().read_text(encoding='utf-8'))['body']['undo_staging']]
        assert [f'distill/{path.name}' for path in after_death] == declared     # the copy on disk is the declared one

    assert distill._call(surface.library) == {'entries': []}       # the next reader recovers
    assert target.read_bytes() == before and not ledger._intent_path().exists()
    left = sorted(path for path in target.parent.iterdir() if path.name.startswith('.authority-undo-'))
    assert not left, (
        f'recovery finished (target restored, intent and its staging declaration removed) and the home still holds '
        f'{len(left)} declared staged undo cop{"y" if len(left) == 1 else "ies"} of '
        f'{sum(path.stat().st_size for path in left)} bytes (the undo bytes are {len(before)}): '
        f'{[path.name for path in left]}; the same file the dead process declared and created: '
        f'{[path.name for path in left] == [path.name for path in after_death]}')
