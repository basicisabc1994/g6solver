"""Real-disk reproductions of incomplete authority publication."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from g6.core.storage import state_path
from g6.distill import acceptance_gate as gate
from g6.distill.library import Library
from g6.mcp import consent, distill
from g6.mcp.distill_backend import Service
from tests.distill import test_acceptance_gate as acceptance
from tests.distill import test_runtime_identity_boundaries as boundaries
from tests.distill.test_runtime_identity_boundaries import admit, method

disk = boundaries.disk
rig = acceptance.rig


@pytest.fixture
def tmp_path():
    # The managed Windows sandbox refuses pytest's recursive basetemp cleanup.
    # Allocate fresh real disk state inside the required basetemp without it.
    path = Path(__file__).resolve().parents[2] / '.bt_rev_dfufu2_2' / uuid4().hex
    path.mkdir(parents=True)
    return path


def test_failed_post_dialog_journal_does_not_silently_restore_a2(disk, monkeypatch):
    admitted = admit(method(), disk)
    held = gate._change(admitted, status='held', authority='A0', reason='owner hold')
    handlers = {}

    class Server:
        def tool(self):
            return lambda fn: handlers.setdefault(fn.__name__, fn)

    async def accept(*args):
        # A genuine filesystem failure after the person's answer: the journal
        # cannot be opened as a file. No backend or store operation is mocked.
        state_path('distill', 'acceptance_gate.jsonl').mkdir()
        return 'accept', {'confirm': 'change', 'name': 'Review owner'}, ''

    monkeypatch.setattr(consent, 'elicit', accept)
    distill.register(Server(), object, None, backend=Service())
    answer = asyncio.run(handlers['g6_library_decide'](
        held.entry_id, 'restore', 'release the hold', object()))
    assert answer['ok'] is False
    assert answer['warning_card']['code'] == 'G6_E_DISTILL_INPUT'
    current = Library().list()[0]
    assert (current.status, current.authority) == ('held', 'A0'), (
        'MCP says no authority was granted, but the failed operation persisted '
        f'{current.status}/{current.authority}; reply={answer}')


def test_withdrawal_between_certificate_and_activation_leaves_no_a1_certificate(rig, monkeypatch):
    _, candidate, _, frozen, _ = rig
    negative = frozen.acceptance[-1].case
    sentinel = admit(method(template='skip_gate.v1'), negative,
                     regression_suite=[{'case_ref': negative.case_id, 'verdict': 'no_concern'}])
    saved, release = Event(), Event()
    original = gate._save_report

    def pause_after_certificate(data, **kwargs):
        ident = original(data, **kwargs)
        if data.get('authority') == 'A1':
            saved.set()
            assert release.wait(15), 'certificate boundary not released'
        return ident

    monkeypatch.setattr(gate, '_save_report', pause_after_certificate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(gate.decide, candidate, Library(), frozen.registration_sha256)
        try:
            while not saved.wait(1):
                assert not running.done(), running.result()
            withdrawn = gate.withdraw(sentinel.entry_id, Library(),
                                      reason='owner withdrawal', person='Review owner')
            assert withdrawn.verdict == 'withdraw'
        finally:
            release.set()
        answer = running.result(timeout=30)
    assert answer.verdict == 'refuse'
    assert not answer.entry_id and not answer.report_sha256
    assert all(entry.candidate_id != candidate.candidate_id for entry in Library().list())
    certificates = [json.loads(path.read_text(encoding='utf-8'))
                    for path in state_path('distill', 'acceptance_gate_reports').glob('*.json')]
    certificates = [data for data in certificates
                    if data.get('candidate') == candidate.candidate_id and data.get('authority') == 'A1']
    assert not certificates, f'refused, never-published method retained an A1 certificate: {certificates}'
