# SPDX-License-Identifier: Apache-2.0
"""``check_staleness``: how old is each passage's source, going by the date the source itself declares?

A passage's age comes only from its ``published_at``: the source's own publication date in ISO 8601 (``2024``,
``2024-05``, ``2024-05-17``, or a date and time). A year or a month counts from its first day, which can only make a
source look older. With no date, a date that cannot be read, or a date after ``today``, the passage is ``unknown``
and gets no freshness score: an undated source is never assumed fresh, and the time the text was indexed or captured
is not its age. There is no year cap (audit 014, GRD-017: the archive read years only up to 2030, and took them from
the citation label).

Ages are compared with thresholds the caller chooses, never with a window the source declares for itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from g6.grounding.retrieval import Passage

WARN_DAYS = 365        # older than this is stale
EXPIRE_DAYS = 1825     # older than this is expired
STALENESS = ("fresh", "stale", "expired", "unknown")
_YEAR_OR_MONTH = re.compile(r"(\d{4})(?:-(\d{2}))?")


@dataclass(frozen=True)
class SourceAge:
    source: str
    status: str                          # one of STALENESS
    published_at: str = ""               # as the source declared it
    age_days: int | None = None
    freshness: float | None = None       # 1.0 fresh, falling to 0.0 across the stale window; None when unknown
    reason: str = ""


@dataclass(frozen=True)
class StalenessReport:
    today: str
    warn_days: int
    expire_days: int
    sources: tuple[SourceAge, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {s: sum(a.status == s for a in self.sources) for s in STALENESS}

    @property
    def all_fresh(self) -> bool:
        """True only when there is a passage and every passage is dated and fresh."""
        return bool(self.sources) and all(a.status == "fresh" for a in self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {"today": self.today, "warn_days": self.warn_days, "expire_days": self.expire_days,
                "counts": self.counts, "all_fresh": self.all_fresh, "sources": [dict(a.__dict__) for a in self.sources]}


def _parse_date(value: Any) -> date | None:
    """The date in ``value`` (a ``date``, ``datetime`` or ISO 8601 text), else None. audit 014, GRD-017: no year cap."""
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    try:
        partial = _YEAR_OR_MONTH.fullmatch(text)
        if partial:
            return date(int(partial.group(1)), int(partial.group(2) or 1), 1)
        return _parse_date(datetime.fromisoformat(text)) if text else None
    except ValueError:
        return None


def _declared_date(passage: Passage) -> date | None:
    """audit 014, GRD-017: the date comes from the source's own metadata, never parsed out of its label."""
    return _parse_date(passage.published_at)


def check_staleness(passages: list[Passage], *, today: date | None = None, warn_days: int = WARN_DAYS,
                    expire_days: int = EXPIRE_DAYS) -> StalenessReport:
    """One ``SourceAge`` per passage, judged against ``today`` (default: the current UTC date)."""
    if not 0 <= warn_days <= expire_days:
        raise ValueError("need 0 <= warn_days <= expire_days")
    if isinstance(today, datetime):
        today = today.date()
    today = today or datetime.now(timezone.utc).date()
    ages = []
    for p in passages:
        declared = str(p.published_at or "")
        published = _declared_date(p)
        if published is None:
            reason = "published_at is not an ISO 8601 date" if declared.strip() else "no published_at"
            ages.append(SourceAge(p.source, "unknown", declared, reason=reason))
            continue
        age = (today - published).days
        if age < 0:
            ages.append(SourceAge(p.source, "unknown", declared, reason="published_at is after today"))
        elif age <= warn_days:
            ages.append(SourceAge(p.source, "fresh", declared, age, 1.0))
        elif age <= expire_days:
            freshness = round(1.0 - (age - warn_days) / (expire_days - warn_days), 3)
            ages.append(SourceAge(p.source, "stale", declared, age, freshness))
        else:
            ages.append(SourceAge(p.source, "expired", declared, age, 0.0))
    return StalenessReport(today.isoformat(), warn_days, expire_days, tuple(ages))
