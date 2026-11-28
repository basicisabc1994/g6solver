"""Review 4, finding 1: the undo intent names its targets by absolute path and recovery trusts them.

``_read_intent`` checks only that a target is absolute and is not the intent itself. Recovery runs at the top of
every reader (``failure_writer(reading=True)``), so a read-only call writes or deletes whatever the intent names:

* a file outside G6_HOME (every other state access goes through ``state_path``, which refuses a path out of the home);
* another home's authority records, when the intent was carried into a copy of the home (a backup, a migration);
  the copy then retires the intent and keeps the half-published records as its own trusted state;
* nothing at all, forever, when the home was moved: the undo cannot reach the old path, and every reply is blocked.

Each case has a control beside it.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from g6.core.ledger import canonical, digest

OPS = str(Path(__file__).with_name('child_ops.py'))
NO_HOST = r'C:\nonexistent\g6-no-host.exe'


def run(home, *args):
    """One fresh process on ``home``; returns (returncode, parsed stdout or None, stderr)."""
    env = {k: v for k, v in os.environ.items() if k not in {'G6_HOME', 'G6_ALLOW_UNCONFINED'}}
    env.update(G6_CLAUDE=NO_HOST, G6_CODEX=NO_HOST, PYTHONDONTWRITEBYTECODE='1')
    out = subprocess.run([sys.executable, OPS, str(home), *args], env=env, capture_output=True, text=True, timeout=180)
    try:
        said = json.loads(out.stdout)
    except ValueError:
        said = None
    return out.returncode, said, out.stderr


def write_intent(home, rows):
    body = {'version': 1, 'committed': False, 'writes': rows}
    (home / 'distill' / 'authority_intent.json').write_text(
        canonical({'body': body, 'sha256': digest(body)}) + '\n', encoding='utf-8')


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home with one stored record, so its distill folder and lock exist."""
    from g6.distill import records as r
    from g6.distill.ledger import Stores
    from tests.distill.test_records import bodies
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    Stores().for_model(r.FailureMode).put(r.FailureMode(**bodies()[6][1]))
    return tmp_path / 'home'


@pytest.mark.parametrize('before', [None, b'rewritten by G6'], ids=['delete', 'overwrite'])
def test_a_library_read_never_writes_outside_the_home(home, tmp_path, before):
    """g6_library list is a read. With an intent that names a file outside G6_HOME it deletes or rewrites it."""
    from g6.distill import surface
    from g6.mcp import distill
    outside = tmp_path / 'not_g6' / 'notes.txt'
    outside.parent.mkdir()
    outside.write_text('the person wrote this', encoding='utf-8')
    write_intent(home, [{'path': str(outside), 'operation': 'replace', 'after': None,
                         'before': None if before is None else base64.b64encode(before).decode('ascii')}])
    reply = distill._call(surface.library)
    observed = outside.read_text(encoding='utf-8') if outside.exists() else '<deleted>'
    assert observed == 'the person wrote this', (
        f'a read-only library call changed a file outside G6_HOME: now {observed!r}; '
        f"the reply reported {'entries' if 'entries' in reply else reply.get('warning_card', {}).get('code')}")


def test_control_an_intent_inside_the_home_is_undone_and_nothing_else_changes(home, tmp_path):
    from g6.distill import surface
    from g6.mcp import distill
    outside = tmp_path / 'not_g6' / 'notes.txt'
    outside.parent.mkdir()
    outside.write_text('the person wrote this', encoding='utf-8')
    chain = home / 'distill' / 'failure_mode.jsonl'
    kept = chain.read_bytes()
    chain.write_bytes(kept + b'half a row')
    write_intent(home, [{'path': str(chain), 'operation': 'replace', 'after': None,
                         'before': base64.b64encode(kept).decode('ascii')}])
    reply = distill._call(surface.library)
    assert reply == {'entries': []}
    assert chain.read_bytes() == kept
    assert not (home / 'distill' / 'authority_intent.json').exists()
    assert outside.read_text(encoding='utf-8') == 'the person wrote this'


def crashed_withdrawal(root):
    """An admitted A2 method whose withdrawal died after its library row and before its decision row."""
    home = root / 'home'
    code, said, err = run(home, 'setup')
    assert code == 0 and said['library'] == [['admitted', 'A2']], err
    code, _, err = run(home, 'withdraw', 'library')
    assert code == 73, err
    assert (home / 'distill' / 'authority_intent.json').exists()
    return home


@pytest.mark.parametrize('read_the_copy', [True, False], ids=['copy_is_read', 'control_copy_is_not_read'])
def test_reading_a_copy_of_a_home_does_not_readmit_what_the_person_withdrew(tmp_path, read_the_copy):
    """No forged file: a withdrawal dies, the home is copied (a backup), the original recovers and the person
    withdraws again, this time to the end. Then the copy is read once."""
    original = crashed_withdrawal(tmp_path / 'original')
    copy = tmp_path / 'copy' / 'home'
    shutil.copytree(original, copy)
    code, said, err = run(original, 'view')            # the original recovers: the dead withdrawal is undone
    assert code == 0 and said == {'library': [['admitted', 'A2']], 'decisions': [], 'intent': False}, err
    code, said, err = run(original, 'withdraw')        # the person withdraws; committed, replied
    assert code == 0 and said['answer']['verdict'] == 'withdraw', err
    assert said['library'] == [['withdrawn', 'A0']] and said['decisions'] == ['withdraw']
    if read_the_copy:
        code, _, err = run(copy, 'view')               # one read-only library list, in the other home
        assert code == 0, err
    code, said, err = run(original, 'review')
    assert code == 0, err
    assert (said['library'], said['found']) == ([['withdrawn', 'A0']], []), (
        'a read in a copy of the home rewrote the original home: the method the person withdrew is '
        f"{said['library']} again, its journal still says {said['decisions']}, and review reports {said['found']}")


