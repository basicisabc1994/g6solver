# SPDX-License-Identifier: Apache-2.0
"""Synthetic protocol shapes, never captured provider data."""
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

NOW = datetime(2026, 11, 8, tzinfo=UTC)


def synthetic_codex():
    return {"ordinaryUsageAllowed": True, "rateLimits": {
        "primary": {"usedPercent": 99, "windowDurationMins": 300, "resetsAt": NOW.timestamp() + 3600},
        "secondary": {"usedPercent": 10, "windowDurationMins": 10080,
                      "resetsAt": NOW.timestamp() + 3 * 86400}}}


def test_all_codex_windows_permission_and_explicit_semantics():
    from g6._allowance.readers import codex_reading

    r = codex_reading(synthetic_codex(), "local-test", NOW, semantics={"default.secondary": "fixed"})
    assert len(r.windows) == 2
    short, week = r.windows
    assert (short.identity, short.duration_seconds, short.used_share) == ("default.primary", 18000, .99)
    assert short.semantics == "unknown" and week.semantics == "fixed"
    assert short.observed_at == NOW and short.source_version
    assert r.ordinary_usage_allowed is True
    assert r.provider == "codex" and r.profile == "local-test"


def test_codex_additional_limit_sets_and_full_unknown_window_are_retained():
    from g6._allowance.readers import codex_reading

    data = synthetic_codex()
    data["ordinaryUsageAllowed"] = False
    data["rateLimitsByLimitId"] = {"synthetic-additional": {"primary": {"usedPercent": 100}}}
    r = codex_reading(data, "local-test", NOW)
    assert len(r.windows) == 3 and r.ordinary_usage_allowed is False
    assert r.windows[-1].quota_blocked is True
    assert r.windows[-1].duration_seconds is None and r.windows[-1].reset_at is None


@pytest.mark.parametrize("value", [-1, 101, True, "10", float("nan"), float("inf"), None])
def test_invalid_share_is_unknown_and_does_not_drop_other_windows(value):
    from g6._allowance.readers import codex_reading

    data = synthetic_codex()
    data["rateLimits"]["primary"]["usedPercent"] = value
    r = codex_reading(data, "local-test", NOW)
    assert len(r.windows) == 2 and r.windows[0].used_share is None
    assert r.windows[0].failure and r.windows[1].used_share == .1


@pytest.mark.parametrize(("field", "value"), [
    ("windowDurationMins", 0), ("windowDurationMins", True), ("windowDurationMins", float("inf")),
    ("resetsAt", "tomorrow"), ("resetsAt", float("nan")), ("resetsAt", 10**100),
])
def test_invalid_duration_or_reset_keeps_known_full_constraint(field, value):
    from g6._allowance.readers import codex_reading

    data = {"rateLimits": {"primary": {"usedPercent": 100, field: value}}}
    w = codex_reading(data, "local-test", NOW).windows[0]
    assert w.quota_blocked and w.failure


@pytest.mark.parametrize("payload", [None, {}, {"rate_limits": {}}, {"rate_limits": []}, {"rate_limits": "bad"}])
def test_missing_or_malformed_claude_windows_are_explicit(payload):
    from g6._allowance.readers import claude_reading

    r = claude_reading(payload, "local-test", NOW, version="2.1.319")
    assert len(r.windows) == 2 and all(w.failure for w in r.windows)
    assert all(w.used_share is None for w in r.windows)
    assert not r.support_established


def test_synthetic_claude_shape_is_not_supported_and_iso_reset_is_checked():
    from g6._allowance.readers import claude_reading

    data = {"rate_limits": {"five_hour": {"used_percentage": 25, "resets_at": (NOW + timedelta(hours=1)).isoformat()},
                            "seven_day": {"used_percentage": 5, "resets_at": NOW.timestamp() + 86400}}}
    r = claude_reading(data, "local-test", NOW, version="2.1.319")
    assert [w.used_share for w in r.windows] == [.25, .05]
    assert r.windows[0].reset_at == NOW + timedelta(hours=1)
    assert not r.support_established and "not established" in r.failure
    bad = claude_reading(data, "local-test", NOW, version="unknown")
    assert all(w.failure for w in bad.windows) and not bad.support_established


def test_window_contract_rejects_naive_times_and_invalid_identity():
    from g6.allowance import Window

    with pytest.raises(ValueError):
        Window("codex", "p", "w", observed_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        Window("codex", "", "w", observed_at=NOW)


def test_synthetic_fixture_is_marked_and_every_claude_window_is_retained():
    from g6._allowance.readers import claude_reading

    path = Path(__file__).parents[1] / "fixtures/allowances/claude_statusline_2_1_319_synthetic.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["_fixture"] == "synthetic; not a provider capture"
    r = claude_reading(data, "p", NOW, version="2.1.319")
    assert len(r.windows) == 2 and [w.duration_seconds for w in r.windows] == [18000, 604800]
    assert all(w.used_share is not None for w in r.windows) and not r.support_established


@pytest.mark.parametrize("permission", ["true", 1, {}, None])
def test_permission_is_not_inferred_from_truthiness(permission):
    from g6._allowance.readers import codex_reading

    data = synthetic_codex()
    data["ordinaryUsageAllowed"] = permission
    assert codex_reading(data, "p", NOW).ordinary_usage_allowed is None


def test_additional_sets_preserve_nonstandard_reported_window_identity():
    from g6._allowance.readers import codex_reading

    payload = synthetic_codex()
    payload["rateLimitsByLimitId"] = {"synthetic": {"other_window": {
        "usedPercent": 100, "windowDurationMins": 60, "resetsAt": NOW.timestamp()+100}}}
    r = codex_reading(payload, "p", NOW)
    assert len(r.windows) == 3 and r.windows[-1].quota_blocked is True
    assert not r.windows[-1].identity.endswith(".missing")


def test_malformed_extra_codex_window_is_retained_beside_valid_sibling():
    from g6._allowance.readers import codex_reading

    payload = synthetic_codex()
    payload["rateLimitsByLimitId"] = {"synthetic": {"primary": None, "secondary": payload["rateLimits"]["secondary"]}}
    r = codex_reading(payload, "p", NOW)
    assert len(r.windows) == 4 and r.windows[2].failure and r.windows[3].used_share == .1


def test_unrecognised_claude_window_retains_constraint_without_granting_semantics():
    from g6._allowance.readers import claude_reading

    r = claude_reading({"rate_limits": {"new_window": {"used_percentage": 100, "resets_at": NOW.timestamp()+100}}},
                       "p", NOW, version="2.1.319")
    assert len(r.windows) == 3 and r.windows[-1].quota_blocked
    assert r.windows[-1].duration_seconds is None and r.windows[-1].semantics == "unknown"
