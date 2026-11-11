# SPDX-License-Identifier: Apache-2.0
"""Stop checks: data and pure functions, never a model's opinion.

A venture budgets two accounts per month: ``model`` (what runs may cost) and ``cash`` (money the venture spends), in
whatever unit the caller chooses; the operator only compares numbers. A budget that is missing, negative or not a
number is a stop, never "no limit", and a cap of 0 means nothing may be spent. The archive skipped a cap of 0 and
never wrote cash or local spend, so its stop could not fire (audit 011, VEN-004).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

ACCOUNTS = ("model", "cash")
_SLACK = 1e-9   # the share of a cap a sum may pass it by: 0.1 + 0.2 must not read as over the 0.3 it meets exactly


def kind_of(value: Any) -> str:
    """What ``value`` is, for a message: the name its class was made with, read so that no class of a caller's can render
    itself, raise, or run long. A refusal names this, never the value."""
    try:
        name = type.__dict__["__name__"].__get__(type(value))
    except Exception:  # noqa: BLE001 - naming a value must never fail
        return "value"
    return name[:60] if type(name) is str else "value"


def exact_number(value: Any) -> float | None:
    """``value`` as a plain ``float``, converted once where it enters: an ``int`` or a ``float`` by the base type's own
    conversion, which no subclass can override; ``None`` for anything else (a flag, a text, a type never seen), for one whose
    own conversion fails or says otherwise, for any exception, and for a value that is not finite. Nothing of the caller's
    object is used after this: its arithmetic, comparisons, truth and rendering are never asked."""
    try:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        if isinstance(value, float):
            number, own = float.__float__(value), float(value)
        else:
            whole, said = int.__int__(value), int(value)
            if type(whole) is not int or type(said) is not int or whole != said:
                return None
            number = own = float(whole)
        if type(number) is not float or type(own) is not float or not math.isfinite(number) or number != own:
            return None
        return number
    except Exception:  # noqa: BLE001 - a value that cannot be converted is no number, whatever it raised
        return None


def exact_text(value: Any) -> str | None:
    """``value`` as a plain ``str``, by ``str``'s own conversion; ``None`` when it is not text, when its own conversion fails
    or says otherwise, or for any exception. Its truth, comparisons and rendering are never asked after this."""
    try:
        if not isinstance(value, str):
            return None
        text, own = str.__str__(value), str(value)
        return text if type(text) is str and type(own) is str and text == own else None
    except Exception:  # noqa: BLE001 - a text that cannot be read is none
        return None


def amount_of(value: Any) -> float | None:
    """``value`` as a finite, positive amount (``exact_number``), or None."""
    number = exact_number(value)
    return number if number is not None and number > 0 else None


def cap_of(caps: Mapping[str, Any] | None, account: str) -> float | None:
    """The finite, non-negative cap for ``account`` (``exact_number``), or None when there is none or it cannot be read."""
    try:
        raw = None if caps is None else caps.get(account)
    except Exception:  # noqa: BLE001 - caps that cannot be read set no cap, which stops the venture
        return None
    number = exact_number(raw)
    return number if number is not None and number >= 0 else None


def over(total: float, cap: float) -> bool:
    """Whether ``total`` is past ``cap``: the one comparison of a sum with a limit, for this module's stop check and for the
    loop's weekly ceiling alike (``g6.self_maintenance.pacing``). The slack is a share of the cap, so it is as small as the
    cap is: a float sum that meets its cap is not over it, nothing past a cap by more than that share is under it at any
    scale, and a cap of zero has no slack at all. Only a total shown to be within the cap is: one that does not compare (a
    NaN, an infinity, a sum that overflowed) is over."""
    return not total - cap <= _SLACK * cap


def _committed(committed: Mapping[str, Any], account: str) -> float | None:
    """What ``committed`` holds for ``account`` as a plain number (``exact_number``), 0 when it names none; ``None`` when
    it cannot be read, or is no number."""
    try:
        raw = committed.get(account, 0.0)
    except Exception:  # noqa: BLE001 - a sum that cannot be read is not zero
        return None
    return exact_number(raw)


def stop_reason(venture: Mapping[str, Any], committed: Mapping[str, float]) -> str | None:
    """Why the venture may not start new work this month, or None when it may.

    ``committed`` is this month's spend plus open reservations, per account."""
    if venture.get("status") != "active":
        return f"venture is {venture.get('status')}"
    for account in ACCOUNTS:
        cap = cap_of(venture.get("caps"), account)
        if cap is None:
            return f"no valid {account} budget is set; an unknown budget stops the venture"
        used = _committed(committed, account)
        if used is None:
            return f"what is committed on {account} cannot be read as a number, which stops the venture"
        if over(used, cap):
            return f"budget_exhausted: {account} {used:.2f} is over this month's cap of {cap:.2f}"
    return None


def spend_refusal(venture: Mapping[str, Any], committed: Mapping[str, float], account: str,
                  amount: float) -> str | None:
    """Why ``amount`` may not be committed against ``account`` this month, or None when it may."""
    why = stop_reason(venture, committed)
    if why:
        return why
    cap = cap_of(venture.get("caps"), account)
    if cap is None:
        return f"no valid {account} budget is set; an unknown budget stops the venture"
    used, value = _committed(committed, account), exact_number(amount)
    if used is None or value is None or value < 0:
        return f"what is committed on {account}, or the amount to commit, cannot be read as a number, so it is not committed"
    if over(used + value, cap):
        return (f"budget_exhausted: committing {value:.2f} would take {account} to {used + value:.2f}, "
                f"over this month's cap of {cap:.2f}")
    return None
