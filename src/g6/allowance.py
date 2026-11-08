# SPDX-License-Identifier: Apache-2.0
"""Both providers' allowances, as advice only (design section 13).

Allowance readings carry no person's data: only quota figures, times and local
opaque profile tokens, never repository content, documents, answers or session
identifiers. No model is called and no source-disclosure grant is inferred.
Codex's raw reader may involve provider I/O; no sign-in file is read. The loop's
weekly meter and its accounting contract are deliberately unchanged.
"""
from __future__ import annotations

import math
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CLAUDE_VERSION = "2.1.319"
CLAUDE_SUPPORT = "Claude Code support is not established until a real sanitised 2.1.319 capture is checked."
SEMANTICS = frozenset({"fixed", "rolling", "replenishing", "unknown"})


def number(value: object) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def aware(value: datetime | None) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def profile_token(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) is None:
        raise ValueError("use an opaque local profile token, never an account identifier")
    return value


@dataclass(frozen=True)
class Window:
    provider: str
    profile: str
    identity: str
    duration_seconds: float | None = None
    used_share: float | None = None
    reset_at: datetime | None = None
    semantics: str = "unknown"
    observed_at: datetime | None = None
    source_version: str = "unknown"
    failure: str = ""
    attempted_at: datetime | None = None
    last_good_at: datetime | None = None

    def __post_init__(self) -> None:
        profile_token(self.profile)
        if self.provider not in {"codex", "claude"} or not self.identity or len(self.identity) > 160:
            raise ValueError("invalid provider or window identity")
        if self.semantics not in SEMANTICS:
            raise ValueError("unknown window semantics")
        if self.duration_seconds is not None and (not number(self.duration_seconds) or self.duration_seconds <= 0):
            raise ValueError("duration must be finite and positive")
        if self.used_share is not None and (not number(self.used_share) or not 0 <= self.used_share <= 1):
            raise ValueError("used share must be in [0, 1]")
        for at in (self.reset_at, self.observed_at, self.attempted_at, self.last_good_at):
            if at is not None and not aware(at):
                raise ValueError("times must carry a time zone")

    @property
    def quota_blocked(self) -> bool | None:
        return None if self.used_share is None else self.used_share >= 1

    def availability(self, now: datetime, max_age_seconds: float) -> str:
        if not aware(now):
            raise ValueError("now must carry a time zone")
        if self.reset_at is not None and self.reset_at <= now:
            return "expired"
        if (self.semantics == "fixed" and self.reset_at is not None and self.duration_seconds is not None
                and (self.reset_at - now).total_seconds() > self.duration_seconds):
            return "future window"
        if self.observed_at is None:
            return "missing"
        age = (now - self.observed_at).total_seconds()
        if age < 0:
            return "future observation"
        if age > max_age_seconds:
            return "stale"
        if self.failure:
            return "failed"
        if self.used_share is None or self.duration_seconds is None or self.reset_at is None:
            return "incomplete"
        return "available"


@dataclass(frozen=True)
class Reading:
    provider: str
    profile: str
    windows: tuple[Window, ...]
    attempted_at: datetime
    ordinary_usage_allowed: bool | None = None
    support_established: bool = False
    failure: str = ""
    continuity_established: bool = False

    def __post_init__(self) -> None:
        profile_token(self.profile)
        if self.provider not in {"codex", "claude"} or not aware(self.attempted_at):
            raise ValueError("invalid provider or observation time")
        if self.ordinary_usage_allowed is not None and type(self.ordinary_usage_allowed) is not bool:
            raise ValueError("ordinary-use permission must be boolean or unknown")
        if type(self.support_established) is not bool or type(self.continuity_established) is not bool:
            raise ValueError("support and continuity must be explicit booleans")
        ids = set()
        for w in self.windows:
            if w.provider != self.provider or w.profile != self.profile or w.identity in ids:
                raise ValueError("windows must have matching binding and distinct identities")
            ids.add(w.identity)


def utc_now() -> datetime:
    return datetime.now(UTC)


