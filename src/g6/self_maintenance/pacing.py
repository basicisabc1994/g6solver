# SPDX-License-Identifier: Apache-2.0
"""The loop's budget: a week per provider, paced so continuous running lasts (design 6.6).

On day *d* of a provider's week the target is the allowance x d / 7 and the ceiling the allowance x min(1, d / 5), less
the owner's reserve, which is released in the last 24 hours. A session starts only if what is used plus its worst case
stays under the ceiling. At a reported limit the loop waits for the reset; nothing here can reach for API credits.

There is one ledger, the operator's (``g6.operator.store``). Each reservation carries its own time span: from when it was
made to when it was settled, or, until then, to the end of the provider week it was made under. It is filed under that
week, and nothing moves between weeks: a reading of a provider's use counts every reservation whose span overlaps the
provider's week as it now stands, however often the provider has moved its weeks. A reservation counts in full until it is
settled, so a tick that crashes is charged its worst case. The unit is whatever the caller counts: model calls until the
relay of design section 3 can read tokens from session records. ``read_account`` reports the whole account's use, the
owner's own sessions included, and the loop gives way to it; an allowance or a reading that is an estimate says so.

The windows come from ``settings.json`` in the loop's folder. An allowance there is an estimate until the file says
``"estimated": false``, which it should only once a real figure exists. A ``week_start`` there holds until a tool reports when
its week ends: from then the meter is on the provider's own week, and the file is left as it is.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from g6.operator.store import BudgetRefused, Held, OperatorStore

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
        """This window on the provider's own week, given a moment one ends. Already on it, it is this same window, whatever
        zone the moment is written in."""
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


def standing(reading: AccountReading | None, now: datetime) -> AccountReading | None:
    """What ``reading`` still says at ``now``. Its figure is of the week that ends at its reset, so from that reset on it says
    nothing: the figure is not known until the tool is read again, and the meter counts its own ledger. The reset stays, as
    it says where the provider's weeks fall and when the week a tick reserved in ended, and so does a limit, which holds
    until its own end."""
    if reading is None or reading.resets_at is None or reading.resets_at > now:
        return reading
    return replace(reading, used=0.0, estimated=True, read_at=None)


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


def _used(held: Sequence[Held], account: AccountReading | None) -> tuple[float, bool]:
    """What a week has used, from the reservations that overlap it, and whether the account's figure is what set it. A
    charge may already be in that figure, so the larger of the two stands. A charge settled after the figure was read cannot
    be in it, nor can one whose settlement has no time, nor a reservation still open, so each goes on top. A reading with
    no date gives nothing to tell them apart."""
    pending = sum(h.amount for h in held if h.charged is None)
    charged = sum(h.charged for h in held if h.charged is not None)
    if account is None:
        return charged + pending, False
    read_at = account.read_at
    since = 0.0 if read_at is None else sum(h.charged for h in held
                                            if h.charged is not None and (h.settled is None or h.settled > read_at))
    figure = account.used + since
    above = figure > charged
    return (figure if above else charged) + pending, above


class BudgetMeter:
    def __init__(self, windows: Sequence[ProviderWindow], *, store: OperatorStore,
                 read_account: Callable[[str, datetime], AccountReading | None] | None = None) -> None:
        """``read_account(provider, now)``: what the account says at ``now``, or ``None``. A folder kept before reservations
        carried their spans is read into them here, once (``OwnerScope.adopt_books``); a provider whose old rows cannot be
        read is refused every reservation, with why, until a person looks."""
        self.windows = {w.provider: w for w in windows}
        self.scope = store.for_owner(OWNER)
        self._read_account = read_account
        self._limits: dict[str, datetime] = {}
        self._unread = self.scope.adopt_books(tuple(self.windows))

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

    def _look(self, provider: str, now: datetime) -> tuple[Admission, AccountReading | None, datetime]:
        """Where this provider's week stands, what the account says of it, and when the week began. The week is where the
        provider last said its weeks fall, kept in the store so a restart with no reading stays on it; a reading that says
        where they fall now moves it there. That moves no reservation: each counts in whichever week its span overlaps."""
        window = self.windows.get(provider)
        if window is None:
            return Admission(provider, 0, 0.0, 0.0, 0.0, False, reason=f"no budget window for {provider!r}"), None, now
        refused = self._unread.get(provider, "")
        try:
            kept = self.scope.week_grid(provider)
        except ValueError as exc:
            kept, refused = None, refused or f"where {provider}'s weeks fall cannot be read from operator.db ({exc})"
        if kept is not None:
            window = self.windows[provider] = window.aligned(kept)
        account = standing(self._read_account(provider, now), now) if self._read_account is not None else None
        reset = (account.resets_at or account.ended_at) if account is not None else None
        if reset is not None:
            window = self.windows[provider] = window.aligned(reset)
            if reset != kept:
                self.scope.keep_grid(provider, reset)
        start, day = window.place(now)
        used, above = _used(self.scope.week_held(provider, _ACCOUNT, start, start + _WEEK), account)
        reported = account.limited_until if account is not None else None
        wait = max((at for at in (reported, self._limits.get(provider)) if at is not None), default=None)
        limited = wait is not None and now < wait
        ends = (account.resets_at if account is not None else None) or window.week_end(now)
        reason = refused or (f"at a reported limit; waiting for the reset at {wait.isoformat()}" if limited else "")
        return Admission(provider, day, used, target(window.allowance, day),
                         ceiling(window.allowance, day, window.reserve_share),
                         window.estimated or (above and account is not None and account.estimated),
                         reason=reason, wait_until=wait if limited else None,
                         venture=self.scope.week_venture(provider, start), ends=ends), account, start

    def reserve(self, provider: str, now: datetime, worst_case: float) -> Admission:
        """Reserve ``worst_case`` when the week's ceiling allows it. A refusal is an admission with ``admitted`` false
        and its reason: the loop waits, and never spends elsewhere. The ceiling is checked inside the transaction that
        reserves, on every reservation overlapping the week as they stand then, so two ticks that each read room cannot both
        pass it. The reservation spans from ``now`` to the end of the week, the latest its tick may send."""
        seen, account, start = self._look(provider, now)
        if seen.reason:
            return seen

        def over_the_ceiling(held: list[Held]) -> str:
            used = _used(held, account)[0]
            if used + worst_case <= seen.ceiling + _SLACK:
                return ""
            return f"{used:g} used plus a worst case of {worst_case:g} is over day {seen.day}'s ceiling of {seen.ceiling:g}"

        end = start + _WEEK
        try:
            venture, reservation = self.scope.reserve_span(
                provider, week=start, start=now, end=end, account=_ACCOUNT, amount=worst_case,
                caps={_ACCOUNT: self.windows[provider].allowance, "cash": 0}, admit=over_the_ceiling)
        except BudgetRefused as exc:
            return replace(seen, reason=str(exc))
        return replace(seen, admitted=True, venture=venture, reservation=reservation, amount=float(worst_case), ends=end)

    def settle(self, admission: Admission, used: float, now: datetime | None = None) -> float:
        """Charge what the host counted, up or down; a tick that never settles stays charged in full. ``now``: when its
        spend ended, on the clock the account's readings are dated by. It ends the reservation's span, and a reading taken
        after it may hold the charge. Without it the span runs to the end of its week, and the charge goes on top of every
        reading: high, never low."""
        return self.scope.settle(admission.venture, admission.reservation, metered=used, at=now)


__all__ = ["AccountReading", "Admission", "BudgetMeter", "ProviderWindow", "SETTINGS_FILE", "ceiling", "read_settings",
           "standing", "target", "windows_from"]
