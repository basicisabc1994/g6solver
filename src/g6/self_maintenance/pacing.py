# SPDX-License-Identifier: Apache-2.0
"""The loop's budget: a week per provider, paced so continuous running lasts (design 6.6).

On day *d* of a provider's week the target is the allowance x d / 7 and the ceiling the allowance x min(1, d / 5), less
the owner's reserve, which is released in the last 24 hours. A session starts only if what is used plus its worst case
stays under the ceiling. At a reported limit the loop waits for the reset; nothing here can reach for API credits.

There is one ledger, the operator's (``g6.operator.store``), with each provider week as its own venture and the week
as its month: a reservation counts in full until it is settled, so a tick that crashes is charged its worst case. The
unit is whatever the caller counts: model calls until the relay of design section 3 can read tokens from session
records. ``read_account`` reports the whole account's use, the owner's own sessions included, and the loop gives way to
it; an allowance or a reading that is an estimate says so.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from g6.operator.store import BudgetRefused, OperatorStore

OWNER = "g6-loop"
_ACCOUNT = "model"
_WEEK = timedelta(days=7)
_DAY = timedelta(days=1)
_SLACK = 1e-9


@dataclass(frozen=True)
class ProviderWindow:
    provider: str
    allowance: float                 # one week of this provider, in the meter's unit
    week_start: datetime             # any moment a week of this provider began
    reserve_share: float = 0.2       # kept for the owner until the last 24 hours (designer's call; a setting)
    estimated: bool = False          # the allowance is estimated from a first observed cap

    def __post_init__(self) -> None:
        if not self.provider.strip() or not math.isfinite(self.allowance) or self.allowance <= 0:
            raise ValueError("a provider window needs a provider and a finite, positive allowance")
        if not 0 <= self.reserve_share < 1:
            raise ValueError("reserve_share must be in [0, 1)")
        if self.week_start.tzinfo is None:
            raise ValueError("week_start must carry a time zone: a naive time reads differently on another machine")

    def place(self, now: datetime) -> tuple[datetime, int]:
        """The start of the provider week ``now`` falls in, and which day of it this is (1 to 7)."""
        elapsed = now - self.week_start
        start = self.week_start + _WEEK * (elapsed // _WEEK)
        return start, (now - start) // _DAY + 1


def target(allowance: float, day: int) -> float:
    return allowance * day / 7


def ceiling(allowance: float, day: int, reserve_share: float) -> float:
    paced = allowance * min(1, day / 5)
    return paced if day >= 7 else min(paced, allowance * (1 - reserve_share))


@dataclass(frozen=True)
class AccountReading:
    used: float = 0.0                       # the whole account's use this week, where a tool reports it
    estimated: bool = True                  # read from limit and reset signals, not from a usage figure
    limited_until: datetime | None = None   # a session window or the weekly cap was hit: nothing starts before this


@dataclass(frozen=True)
class Admission:
    provider: str
    day: int
    used: float
    target: float
    ceiling: float
    estimated: bool
    admitted: bool = False
    reason: str = ""
    wait_until: datetime | None = None
    venture: str = ""
    reservation: str = ""
    amount: float = 0.0


def _used(committed: float, pending: float, account: AccountReading | None) -> tuple[float, bool]:
    """What this week has used, and whether the account's figure is what set it. A charge may already be in that figure,
    so the larger of the two stands. A pending reservation cannot be, so it goes on top of whichever that is."""
    charged = committed - pending
    above = account is not None and account.used > charged
    return (account.used if above else charged) + pending, above


class BudgetMeter:
    def __init__(self, windows: Sequence[ProviderWindow], *, store: OperatorStore,
                 read_account: Callable[[str, datetime], AccountReading | None] | None = None) -> None:
        self.windows = {w.provider: w for w in windows}
        self.scope = store.for_owner(OWNER)
        self._read_account = read_account

    def _venture(self, window: ProviderWindow, start: datetime) -> str:
        """The ledger's venture for this provider week; its cap is the allowance that held when the week began."""
        name = f"{window.provider}@{start.isoformat()}"
        for venture in self.scope.list_ventures():
            if venture["name"] == name:
                return venture["venture_id"]
        return self.scope.create_venture(name, caps={_ACCOUNT: window.allowance, "cash": 0})["venture_id"]

    def reading(self, provider: str, now: datetime) -> Admission:
        """Where this provider's week stands; ``admitted`` is left false, since nothing was asked for."""
        window = self.windows.get(provider)
        if window is None:
            return Admission(provider, 0, 0.0, 0.0, 0.0, False, reason=f"no budget window for {provider!r}")
        start, day = window.place(now)
        venture = self._venture(window, start)
        account = self._read_account(provider, start) if self._read_account is not None else None
        used, above = _used(self.scope.committed(venture, start.isoformat()).get(_ACCOUNT, 0.0),
                            self.scope.outstanding(venture, start.isoformat()).get(_ACCOUNT, 0.0), account)
        wait = account.limited_until if account is not None and account.limited_until is not None else None
        limited = wait is not None and now < wait
        return Admission(provider, day, used, target(window.allowance, day),
                         ceiling(window.allowance, day, window.reserve_share),
                         window.estimated or (above and account is not None and account.estimated),
                         reason=f"at a reported limit; waiting for the reset at {wait.isoformat()}" if limited else "",
                         wait_until=wait if limited else None, venture=venture)

    def reserve(self, provider: str, now: datetime, worst_case: float) -> Admission:
        """Reserve ``worst_case`` when the week's ceiling allows it. A refusal is an admission with ``admitted`` false
        and its reason: the loop waits, and never spends elsewhere."""
        seen = self.reading(provider, now)
        if seen.reason:
            return seen
        if seen.used + worst_case > seen.ceiling + _SLACK:
            return replace(seen, reason=f"{seen.used:g} used plus a worst case of {worst_case:g} is over day "
                                         f"{seen.day}'s ceiling of {seen.ceiling:g}")
        window = self.windows[provider]
        start, _ = window.place(now)
        try:
            reservation = self.scope.reserve(seen.venture, month=start.isoformat(), account=_ACCOUNT, amount=worst_case)
        except BudgetRefused as exc:
            return replace(seen, reason=str(exc))
        return replace(seen, admitted=True, reservation=reservation, amount=float(worst_case))

    def settle(self, admission: Admission, used: float) -> float:
        """Charge what the host counted, up or down; a tick that never settles stays charged in full."""
        return self.scope.settle(admission.venture, admission.reservation, metered=used)


__all__ = ["AccountReading", "Admission", "BudgetMeter", "ProviderWindow", "ceiling", "target"]
