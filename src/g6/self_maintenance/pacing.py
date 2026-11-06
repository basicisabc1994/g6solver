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

from g6.operator.gates import amount_of, exact_number, exact_text, kind_of
from g6.operator.store import BudgetRefused, Held, OperatorStore, utc

OWNER = "g6-loop"
SETTINGS_FILE = "settings.json"
#: How long a tool's reading lasts before it is asked again, and how soon an account that could not be read is looked at
#: again.
REFRESH = timedelta(minutes=15)
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
        """Each field converted once to a plain value (``exact_text``, ``amount_of``, ``exact_number``), which is all the
        ceiling is computed from."""
        provider, allowance, share = exact_text(self.provider), amount_of(self.allowance), exact_number(self.reserve_share)
        if provider is None or not provider.strip() or allowance is None or not isinstance(self.estimated, bool):
            raise ValueError("a provider window needs a provider, a finite, positive allowance and whether it is an estimate")
        if share is None or not 0 <= share < 1:
            raise ValueError("reserve_share must be in [0, 1)")
        for name, value in (("provider", provider), ("allowance", allowance), ("reserve_share", share),
                            ("week_start", utc(self.week_start))):
            object.__setattr__(self, name, value)

    def place(self, now: datetime) -> tuple[datetime, int]:
        """The start of the provider week ``now`` falls in, and which day of it this is (1 to 7)."""
        now = utc(now)
        elapsed = now - self.week_start
        start = self.week_start + _WEEK * (elapsed // _WEEK)
        return start, (now - start) // _DAY + 1

    def next_day(self, now: datetime) -> datetime:
        """When this provider's next day begins, which is when its ceiling can rise."""
        now = utc(now)
        start, day = self.place(now)
        return start + _DAY * day

    def week_end(self, now: datetime) -> datetime:
        """When the provider week ``now`` falls in ends, which is the one time its ceiling falls."""
        now = utc(now)
        return self.place(now)[0] + _WEEK

    def aligned(self, reset: datetime) -> ProviderWindow:
        """This window on the provider's own week, given a moment one ends. Already on it, it is this same window."""
        reset = utc(reset)
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
        return tuple(ProviderWindow(str(row["provider"]), float(row["allowance"]), utc(datetime.fromisoformat(row["week_start"])),
                                    float(row.get("reserve_share", 0.2)), row.get("estimated", True) is not False)
                     for row in rows)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{SETTINGS_FILE}: a window needs provider, allowance and week_start ({exc!r})") from None


def target(allowance: float, day: int) -> float:
    return allowance * day / 7


def ceiling(allowance: float, day: int, reserve_share: float) -> float:
    paced = allowance * min(1, day / 5)
    return paced if day >= 7 else min(paced, allowance * (1 - reserve_share))


def figure_of(value: Any, most: float = math.inf) -> float | None:
    """The one check of a figure an account reports, wherever it is made: a number (not a flag), finite, from zero to
    ``most``, as a plain float converted once (``exact_number``), which is all that is used after it. ``None`` for anything
    else: a NaN, an infinity, a negative, one past ``most``, a type never seen, or one whose own conversion fails or says
    otherwise. A reading with such a figure is unread (``AccountReading``); a reply with one is no reading (``usage``)."""
    number = exact_number(value)
    return number if number is not None and 0 <= number <= most else None


@dataclass(frozen=True)
class AccountReading:
    used: float = 0.0                       # the whole account's use this week, where a tool reports it
    estimated: bool = True                  # read from limit and reset signals, not from a usage figure
    limited_until: datetime | None = None   # a session window or the weekly cap was hit: nothing starts before this
    read_at: datetime | None = None         # when the figure was read; a charge after that cannot be in it
    resets_at: datetime | None = field(default=None, compare=False)     # when the week that figure is of ends, which sets the week
    ended_at: datetime | None = field(default=None, compare=False)      # a week's end already past, which still says where its weeks fall
    unread: str = ""                        # why a tool that reports could not be read, with no good figure left standing

    def __post_init__(self) -> None:
        """Every field is converted once, as the reading is made (by the reader, a caller, or ``replace``), to a plain value:
        each time through ``utc``, the figure through ``figure_of``, the flag as a ``bool`` and why it is unread as a plain
        ``str`` (``exact_text``). Nothing of a caller's object is kept. One that does not convert, for any reason, loses the
        whole reading, as a reply that wrong says nothing of the rest: it is unread, and admits nothing. Its message names
        what was given by its type, and never renders it."""
        why = ""
        for name in ("limited_until", "read_at", "resets_at", "ended_at"):
            given = getattr(self, name)
            try:
                object.__setattr__(self, name, utc(given))
            except (TypeError, ValueError):
                why = why or f"its {name}, a {kind_of(given)}, is not a moment with its zone"
        used, unread = figure_of(self.used), exact_text(self.unread)
        estimated = self.estimated if isinstance(self.estimated, bool) else None       # no class can extend ``bool``
        why = why or (f"its figure, a {kind_of(self.used)}, is not a finite number of zero or more" if used is None
                      else f"whether its figure is an estimate, a {kind_of(self.estimated)}, is not yes or no"
                      if estimated is None else f"why it is unread, a {kind_of(self.unread)}, is not text"
                      if unread is None else "")
        if not why:
            for name, value in (("used", used), ("estimated", estimated), ("unread", unread)):
                object.__setattr__(self, name, value)
            return
        for name, value in (("used", 0.0), ("estimated", True), ("limited_until", None), ("read_at", None),
                            ("resets_at", None), ("ended_at", None), ("unread", f"the account's reading does not read: {why}")):
            object.__setattr__(self, name, value)


def standing(reading: AccountReading | None, now: datetime) -> AccountReading | None:
    """What ``reading`` still says at ``now``: the one rule for whether a reading holds, which the reader hands back, its
    refresh decides by, a failed read keeps by, and the meter admits by. A figure stands until its reset (one with no
    reset, until a read replaces it). Past that reset, a limit still ahead stands alone, without the figure. Past both,
    nothing stands, and the reading says so: it is unread, and nothing is admitted on it until a read succeeds. The reset
    stays on it, as it says where the provider's weeks fall and when the week a tick reserved in ended."""
    now = utc(now)
    if reading is None or reading.unread or reading.resets_at is None or reading.resets_at > now:
        return reading
    if reading.limited_until is not None and reading.limited_until > now:
        return replace(reading, used=0.0, estimated=True, read_at=None)
    return AccountReading(unread=f"what was last read is of a week that ended at {reading.resets_at.isoformat()}, and "
                                 "nothing read since has replaced it", resets_at=reading.resets_at)


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

    def __post_init__(self) -> None:
        for name in ("wait_until", "ends"):
            object.__setattr__(self, name, utc(getattr(self, name)))


def _used(held: Sequence[Held], account: AccountReading | None) -> tuple[float, bool]:
    """What a week has used, from the reservations that overlap it, and whether the account's figure is what set it. A
    charge settled by the time the figure was read may already be in it, so the larger of the two stands. A charge settled
    after it cannot be, nor can one with no time, nor a reservation still open, so each goes on top. A reading with no
    time of its own cannot say which charges it holds, so every charge goes on top of it: high, never low."""
    pending = sum(h.amount for h in held if h.charged is None)
    charged = sum(h.charged for h in held if h.charged is not None)
    if account is None:
        return charged + pending, False
    read_at = account.read_at
    since = sum(h.charged for h in held
                if h.charged is not None and (read_at is None or h.settled is None or h.settled > read_at))
    figure = account.used + since
    above = figure > charged
    return (figure if above else charged) + pending, above


class BudgetMeter:
    def __init__(self, windows: Sequence[ProviderWindow], *, store: OperatorStore,
                 read_account: Callable[[str, datetime], AccountReading | None] | None = None) -> None:
        """``read_account(provider, now)``: what the account says at ``now``, or ``None``."""
        self.windows = {w.provider: w for w in windows}
        self.scope = store.for_owner(OWNER)
        self._read_account = read_account
        self._limits: dict[str, datetime] = {}

    def reading(self, provider: str, now: datetime) -> Admission:
        """Where this provider's week stands; ``admitted`` is left false, since nothing was asked for."""
        now = utc(now)
        name = exact_text(provider)
        if name is None:
            return Admission("", 0, 0.0, 0.0, 0.0, False, reason=f"a provider is named by text, not a {kind_of(provider)}")
        return self._look(name, now)[0]

    def _account(self, provider: str, now: datetime) -> AccountReading | None:
        """The one reader of what ``read_account`` hands back, for every use of it: an ``AccountReading`` of that very class,
        or ``None`` for no reading. Anything else it gives, and any failure of its own, is an unread reading."""
        now = utc(now)
        if self._read_account is None:
            return None
        try:
            given = self._read_account(provider, now)
        except Exception as exc:  # noqa: BLE001 - a reader that fails says nothing of the account, whatever it raised
            return AccountReading(unread=f"what reads the account failed ({kind_of(exc)})")
        if given is None or type(given) is AccountReading:
            return given
        return AccountReading(unread=f"what reads the account gave a {kind_of(given)}, not a reading")

    def limit_reached(self, provider: str, until: datetime) -> None:
        """A limit a request ran into, which the account's figure may not show yet: it stands as a reported one does. It
        is held in memory, and the loop's records hand it back after a restart."""
        until = utc(until)
        name = exact_text(provider)
        if name is None:
            raise ValueError(f"a provider is named by text, not a {kind_of(provider)}")
        self._limits[name] = until

    def next_room(self, now: datetime) -> datetime:
        """The next time any ceiling can rise: the soonest a tick refused for its budget could be admitted. An account that
        could not be read is looked at again a refresh later."""
        now = utc(now)
        soonest = min(window.next_day(now) for window in self.windows.values())
        unread = any((seen := standing(self._account(provider, now), now)) is not None and seen.unread
                     for provider in self.windows)
        return min(soonest, now + REFRESH) if unread else soonest

    def _look(self, provider: str, now: datetime) -> tuple[Admission, AccountReading | None, datetime]:
        """Where this provider's week stands, what the account says of it, and when the week began. The week is where the
        provider last said its weeks fall, kept in the store so a restart with no reading stays on it; a reading that says
        where they fall now moves it there. That moves no reservation: each counts in whichever week its span overlaps.
        Any reservation of this provider written with no span, as the book code wrote them and the operator's own
        ``reserve`` still can, is read into its span first (``OwnerScope.adopt_books``), inside the same transaction when
        this decides an admission; one that cannot be read refuses every reservation, with why, until a person looks."""
        now = utc(now)
        window = self.windows.get(provider)
        if window is None:
            return Admission(provider, 0, 0.0, 0.0, 0.0, False, reason=f"no budget window for {provider!r}"), None, now
        refused = self.scope.adopt_books((provider,)).get(provider, "")
        try:
            grid, grid_at = self.scope.kept_grid(provider)
        except ValueError as exc:
            grid, grid_at, refused = None, None, refused or f"where {provider}'s weeks fall cannot be read from operator.db ({exc})"
        given = self._account(provider, now)
        account = standing(given, now)
        if account is not None and account.unread:
            refused = refused or (f"{provider}'s account is not known ({account.unread}), so nothing is reserved on it until "
                                  "it is read again")
        reset = (account.resets_at or account.ended_at) if account is not None else None
        if reset is not None:              # a grid moves only forward: the store keeps the newer reading's (``keep_grid``)
            read_at = given.read_at if given is not None else None
            if (grid, grid_at) != (reset, read_at):
                grid, grid_at = self.scope.keep_grid(provider, reset, read_at)
        if grid is not None:
            window = self.windows[provider] = window.aligned(grid)
        start, day = window.place(now)
        try:
            held = self.scope.week_held(provider, _ACCOUNT, start, start + _WEEK)
        except ValueError as exc:
            held, refused = [], refused or (f"an amount of {provider}'s in operator.db cannot be read ({exc}), so nothing "
                                            "more is reserved on it until a person looks")
        used, above = _used(held, account)
        reported = account.limited_until if account is not None else None
        wait = max((at for at in (reported, utc(self._limits.get(provider))) if at is not None), default=None)
        limited = wait is not None and now < wait
        won = reset is not None and grid == reset and account.resets_at is not None       # the reading's own reset stands
        ends = (reset if won else None) or window.week_end(now)
        reason = refused or (f"at a reported limit; waiting for the reset at {wait.isoformat()}" if limited else "")
        return Admission(provider, day, used, target(window.allowance, day),
                         ceiling(window.allowance, day, window.reserve_share),
                         window.estimated or (above and account is not None and account.estimated),
                         reason=reason, wait_until=wait if limited else None,
                         venture=self.scope.week_venture(provider, start), ends=ends), account, start

    def reserve(self, provider: str, now: datetime, worst_case: float, *, tick: str = "") -> Admission:
        """Reserve ``worst_case`` when the week's ceiling allows it. A refusal is an admission with ``admitted`` false
        and its reason: the loop waits, and never spends elsewhere. Everything admission reads (the kept grid, the week, its
        venture, every reservation overlapping it, and so the reading's baseline) is read inside the one transaction that
        writes the reservation, so nothing it was checked against can change before it is written. The reservation runs
        from ``now``, in the week ``now`` falls in. ``tick``: the tick that makes it, which only the loop gives, under its
        folder's tick lock; a reservation made with none is never recovered (``recover``). The provider, the worst case and
        the tick are converted once, as they enter (``exact_text``, ``amount_of``): the ceiling is checked on, and the store
        reserves, the one plain amount."""
        now = utc(now)
        given, provider, worst_case, tick = provider, exact_text(provider), amount_of(worst_case), exact_text(tick)
        if provider is None or tick is None:
            return Admission("", 0, 0.0, 0.0, 0.0, False, reason=f"a provider and a tick are named by text, not a "
                                                                 f"{kind_of(given)}")
        if worst_case is None:
            return Admission(provider, 0, 0.0, 0.0, 0.0, False, reason="a worst case must be a finite, positive amount")
        seen: list[tuple[Admission, datetime]] = []

        def decide() -> tuple[str, datetime, datetime, datetime]:
            look, _, start = self._look(provider, now)
            if not look.reason and look.used + worst_case > look.ceiling + _SLACK:
                look = replace(look, reason=f"{look.used:g} used plus a worst case of {worst_case:g} is over day "
                                            f"{look.day}'s ceiling of {look.ceiling:g}")
            seen.append((look, start))
            return look.reason, start, now, start + _WEEK

        allowance = self.windows[provider].allowance if provider in self.windows else 0.0
        try:
            venture, reservation = self.scope.reserve_span(provider, account=_ACCOUNT, amount=worst_case,
                                                           caps={_ACCOUNT: allowance, "cash": 0}, decide=decide, tick=tick)
        except BudgetRefused as exc:
            if not seen:
                return Admission(provider, 0, 0.0, 0.0, 0.0, False, reason=str(exc))
            return replace(seen[-1][0], reason=str(exc))
        look, start = seen[-1]
        return replace(look, admitted=True, venture=venture, reservation=reservation, amount=worst_case, ends=start + _WEEK)

    def settle(self, admission: Admission, used: float, now: datetime | None = None) -> float:
        """Charge what the host counted, up or down; a tick that never settles stays charged in full, in every week from
        its start. The store dates the settlement by its own clock alone: a time a caller gives (``now``) is refused."""
        now = utc(now)
        if now is not None:
            raise ValueError("a settlement is dated by the store's own clock as it is written: a time a caller gives is "
                             "refused, one before its reservation began or any other")
        try:
            venture, reservation = exact_text(admission.venture), exact_text(admission.reservation)
        except Exception:  # noqa: BLE001 - an admission that cannot be read settles nothing
            venture = reservation = None
        if venture is None or reservation is None:
            raise ValueError(f"an admission names its venture and reservation by text; a {kind_of(admission)} did not")
        return self.scope.settle(venture, reservation, metered=used)

    def recover(self, provider: str) -> float:
        """Settle in full the reservations on ``provider`` that a tick made and left open, and give what was charged: only
        under the folder's tick lock, which one tick holds at a time, so every such reservation found open is one a tick
        that is gone left. One made outside a tick stays open, and counts, until its maker settles it. When an amount among
        them cannot be read (``stored_amount``) none is settled: they stay open, and admission refuses on the provider until a
        person looks (``_look``), never reading that amount as zero."""
        try:
            return self.scope.settle_open(provider, _ACCOUNT, made_by_a_tick=True)
        except ValueError:                 # nothing of it was kept: it is all or none (``OwnerScope.settle_open``)
            return 0.0

    def settle_open(self, provider: str) -> float:
        """Settle in full every reservation on ``provider`` still open (``OwnerScope.settle_open``): only for an owner that
        knows none of them can still be spending."""
        return self.scope.settle_open(provider, _ACCOUNT)


__all__ = ["REFRESH", "AccountReading", "Admission", "BudgetMeter", "ProviderWindow", "SETTINGS_FILE", "ceiling",
           "figure_of", "read_settings", "standing", "target", "utc", "windows_from"]
