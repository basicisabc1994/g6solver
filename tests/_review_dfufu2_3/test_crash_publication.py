"""Real-process crashes must not publish a strict subset of authority records."""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from g6.core.ledger import ChainLedger
from g6.distill.library import Library
from g6.harness import library_review_gate as review
from tests.distill.test_runtime_identity_boundaries import AFTER, BEFORE


@pytest.fixture
def tmp_path():
    path = Path(__file__).resolve().parents[2] / '.bt_rev_dfufu2_3' / uuid4().hex
    path.mkdir(parents=True)
    return path


CHILD = r'''
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
step = sys.argv[2]
append = ChainLedger.append
write = gate._write_report

def crash_after_append(self, kind, ident, body):
    result = append(self, kind, ident, body)
    if step == 'library' and kind == 'library':
        os._exit(73)
    return result

def crash_after_certificate(data):
    result = write(data)
    if step == 'certificate' and data.get('authority') == 'A1':
        os._exit(73)
    return result

ChainLedger.append = crash_after_append
gate._write_report = crash_after_certificate
answer = gate.decide(candidate, Library(), frozen.registration_sha256)
raise AssertionError(('crash boundary was not reached', answer))
'''


@pytest.mark.parametrize('step', ['certificate', 'library'])
def test_process_crash_does_not_leave_partial_admission(tmp_path, monkeypatch, step):
    env = dict(os.environ, G6_CLAUDE=r'C:\nonexistent\g6-no-host.exe',
               G6_CODEX=r'C:\nonexistent\g6-no-host.exe', PYTHONDONTWRITEBYTECODE='1')
    child = subprocess.run([sys.executable, '-c', CHILD, str(tmp_path), step],
                           env=env, capture_output=True, text=True, timeout=90)
    assert child.returncode == 73, child.stdout + child.stderr
    home = tmp_path / 'home'
    monkeypatch.setenv('G6_HOME', str(home))
    entries = Library().list()  # Fresh normal reader, after the process lock was released.
    certificates = [json.loads(p.read_text(encoding='utf-8')) for p in
                    (home / 'distill/acceptance_gate_reports').glob('*.json')]
    certificates = [c for c in certificates if c.get('authority') == 'A1']
    decisions = [row for row in ChainLedger(home / 'distill/acceptance_gate.jsonl').entries()
                 if row['kind'] == 'decision' and row['body'].get('verdict') == 'admit_probation']
    observed = (len(certificates), len(entries), len(decisions))
    assert observed in {(0, 0, 0), (1, 1, 1)}, (
        'fresh reader accepts partial authority after a process crash: '
        f'A1 certificates, active entries, admission decisions = {observed}')


DIALOG_CHILD = r'''
import asyncio
import os
from pathlib import Path
import sys
import pytest
from g6.core.ledger import ChainLedger
from g6.distill import acceptance_gate as gate
from g6.distill.library import Library
from g6.mcp import consent, distill
from g6.mcp.distill_backend import Service
from tests.distill.test_runtime_identity_boundaries import disk, admit, method

root = Path(sys.argv[1])
patch = pytest.MonkeyPatch()
frozen = disk.__wrapped__(patch, root)
entry = admit(method(), frozen)
held = gate._change(entry, status='held', authority='A0', reason='owner hold')
handlers = {}

class Server:
    def tool(self):
        return lambda fn: handlers.setdefault(fn.__name__, fn)

async def accept(*args):
    (root / 'answered').write_text('accept', encoding='utf-8')
    return 'accept', {'confirm': 'change', 'name': 'Review owner'}, ''

append = ChainLedger.append
def crash(self, kind, ident, body):
    result = append(self, kind, ident, body)
    if kind == 'library' and body['status'] == 'admitted':
        os._exit(73)
    return result

consent.elicit = accept
ChainLedger.append = crash
distill.register(Server(), object, None, backend=Service())
answer = asyncio.run(handlers['g6_library_decide'](
    held.entry_id, 'restore', 'release the hold', object()))
raise AssertionError(('crash boundary was not reached', answer))
'''


def test_post_dialog_crash_does_not_activate_unjournalled_a2(tmp_path, monkeypatch):
    env = dict(os.environ, G6_CLAUDE=r'C:\nonexistent\g6-no-host.exe',
               G6_CODEX=r'C:\nonexistent\g6-no-host.exe', PYTHONDONTWRITEBYTECODE='1')
    child = subprocess.run([sys.executable, '-c', DIALOG_CHILD, str(tmp_path)],
                           env=env, capture_output=True, text=True, timeout=90)
    assert child.returncode == 73, child.stdout + child.stderr
    assert (tmp_path / 'answered').read_text(encoding='utf-8') == 'accept'
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    assert ChainLedger(tmp_path / 'home/distill/acceptance_gate.jsonl').entries() == []
    [found] = review.check(BEFORE, AFTER)
    assert found['authority'] != 'A2', (
        'fresh review trusts an unjournalled post-dialog grant: '
        f"{found['authority']}/{found['outcome']}")
