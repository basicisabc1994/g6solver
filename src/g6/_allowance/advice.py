# SPDX-License-Identifier: Apache-2.0
"""Pure fractional weekly objectives, subordinate to every capacity constraint.

This is advice, never a launch boundary. An unknown reading's recommendation is
zero additional optional agents. There are no reservations or provider transfers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from g6.allowance import Reading, display, number

WEEK_SECONDS = 604800


@dataclass(frozen=True)
class Settings:
    reserve_share: float = .2
    reserve_release_fraction: float = 6 / 7
    max_age_seconds: float = 120
    refresh_seconds: float = 60
    near_full_share: float = .95
    pace_tolerance: float = .05
    parallelism_cap: int = 2

    def __post_init__(self) -> None:
        for field in ("reserve_share", "reserve_release_fraction", "max_age_seconds", "refresh_seconds",
                      "near_full_share", "pace_tolerance"):
            if not number(getattr(self, field)):
                raise ValueError("policy numbers must be finite, never booleans")
        if not 0 <= self.reserve_share < 1 or not 0 <= self.reserve_release_fraction <= 1:
            raise ValueError("reserve must be in [0,1), release fraction in [0,1]")
        if not 0 < self.max_age_seconds or not 35 <= self.refresh_seconds <= self.max_age_seconds:
            raise ValueError("age must be positive; refresh interval must be at least 35s and no greater than age")
        if not 0 < self.near_full_share <= 1 or not 0 <= self.pace_tolerance <= 1:
            raise ValueError("near-full must be in (0,1], tolerance in [0,1]")
        if type(self.parallelism_cap) is not int or not 0 <= self.parallelism_cap <= 32:
            raise ValueError("parallelism cap must be an integer in [0,32]")


def advise(reading: Reading, now: datetime, settings: Settings, *, useful_parallel_work: bool = False) -> dict[str, Any]:
    result = display(reading)
    reasons = []
    if not reading.support_established:
        reasons.append("provider support not established")
    if not reading.continuity_established:
        reasons.append("profile continuity unknown")
    if reading.ordinary_usage_allowed is None:
        reasons.append("ordinary-use permission unknown")
    if reading.failure:
        reasons.append(reading.failure)
    if not reading.windows:
        reasons.append("no windows reported")
    blocked = reading.ordinary_usage_allowed is False
    ready = bool(reading.windows) and not reasons and reading.ordinary_usage_allowed is True
    paces = []
    ceiling_room = True
    for w, row in zip(reading.windows, result["windows"], strict=True):
        age = (now - w.observed_at).total_seconds() if w.observed_at is not None else None
        availability = w.availability(now, settings.max_age_seconds)
        expired = availability == "expired"
        fresh = (age is not None and 0 <= age <= settings.max_age_seconds
                 and not expired and availability != "future window")
        near_full = not expired and w.used_share is not None and w.used_share >= settings.near_full_share
        blocked = blocked or near_full or (not expired and w.quota_blocked is True)
        window_ready = availability == "available" and w.semantics == "fixed"
        ready = ready and window_ready
        why = []
        if availability != "available":
            why.append(availability)
        if w.semantics != "fixed":
            why.append("pace unknown for these semantics")
        if near_full:
            why.append("near full or full")
        row.update(age_seconds=age, availability=availability,
                   headroom_share=1 - w.used_share if fresh and w.used_share is not None else None,
                   reported_quota_blocked=w.quota_blocked,
                   quota_blocked=None if expired else w.quota_blocked,
                   blocking_state="unknown" if expired or w.used_share is None else "blocked" if near_full else "room",
                   pace="unknown", target_share=None, ceiling_share=None, elapsed_fraction=None)
        if (window_ready and w.duration_seconds == WEEK_SECONDS and reading.support_established
                and reading.continuity_established):
            fraction = max(0.0, min(1.0, 1 - (w.reset_at - now).total_seconds() / w.duration_seconds))
            ceiling = min(1.0, 7 * fraction / 5)
            if fraction < settings.reserve_release_fraction:
                ceiling = min(ceiling, 1 - settings.reserve_share)
            delta = w.used_share - fraction
            on = abs(delta) <= settings.pace_tolerance or math.isclose(abs(delta), settings.pace_tolerance, abs_tol=1e-12)
            pace = "on pace" if on else "behind pace" if delta < 0 else "ahead of pace"
            row.update(pace=pace, target_share=fraction, ceiling_share=ceiling, elapsed_fraction=fraction)
            paces.append(pace)
            ceiling_room = ceiling_room and w.used_share < ceiling
            if w.used_share >= ceiling:
                why.append("at advisory ceiling or reserve")
        elif w.duration_seconds != WEEK_SECONDS:
            why.append("capacity constraint only; no weekly objective")
        else:
            why.append("weekly objective unavailable")
        row["reason"] = "; ".join(why) or "fresh declared fixed weekly objective"
    increase = (ready and not blocked and ceiling_room and "behind pace" in paces and "ahead of pace" not in paces
                and useful_parallel_work is True and settings.parallelism_cap > 0)
    if blocked:
        recommendation = "no additional optional subagents"
    elif "ahead of pace" in paces or not ceiling_room:
        recommendation = "conserve optional fan-out"
    elif increase:
        recommendation = "use more for useful independent work"
    elif ready and paces:
        recommendation = "usual workload"
    else:
        recommendation = "unknown"
    result.update(suggest_increase=increase, optional_parallelism_cap=settings.parallelism_cap if increase else 0,
                  owner_parallelism_cap=settings.parallelism_cap, recommendation=recommendation,
                  reasons=reasons, reserve_share=settings.reserve_share)
    return result
