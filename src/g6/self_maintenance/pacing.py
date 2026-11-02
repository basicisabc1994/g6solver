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

The windows come from ``settings.json`` in the loop's folder. An allowance there is an estimate until the file says
``"estimated": false``, which it should only once a real figure exists. A ``week_start`` there holds until a tool reports when
its week ends: from then the meter is on the provider's own week, and the file is left as it is.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from g6.operator.store import BudgetRefused, OperatorStore

OWNER = "g6-loop"
SETTINGS_FILE = "settings.json"
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

    def next_day(self, now: datetime) -> datetime:
        """When this provider's next day begins, which is when its ceiling can rise."""
        start, day = self.place(now)
        return start + _DAY * day

    def week_end(self, now: datetime) -> datetime:
        """When the provider week ``now`` falls in ends, which is the one time its ceiling falls."""
        return self.place(now)[0] + _WEEK

    def aligned(self, reset: datetime) -> ProviderWindow:
        """This window on the provider's own week, given a moment one ends. Already on it, it is this same window, since a
        week's start names it in the ledger and the same moment written in another zone must not name a new one."""
        return self if self.place(reset)[0] == reset else replace(self, week_start=reset)


def read_settings(root: str | Path) -> dict[str, Any]:
    """The settings file in the loop's folder. A ``ValueError`` says what is wrong with it."""
    path = Path(root) / SETTINGS_FILE
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{SETTINGS_FILE} cannot be read in {root} ({type(exc).__name__})") from None
    except ValueError as exc:
        raise ValueError(f"{SETTINGS_FILE} is not JSON: {exc}") from None
    if not isinstance(found, dict):
        raise ValueError(f"{SETTINGS_FILE} is not a JSON object")
    return found


def windows_from(settings: Mapping[str, Any]) -> tuple[ProviderWindow, ...]:
    """The provider windows a settings file declares; a ``ValueError`` when there are none, or one makes no sense."""
    rows = settings.get("windows")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{SETTINGS_FILE} needs windows: a list of at least one provider's week")
    try:
        return tuple(ProviderWindow(str(row["provider"]), float(row["allowance"]), datetime.fromisoformat(row["week_start"]),
                                    float(row.get("reserve_share", 0.2)), row.get("estimated", True) is not False)
                     for row in rows)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{SETTINGS_FILE}: a window needs provider, allowance and week_start ({exc!r})") from None


def _utc(moment: datetime) -> str:
    """How the ledger names a moment: the same instant has one name, in whatever zone it was written."""
    return moment.astimezone(UTC).isoformat()


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
    read_at: datetime | None = None         # when the figure was read; a charge after that cannot be in it
    resets_at: datetime | None = field(default=None, compare=False)     # when the week that figure is of ends, which sets the week
    ended_at: datetime | None = field(default=None, compare=False)      # a week's end already past, which still says where its weeks fall


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
    ends: datetime | None = None     # when this week ends, as a provider last said; a reservation covers nothing sent after


def _used(booked: float, pending: float, account: AccountReading | None,
          charged_when_read: float | None = None) -> tuple[float, bool]:
    """What this week has used, and whether the account's figure is what set it. A charge may already be in that figure,
    so the larger of the two stands. A charge made since the figure was read cannot be in it, nor can a pending
    reservation, so each goes on top. A reading with no date gives nothing to tell them apart."""
    charged = booked - pending
    since = max(0.0, charged - charged_when_read) if charged_when_read is not None else 0.0
    figure = account.used + since if account is not None else 0.0
    above = account is not None and figure > charged
    return (figure if above else charged) + pending, above