def crashed_hold_release(root):
    """A held method whose person-authorized release died after its A2 library row, before its decision row."""
    home = root / 'home'
    code, said, err = run(home, 'setup', 'held')
    assert code == 0 and said['library'] == [['held', 'A0']], err
    code, _, err = run(home, 'restore', 'library')
    assert code == 73, err
    return home


@pytest.mark.parametrize('where', ['copy', 'control_in_place'])
def test_a_copied_home_does_not_serve_an_unjournalled_grant(tmp_path, where):
    """Review 3's finding (an A2 grant with no decision row is served), with the home copied after the crash."""
    home = crashed_hold_release(tmp_path / 'original')
    if where == 'copy':
        home = tmp_path / 'copy' / 'home'
        shutil.copytree(tmp_path / 'original' / 'home', home)
    code, said, err = run(home, 'review')
    assert code == 0, err
    assert (said['library'], said['decisions'], said['found']) == ([['held', 'A0']], [], []), (
        f"the fresh reader retired the intent (still pending: {said['intent']}) and trusts a half-published "
        f"release: library {said['library']}, decisions {said['decisions']}, review reports {said['found']}")


# Review 3's child, unchanged (tests/_review_dfufu2_3/test_crash_publication.py): a real first admission that dies
# right after its library row, before its decision row.
ADMISSION_CHILD = r'''
import os
from pathlib import Path
import sys
import pytest
from g6.core.ledger import ChainLedger
from g6.distill import acceptance_gate as gate
from g6.distill.library import Library
from tests.distill.test_acceptance_gate import rig

patch = pytest.MonkeyPatch()
gate, candidate, _, frozen, _ = rig.__wrapped__(patch, Path(sys.argv[1]))
append = ChainLedger.append

def crash_after_append(self, kind, ident, body):
    result = append(self, kind, ident, body)
    if kind == 'library':
        os._exit(73)
    return result

ChainLedger.append = crash_after_append
answer = gate.decide(candidate, Library(), frozen.registration_sha256)
raise AssertionError(('crash boundary was not reached', answer))
'''


@pytest.mark.parametrize('where', ['moved', 'control_in_place'])
def test_a_moved_home_does_not_keep_a_half_published_first_admission(tmp_path, monkeypatch, where):
    """Every target of a first admission is new, so its undo is three deletions. In a moved home they delete nothing
    (the old paths are gone, and a missing file is not an error), the intent is retired, and the certificate and the
    A1 entry stay without their decision: review 3's finding, with recovery reporting complete."""
    from g6.core.ledger import ChainLedger
    from g6.distill.library import Library
    env = dict(os.environ, G6_CLAUDE=NO_HOST, G6_CODEX=NO_HOST, PYTHONDONTWRITEBYTECODE='1')
    env.pop('G6_ALLOW_UNCONFINED', None)
    root = tmp_path / 'original'
    child = subprocess.run([sys.executable, '-c', ADMISSION_CHILD, str(root)], env=env, capture_output=True,
                           text=True, timeout=120)
    assert child.returncode == 73, child.stdout + child.stderr
    home = root / 'home'
    assert (home / 'distill' / 'authority_intent.json').exists()
    if where == 'moved':
        home = tmp_path / 'moved' / 'home'
        home.parent.mkdir()
        os.rename(root / 'home', home)
    monkeypatch.setenv('G6_HOME', str(home))
    entries = Library().list()      # a fresh normal reader
    certificates = [json.loads(p.read_text(encoding='utf-8')) for p in
                    (home / 'distill' / 'acceptance_gate_reports').glob('*.json')]
    certificates = [c for c in certificates if c.get('authority') == 'A1']
    decisions = [row for row in ChainLedger(home / 'distill' / 'acceptance_gate.jsonl').entries()
                 if row['kind'] == 'decision' and row['body'].get('verdict') == 'admit_probation']
    observed = (len(certificates), len(entries), len(decisions))
    pending = (home / 'distill' / 'authority_intent.json').exists()
    assert observed in {(0, 0, 0), (1, 1, 1)}, (
        f'A1 certificates, active entries, admission decisions = {observed}; intent still pending: {pending}; '
        f'the entry is {[(e.status, e.authority) for e in entries]}')


@pytest.mark.parametrize('where', ['moved', 'control_in_place'])
def test_a_moved_home_recovers_its_own_records(tmp_path, where):
    """The same crash, then the home folder is renamed. Its undo names the old path, so it can never finish."""
    home = crashed_hold_release(tmp_path / 'original')
    if where == 'moved':
        home = tmp_path / 'moved' / 'home'
        home.parent.mkdir()
        os.rename(tmp_path / 'original' / 'home', home)
    replies = [run(home, 'view')[1] for _ in range(2)]
    assert replies[-1] == {'library': [['held', 'A0']], 'decisions': [], 'intent': False}, (
        'recovery cannot complete in a moved home, on every call: '
        f"{replies[-1]['reply']['error'] if replies[-1] and 'reply' in replies[-1] else replies[-1]}")
