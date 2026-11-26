import os
from pathlib import Path

import pytest

from g6.core.ledger import digest
from g6.distill import acceptance_gate as gate
from g6.distill.library import Library
from g6.mcp.distill import _call
from g6.mcp.distill_backend import Service
from tests._review_dfufu2_1.test_authority_windows import disk
from tests.distill.test_runtime_identity_boundaries import admit, method


@pytest.mark.parametrize('route', ['restore', 'start'])
def test_missing_owner_policy_returns_a_refusal_envelope(disk, monkeypatch, route):
    entry = admit(method(), disk)
    withdrawn = gate.withdraw(
        entry.entry_id, Library(), reason='owner stopped method', person='Owner',
    )
    assert withdrawn.verdict == 'withdraw'
    # Installed wheels do not carry the checkout's validation directory.
    root = Path(os.environ['G6_HOME']).parent / 'installed-root'
    root.mkdir()
    policy = root / 'validation/distill/held_out/policy.json'
    assert not policy.exists()
    monkeypatch.setattr(gate, 'ROOT', root)
    monkeypatch.setattr(gate, 'POLICY_PATH', policy)
    service = Service(registration=digest('configured-registration'))
    if route == 'restore':
        answer = _call(service.restore, withdrawn.entry_id)
    else:
        answer = _call(service.distill_start, 'test_weakening')
    assert answer.get('ok') is False or answer.get('verdict') in {'block', 'refuse'}
