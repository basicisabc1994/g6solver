"""A malformed foreign label must not discard valid sibling quota windows."""
from datetime import UTC, datetime
import json
import pytest


@pytest.mark.parametrize('location', ['claude-window', 'codex-window', 'codex-limit-set'])
def test_escaped_surrogate_label_stays_visible_without_dropping_siblings(tmp_path, monkeypatch, location):
    from g6._allowance.statusline import observe_payload
    from g6._allowance.store import Store, refresh_codex

    monkeypatch.setenv('G6_HOME', str(tmp_path))
    at = datetime(2026, 11, 8, tzinfo=UTC)
    store = Store(tmp_path / 'allowances')
    if location == 'claude-window':
        payload = json.dumps({'rate_limits': {
            'five_hour': {'used_percentage': 100, 'resets_at': 2000000000},
            '\ud800': {'used_percentage': 99, 'resets_at': 2000000000}}}).encode('ascii')
        observe_payload(payload, 'p', '2.1.319', observed_at=at)
        provider = 'claude'
    else:
        from g6.self_maintenance import usage
        full = {'usedPercent': 100, 'windowDurationMins': 300, 'resetsAt': 2000000000}
        data = {'rateLimits': {'primary': full}}
        if location == 'codex-window':
            data['rateLimits']['\ud800'] = full
        else:
            data['rateLimitsByLimitId'] = {'\ud800': {'primary': full}}
        wire = json.dumps(data)
        monkeypatch.setattr(usage, 'codex_limits', lambda: json.loads(wire))
        refresh_codex(store, 'p', now=lambda: at)
        provider = 'codex'
    result = store.load(provider, 'p', at)
    assert len(result.windows) == 3, 'one foreign label discarded the whole observation'
