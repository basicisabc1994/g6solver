"""All registered handlers keep ordinary preflight failures in their envelope."""
import asyncio
import inspect

import pytest

from g6.core.ledger import digest
from g6.distill import acceptance_gate as gate
from g6.mcp import consent, distill
from g6.mcp.distill_backend import Service
from tests.distill import test_runtime_identity_boundaries as boundaries
from tests.distill.test_runtime_identity_boundaries import admit, method

disk = boundaries.disk

ROUTES = {
    'g6_failure_record': ('missed_defect', 'test_weakening', '{}', 'reported'),
    'g6_distill_start': ('test_weakening',),
    'g6_distill_job': ('missing-job',),
    'g6_library': ('list',),
    'g6_library_decide': None,
    'g6_self_model': (),
    'g6_simulate': ('{}',),
    'g6_extend': ('add a check',),
    'g6_research_start': (object(),),
    'g6_research_stop': (),
    'g6_research_status': (),
    'g6_research_digest': (),
}


def _handlers(backend):
    handlers = {}

    class Server:
        def tool(self):
            return lambda fn: handlers.setdefault(fn.__name__, fn)

    distill.register(Server(), object, None, backend=backend)
    return handlers


@pytest.mark.parametrize('route', ROUTES)
def test_every_handler_with_owner_policy_absent(disk, monkeypatch, tmp_path, route):
    from g6.core import door

    entry = admit(method(), disk)
    policy = tmp_path / 'absent-policy.json'
    monkeypatch.setattr(gate, 'POLICY_PATH', policy)
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    monkeypatch.setattr(door, 'is_allowed', lambda *args: True)

    async def accept(*args):
        return 'accept', {'confirm': 'change', 'name': 'Owner'}, ''

    monkeypatch.setattr(consent, 'elicit', accept)
    handlers = _handlers(Service(registration=digest('configured-registration')))
    assert set(handlers) == set(ROUTES)
    args = ROUTES[route] if route != 'g6_library_decide' else (
        entry.entry_id, 'restore', 'fresh admission', object())
    answer = handlers[route](*args)
    if inspect.isawaitable(answer):
        answer = asyncio.run(answer)
    assert isinstance(answer, dict)
    if route in {'g6_distill_start', 'g6_library_decide'}:
        assert answer.get('ok') is False
        assert answer['warning_card']['code'] == 'G6_E_DISTILL_INPUT'


@pytest.mark.parametrize('error', [FileNotFoundError, PermissionError, OSError])
def test_start_sensitive_preflight_io_error_is_enveloped(monkeypatch, error):
    from g6.core import door

    def unreadable():
        raise error('private diagnostic must stay local')

    monkeypatch.setattr(door, 'allowed_sensitive', unreadable)
    answer = _handlers(Service())['g6_distill_start']('test_weakening')
    assert answer['ok'] is False
    assert answer['warning_card']['code'] == 'G6_E_DISTILL_INPUT'
    assert 'private diagnostic' not in str(answer)


@pytest.mark.parametrize('route', ['start', 'restore'])
def test_unreadable_policy_refuses_before_store_access(disk, monkeypatch, route):
    from pathlib import Path

    read = Path.read_bytes

    def unreadable(path):
        if path == gate.POLICY_PATH:
            raise PermissionError('private policy diagnostic')
        return read(path)

    monkeypatch.setattr(Path, 'read_bytes', unreadable)
    service = Service(registration=digest('configured-registration'))
    answer = distill._call(service.distill_start, 'test_weakening') if route == 'start' else (
        distill._call(service.restore, digest('entry-not-stored')))
    assert answer['ok'] is False
    assert answer['warning_card']['code'] == 'G6_E_DISTILL_INPUT'
    assert 'private policy diagnostic' not in str(answer)


@pytest.mark.parametrize('action', ['restore', 'demote', 'withdraw'])
def test_dialog_gate_store_error_is_enveloped(disk, monkeypatch, action):
    entry = admit(method(), disk)

    async def accept(*args):
        return 'accept', {'confirm': 'change', 'name': 'Owner'}, ''

    def unreadable(*args, **kwargs):
        raise OSError('private store diagnostic')

    monkeypatch.setattr(consent, 'elicit', accept)
    monkeypatch.setattr(Service, 'restore', unreadable)
    monkeypatch.setattr(gate, 'demote', unreadable)
    monkeypatch.setattr(gate, 'withdraw', unreadable)
    answer = asyncio.run(_handlers(Service())['g6_library_decide'](
        entry.entry_id, action, 'owner decision', object()))
    assert answer['ok'] is False
    assert answer['warning_card']['code'] == 'G6_E_DISTILL_INPUT'
    assert 'private store diagnostic' not in str(answer)
