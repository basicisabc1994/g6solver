# SPDX-License-Identifier: Apache-2.0
"""Frozen fractional policy boundaries; no live accounts."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 11, 8, tzinfo=UTC)
WEEK = 604800


def reading(used=.1, fraction=.5):
    from g6.allowance import Reading, Window

    w = Window("codex", "p", "week", WEEK, used, NOW + timedelta(seconds=WEEK * (1 - fraction)),
               "fixed", NOW, "synthetic/v1")
    return Reading("codex", "p", (w,), NOW, True, True, continuity_established=True)


@pytest.mark.parametrize(("used", "status"), [(.1, "behind pace"), (.45, "on pace"), (.55, "on pace"), (.56, "ahead of pace")])
def test_fractional_weekly_pace(used, status):
    from g6._allowance.advice import Settings, advise

    a = advise(reading(used), NOW, Settings(), useful_parallel_work=True)
    assert a["windows"][0]["pace"] == status
    assert a["windows"][0]["target_share"] == .5
    assert a["windows"][0]["ceiling_share"] == .7
    assert a["optional_parallelism_cap"] <= 2


@pytest.mark.parametrize(("fraction", "ceiling"), [(0, 0), (5 / 7, .8), (6 / 7 - 1e-6, .8), (6 / 7, 1)])
def test_reserve_release_is_fractional_week_only(fraction, ceiling):
    from g6._allowance.advice import Settings, advise

    a = advise(reading(0, fraction), NOW, Settings())
    assert a["windows"][0]["ceiling_share"] == pytest.approx(ceiling)


@pytest.mark.parametrize("condition", ["stale", "future", "expired", "missing", "failed", "blocked", "full short",
                                       "near full", "rolling", "unsupported", "continuity", "no work", "reserve", "owner cap"])
def test_no_increase_without_every_condition(condition):
    from g6.allowance import Window
    from g6._allowance.advice import Settings, advise

    r = reading()
    w = r.windows[0]
    settings = Settings()
    useful = True
    if condition == "stale":
        w = replace(w, observed_at=NOW - timedelta(seconds=121))
    elif condition == "future":
        w = replace(w, observed_at=NOW + timedelta(seconds=1))
    elif condition == "expired":
        w = replace(w, reset_at=NOW)
    elif condition == "missing":
        w = replace(w, used_share=None)
    elif condition == "failed":
        w = replace(w, failure="missing", attempted_at=NOW)
    elif condition == "blocked":
        r = replace(r, ordinary_usage_allowed=False)
    elif condition in {"full short", "near full"}:
        short = Window("codex", "p", "short", 18000, 1 if condition == "full short" else .95,
                       NOW + timedelta(hours=1), "unknown", NOW, "synthetic/v1")
        r = replace(r, windows=(w, short))
    elif condition == "rolling":
        w = replace(w, semantics="rolling")
    elif condition == "unsupported":
        r = replace(r, support_established=False)
    elif condition == "continuity":
        r = replace(r, continuity_established=False)
    elif condition == "no work":
        useful = False
    elif condition == "reserve":
        settings = Settings(reserve_share=.95)
    elif condition == "owner cap":
        settings = Settings(parallelism_cap=0)
    if condition not in {"full short", "near full"}:
        r = replace(r, windows=(w,))
    a = advise(r, NOW, settings, useful_parallel_work=useful)
    assert not a["suggest_increase"] and a["optional_parallelism_cap"] <= settings.parallelism_cap
    assert a["advisory_only"] and not a["cross_provider_transfer"]
    if condition in {"full short", "near full", "blocked"}:
        assert a["optional_parallelism_cap"] == 0


def test_fresh_behind_pace_with_independent_work_permits_advice_and_age_boundary():
    from g6._allowance.advice import Settings, advise

    r = reading()
    r = replace(r, windows=(replace(r.windows[0], observed_at=NOW - timedelta(seconds=120)),))
    a = advise(r, NOW, Settings(), useful_parallel_work=True)
    assert a["suggest_increase"] and a["windows"][0]["age_seconds"] == 120
    assert a["windows"][0]["headroom_share"] == .9


@pytest.mark.parametrize(("key", "value"), [("reserve_share", 1), ("reserve_share", -1), ("max_age_seconds", 0),
    ("near_full_share", 0), ("near_full_share", 1.1), ("pace_tolerance", float("nan")),
    ("parallelism_cap", True), ("parallelism_cap", -1), ("parallelism_cap", 1.5),
    ("reserve_release_fraction", 1.1), ("refresh_seconds", 121)])
def test_settings_ranges(key, value):
    from g6._allowance.advice import Settings

    with pytest.raises(ValueError):
        Settings(**{key: value})


@pytest.mark.parametrize("duration", [18000, WEEK])
def test_fixed_window_whose_start_is_in_future_cannot_supply_current_capacity(duration):
    from g6.allowance import Window
    from g6._allowance.advice import Settings, advise

    r = reading()
    future = Window("codex", "p", "future", duration, .1, NOW + timedelta(seconds=duration + 1),
                    "fixed", NOW, "synthetic/v1")
    r = replace(r, windows=(*r.windows, future))
    a = advise(r, NOW, Settings(), useful_parallel_work=True)
    assert a["windows"][-1]["availability"] != "available"
    assert a["windows"][-1]["headroom_share"] is None
    assert a["windows"][-1]["pace"] == "unknown" and not a["suggest_increase"]
