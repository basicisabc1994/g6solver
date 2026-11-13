"""The watched host seam must remain outside subclass command rewrites."""
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core import host_models
from g6.core.llm import LLMError
from g6.metaharness.domain import sender_problems
from g6.metaharness.loop import _Counted, _Requests
from g6.self_maintenance import usage


@pytest.mark.parametrize('base,provider', [(usage.WatchedClaude, 'claude'), (usage.WatchedCodex, 'codex')])
def test_a_watched_subclass_cannot_rewrite_an_admitted_send(monkeypatch, base, provider, consented_provider_fakes):
    root = Path(__file__).resolve().parents[2] / '.review' / 'finding-2' / uuid4().hex
    root.mkdir(parents=True)

    class CanonicalHost(base):
        def _command(self, *args):
            argv, stdin = super()._command(*args)
            return argv, 'one canonical refused input'

    @contextmanager
    def folders():
        call = root / uuid4().hex
        work, files = call / 'work', call / 'files'
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    launched = []

    def run(argv, stdin, cwd, files, *args):
        launched.append(usage.request_identity(argv, stdin, files.parent))
        if provider == 'claude':
            return 1, json.dumps({'type': 'result', 'is_error': True, 'stop_reason': 'refusal'}), ''
        return 1, json.dumps({'type': 'error', 'codex_error_info': 'cyber_policy'}), ''

    monkeypatch.setattr(host_models, '_folders', folders)
    monkeypatch.setattr(host_models, '_environment', lambda *args: {})
    monkeypatch.setattr(host_models.key_file, 'read', lambda: {})
    monkeypatch.setattr(CanonicalHost, 'locate', lambda self: 'fake-host')
    monkeypatch.setattr(host_models, '_run', run)
    meter = _Requests()
    backend = CanonicalHost()
    assert sender_problems(backend, provider) == []
    caller = _Counted(backend, meter)
    for user in ('first wrapper input', 'second wrapper input'):
        with pytest.raises(LLMError):
            caller.complete('system', user)
    assert len(set(launched)) == 1, 'the fake host must have received identical bytes and normalized arguments'
    assert not meter.in_flight and not usage._SENT
    assert len(launched) == 1, 'a watched subclass sent identical refused input twice after rewriting admitted stdin'
