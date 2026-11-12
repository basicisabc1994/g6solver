# SPDX-License-Identifier: Apache-2.0
"""Durable cache ordering and failures under two local sessions."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import pytest

NOW = datetime(2026, 11, 8, tzinfo=UTC)


def good(at=NOW, used=10, profile="p"):
    from g6._allowance.readers import codex_reading

    return codex_reading({"ordinaryUsageAllowed": True, "rateLimits": {
        "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": NOW.timestamp() + 1000},
        "secondary": {"usedPercent": 20, "windowDurationMins": 10080, "resetsAt": NOW.timestamp() + 86400}}}, profile, at)


def test_failure_merges_by_identity_without_refreshing_last_good_and_restart(tmp_path):
    from g6._allowance.store import Store

    s = Store(tmp_path)
    s.save(good())
    failed = good(NOW + timedelta(seconds=30))
    failed = replace(failed, windows=(replace(failed.windows[0], used_share=None, failure="missing"),
                                      failed.windows[1]), ordinary_usage_allowed=None)
    s.save(failed)
    r = Store(tmp_path).load("codex", "p", NOW + timedelta(seconds=31))
    assert r.windows[0].used_share == .1 and r.windows[0].observed_at == NOW
    assert r.windows[0].attempted_at == NOW + timedelta(seconds=30) and r.windows[0].failure
    assert r.windows[1].observed_at == NOW + timedelta(seconds=30)
    assert r.ordinary_usage_allowed is None


def test_racing_sessions_and_late_old_write_cannot_replace_newer_observation(tmp_path):
    from g6._allowance.store import Store

    s = Store(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(s.save, [good(NOW + timedelta(seconds=10), 70), good(NOW, 10)]))
    s.save(good(NOW, 5))
    r = Store(tmp_path).load("codex", "p", NOW + timedelta(seconds=11))
    assert r.windows[0].used_share == .7 and r.windows[0].observed_at == NOW + timedelta(seconds=10)


def test_profile_switch_missing_background_producer_and_expiry_are_unknown(tmp_path):
    from g6._allowance.store import Store

    s = Store(tmp_path)
    s.save(good())
    assert s.load("codex", "new-profile", NOW).failure
    assert s.load("claude", "p", NOW).failure
    old = s.load("codex", "p", NOW + timedelta(seconds=1000))
    assert old.windows[0].availability(NOW + timedelta(seconds=1000), 120) == "expired"


def test_refresh_lease_is_atomic_and_expires_after_crash(tmp_path):
    from g6._allowance.store import Store

    s = Store(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: s.claim_refresh("codex", "p", NOW, 60), range(2)))
    assert result.count(True) == 1
    assert not s.claim_refresh("codex", "p", NOW + timedelta(seconds=59), 60)
    assert s.claim_refresh("codex", "p", NOW + timedelta(seconds=60), 60)


def test_capture_is_allowlisted_ordered_and_usable_as_fixture(tmp_path):
    from g6._allowance.store import Store

    s = Store(tmp_path)
    payload = {"rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": NOW.timestamp()+100,
                                            "account_id": "secret"}}, "session_id": "secret"}
    s.observe_claude(payload, "p", NOW, "2.1.319")
    s.observe_claude({"rate_limits": {}}, "p", NOW - timedelta(seconds=1), "2.1.319")
    data = json.loads(s.capture_path("p").read_text(encoding="utf-8"))
    assert set(data) == {"rate_limits", "read_at"}
    assert set(data["rate_limits"]["five_hour"]) == {"used_percentage", "resets_at"}
    assert "secret" not in json.dumps(data) and data["read_at"] == NOW.isoformat()


def test_refresh_uses_only_raw_reader_and_failure_keeps_good_time(tmp_path, monkeypatch):
    from g6._allowance.store import Store, refresh_codex
    from g6.self_maintenance import usage

    s = Store(tmp_path)
    monkeypatch.setattr(usage, "codex_limits", lambda: {"rateLimits": {"primary": {"usedPercent": 100}}})
    refresh_codex(s, "p", now=lambda: NOW)
    r = s.load("codex", "p", NOW)
    assert r.windows[0].quota_blocked
    def fail():
        raise usage.UsageUnavailable("synthetic failure with data that must not escape")
    monkeypatch.setattr(usage, "codex_limits", fail)
    refresh_codex(s, "p", now=lambda: NOW + timedelta(seconds=30))
    r = s.load("codex", "p", NOW + timedelta(seconds=30))
    assert r.windows[0].observed_at == NOW and r.failure == "read failed"


def test_partial_near_full_update_keeps_new_constraint_and_old_last_good_time(tmp_path):
    from g6._allowance.advice import Settings, advise
    from g6._allowance.store import Store

    s = Store(tmp_path)
    s.save(good())
    partial = good(NOW + timedelta(seconds=30), used=99)
    partial = replace(partial, windows=(replace(partial.windows[0], reset_at=None, failure="invalid reset"),
                                        partial.windows[1]))
    s.save(partial)
    r = s.load("codex", "p", NOW + timedelta(seconds=31))
    assert r.windows[0].used_share == .99
    assert r.windows[0].last_good_at == NOW
    assert r.windows[0].observed_at == NOW + timedelta(seconds=30)
    assert advise(r, NOW + timedelta(seconds=31), Settings())["recommendation"] == "no additional optional subagents"


def test_profile_change_fences_late_writer_and_switching_back_does_not_revive_cache(tmp_path):
    from g6._allowance.store import Store

    s = Store(tmp_path)
    s.bind("codex", "p")
    s.save(good())
    s.bind("codex", "new-profile")
    s.save(good(NOW + timedelta(seconds=30)))
    assert s.load("codex", "p", NOW).windows == ()
    assert not s.claim_refresh("codex", "p", NOW, 60)
    s.bind("codex", "p")
    assert s.load("codex", "p", NOW).windows == ()
    s.bind("claude", "new-profile")
    s.observe_claude({"rate_limits": {"five_hour": {"used_percentage": 99}}}, "p", NOW, "2.1.319")
    assert not s.capture_path("p").exists()


def test_equal_time_conflict_keeps_stronger_constraint_and_records_unknown(tmp_path):
    from g6._allowance.store import Store

    s = Store(tmp_path)
    s.save(good(used=99))
    s.save(good(used=10))
    r = s.load("codex", "p", NOW)
    assert r.windows[0].used_share == .99 and r.windows[0].failure
    assert r.ordinary_usage_allowed is None


def test_equal_time_capture_cannot_reverse_first_saved_observation(tmp_path):
    from g6._allowance.store import Store

    s = Store(tmp_path)
    for used in (99, 10):
        s.observe_claude({"rate_limits": {"five_hour": {"used_percentage": used}}}, "p", NOW, "2.1.319")
    capture = json.loads(s.capture_path("p").read_text(encoding="utf-8"))
    assert capture["rate_limits"]["five_hour"]["used_percentage"] == 99


def test_failure_after_capture_publication_does_not_rollback_durable_observation(tmp_path, monkeypatch):
    from g6._allowance import store

    s = store.Store(tmp_path)
    s.observe_claude({"rate_limits": {"five_hour": {"used_percentage": 10}}}, "p", NOW, "2.1.319")
    real_replace = store.os.replace
    def fail_after_publish(source, target):
        real_replace(source, target)
        raise OSError("synthetic interruption after atomic publication")
    monkeypatch.setattr(store.os, "replace", fail_after_publish)
    later = NOW + timedelta(seconds=30)
    with pytest.raises(OSError):
        s.observe_claude({"rate_limits": {"five_hour": {"used_percentage": 99}}}, "p", later, "2.1.319")
    assert store.Store(tmp_path).load("claude", "p", later).windows[0].used_share == .99
    capture = json.loads(s.capture_path("p").read_text(encoding="utf-8"))
    assert capture["read_at"] == later.isoformat()