class BudgetMeter:
    def __init__(self, windows: Sequence[ProviderWindow], *, store: OperatorStore,
                 read_account: Callable[[str, datetime], AccountReading | None] | None = None) -> None:
        self.windows = {w.provider: w for w in windows}
        self.scope = store.for_owner(OWNER)
        self._read_account = read_account
        self._limits: dict[str, datetime] = {}

    def _charged_when_read(self, provider: str, book: str, account: AccountReading | None,
                           charged: float) -> float | None:
        """What the ledger had charged when this reading first reached a meter, or ``None`` for an undated one. The
        account is read between ticks, so nothing is charged between the read and that first look. The one read inside a
        tick may hold what that tick had sent by then, and the charge goes on top of it until the next reading: high, never
        low. The first look is kept in the store, so a meter that sees the same reading later, in this process or another,
        counts what was charged since from the same point."""
        if account is None or account.read_at is None:
            return None
        return self.scope.reading_baseline(provider, book, _utc(account.read_at), charged)

    def _ledger(self, window: ProviderWindow, now: datetime, moved_from: str = "") -> tuple[str, str]:
        """The ledger's venture for the week ``now`` falls in, and its book (``OwnerScope.week_ledger``): one read, which
        the reading and the reservation share. A week is named by the UTC rendering of its start, so the same instant written
        in another zone is the same week. Its cap is the allowance that held when the venture was made."""
        return self.scope.week_ledger(window.provider, _utc(window.place(now)[0]),
                                      caps={_ACCOUNT: window.allowance, "cash": 0}, moved_from=moved_from)

    def _on_the_providers_week(self, window: ProviderWindow, reset: datetime, now: datetime) -> tuple[ProviderWindow, str]:
        """``window`` moved to the week its provider reports, and the week it was counted in until then, when that week is
        still open and the move is new: the week ``now`` falls in may then keep that week's book (``_ledger``), or what was
        charged in it, and what a tick reserved there and settles there, would stop counting against the ceiling. A week
        already over gives none: the week it gives way to is a new one. The decision is in the store, so a restart finds
        it."""
        moved = self.windows[window.provider] = window.aligned(reset)
        if self.scope.week_grid(window.provider) != _utc(reset):
            self.scope.keep_week_grid(window.provider, _utc(reset))   # so a restart with no reading stays on these weeks
        return moved, (_utc(window.place(now)[0]) if moved is not window and reset > now else "")

    def reading(self, provider: str, now: datetime) -> Admission:
        """Where this provider's week stands; ``admitted`` is left false, since nothing was asked for."""
        return self._look(provider, now)[0]

    def limit_reached(self, provider: str, until: datetime) -> None:
        """A limit a request ran into, which the account's figure may not show yet: it stands as a reported one does. It
        is held in memory, and the loop's records hand it back after a restart."""
        self._limits[provider] = until

    def next_room(self, now: datetime) -> datetime:
        """The next time any ceiling can rise: the soonest a tick refused for its budget could be admitted."""
        return min(window.next_day(now) for window in self.windows.values())

    def _look(self, provider: str, now: datetime) -> tuple[Admission, AccountReading | None, float | None, str]:
        """Where this provider's week stands, what the account said, what the ledger had charged when it said it, and the
        week's book."""
        window = self.windows.get(provider)
        if window is None:
            return Admission(provider, 0, 0.0, 0.0, 0.0, False, reason=f"no budget window for {provider!r}"), None, None, ""
        account = self._read_account(provider, window.place(now)[0]) if self._read_account is not None else None
        moved_from = ""
        if account is not None and account.resets_at is not None:
            window, moved_from = self._on_the_providers_week(window, account.resets_at, now)  # it stays so, should the reading go
            if account.resets_at <= now:
                account = replace(account, used=0.0, read_at=None)  # its figure is of a week that has ended; a limit may not be
        elif account is not None and account.ended_at is not None:
            window, moved_from = self._on_the_providers_week(window, account.ended_at, now)
        elif (grid := self.scope.week_grid(provider)) is not None:   # no word on the week now: where it last said they fall
            window = self.windows[provider] = window.aligned(datetime.fromisoformat(grid))
        day = window.place(now)[1]
        venture, book = self._ledger(window, now, moved_from)
        booked = self.scope.committed(venture, book).get(_ACCOUNT, 0.0)
        pending = self.scope.outstanding(venture, book).get(_ACCOUNT, 0.0)
        charged_when_read = self._charged_when_read(provider, book, account, booked - pending)
        used, above = _used(booked, pending, account, charged_when_read)
        reported = account.limited_until if account is not None else None
        wait = max((at for at in (reported, self._limits.get(provider)) if at is not None), default=None)
        limited = wait is not None and now < wait
        ends = (account.resets_at if account is not None else None) or window.week_end(now)
        return Admission(provider, day, used, target(window.allowance, day),
                         ceiling(window.allowance, day, window.reserve_share),
                         window.estimated or (above and account is not None and account.estimated),
                         reason=f"at a reported limit; waiting for the reset at {wait.isoformat()}" if limited else "",
                         wait_until=wait if limited else None, venture=venture, ends=ends), account, charged_when_read, book

    def reserve(self, provider: str, now: datetime, worst_case: float) -> Admission:
        """Reserve ``worst_case`` when the week's ceiling allows it. A refusal is an admission with ``admitted`` false
        and its reason: the loop waits, and never spends elsewhere. The ceiling is checked inside the transaction that
        reserves, on the ledger as it stands then, so two ticks that each read room cannot both pass it. The venture and the
        book are those the look read, once: a week's book, once decided, never changes."""
        seen, account, charged_when_read, book = self._look(provider, now)
        if seen.reason:
            return seen

        def over_the_ceiling(booked: float, pending: float) -> str:
            used = _used(booked, pending, account, charged_when_read)[0]
            if used + worst_case <= seen.ceiling + _SLACK:
                return ""
            return f"{used:g} used plus a worst case of {worst_case:g} is over day {seen.day}'s ceiling of {seen.ceiling:g}"

        try:
            reservation = self.scope.reserve(seen.venture, month=book, account=_ACCOUNT, amount=worst_case,
                                             admit=over_the_ceiling)
        except BudgetRefused as exc:
            return replace(seen, reason=str(exc))
        return replace(seen, admitted=True, reservation=reservation, amount=float(worst_case),
                       ends=self.windows[provider].week_end(now))

    def settle(self, admission: Admission, used: float) -> float:
        """Charge what the host counted, up or down; a tick that never settles stays charged in full."""
        return self.scope.settle(admission.venture, admission.reservation, metered=used)


__all__ = ["AccountReading", "Admission", "BudgetMeter", "ProviderWindow", "SETTINGS_FILE", "ceiling", "read_settings",
           "target", "windows_from"]
