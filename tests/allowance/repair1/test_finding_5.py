"""A report holding stale settings must not undo a newer profile binding."""
from datetime import UTC, datetime, timedelta
import json


def test_old_report_cannot_rebind_after_new_settings_have_been_applied(tmp_path, monkeypatch):
    from g6.allowance import report
    from g6._allowance.readers import codex_reading
    from g6._allowance.store import Store

    monkeypatch.setenv('G6_HOME', str(tmp_path))
    at = datetime(2026, 11, 8, tzinfo=UTC)
    root = tmp_path / 'allowances'
    store = Store(root)
    settings = root / 'settings.json'
    settings.write_text(json.dumps({'profiles': {'codex': 'p'}}), encoding='utf-8')
    store.bind('codex', 'p')
    real_bind = Store.bind
    switched = False

    def bind_after_concurrent_report(self, provider, profile):
        nonlocal switched
        if provider == 'codex' and profile == 'p' and not switched:
            switched = True
            settings.write_text(json.dumps({'profiles': {'codex': 'q'}}), encoding='utf-8')
            report(now=at, refresh=False)
            store.save(codex_reading({'rateLimits': {'primary': {
                'usedPercent': 99, 'windowDurationMins': 300,
                'resetsAt': (at + timedelta(hours=1)).timestamp()}}}, 'q', at))
        real_bind(self, provider, profile)

    monkeypatch.setattr(Store, 'bind', bind_after_concurrent_report)
    report(now=at, refresh=False)
    current = store.load('codex', 'q', at)
    assert len(current.windows) == 2, 'stale report rolled binding back and deleted the new profile reading'
