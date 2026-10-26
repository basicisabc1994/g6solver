# SPDX-License-Identifier: Apache-2.0
"""The committed prices are dated, and a price older than ``advice.STALE_AFTER_DAYS`` turns an answer's card into a
stale-price warning. So that a test of anything else does not change with the calendar, every test here runs on the day
after the table's date; the tests of the price's age set the day themselves."""
from __future__ import annotations

from datetime import date, timedelta

import pytest


@pytest.fixture(autouse=True)
def fresh_prices(monkeypatch: pytest.MonkeyPatch) -> date:
    from g6.llm_router import advice, registry

    day = date.fromisoformat(registry.table()["prices_as_of"]) + timedelta(days=1)
    monkeypatch.setattr(advice, "_today", lambda: day, raising=False)
    return day