def display(reading: Reading) -> dict[str, Any]:
    """Lossless display fields; recommendations add no evidence authority."""
    windows = []
    for w in reading.windows:
        row = asdict(w)
        for key in ("reset_at", "observed_at", "attempted_at", "last_good_at"):
            row[key] = row[key].isoformat() if row[key] is not None else None
        row["quota_blocked"] = w.quota_blocked
        windows.append(row)
    return {"provider": reading.provider, "profile": reading.profile, "windows": windows,
            "last_attempt_at": reading.attempted_at.isoformat(), "failure": reading.failure,
            "ordinary_usage_allowed": reading.ordinary_usage_allowed,
            "support_established": reading.support_established, "continuity_established": reading.continuity_established,
            "suggest_increase": False, "optional_parallelism_cap": 0, "recommendation": "unknown",
            "advisory_only": True, "cross_provider_transfer": False}


def report(*, now: datetime | None = None, refresh: bool = True,
           useful_parallel_work: bool = False) -> dict[str, Any]:
    """Shared MCP/cache view. Hooks explicitly disable refresh; neither calls a model."""
    from g6._allowance import store
    from g6._allowance.advice import Settings, advise

    at = now or utc_now()
    root = Path(os.environ.get("G6_HOME", str(Path.home() / ".g6"))) / "allowances"
    config: dict[str, Any] = {}
    failure = ""
    settings = Settings()
    try:
        cache = store.Store(root)  # capture generations before reading any settings
        path = root / "settings.json"
        if path.exists():
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized settings")
            config = json.loads(raw)
        if not isinstance(config, dict):
            raise ValueError("invalid settings")
        settings = Settings(**config.get("policy", {}))
        profiles = config.get("profiles", {})
        semantics = config.get("semantics", {})
        if not isinstance(profiles, dict) or not isinstance(semantics, dict):
            raise ValueError("invalid settings")
        for key, token in profiles.items():
            if key not in {"codex", "claude"}:
                raise ValueError("unknown provider")
            profile_token(token)
        for provider, declarations in semantics.items():
            if provider not in {"codex", "claude"} or not isinstance(declarations, dict):
                raise ValueError("invalid semantics")
            if any(value not in SEMANTICS for value in declarations.values()):
                raise ValueError("invalid semantics")
    except Exception:
        failure = "settings or cache unavailable"
        profiles, semantics, cache = {}, {}, None
    readings = {}
    generations = {}
    for provider in ("codex", "claude"):
        profile = profiles.get(provider, "unbound")
        try:
            if cache is not None:
                generation = cache.bind(provider, profiles.get(provider))
                if generation is None:
                    raise ValueError("binding changed during settings read")
                generations[provider] = generation
            if cache is not None and provider in profiles:
                reading = cache.load(provider, profile, at)
                reading = replace(reading, windows=tuple(replace(w, semantics=semantics.get(provider, {}).get(
                    w.identity, "unknown")) for w in reading.windows))
            else:
                reading = Reading(provider, profile, (), at, failure=failure or "no profile binding")
            readings[provider] = advise(reading, at, settings, useful_parallel_work=useful_parallel_work)
        except Exception:
            readings[provider] = advise(Reading(provider, profile, (), at, failure="cache unavailable"), at, settings)
    if refresh and cache is not None and "codex" in profiles and "codex" in generations:
        try:
            store.start_refresh(cache, profiles["codex"], at, settings.refresh_seconds,
                                semantics=semantics.get("codex", {}), generation=generations["codex"])
        except Exception:
            readings["codex"]["refresh_failure"] = "refresh unavailable"
    return {"schema_version": SCHEMA_VERSION, "providers": readings, "claude_support": CLAUDE_SUPPORT,
            "advisory_only": True, "useful_parallel_work": useful_parallel_work is True,
            "limitations": ["Advice only; parallel sessions and already-running work can consume the same headroom.",
                            "Neither raw reader establishes sign-in continuity.", CLAUDE_SUPPORT]}
