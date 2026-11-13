"""A switch away and back must fence work started before the switch."""
from datetime import UTC, datetime, timedelta


def test_refresh_started_before_profile_rotation_cannot_write_after_return(tmp_path, monkeypatch):
    from g6._allowance.store import Store, refresh_codex
    from g6.self_maintenance import usage

    at = datetime(2026, 11, 8, tzinfo=UTC)
    store = Store(tmp_path / 'allowances')
    store.bind('codex', 'p')

    def suspended_provider_read():
        # The request began under p. Two profile changes happen while it is in flight.
        store.bind('codex', 'q')
        store.bind('codex', 'p')
        return {'ordinaryUsageAllowed': True, 'rateLimits': {
            'primary': {'usedPercent': 10, 'windowDurationMins': 300,
                        'resetsAt': (at + timedelta(hours=1)).timestamp()}}}

    monkeypatch.setattr(usage, 'codex_limits', suspended_provider_read)
    refresh_codex(store, 'p', now=lambda: at)
    reloaded = Store(tmp_path / 'allowances').load('codex', 'p', at)
    assert reloaded.windows == (), 'late pre-rotation observation revived the invalidated cache'
