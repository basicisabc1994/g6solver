# SPDX-License-Identifier: Apache-2.0
"""Resource limits a component declares, the usage it counts itself, and the check made before each unit of work.

:class:`ResourceBounds` holds the limits (``None`` is no limit): time, disk, tokens per minute, hour, day, week and 30
days, and cost per day and per 30 days in US dollars (the unit :mod:`g6.llm_router` estimates in). Its field names
include every limit :class:`g6.goal_engine.schema.ResourceBoundsSpec` declares, so a goal's bounds construct one.

:class:`ResourceUsage` is what the component itself counted, never a figure a caller reported: tokens and cost with the
time they were recorded (on an injectable clock), disk bytes, and the time since it was made.

``check(bounds, usage)`` answers whether the next unit of work may start: ``None``, or the first limit already
reached, in words (time, then disk, then cost, then tokens from the tightest window). A limit refuses once it is
reached, because nothing is left for the next unit; :mod:`g6.goal_engine` asks a different question of a finished
observation, whether the usage stayed within its limits, so usage equal to a limit passes there. A component that
refuses returns :data:`RESOURCE_LIMIT`. G6 keeps no bill: these are the component's own counters.
"""
from __future__ import annotations

import math
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields

RESOURCE_LIMIT = "G6_E_RESOURCE_LIMIT"

_MINUTE, _HOUR, _DAY, _WEEK, _MONTH = 60.0, 3_600.0, 86_400.0, 604_800.0, 2_592_000.0


@dataclass(frozen=True)
class ResourceBounds:
    """Declared limits; ``None`` is no limit. Each one is a finite number, zero or more."""

    max_execution_seconds: float | None = None
    max_disk_bytes: int | None = None
    max_tokens_per_minute: int | None = None
    max_tokens_per_hour: int | None = None
    max_tokens_per_day: int | None = None
    max_tokens_per_week: int | None = None
    max_tokens_per_month: int | None = None
    max_cost_per_day: float | None = None
    max_cost_per_month: float | None = None

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{item.name} must be a finite number, zero or more, or None; got {value!r}")


def _amount(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite number, zero or more; got {value!r}")
    return value


@dataclass
class ResourceUsage:
    """What the component counted: tokens and cost with when they were recorded, disk bytes, and elapsed time."""

    clock: Callable[[], float] = time.monotonic
    tokens_used: int = 0
    cost_used: float = 0.0
    disk_bytes_used: int = 0
    _start: float = field(init=False)
    _tokens: list[tuple[float, int]] = field(init=False, default_factory=list)
    _costs: list[tuple[float, float]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self._start = self.clock()

    def record_tokens(self, count: int) -> None:
        count = int(_amount("count", count))
        self.tokens_used += count
        self._tokens.append((self.clock(), count))

    def record_cost(self, usd: float) -> None:
        usd = float(_amount("usd", usd))
        self.cost_used += usd
        self._costs.append((self.clock(), usd))

    def record_disk(self, nbytes: int) -> None:
        self.disk_bytes_used += int(_amount("nbytes", nbytes))

    def tokens_in_window(self, seconds: float) -> int:
        cutoff = self.clock() - seconds
        return sum(count for at, count in self._tokens if at >= cutoff)

    def cost_in_window(self, seconds: float) -> float:
        cutoff = self.clock() - seconds
        return sum(usd for at, usd in self._costs if at >= cutoff)

    @property
    def elapsed_seconds(self) -> float:
        return self.clock() - self._start


def check(bounds: ResourceBounds, usage: ResourceUsage) -> str | None:
    """``None`` when the next unit of work may start, else the first limit already reached, in words."""
    if bounds.max_execution_seconds is not None and usage.elapsed_seconds >= bounds.max_execution_seconds:
        return f"{usage.elapsed_seconds:.1f} seconds reach the limit of {bounds.max_execution_seconds:g} seconds"
    if bounds.max_disk_bytes is not None and usage.disk_bytes_used >= bounds.max_disk_bytes:
        return f"{usage.disk_bytes_used} bytes on disk reach the limit of {bounds.max_disk_bytes} bytes"
    for limit, window, label in ((bounds.max_cost_per_day, _DAY, "day"), (bounds.max_cost_per_month, _MONTH,
                                                                          "30 days")):
        if limit is not None and (spent := usage.cost_in_window(window)) >= limit:
            return f"US${spent:.4f} in the last {label} reaches the limit of US${limit:.4f}"
    for limit, window, label in ((bounds.max_tokens_per_minute, _MINUTE, "minute"),
                                 (bounds.max_tokens_per_hour, _HOUR, "hour"),
                                 (bounds.max_tokens_per_day, _DAY, "day"),
                                 (bounds.max_tokens_per_week, _WEEK, "week"),
                                 (bounds.max_tokens_per_month, _MONTH, "30 days")):
        if limit is not None and (used := usage.tokens_in_window(window)) >= limit:
            return f"{used} tokens in the last {label} reach the limit of {limit}"
    return None


def check_case_bounds(count: int, payloads: Callable[[], Iterable[object]], *,
                      max_cases: int, max_suite_bytes: int) -> None:
    """Refuse oversized suites before copying them. Bytes count builtin objects and their contents.

    Walk containers incrementally, counting shared objects once and stopping at the limit. Opaque objects
    cannot be bounded this way: callers must supply builtin data. No serialisation or user hooks run here.
    """
    for name, value in (("max_cases", max_cases), ("max_suite_bytes", max_suite_bytes)):
        if type(value) is not int or value < 0:
            raise ValueError(f"{RESOURCE_LIMIT}: {name} must be a non-negative integer")
    if count > max_cases:
        raise ValueError(f"{RESOURCE_LIMIT}: {count} cases exceed the limit of {max_cases}")
    # Iterators keep traversal memory proportional to nesting, not the number of items in a container.
    pending = [iter(payloads())]
    seen = {}
    total = 0
    while pending:
        try:
            value = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        kind = type(value)
        if kind not in (str, bytes, bytearray, int, float, bool, type(None), dict, list, tuple, set, frozenset):
            raise ValueError(f"{RESOURCE_LIMIT}: cannot bound {kind.__name__}; supply builtin case data")
        # Payload iterators may create temporary tuples: retain containers so their ids cannot be reused.
        if id(value) in seen:
            continue
        seen[id(value)] = value
        total += sys.getsizeof(value)
        if total > max_suite_bytes:
            raise ValueError(f"{RESOURCE_LIMIT}: suite exceeds {max_suite_bytes} bytes")
        if kind is dict:
            pending.append(iter(value.values()))
            pending.append(iter(value))
        elif kind in (list, tuple, set, frozenset):
            pending.append(iter(value))


__all__ = ["RESOURCE_LIMIT", "ResourceBounds", "ResourceUsage", "check", "check_case_bounds"]
