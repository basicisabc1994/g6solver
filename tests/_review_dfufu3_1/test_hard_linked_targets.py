"""Review 1 of fu-3: an undo target that is a hard link is rewritten in place, so the write lands outside the home.

``_intent_target`` resolves a target's name through ``state_path``; a second name for a file kept elsewhere (a hard
link) resolves to itself, inside the home (``g6.core.storage.plain_file`` says of it: "which resolving never shows").
``_restore`` then opens that name with ``'wb'``, which truncates and rewrites the one file both names share.

* With a home copied by hard links (``cp -al``, ``rsync --link-dest``, a snapshot tool) while a publication is in
  flight, no file is forged: the person's withdrawal completes in the original, the snapshot is read once, and its
  recovery rewrites the original's ``library.jsonl``. The method the person withdrew is admitted at A2 again in the
  original home, whose decision journal still says ``withdraw``, and review reports A2 defects from it.
* With a planted intent, a library read rewrites a file outside G6_HOME (the parent's review 4, finding 1, through a
  name inside the home).

Each has its control: the same steps with an ordinary copy, or an ordinary file.
"""
import base64
import os
from pathlib import Path
import shutil

import pytest

from g6.core.ledger import ChainLedger, canonical, digest
from g6.core.storage import state_path
from g6.distill import acceptance_gate as gate
from g6.distill.library import Library
from g6.harness import library_review_gate as review
from g6.mcp import distill
from tests.distill import test_runtime_identity_boundaries as runtime


def snapshot(source: Path, target: Path, *, linked: bool) -> None:
    """A backup of the home: every file but the lock files, as hard links or as ordinary copies."""
    for folder, _, files in os.walk(source):
        there = target / Path(folder).relative_to(source)
        there.mkdir(parents=True, exist_ok=True)
        for name in files:
            if name.endswith('.lock'):
                continue
            if linked:
                os.link(Path(folder) / name, there / name)
            else:
                shutil.copy2(Path(folder) / name, there / name)


@pytest.mark.parametrize('linked', [True, False], ids=['hard_link_snapshot', 'control_ordinary_copy'])
def test_reading_a_snapshot_does_not_readmit_what_the_person_withdrew(monkeypatch, tmp_path, linked):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)
    home, backup = tmp_path / 'home', tmp_path / 'backup' / 'home'
    append, taken = ChainLedger.append, []

    def backup_runs_now(self, kind, ident, body):
        result = append(self, kind, ident, body)
        if kind == 'library' and not taken:      # the backup runs while the withdrawal is being published
            taken.append(True)
            snapshot(home, backup, linked=linked)
        return result

    monkeypatch.setattr(ChainLedger, 'append', backup_runs_now)
    answer = gate.withdraw(entry.entry_id, Library(), reason='the person withdraws it', person='Owner')
    monkeypatch.setattr(ChainLedger, 'append', append)
    assert taken and answer.verdict == 'withdraw'
    assert [(e.status, e.authority) for e in Library().list()] == [('withdrawn', 'A0')]
    assert review.check(runtime.BEFORE, runtime.AFTER) == []
    assert (backup / 'distill' / 'authority_intent.json').exists()      # the snapshot holds the pending intent

    monkeypatch.setenv('G6_HOME', str(backup))                          # one read-only library list in the snapshot
    assert 'entries' in distill._call(distill.surface.library)
    monkeypatch.setenv('G6_HOME', str(home))

    decisions = [row['body']['verdict'] for row in ChainLedger(state_path('distill', 'acceptance_gate.jsonl')).entries()]
    assert decisions == ['withdraw']
    now = [(e.status, e.authority) for e in Library().list()]
    served = [(f['authority'], f['outcome']) for f in review.check(runtime.BEFORE, runtime.AFTER)]
    assert (now, served) == ([('withdrawn', 'A0')], []), (
        f'a read in a snapshot of the home rewrote the original home: the method the person withdrew is {now} again, '
        f'its journal still says {decisions}, and review reports {served}')


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / 'home'
    monkeypatch.setenv('G6_HOME', str(root))
    (root / 'distill').mkdir(parents=True)
    return root


@pytest.mark.parametrize('linked', [True, False], ids=['hard_link', 'control_ordinary_file'])
def test_a_library_read_never_rewrites_a_file_outside_the_home(home, tmp_path, linked):
    outside = tmp_path / 'not_g6' / 'notes.txt'
    outside.parent.mkdir()
    outside.write_text('the person wrote this', encoding='utf-8')
    target = home / 'distill' / 'retained.dat'
    if linked:
        os.link(outside, target)
    else:
        target.write_bytes(b'half-published')
    body = {'version': 1, 'committed': False, 'writes': [
        {'path': 'distill/retained.dat', 'operation': 'replace', 'after': None,
         'before': base64.b64encode(b'rewritten by G6').decode('ascii')}]}
    (home / 'distill' / 'authority_intent.json').write_text(
        canonical({'body': body, 'sha256': digest(body)}) + '\n', encoding='utf-8')
    reply = distill._call(distill.surface.library)
    observed = outside.read_text(encoding='utf-8')
    assert observed == 'the person wrote this', (
        f'a read-only library call changed a file outside G6_HOME: now {observed!r}; the reply reported '
        f"{'entries' if 'entries' in reply else reply.get('warning_card', {}).get('code')}")
    if not linked:
        assert reply == {'entries': []} and target.read_bytes() == b'rewritten by G6'
