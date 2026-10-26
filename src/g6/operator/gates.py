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
_SLACK = 1e-9   # float sums such as 0.1 + 0.2 must not read as over a cap they meet exactly


def amount_of(value: Any) -> float | None:
    """``value`` as a finite, positive amount, or None."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def cap_of(caps: Mapping[str, Any] | None, account: str) -> float | None:
    """The finite, non-negative cap for ``account``, or None when there is none."""
    raw = (caps or {}).get(account)
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw) or raw < 0:
        return None
    return float(raw)


def stop_reason(venture: Mapping[str, Any], committed: Mapping[str, float]) -> str | None:
    """Why the venture may not start new work this month, or None when it may.

    ``committed`` is this month's spend plus open reservations, per account."""
    if venture.get("status") != "active":
        return f"venture is {venture.get('status')}"
    for account in ACCOUNTS:
        cap = cap_of(venture.get("caps"), account)
        if cap is None:
            return f"no valid {account} budget is set; an unknown budget stops the venture"
        used = float(committed.get(account, 0.0))
        if used > cap + _SLACK:
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
    used = float(committed.get(account, 0.0))
    if used + amount > cap + _SLACK:
        return (f"budget_exhausted: committing {amount:.2f} would take {account} to {used + amount:.2f}, "
                f"over this month's cap of {cap:.2f}")
    return None
