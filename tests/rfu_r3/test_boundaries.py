"""Repair 3 class checks: ambiguous results and failures at JSON refusal boundaries."""
import io
import json
import sys
from types import SimpleNamespace

import pytest

from g6.core.decoder_limits import UndecodableRecord, decode

PAYLOADS = [
    pytest.param(b'{"n":' + b'9' * (sys.get_int_max_str_digits() + 1) + b'}', id='integer'),
    pytest.param(b'[' * 200000 + b'0' + b']' * 200000, id='depth'),
    pytest.param(b'{"n":"\xff"}', id='utf8'),
]


@pytest.mark.parametrize('raw', PAYLOADS)
def test_shared_decoder_owns_all_decode_failures(raw):
    with pytest.raises(UndecodableRecord):
        decode(raw)


@pytest.mark.parametrize('failure', [ValueError, RecursionError, MemoryError])
def test_shared_decoder_normalizes_decoder_failures(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure('synthetic decoder failure')
    monkeypatch.setattr(json, 'loads', fail)
    with pytest.raises(UndecodableRecord):
        decode('{}')


@pytest.mark.parametrize('raw', PAYLOADS)
@pytest.mark.parametrize('route', ['catalogue', 'list', 'pull', 'delete'])
def test_provider_routes_keep_their_refusal(raw, route):
    from g6.llm_router import providers
    from g6.llm_router.envelope import PROVIDER_ERROR
    client = SimpleNamespace(get=lambda *a, **k: SimpleNamespace(body=raw))
    def call(*a, **k):
        return SimpleNamespace(body=raw)
    if route == 'catalogue':
        reply = providers.fetch_openrouter_catalogue(client=client)
    elif route == 'list':
        reply = providers.list_local_models(call=call)
    elif route == 'pull':
        reply = providers.pull_model('fixture', call=call)
    else:
        reply = providers.delete_model('fixture', call=call)
    assert reply.code == PROVIDER_ERROR and reply.completion_state == 'blocked-escalated'


@pytest.mark.parametrize('raw', PAYLOADS)
def test_child_request_is_refused_in_its_reply(monkeypatch, capsys, raw):
    from g6._agentic import _child
    # stdin normally yields text; bytes also test the decoder's invalid UTF-8 boundary.
    monkeypatch.setattr(sys, 'stdin', io.BytesIO(raw))
    assert _child.main() == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply['refused'].startswith('[G6_E_AGENTIC_CHILD_FAILED]')


@pytest.mark.parametrize('raw', PAYLOADS)
def test_certificate_inventory_is_unknown_when_not_read(monkeypatch, raw):
    from g6._agentic import protected
    from pathlib import Path
    fake = SimpleNamespace(read_text=lambda **k: raw, parent=SimpleNamespace(parent=SimpleNamespace(name='fixture')))
    monkeypatch.setattr(Path, 'rglob', lambda *a: [fake])
    protected.certificate_bound.cache_clear()
    try:
        with pytest.raises(ValueError, match='cannot be read'):
            protected.certificate_bound()
    finally:
        protected.certificate_bound.cache_clear()


@pytest.mark.parametrize('raw', PAYLOADS[:2])
@pytest.mark.parametrize('site', ['read', 'items', 'validate', 'check_op', 'sketch'])
def test_selector_decoder_sites_fail_closed(raw, site):
    from g6.core import selector
    text = raw.decode('utf-8')
    if site == 'read':
        value = selector._read(text)
        assert not any(value.values())
    elif site == 'items':
        with pytest.raises(selector._Refusal):
            selector._items(text)
    else:
        entry = {'id': 'op:fixture.run', 'kind': 'op', 'fields': [], 'validated': True,
                 'issues': [], 'sensitive': False, 'call': {'arguments': {'params_json': text}}}
        if site == 'validate':
            assert selector._validate(entry, {'arguments': {'params_json': text}, 'changed': True})[0] is False
        else:
            with pytest.raises(ValueError):
                if site == 'check_op':
                    selector._check_op(entry, {})
                else:
                    selector.invoke_sketch(['fixture.run'], {'entries': [entry]})


@pytest.mark.parametrize('raw', PAYLOADS)
@pytest.mark.parametrize('site', ['catalogue', 'corpus'])
def test_static_readers_never_supply_a_catalogue_from_bad_json(monkeypatch, raw, site):
    from g6.mcp import catalogue
    from g6.skills import corpus
    if site == 'corpus':
        corpus._loaded.cache_clear()
        with pytest.raises(ValueError):
            corpus._loaded(raw)
    else:
        catalogue.shipped.cache_clear()
        monkeypatch.setattr(catalogue, 'PATH', SimpleNamespace(read_text=lambda **k: raw))
        try:
            with pytest.raises(ValueError):
                catalogue.shipped()
        finally:
            catalogue.shipped.cache_clear()


@pytest.mark.parametrize('raw', PAYLOADS[:2])
@pytest.mark.parametrize('site', ['text_item', 'response'])
def test_repair_hook_does_not_crash_on_an_unreadable_reply(raw, site):
    from g6.harness.completion_hook import _repair
    text = '{"repair":' + raw.decode() + '}'
    response = {'content': [{'text': text}]} if site == 'text_item' else text
    assert _repair(response) is None

@pytest.mark.parametrize('failure', [RecursionError, MemoryError])
def test_override_ledger_resource_failure_is_a_ledger_refusal(monkeypatch, failure):
    from g6._agentic import ledger
    from g6.core.ledger import LedgerError
    body = {
        'outcome': 'degraded', 'component': 'fixture', 'op': 'run', 'part': 'step', 'functions': [],
        'mode': 'prompt', 'model': 'fixture', 'trigger': {'kind': 'asked'},
        'person': {'answer': 'allowed in code', 'name': 'Fixture', 'authenticated': False},
        'budget': {'max_model_calls': 1, 'max_output_chars': 10, 'max_wall_clock_s': 1.0},
        'checks': [], 'rechecked': False,
        'digests': {'source': '0'*64, 'question': '0'*64, 'params': '0'*64, 'result': '0'*64, 'prompts': []},
        'cost': {'model_calls': 0, 'failed_model_calls': 0, 'output_chars': 0, 'child_runs': 0, 'seconds': 0.0},
    }
    target = SimpleNamespace(parent=SimpleNamespace(mkdir=lambda **k: None))
    monkeypatch.setattr(ledger, 'path', lambda: target)
    monkeypatch.setattr(ledger, '_readable', lambda path: None)
    def failed_read(path):
        raise failure('synthetic bound-ledger read failure')
    monkeypatch.setattr(ledger, 'ChainLedger', failed_read)
    with pytest.raises(LedgerError):
        ledger.append(body)
